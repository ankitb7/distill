from unittest.mock import patch

import pytest

from distill.models import CollectedArticle, Source
from distill.processing.extractor import ExtractedArticle, ExtractionMethod
from distill.processing.intake import (
    IntakeStatus,
    ManualArticleRequest,
    add_manual_articles,
    ingest_articles,
)


def test_ingest_articles_preserves_collected_article_semantics(tmp_db):
    article = CollectedArticle(
        url="https://example.com/slack-post",
        title="Shared in Slack",
        source=Source.SLACK,
        tags=["engineering"],
    )

    [result] = ingest_articles(tmp_db, [article])

    assert result.status is IntakeStatus.ADDED
    stored, _ = tmp_db.get_article_with_score(result.article_id)
    assert stored.source is Source.SLACK
    assert stored.tags == ["engineering"]


@pytest.mark.asyncio
async def test_add_manual_articles_owns_enrichment_and_conventions(tmp_db):
    extraction = ExtractedArticle(
        requested_url="https://example.com/article",
        url="https://example.com/article",
        title="Extracted title",
        content="content " * 20,
        method=ExtractionMethod.TRAFILATURA,
    )
    with patch("distill.processing.intake.extract_articles", return_value=[extraction]) as extract:
        [result] = await add_manual_articles(tmp_db, [ManualArticleRequest("example.com/article")])

    extract.assert_awaited_once_with(["https://example.com/article"])
    assert result.status is IntakeStatus.EXTRACTED
    stored, _ = tmp_db.get_article_with_score(result.article_id)
    assert stored.title == "Extracted title"
    assert stored.source is Source.RSS
    assert stored.source_id == "manual:https://example.com/article"
    assert stored.tags == ["manual"]
    assert stored.content_text == "content " * 20


@pytest.mark.asyncio
async def test_add_manual_articles_reports_duplicates(tmp_db):
    extraction = ExtractedArticle(
        requested_url="https://example.com/article",
        url="https://example.com/article",
        title=None,
        content=None,
        method=None,
    )
    with patch("distill.processing.intake.extract_articles", return_value=[extraction]):
        first = await add_manual_articles(
            tmp_db, [ManualArticleRequest("https://example.com/article", "Chosen title")]
        )
        second = await add_manual_articles(
            tmp_db, [ManualArticleRequest("https://example.com/article", "Chosen title")]
        )

    assert first[0].status is IntakeStatus.ADDED
    assert second[0].status is IntakeStatus.EXISTS
