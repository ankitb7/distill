"""Optional Podcastfy provider, isolated from the dashboard's dependencies and event loop."""

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from distill.config import PROJECT_ROOT
from distill.outputs.podcast_providers import PodcastSource, StatusCallback, _article_text

SCRIPT_PROVIDERS = {"anthropic": "Claude (Anthropic)", "openai": "OpenAI", "gemini": "Gemini"}
SCRIPT_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5-20250929",
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-3.8-flash",
}
WORKER = Path(__file__).with_name("podcastfy_worker.py")


class PodcastfySettings(BaseModel):
    script_provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    models: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_MODELS))
    python: Path = Path(".venv-podcastfy/bin/python")
    voice_a: str = Field(default="en-US-AndrewMultilingualNeural", min_length=1)
    voice_b: str = Field(default="en-US-AvaMultilingualNeural", min_length=1)
    target_words: int = Field(default=2000, ge=100, le=3000)
    timeout_seconds: int = Field(default=1800, ge=30, le=7200)

    @property
    def interpreter(self) -> Path:
        return PROJECT_ROOT / self.python


@dataclass(frozen=True)
class PodcastfyProvider:
    settings: PodcastfySettings

    def validate_setup(self) -> None:
        if not self.settings.interpreter.is_file():
            raise ValueError("Podcastfy is not installed. Run: uv run distill podcast-setup")
        key = SCRIPT_KEYS[self.settings.script_provider]
        if not os.environ.get(key):
            raise ValueError(
                f"Set {key} in .env for Podcastfy script generation, then restart Distill."
            )
        if not self.settings.models.get(self.settings.script_provider, "").strip():
            raise ValueError("Set the selected provider's model under podcast.podcastfy.models.")

    async def generate(
        self, source: PodcastSource, output_dir: Path, on_status: StatusCallback
    ) -> Path:
        self.validate_setup()
        output_dir.mkdir(parents=True, exist_ok=True)
        settings = self.settings.model_dump(mode="json")
        provider = self.settings.script_provider
        settings["model"] = self.settings.models[provider]
        settings["api_key_label"] = SCRIPT_KEYS[provider]
        packet = "\n\n---\n\n".join(
            f"Article {index}: {article.title}\nOriginal URL: {article.url}\n"
            f"Content: {_article_text(source, article)}\n"
            f"Editorial context: {score.reasoning or ''}"
            for index, (article, score) in enumerate(source.articles, 1)
        )
        on_status(
            f"Podcastfy: writing with {SCRIPT_PROVIDERS[provider]}, then voicing with Edge..."
        )
        with tempfile.TemporaryDirectory(prefix=".podcastfy-", dir=output_dir) as folder:
            work = Path(folder).resolve()
            request = work / "request.json"
            request.write_text(json.dumps({"settings": settings, "text": packet}))
            env = dict(os.environ, LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
            # Third-party logs may contain request details; never forward them to the UI.
            process = await asyncio.create_subprocess_exec(
                str(self.settings.interpreter),
                "-I",
                str(WORKER),
                str(request),
                cwd=work,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=self.settings.timeout_seconds)
            except (TimeoutError, asyncio.CancelledError) as exc:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                if isinstance(exc, TimeoutError):
                    raise RuntimeError(
                        "Podcastfy timed out. Retry or reduce the episode length."
                    ) from exc
                raise
            result_path = work / "result.json"
            result = json.loads(result_path.read_text()) if result_path.exists() else {}
            if process.returncode or not result.get("ok"):
                raise RuntimeError(
                    result.get("error", "Podcastfy failed. Run podcast-setup and retry.")
                )
            audio = work / "episode.mp3"
            transcript = work / "transcript.txt"
            if not audio.is_file() or audio.stat().st_size < 1024 or not transcript.is_file():
                raise RuntimeError("Podcastfy did not produce a complete episode.")
            destination = output_dir / f"podcast-{source.label}.mp3"
            transcript.replace(output_dir / f"podcast-script-{source.label}.txt")
            audio.replace(destination)
        on_status("Podcastfy episode ready.")
        return destination
