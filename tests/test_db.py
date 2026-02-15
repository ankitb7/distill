from datetime import UTC, datetime

from ai_pulse.db import normalize_url
from ai_pulse.models import CollectedArticle, ScoreBreakdown, Source


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

    row = tmp_db.conn.execute(
        "SELECT points, comment_count FROM articles WHERE id = ?", (id1,)
    ).fetchone()
    assert row["points"] == 100
    assert row["comment_count"] == 20


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
