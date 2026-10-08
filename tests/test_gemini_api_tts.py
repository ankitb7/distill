import base64
import io
import json
import wave
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from distill.outputs.gemini_api_tts import GeminiAPITTSProvider
from distill.outputs.podcast_providers import PodcastSource, get_podcast_provider


@pytest.fixture(autouse=True)
def gemini_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


def audio_response(request: httpx.Request, value: int = 1) -> tuple[dict, bytes]:
    turns = json.loads(request.content)["input"][0]["content"]
    words = sum(len(turn["text"].split()) for turn in turns)
    pcm = value.to_bytes(2, "little") * max(24000, words * 8000)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        output.writeframes(pcm)
    return {
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "audio",
                        "mime_type": "audio/wav",
                        "data": base64.b64encode(buffer.getvalue()).decode(),
                    }
                ],
            }
        ],
    }, pcm


def test_provider_configuration():
    provider = get_podcast_provider(
        "gemini-api-tts",
        {
            "gemini_api_model": "gemini-3.8-flash-lite-tts",
            "gemini_voice_a": "Charon",
            "gemini_voice_b": "Kore",
            "gemini_style_a": "Curious and relaxed",
            "gemini_style_b": "Thoughtful and playful",
        },
    )
    assert provider == GeminiAPITTSProvider(
        "gemini-3.8-flash-lite-tts",
        "Charon",
        "Kore",
        "Curious and relaxed",
        "Thoughtful and playful",
    )


@pytest.mark.asyncio
async def test_developer_api_preserves_turns_and_joins_validated_audio(tmp_path):
    requests = []
    pcm = []

    def handle(request):
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
        assert request.headers["x-goog-api-key"] == "test-gemini-key"
        assert "authorization" not in request.headers
        assert "test-gemini-key" not in str(request.url)
        body = json.loads(request.content)
        requests.append(body)
        data, frames = audio_response(request, len(requests))
        pcm.append(frames)
        return httpx.Response(200, json=data)

    segments = [("alex", "déjà vu 東京 " * 400), ("sarah", "Show\nme the evidence.")]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    path = tmp_path / "episode.wav"
    statuses = []
    with patch("distill.outputs.gemini_api_tts.httpx.AsyncClient", return_value=client):
        await GeminiAPITTSProvider().synthesize(segments, path, statuses.append)
    assert len(requests) > 1
    assert len(statuses) == len(requests)
    assert all(request["model"] == "gemini-3.8-flash-tts" for request in requests)
    assert requests[0]["generation_config"]["speech_config"]["speakers"] == [
        {"speaker": "Alex", "voice": "Puck"},
        {"speaker": "Sarah", "voice": "Aoede"},
    ]
    spoken = {"Alex": [], "Sarah": []}
    for request in requests:
        for turn in request["input"][0]["content"]:
            speaker = turn["annotations"][0]["speaker"]
            spoken[speaker].append(turn["text"])
    for speaker, text in segments:
        assert " ".join(spoken[speaker.title()]).split() == text.split()
    with wave.open(str(path)) as audio:
        assert audio.readframes(audio.getnframes()) == b"".join(pcm)


def test_delivery_directions_stay_out_of_spoken_dialogue():
    provider = get_podcast_provider(
        "gemini-api-tts",
        {"gemini_style_a": "Curious and relaxed", "gemini_style_b": "Thoughtful and playful"},
    )
    turns = provider._payload("Alex: Why does this matter?\nSarah: Consider the review.")["input"][
        0
    ]["content"]
    assert [turn["text"] for turn in turns] == ["Why does this matter?", "Consider the review."]
    assert [turn["annotations"][0] for turn in turns] == [
        {"type": "speech_metadata", "speaker": "Alex", "style": "Curious and relaxed"},
        {"type": "speech_metadata", "speaker": "Sarah", "style": "Thoughtful and playful"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["quota", "permission", "incomplete", "empty", "invalid", "truncated", "format"]
)
async def test_failed_section_preserves_existing_episode(tmp_path, failure):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        data, _ = audio_response(request)
        if calls == 1:
            return httpx.Response(200, json=data)
        if failure == "quota":
            return httpx.Response(429, json={"error": {"message": "test-gemini-key"}})
        if failure == "permission":
            return httpx.Response(403)
        audio = data["steps"][0]["content"][0]
        if failure == "incomplete":
            data["status"] = "in_progress"
        elif failure == "empty":
            data["steps"] = []
        elif failure == "invalid":
            audio["data"] = "not-base64!"
        elif failure == "truncated":
            audio["data"] = base64.b64encode(base64.b64decode(audio["data"])[:-12]).decode()
        elif failure == "format":
            audio["mime_type"] = "audio/l16"
        return httpx.Response(200, json=data)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    path = tmp_path / "episode.wav"
    path.write_bytes(b"existing episode")
    with (
        patch("distill.outputs.gemini_api_tts.httpx.AsyncClient", return_value=client),
        patch("distill.outputs.gemini_api_tts.asyncio.sleep", new_callable=AsyncMock),
    ):
        with pytest.raises(RuntimeError) as error:
            await GeminiAPITTSProvider().synthesize(
                [("alex", "hello " * 650)], path, lambda _: None
            )
    assert "test-gemini-key" not in str(error.value)
    if failure == "quota":
        assert "quota" in str(error.value)
        assert calls == 4  # First section, followed by three attempts for the second.
    assert path.read_bytes() == b"existing episode"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
async def test_retry_respects_retry_after(tmp_path):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "15"})
        data, _ = audio_response(request)
        return httpx.Response(200, json=data)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with (
        patch("distill.outputs.gemini_api_tts.httpx.AsyncClient", return_value=client),
        patch("distill.outputs.gemini_api_tts.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        await GeminiAPITTSProvider().synthesize(
            [("alex", "Hello")], tmp_path / "episode.wav", lambda _: None
        )
    sleep.assert_awaited_once_with(15)
    assert calls == 2


@pytest.mark.asyncio
async def test_missing_key_fails_before_script_generation(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with patch(
        "distill.outputs.podcast_providers._generate_script", new_callable=AsyncMock
    ) as script:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            await GeminiAPITTSProvider().generate(
                PodcastSource("test", [], {}), tmp_path, lambda _: None
            )
    script.assert_not_called()


@pytest.mark.asyncio
async def test_generate_saves_script_with_google_api_key_alias(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GOOGLE_API_KEY", "alias-key")

    def handle(request):
        assert request.headers["x-goog-api-key"] == "alias-key"
        data, _ = audio_response(request)
        return httpx.Response(200, json=data)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    script = "[Alex] Welcome.\n[Sarah] Show me the evidence."
    with (
        patch("distill.outputs.gemini_api_tts.httpx.AsyncClient", return_value=client),
        patch(
            "distill.outputs.podcast_providers._generate_script",
            new_callable=AsyncMock,
            return_value=script,
        ),
    ):
        path = await GeminiAPITTSProvider().generate(
            PodcastSource("test", [], {}), tmp_path, lambda _: None
        )
    assert path == tmp_path / "podcast-test.wav"
    assert (tmp_path / "podcast-script-test.md").read_text() == script
    with wave.open(str(path)) as audio:
        assert audio.getnframes() > 0
