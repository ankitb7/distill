from datetime import datetime

from ai_pulse.models import Article, Source
from ai_pulse.processing.scorer import compute_engagement_score


def _make_article(points: int = 0, comment_count: int = 0, **kwargs) -> Article:
    return Article(
        id=kwargs.get("id", 1),
        url="https://example.com/test",
        normalized_url="https://example.com/test",
        title="Test",
        source=Source.HACKERNEWS,
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
