import base64
import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from google.auth.exceptions import DefaultCredentialsError

from distill.outputs.gemini_tts import GeminiTTSProvider, chunk_dialogue
from distill.outputs.podcast_providers import PodcastSource, get_podcast_provider


def audio_response(text: str, value: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        frames = max(24000, len(text.split()) * 8000)
        output.writeframes(value.to_bytes(2, "little") * frames)
    return buffer.getvalue()


@pytest.fixture
def google_auth(monkeypatch):
    credentials = SimpleNamespace(valid=True, token="test-token")
    monkeypatch.setattr(
        GeminiTTSProvider, "_authenticate", lambda self: (credentials, "test-project")
    )
    return credentials


def test_provider_configuration():
    provider = get_podcast_provider(
        "gemini-tts",
        {
            "gemini_project": "my-project",
            "gemini_model": "gemini-2.5-flash-tts",
            "gemini_voice_a": "Puck",
            "gemini_voice_b": "Kore",
        },
    )
    assert provider == GeminiTTSProvider("my-project", "gemini-2.5-flash-tts", "Puck", "Kore")


def test_chunking_preserves_unicode_text_order_and_speaker_labels():
    segments = [("alex", "déjà vu 東京 " * 600), ("sarah", "🙂" * 1600)]
    chunks = chunk_dialogue(segments)
    assert len(chunks) > 2
    assert all(0 < len(chunk.encode()) <= 3000 for chunk in chunks)
    reconstructed = {"Alex": [], "Sarah": []}
    for chunk in chunks:
        for line in chunk.splitlines():
            speaker, text = line.split(": ", 1)
            reconstructed[speaker].append(text)
    for speaker, text in segments:
        assert "".join("".join(reconstructed[speaker.title()]).split()) == "".join(text.split())
    assert chunks[-1].startswith("Sarah: ")


@pytest.mark.parametrize("segments", [[], [("alex", " ")], [("unknown", "Hello")]])
def test_rejects_empty_or_unknown_dialogue(segments):
    with pytest.raises(ValueError):
        chunk_dialogue(segments)


@pytest.mark.asyncio
async def test_synthesis_uses_official_multispeaker_api_and_joins_pcm(tmp_path, google_auth):
    requests = []
    pcm = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == "https://texttospeech.googleapis.com/v1/text:synthesize"
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.headers["x-goog-user-project"] == "test-project"
        audio = audio_response(body["input"]["text"], len(requests))
        with wave.open(io.BytesIO(audio)) as wav:
            pcm.append(wav.readframes(wav.getnframes()))
        return httpx.Response(200, json={"audioContent": base64.b64encode(audio).decode()})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    output = tmp_path / "podcast.wav"
    statuses = []
    with patch("distill.outputs.gemini_tts.httpx.AsyncClient", return_value=client):
        await GeminiTTSProvider().synthesize(
            [("alex", "Evidence matters. " * 250), ("sarah", "Show the result.")],
            output,
            statuses.append,
        )
    assert len(requests) >= 2
    assert len(statuses) == len(requests)
    assert requests[0]["voice"]["modelName"] == "gemini-2.5-pro-tts"
    assert requests[0]["voice"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"] == [
        {"speakerAlias": "Alex", "speakerId": "Charon"},
        {"speakerAlias": "Sarah", "speakerId": "Kore"},
    ]
    with wave.open(str(output)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 24000
        assert wav.readframes(wav.getnframes()) == b"".join(pcm)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "empty", "invalid", "truncated"])
async def test_failed_audio_never_overwrites_existing_episode(tmp_path, google_auth, failure):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            audio = audio_response(json.loads(request.content)["input"]["text"])
            return httpx.Response(200, json={"audioContent": base64.b64encode(audio).decode()})
        if failure == "permission":
            return httpx.Response(403)
        if failure == "invalid":
            return httpx.Response(200, json={"audioContent": "not-base64!"})
        audio = audio_response("Hello")[:-12] if failure == "truncated" else b""
        return httpx.Response(200, json={"audioContent": base64.b64encode(audio).decode()})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    output = tmp_path / "existing.wav"
    output.write_bytes(b"original episode")
    with patch("distill.outputs.gemini_tts.httpx.AsyncClient", return_value=client):
        with pytest.raises(RuntimeError):
            await GeminiTTSProvider().synthesize(
                [("alex", "hello " * 600)],
                output,
                lambda _: None,
            )
    assert output.read_bytes() == b"original episode"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.asyncio
async def test_retries_transient_errors(tmp_path, google_auth):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "audioContent": base64.b64encode(audio_response("Hello")).decode(),
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with (
        patch("distill.outputs.gemini_tts.httpx.AsyncClient", return_value=client),
        patch("distill.outputs.gemini_tts.asyncio.sleep", new_callable=AsyncMock),
    ):
        await GeminiTTSProvider().synthesize(
            [("alex", "Hello")], tmp_path / "episode.wav", lambda _: None
        )
    assert calls == 2


@pytest.mark.asyncio
async def test_missing_credentials_fails_before_script_generation(tmp_path):
    with (
        patch(
            "distill.outputs.gemini_tts.google.auth.default", side_effect=DefaultCredentialsError()
        ),
        patch(
            "distill.outputs.podcast_providers._generate_script", new_callable=AsyncMock
        ) as script,
    ):
        with pytest.raises(RuntimeError, match="application-default login"):
            await GeminiTTSProvider().generate(
                PodcastSource("test", [], {}), tmp_path, lambda _: None
            )
    script.assert_not_called()


@pytest.mark.asyncio
async def test_generate_saves_script_and_returns_playable_audio(tmp_path, google_auth):
    def handle(request):
        audio = audio_response(json.loads(request.content)["input"]["text"])
        return httpx.Response(200, json={"audioContent": base64.b64encode(audio).decode()})

    script = "[Alex] Welcome.\n[Sarah] Show me the evidence."
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with (
        patch("distill.outputs.gemini_tts.httpx.AsyncClient", return_value=client),
        patch(
            "distill.outputs.podcast_providers._generate_script",
            new_callable=AsyncMock,
            return_value=script,
        ),
    ):
        path = await GeminiTTSProvider().generate(
            PodcastSource("test", [], {}), tmp_path, lambda _: None
        )
    assert path == tmp_path / "podcast-test.wav"
    assert (tmp_path / "podcast-script-test.md").read_text() == script
    with wave.open(str(path)) as audio:
        assert audio.getnframes() > 0
