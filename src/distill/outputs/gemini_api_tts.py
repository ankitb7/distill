"""Two-host podcasts through the official Gemini Developer API."""

import asyncio
import base64
import binascii
import os
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from distill.outputs.gemini_tts import chunk_dialogue, validated_pcm

if TYPE_CHECKING:
    from distill.outputs.podcast_providers import PodcastSource

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
STYLE = "Warm, conversational engineering podcast; natural pauses, measured pace."


@dataclass(frozen=True)
class GeminiAPITTSProvider:
    model: str = "gemini-3.8-flash-tts"
    voice_a: str = "Charon"
    voice_b: str = "Kore"

    def _api_key(self) -> str:
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "Set GEMINI_API_KEY in .env using a key from https://aistudio.google.com/api-keys. "
                "Use a free-tier project for free Gemini Flash TTS quota."
            )
        return key

    async def generate(
        self, source: "PodcastSource", output_dir: Path, on_status: Callable[[str], None]
    ) -> Path:
        from distill.outputs.podcast_providers import _generate_script, _parse_script

        # Check configuration before paying for Claude script generation.
        self._api_key()
        on_status("Generating podcast script with Claude...")
        script = await _generate_script(source)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"podcast-script-{source.label}.md").write_text(script)
        path = output_dir / f"podcast-{source.label}.wav"
        await self.synthesize(_parse_script(script), path, on_status)
        return path

    async def synthesize(
        self,
        segments: list[tuple[str, str]],
        output_path: Path,
        on_status: Callable[[str], None],
    ) -> None:
        key = self._api_key()
        chunks = chunk_dialogue([(speaker, " ".join(text.split())) for speaker, text in segments])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=180) as client:
            with tempfile.TemporaryDirectory(dir=output_path.parent) as directory:
                temporary = Path(directory) / "episode.wav"
                with wave.open(str(temporary), "wb") as output:
                    output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                    for index, text in enumerate(chunks, 1):
                        payload = self._payload(text)
                        for attempt in range(3):
                            response = await client.post(
                                ENDPOINT, headers={"x-goog-api-key": key}, json=payload
                            )
                            if (
                                response.status_code not in {429, 500, 502, 503, 504}
                                or attempt == 2
                            ):
                                break
                            try:
                                delay = float(response.headers.get("retry-after", 2**attempt))
                            except ValueError:
                                delay = 2**attempt
                            await asyncio.sleep(max(0, min(delay, 60)))
                        if response.is_error:
                            hint = (
                                "Free-tier quota may be exhausted; retry later or check "
                                "your project's limits in Google AI Studio."
                                if response.status_code == 429
                                else "Check the API key, Gemini API access, and model availability."
                            )
                            raise RuntimeError(
                                f"Gemini Developer API request failed ({response.status_code}). "
                                f"{hint} No provider fallback was attempted."
                            )
                        try:
                            data = response.json()
                            if data.get("status") != "completed":
                                raise ValueError("Generation did not complete")
                            audio = [
                                part
                                for step in data["steps"]
                                if step.get("type") == "model_output"
                                for part in step.get("content", [])
                                if part.get("type") == "audio"
                            ]
                            if len(audio) != 1 or audio[0].get("mime_type") != "audio/wav":
                                raise ValueError("Expected one complete WAV audio response")
                            decoded = base64.b64decode(audio[0]["data"], validate=True)
                            output.writeframes(validated_pcm(decoded, text))
                        except (
                            KeyError,
                            TypeError,
                            AttributeError,
                            ValueError,
                            binascii.Error,
                            wave.Error,
                            EOFError,
                        ) as exc:
                            raise RuntimeError(
                                f"Invalid Gemini Developer API audio in section {index}"
                            ) from exc
                        on_status(f"Gemini Flash TTS: audio section {index}/{len(chunks)} ready")
                temporary.replace(output_path)

    def _payload(self, text: str) -> dict:
        content = []
        for line in text.splitlines():
            speaker, dialogue = line.split(": ", 1)
            content.append(
                {
                    "type": "text",
                    "text": dialogue,
                    "annotations": [
                        {"type": "speech_metadata", "speaker": speaker, "style": STYLE}
                    ],
                }
            )
        return {
            "model": self.model,
            "input": [{"type": "user_input", "content": content}],
            "response_format": {"type": "audio", "mime_type": "audio/wav", "sample_rate": 24000},
            "generation_config": {
                "speech_config": {
                    "mode": "conversational",
                    "speakers": [
                        {"speaker": "Alex", "voice": self.voice_a},
                        {"speaker": "Sarah", "voice": self.voice_b},
                    ],
                }
            },
        }
