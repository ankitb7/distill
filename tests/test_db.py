from datetime import UTC, datetime, timedelta

from distill.db import normalize_url
from distill.models import CollectedArticle, ScoreBreakdown, Source


def test_normalize_url_strips_tracking():
    url = "https://www.example.com/article?utm_source=twitter&utm_medium=social&id=123"
    result = normalize_url(url)
    assert "utm_source" not in result
    assert "id=123" in result
    assert result.startswith("https://example.com/")


def test_normalize_url_strips_www_and_trailing_slash():
    assert normalize_url("http://www.example.com/path/") == "https://example.com/path"


def test_insert_article(tmp_db):
    article = CollectedArticle(
        url="https://example.com/test-article",
        title="Test Article",
        author="Test Author",
        source=Source.HACKERNEWS,
        source_id="12345",
        published_at=datetime.now(tz=UTC),
        points=100,
        comment_count=50,
    )
    article_id = tmp_db.insert_article(article)
    assert article_id is not None
    assert article_id > 0


def test_insert_duplicate_url_updates_engagement(tmp_db):
    article1 = CollectedArticle(
        url="https://example.com/same-article",
        title="Same Article",
        source=Source.HACKERNEWS,
        points=50,
        comment_count=10,
    )
    article2 = CollectedArticle(
        url="https://example.com/same-article",
        title="Same Article",
        source=Source.RSS,
        points=100,
        comment_count=20,
    )
    id1 = tmp_db.insert_article(article1)
    id2 = tmp_db.insert_article(article2)
    assert id1 is not None
    assert id2 is None  # duplicate returns None

    stored, _ = tmp_db.get_article_with_score(id1)
    assert stored.points == 100
    assert stored.comment_count == 20


def test_insert_duplicate_url_keeps_richest_content(tmp_db):
    article_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/refresh",
            title="Refresh",
            source=Source.HACKERNEWS,
            summary="Short",
            content_text="old",
            content_length=3,
        )
    )
    tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/refresh",
            title="Refresh",
            source=Source.RSS,
            summary="A much richer summary",
            content_text="new evidence " * 100,
            content_length=1300,
        )
    )

    stored, _ = tmp_db.get_article_with_score(article_id)
    assert stored.summary == "A much richer summary"
    assert stored.content_text == "new evidence " * 100
    assert stored.content_length == 1300


def test_get_stats(tmp_db):
    article = CollectedArticle(
        url="https://example.com/stats-test",
        title="Stats Test",
        source=Source.RSS,
    )
    tmp_db.insert_article(article)
    stats = tmp_db.get_stats()
    assert stats["total_articles"] == 1
    assert stats["by_source"]["rss"] == 1


def test_insert_and_get_score(tmp_db):
    article = CollectedArticle(
        url="https://example.com/score-test",
        title="Score Test",
        source=Source.HACKERNEWS,
        points=50,
    )
    article_id = tmp_db.insert_article(article)
    tmp_db.update_content(article_id, "Some content here for testing purposes. " * 10)

    score = ScoreBreakdown(
        engagement_score=0.8,
        technical_depth=0.7,
        novelty=0.6,
        applicability=0.9,
        composite_score=0.75,
        reasoning="Good article",
    )
    tmp_db.insert_score(article_id, score)

    results = tmp_db.get_top_articles(limit=10)
    assert len(results) == 1
    a, s = results[0]
    assert s.composite_score == 0.75
    assert s.reasoning == "Good article"


def test_get_article_with_score(tmp_db):
    article_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/detail",
            title="Detail",
            source=Source.RSS,
        )
    )

    article, score = tmp_db.get_article_with_score(article_id)

    assert article.id == article_id
    assert article.title == "Detail"
    assert score.composite_score == 0


def test_article_assessment_version_controls_rescoring(tmp_db):
    article_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/versioned",
            title="Versioned assessment",
            source=Source.RSS,
        )
    )
    tmp_db.insert_score(
        article_id,
        ScoreBreakdown(composite_score=0.7, score_version="profile-v1", status="success"),
    )

    assert tmp_db.get_articles_for_assessment("profile-v1") == []
    assert [article.id for article in tmp_db.get_articles_for_assessment("profile-v2")] == [
        article_id
    ]


def test_article_assessment_respects_recency_horizon(tmp_db):
    recent_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/recent",
            title="Recent",
            source=Source.RSS,
            published_at=datetime.now(tz=UTC),
        )
    )
    tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/old",
            title="Old",
            source=Source.RSS,
            published_at=datetime.now(tz=UTC) - timedelta(days=90),
        )
    )

    pending = tmp_db.get_articles_for_assessment("current", max_age_days=45)

    assert [article.id for article in pending] == [recent_id]


def test_incomplete_assessment_waits_for_new_evidence(tmp_db):
    article_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/incomplete",
            title="Incomplete",
            source=Source.HACKERNEWS,
        )
    )
    tmp_db.insert_score(
        article_id,
        ScoreBreakdown(score_version="current", status="incomplete"),
    )

    assert tmp_db.get_articles_for_assessment("current") == []

    tmp_db.update_content(article_id, "Newly extracted implementation evidence")

    assert [a.id for a in tmp_db.get_articles_for_assessment("current")] == [article_id]


def test_digest_and_podcast_persistence(tmp_db, tmp_path):
    tmp_db.insert_digest("2026-W36", "# Digest", 3)

    digest = tmp_db.get_digest("2026-W36")
    assert digest is not None
    assert digest.markdown == "# Digest"
    assert tmp_db.list_digests() == [digest]

    podcast_path = tmp_path / "podcast.mp3"
    tmp_db.save_podcast("2026-W36", podcast_path, 4)

    updated = tmp_db.get_digest("2026-W36")
    assert updated is not None
    assert updated.markdown == "# Digest"
    assert updated.podcast_path == str(podcast_path)
    assert updated.article_count == 4
