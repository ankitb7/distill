"""Two-host podcasts through Google's official Cloud Text-to-Speech REST API."""

import asyncio
import base64
import binascii
import io
import os
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import google.auth
import httpx
from google.auth.credentials import Credentials
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request

if TYPE_CHECKING:
    from distill.outputs.podcast_providers import PodcastSource

ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
PROMPT = (
    "Read this podcast dialogue exactly as written. Two knowledgeable engineering colleagues, "
    "warm and conversational but information-dense. Use natural pauses and a measured pace. "
    "Do not add introductions, conclusions, or extra words. Keep each voice consistent."
)


def chunk_dialogue(segments: list[tuple[str, str]], max_bytes: int = 3000) -> list[str]:
    """Keep speaker labels and Unicode intact within Google's 4,000-byte text limit."""
    if not 64 <= max_bytes <= 4000:
        raise ValueError("Dialogue chunk size must be between 64 and 4000 bytes")
    chunks: list[str] = []
    current = ""
    for speaker, text in segments:
        if speaker not in {"alex", "sarah"}:
            raise ValueError(f"Unknown podcast speaker: {speaker}")
        prefix = f"{speaker.title()}: "
        remaining = text.strip()
        while remaining:
            budget = max_bytes - len(prefix.encode())
            part = remaining.encode()[:budget].decode("utf-8", errors="ignore")
            if len(part) < len(remaining) and " " in part:
                part = part.rsplit(" ", 1)[0]
            remaining = remaining[len(part) :].lstrip()
            line = prefix + part
            candidate = f"{current}\n{line}" if current else line
            if len(candidate.encode()) > max_bytes:
                chunks.append(current)
                current = line
            else:
                current = candidate
    if current:
        chunks.append(current)
    if not chunks:
        raise ValueError("Podcast script contains no spoken dialogue")
    return chunks


def validated_pcm(audio: bytes, text: str) -> bytes:
    """Validate a complete 24 kHz mono WAV before appending it to an episode."""
    with wave.open(io.BytesIO(audio), "rb") as chunk:
        if (chunk.getnchannels(), chunk.getsampwidth(), chunk.getframerate()) != (1, 2, 24000):
            raise ValueError("Unexpected audio format")
        duration = chunk.getnframes() / chunk.getframerate()
        frames = chunk.readframes(chunk.getnframes())
        if not frames or len(frames) != chunk.getnframes() * 2:
            raise ValueError("Empty or incomplete audio")
        if not len(text.split()) / 6 <= duration < 600:
            raise ValueError("Audio may be truncated; unexpected duration")
        return frames


@dataclass(frozen=True)
class GeminiTTSProvider:
    project: str = ""
    model: str = "gemini-2.5-pro-tts"
    voice_a: str = "Charon"
    voice_b: str = "Kore"

    def _authenticate(self) -> tuple[Credentials, str]:
        try:
            credentials, detected_project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            project = self.project or os.getenv("GOOGLE_CLOUD_PROJECT") or detected_project
            project = project or getattr(credentials, "quota_project_id", None)
            if not project:
                raise RuntimeError("Set GOOGLE_CLOUD_PROJECT or podcast.gemini_project.")
            if not credentials.valid:
                credentials.refresh(Request())
            return credentials, project
        except GoogleAuthError as exc:
            raise RuntimeError(
                "Google Cloud authentication failed. Run gcloud auth application-default login, "
                "or configure GOOGLE_APPLICATION_CREDENTIALS."
            ) from exc

    async def generate(
        self, source: "PodcastSource", output_dir: Path, on_status: Callable[[str], None]
    ) -> Path:
        from distill.outputs.podcast_providers import _generate_script, _parse_script

        # Fail before paying for a script if Cloud credentials are unavailable.
        auth = await asyncio.to_thread(self._authenticate)
        on_status("Generating podcast script with Claude...")
        script = await _generate_script(source)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"podcast-script-{source.label}.md").write_text(script)
        path = output_dir / f"podcast-{source.label}.wav"
        await self.synthesize(_parse_script(script), path, on_status, auth=auth)
        return path

    async def synthesize(
        self,
        segments: list[tuple[str, str]],
        output_path: Path,
        on_status: Callable[[str], None],
        *,
        auth: tuple[Credentials, str] | None = None,
    ) -> None:
        chunks = chunk_dialogue(segments)
        credentials, project = auth or await asyncio.to_thread(self._authenticate)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=180) as client:
            with tempfile.TemporaryDirectory(dir=output_path.parent) as directory:
                temporary = Path(directory) / "episode.wav"
                with wave.open(str(temporary), "wb") as output:
                    output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                    for index, text in enumerate(chunks, 1):
                        if not credentials.valid:
                            await asyncio.to_thread(credentials.refresh, Request())
                        payload = {
                            "input": {"text": text, "prompt": PROMPT},
                            "voice": {
                                "languageCode": "en-US",
                                "modelName": self.model,
                                "multiSpeakerVoiceConfig": {
                                    "speakerVoiceConfigs": [
                                        {"speakerAlias": "Alex", "speakerId": self.voice_a},
                                        {"speakerAlias": "Sarah", "speakerId": self.voice_b},
                                    ]
                                },
                            },
                            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000},
                        }
                        for attempt in range(3):
                            response = await client.post(
                                ENDPOINT,
                                headers={
                                    "Authorization": f"Bearer {credentials.token}",
                                    "x-goog-user-project": project,
                                },
                                json=payload,
                            )
                            if (
                                response.status_code not in {429, 500, 502, 503, 504}
                                or attempt == 2
                            ):
                                break
                            await asyncio.sleep(2**attempt)
                        if response.is_error:
                            raise RuntimeError(
                                f"Google TTS request failed ({response.status_code}). "
                                "Check Cloud TTS API enablement, billing, "
                                "Vertex AI User permissions, "
                                "and quota for the configured project."
                            )
                        try:
                            audio = base64.b64decode(response.json()["audioContent"], validate=True)
                            output.writeframes(validated_pcm(audio, text))
                        except (KeyError, ValueError, binascii.Error, wave.Error, EOFError) as exc:
                            raise RuntimeError(
                                f"Invalid Google TTS audio in chunk {index}: {exc}"
                            ) from exc
                        on_status(f"Gemini TTS: audio section {index}/{len(chunks)} ready")
                temporary.replace(output_path)
