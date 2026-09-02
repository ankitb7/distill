import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from distill.models import Article, CollectedArticle, ScoreBreakdown, Source
from distill.processing.scorer import (
    assessment_version,
    compute_composite_score,
    compute_engagement_score,
    score_articles,
    score_with_llm,
)


def _make_article(points: int = 0, comment_count: int = 0, **kwargs) -> Article:
    return Article(
        id=kwargs.get("id", 1),
        url="https://example.com/test",
        normalized_url="https://example.com/test",
        title="Test",
        source=kwargs.get("source", Source.HACKERNEWS),
        collected_at=datetime.now(),
        points=points,
        comment_count=comment_count,
    )


def test_engagement_score_basic():
    articles = [
        _make_article(id=i, points=p, comment_count=c)
        for i, (p, c) in enumerate([(10, 5), (50, 20), (100, 50), (200, 100)])
    ]
    low = compute_engagement_score(articles[0], articles)
    high = compute_engagement_score(articles[-1], articles)
    assert high > low
    assert 0 <= low <= 1
    assert 0 <= high <= 1


def test_engagement_score_no_points():
    articles = [_make_article(id=1)]
    score = compute_engagement_score(articles[0], articles)
    assert score == 0.5


def test_engagement_score_uniform():
    articles = [_make_article(id=i, points=50, comment_count=10) for i in range(5)]
    scores = [compute_engagement_score(a, articles) for a in articles]
    assert all(s == scores[0] for s in scores)


def test_engagement_is_normalized_within_source():
    target = _make_article(id=1, points=20, comment_count=5)
    same_source = _make_article(id=2, points=40, comment_count=10)
    unrelated_outlier = _make_article(id=3, points=10_000, comment_count=5_000, source=Source.DEVTO)

    with_outlier = compute_engagement_score(target, [target, same_source, unrelated_outlier])
    without_outlier = compute_engagement_score(target, [target, same_source])

    assert with_outlier == without_outlier


def test_noise_penalty_reduces_composite_and_values_are_bounded():
    useful = ScoreBreakdown(
        relevance=1,
        technical_depth=1,
        novelty=1,
        applicability=1,
        evidence_quality=1,
    )
    noisy = useful.model_copy(update={"noise_penalty": 1})

    assert compute_composite_score(1, useful, {}) == 1
    assert compute_composite_score(1, noisy, {}) == 0.75


def test_assessment_version_changes_with_reader_profile():
    first = assessment_version({"reader_profile": {"mission": "A"}}, "model")
    second = assessment_version({"reader_profile": {"mission": "B"}}, "model")

    assert first != second


def test_assessment_version_changes_with_evidence_window():
    first = assessment_version({"scoring": {"content_preview_chars": 3000}}, "model")
    second = assessment_version({"scoring": {"content_preview_chars": 6000}}, "model")

    assert first != second


@pytest.mark.asyncio
async def test_llm_assessment_retries_rate_limit(monkeypatch):
    article = _make_article(source=Source.RSS)
    article.summary = "A production case study with measured results."
    responses = [
        httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com")),
        httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.anthropic.com"),
            json={
                "content": [
                    {
                        "text": json.dumps(
                            {
                                "relevance": 0.9,
                                "technical_depth": 0.8,
                                "novelty": 0.7,
                                "applicability": 0.9,
                                "evidence_quality": 0.8,
                                "noise_penalty": 0.1,
                                "recommended_action": "Run the experiment",
                                "reasoning": "Measured production evidence",
                            }
                        )
                    }
                ]
            },
        ),
    ]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return responses.pop(0)

    monkeypatch.setattr(
        "distill.processing.scorer.httpx.AsyncClient", lambda **kwargs: FakeClient()
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    scores = await score_with_llm(
        [article], config={"scoring": {"max_retries": 1, "retry_base_seconds": 0}}
    )

    assert scores[article.id].recommended_action == "Run the experiment"
    assert responses == []


@pytest.mark.asyncio
async def test_failed_assessment_is_capped_and_remains_retryable(tmp_db):
    article_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/retry",
            title="Retry assessment",
            source=Source.HACKERNEWS,
            points=100,
            content_text="Detailed article content " * 30,
            content_length=750,
        )
    )
    config = {"scoring": {"model": "test-model"}, "reader_profile": {"mission": "test"}}

    with (
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}),
        patch("distill.processing.scorer.score_with_llm", new=AsyncMock(return_value={})),
    ):
        await score_articles(tmp_db, config)

    _, score = tmp_db.get_article_with_score(article_id)
    pending = tmp_db.get_articles_for_assessment(assessment_version(config, "test-model"))

    assert score.composite_score <= 0.4
    assert score.status == "failed"
    assert article_id in {article.id for article in pending}
