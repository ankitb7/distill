from unittest.mock import AsyncMock, patch

import pytest

from distill.models import CollectedArticle, Source
from distill.outputs.podcast import generate_podcast
from distill.outputs.podcast_providers import (
    EdgeTTSProvider,
    NotebookLMProvider,
    get_podcast_provider,
)


def test_get_podcast_provider_returns_configured_adapter():
    assert isinstance(get_podcast_provider("notebooklm", {}), NotebookLMProvider)
    edge = get_podcast_provider("edge-tts", {"voice_a": "A", "voice_b": "B"})
    assert edge == EdgeTTSProvider(voice_a="A", voice_b="B")


def test_get_podcast_provider_rejects_unknown_adapter():
    with pytest.raises(ValueError, match="Unknown podcast provider"):
        get_podcast_provider("other", {})


@pytest.mark.asyncio
async def test_generate_podcast_uses_provider_seam(tmp_db, tmp_path):
    article_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/podcast",
            title="Podcast Article",
            source=Source.RSS,
            content_text="content " * 100,
            content_length=800,
        )
    )
    audio_path = tmp_path / "generated.mp3"
    audio_path.write_bytes(b"audio")
    provider = AsyncMock()
    provider.generate.return_value = audio_path

    with patch("distill.outputs.podcast.get_podcast_provider", return_value=provider):
        result = await generate_podcast(
            tmp_db,
            {"podcast": {"provider": "fake"}},
            tmp_path,
            article_ids=[article_id],
        )

    assert result == audio_path
    source = provider.generate.await_args.args[0]
    assert source.on_demand is True
    assert [article.id for article, _ in source.articles] == [article_id]
