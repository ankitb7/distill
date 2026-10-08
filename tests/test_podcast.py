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
    episode = tmp_db.list_podcasts()[0]
    assert episode.week_label == source.label
    assert episode.podcast_path == str(audio_path)
    assert [article.url for article in tmp_db.get_podcast_articles(source.label)] == [
        "https://example.com/podcast"
    ]


@pytest.mark.asyncio
async def test_custom_podcasts_preserve_selection_and_have_distinct_labels(tmp_db, tmp_path):
    ids = [
        tmp_db.insert_article(
            CollectedArticle(
                url=f"https://example.com/{i}",
                title=f"Article {i}",
                source=Source.RSS,
                content_text="content " * 100,
            )
        )
        for i in range(3)
    ]
    audio = tmp_path / "generated.mp3"
    audio.write_bytes(b"audio")
    provider = AsyncMock()
    provider.generate.return_value = audio
    with patch("distill.outputs.podcast.get_podcast_provider", return_value=provider):
        for _ in range(2):
            await generate_podcast(tmp_db, {}, tmp_path, article_ids=[ids[2], ids[0], ids[2]])

    assert len(tmp_db.list_podcasts()) == 2
    for call in provider.generate.await_args_list:
        assert [a.id for a, _ in call.args[0].articles] == [ids[2], ids[0]]
    for episode in tmp_db.list_podcasts():
        assert [a.title for a in tmp_db.get_podcast_articles(episode.week_label)] == [
            "Article 2",
            "Article 0",
        ]


@pytest.mark.asyncio
async def test_empty_or_missing_custom_selection_never_generates_weekly_podcast(tmp_db, tmp_path):
    with patch("distill.outputs.podcast.get_podcast_provider") as provider:
        assert await generate_podcast(tmp_db, {}, tmp_path, article_ids=[]) is None
        with pytest.raises(ValueError, match="Articles not found: 999"):
            await generate_podcast(tmp_db, {}, tmp_path, article_ids=[999])
    provider.assert_not_called()
    assert tmp_db.list_podcasts() == []


@pytest.mark.asyncio
async def test_failed_generation_does_not_publish_episode(tmp_db, tmp_path):
    aid = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/failure",
            title="Article",
            source=Source.RSS,
            content_text="content " * 100,
        )
    )
    provider = AsyncMock()
    provider.generate.side_effect = RuntimeError("Provider unavailable")
    with patch("distill.outputs.podcast.get_podcast_provider", return_value=provider):
        with pytest.raises(RuntimeError, match="Provider unavailable"):
            await generate_podcast(tmp_db, {}, tmp_path, article_ids=[aid])
    assert tmp_db.list_podcasts() == []


@pytest.mark.asyncio
async def test_weekly_podcast_records_source_links(tmp_db, tmp_path):
    aid = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/manual",
            title="Manual article",
            source=Source.RSS,
            tags=["manual"],
            content_text="content " * 100,
        )
    )
    audio = tmp_path / "weekly.mp3"
    audio.write_bytes(b"audio")
    provider = AsyncMock()
    provider.generate.return_value = audio
    with patch("distill.outputs.podcast.get_podcast_provider", return_value=provider):
        await generate_podcast(tmp_db, {}, tmp_path)
    source = provider.generate.await_args.args[0]
    assert source.on_demand is False
    assert [a.id for a, _ in source.articles] == [aid]
    assert tmp_db.get_podcast_articles(source.label)[0].url == "https://example.com/manual"
