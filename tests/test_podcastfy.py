import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from distill.models import CollectedArticle, Source
from distill.outputs.podcast_providers import (
    PodcastSource,
    configure_podcast,
    get_podcast_provider,
)
from distill.outputs.podcastfy import PodcastfyProvider, PodcastfySettings
from distill.outputs.podcastfy_worker import clean_dialogue

DIALOGUE = "<Person1>A checkable result.</Person1><Person2>Show the evidence.</Person2>"


@pytest.fixture
def source(tmp_db):
    aid = tmp_db.insert_article(
        CollectedArticle(
            title="Evidence",
            url="https://example.com/evidence",
            source=Source.RSS,
            content_text="A repeatable test shows the behavior changed.",
        )
    )
    article, score = tmp_db.get_articles_by_ids([aid])[0]
    return PodcastSource("test-episode", [(article, score)], {}, on_demand=True)


def test_factory_and_selection_preserve_defaults():
    original = {
        "podcast": {"provider": "gemini-api-tts", "podcastfy": {"script_provider": "anthropic"}}
    }
    selected = configure_podcast(original, "podcastfy-edge", "openai")
    adapter = get_podcast_provider("podcastfy-edge", selected["podcast"])
    assert isinstance(adapter, PodcastfyProvider)
    assert adapter.settings.script_provider == "openai"
    assert original["podcast"]["provider"] == "gemini-api-tts"
    assert original["podcast"]["podcastfy"]["script_provider"] == "anthropic"
    with pytest.raises(ValueError):
        configure_podcast(original, "unknown")
    with pytest.raises(ValueError):
        configure_podcast(original, "podcastfy-edge", "unknown")


@pytest.mark.parametrize(
    "provider,key",
    [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
    ],
)
def test_selected_script_key_is_required(monkeypatch, provider, key):
    monkeypatch.delenv(key, raising=False)
    settings = PodcastfySettings(python=Path(sys.executable), script_provider=provider)
    with pytest.raises(ValueError, match=key):
        PodcastfyProvider(settings).validate_setup()


def test_missing_optional_runtime_has_setup_instruction(tmp_path):
    with pytest.raises(ValueError, match="podcast-setup"):
        PodcastfyProvider(PodcastfySettings(python=tmp_path / "missing")).validate_setup()


@pytest.mark.asyncio
async def test_worker_publishes_only_complete_outputs_without_serializing_keys(
    tmp_path, monkeypatch, source
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-test-key")
    requests = []

    async def spawn(*args, **kwargs):
        request = Path(args[-1])
        requests.append(json.loads(request.read_text()))
        assert "private-test-key" not in request.read_text()
        assert kwargs["env"]["ANTHROPIC_API_KEY"] == "private-test-key"
        assert kwargs["env"]["LANGSMITH_TRACING"] == "false"
        assert "-I" in args
        work = request.parent
        (work / "episode.mp3").write_bytes(b"audio" * 400)
        (work / "transcript.txt").write_text(DIALOGUE)
        (work / "result.json").write_text('{"ok": true}')
        return SimpleNamespace(wait=AsyncMock(return_value=0), returncode=0)

    monkeypatch.setattr("distill.outputs.podcastfy.asyncio.create_subprocess_exec", spawn)
    provider = PodcastfyProvider(PodcastfySettings(python=Path(sys.executable)))
    result = await provider.generate(source, tmp_path, lambda _: None)
    assert result.read_bytes() == b"audio" * 400
    assert (tmp_path / "podcast-script-test-episode.txt").read_text() == DIALOGUE
    assert "https://example.com/evidence" in requests[0]["text"]
    assert "repeatable test" in requests[0]["text"]
    assert not list(tmp_path.glob(".podcastfy-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error", "missing_audio", "timeout", "cancel"])
async def test_failed_or_cancelled_worker_never_publishes(tmp_path, monkeypatch, source, mode):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-test-key")
    process = SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0), kill=lambda: None)

    async def spawn(*args, **kwargs):
        work = Path(args[-1]).parent
        if mode in {"error", "missing_audio"}:
            result = {"ok": mode == "missing_audio", "error": "A safe error"}
            (work / "result.json").write_text(json.dumps(result))
        if mode in {"timeout", "cancel"}:
            process.returncode = None
            process.wait = AsyncMock(
                side_effect=[
                    TimeoutError() if mode == "timeout" else asyncio.CancelledError(),
                    0,
                ]
            )
        return process

    monkeypatch.setattr("distill.outputs.podcastfy.asyncio.create_subprocess_exec", spawn)
    provider = PodcastfyProvider(PodcastfySettings(python=Path(sys.executable)))
    error = asyncio.CancelledError if mode == "cancel" else RuntimeError
    with pytest.raises(error):
        await provider.generate(source, tmp_path, lambda _: None)
    assert not list(tmp_path.iterdir())


def test_dialogue_excludes_non_spoken_preamble():
    assert clean_dialogue("Planning text\n" + DIALOGUE) == DIALOGUE.replace(
        "</Person1><Person2>", "</Person1>\n<Person2>"
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "plain text",
        "<Person1>Only one turn</Person1>",
        DIALOGUE + "<Person1>Truncated",
        DIALOGUE.replace("Show the evidence.", ""),
        DIALOGUE.replace("Show the evidence.", "<laugh>Ha</laugh>"),
        DIALOGUE.replace("<Person2>", "<Person1>").replace("</Person2>", "</Person1>"),
    ],
)
def test_invalid_dialogue_is_rejected_before_synthesis(text):
    with pytest.raises(ValueError):
        clean_dialogue(text)


def test_worker_error_does_not_expose_provider_details(tmp_path, monkeypatch):
    from distill.outputs import podcastfy_worker

    request = tmp_path / "request.json"
    request.write_text("{}")
    monkeypatch.setattr(sys, "argv", ["worker", str(request)])
    with patch.object(podcastfy_worker, "render", side_effect=RuntimeError("secret-key-in-header")):
        assert podcastfy_worker.main() == 1
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["ok"] is False
    assert "secret-key" not in result["error"]
