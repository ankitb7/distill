from distill.models import CollectedArticle, Source
from distill.processing.dedup import run_title_dedup


def test_title_dedup_marks_similar_titles(tmp_db):
    articles = [
        CollectedArticle(
            url="https://example.com/article-1",
            title="How to Build AI Agents with Claude",
            source=Source.HACKERNEWS,
        ),
        CollectedArticle(
            url="https://example.com/article-2",
            title="How to Build AI Agents with Claude - A Guide",
            source=Source.RSS,
        ),
        CollectedArticle(
            url="https://example.com/article-3",
            title="Completely Different Article About Cooking",
            source=Source.DEVTO,
        ),
    ]
    for a in articles:
        tmp_db.insert_article(a)

    marked = run_title_dedup(tmp_db, threshold=0.75)
    assert marked == 1

    stats = tmp_db.get_stats()
    assert stats["duplicates"] == 1
    assert stats["unique_articles"] == 2


def test_title_dedup_no_duplicates(tmp_db):
    articles = [
        CollectedArticle(
            url="https://example.com/a",
            title="First Completely Unique Article",
            source=Source.HACKERNEWS,
        ),
        CollectedArticle(
            url="https://example.com/b",
            title="Second Totally Different Article",
            source=Source.RSS,
        ),
    ]
    for a in articles:
        tmp_db.insert_article(a)

    marked = run_title_dedup(tmp_db, threshold=0.85)
    assert marked == 0


def test_title_dedup_keeps_article_with_richer_content(tmp_db):
    shallow_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/shallow",
            title="Operating a Production AI Feature Factory",
            source=Source.HACKERNEWS,
            content_text="short",
            content_length=5,
        )
    )
    rich_id = tmp_db.insert_article(
        CollectedArticle(
            url="https://author.example/rich",
            title="Operating a Production AI Feature Factory",
            source=Source.RSS,
            content_text="implementation evidence " * 100,
            content_length=2400,
        )
    )

    run_title_dedup(tmp_db)

    articles = {article.id: article for article in tmp_db.get_all_articles(False)}
    assert articles[shallow_id].canonical_id == rich_id
    assert articles[rich_id].is_duplicate is False
