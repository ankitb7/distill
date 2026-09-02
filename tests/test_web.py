import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from distill.db import Database
from distill.models import CollectedArticle, ScoreBreakdown, Source
from distill.outputs.web import create_app


def _make_config(db_path: Path) -> dict:
    return {
        "database": {"path": str(db_path)},
        "output": {"dir": str(db_path.parent / "output")},
    }


def test_index_empty_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()
    db.close()

    config = _make_config(db_path)
    # Patch get_db_path to return our temp path
    app = create_app(config)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<title>Distill — AI Engineering Signal</title>" in resp.text
    assert 'rel="icon"' in resp.text
    assert 'class="brand-mark"' in resp.text
    assert 'aria-label="Primary navigation"' in resp.text
    assert "<h1>AI engineering briefing</h1>" in resp.text
    assert 'for="source-filter"' in resp.text

    favicon = client.get("/static/favicon.svg")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")


def test_index_with_articles():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()
    article = CollectedArticle(
        url="https://example.com/web-test",
        title="Web Test Article",
        source=Source.HACKERNEWS,
        points=42,
    )
    aid = db.insert_article(article)
    db.update_content(aid, "Test content " * 50)
    db.insert_score(
        aid,
        ScoreBreakdown(
            composite_score=0.5,
            recommended_action="Prototype this migration workflow.",
        ),
    )
    db.close()

    config = _make_config(db_path)
    app = create_app(config)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Web Test Article" in resp.text
    assert "Try this:" in resp.text
    assert "Prototype this migration workflow." in resp.text
    assert "Why this?" in resp.text

    resp = client.get(f"/article/{aid}")
    assert resp.status_code == 200
    assert "Web Test Article" in resp.text


def test_index_uses_plain_text_content_when_summary_is_missing():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()
    article = CollectedArticle(
        url="https://example.com/hn-summary",
        title="HN Article",
        source=Source.HACKERNEWS,
    )
    article_id = db.insert_article(article)
    db.update_content(article_id, "A useful extracted description for this Hacker News article.")
    db.insert_score(article_id, ScoreBreakdown(composite_score=0.5))
    db.close()

    resp = TestClient(create_app(_make_config(db_path))).get("/")

    assert "A useful extracted description for this Hacker News article." in resp.text


def test_index_strips_html_from_stored_summary():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()
    article = CollectedArticle(
        url="https://example.com/rss-summary",
        title="RSS Article",
        source=Source.RSS,
        summary="<p>A <strong>useful</strong> summary &amp; description.</p><b clas...",
        content_text="Extracted article content.",
        content_length=26,
    )
    article_id = db.insert_article(article)
    db.insert_score(article_id, ScoreBreakdown(composite_score=0.5))
    db.close()

    resp = TestClient(create_app(_make_config(db_path))).get("/")

    assert "A useful summary &amp; description." in resp.text
    assert "&lt;p&gt;" not in resp.text
    assert "&lt;b clas" not in resp.text


def test_stats_page():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()
    db.close()

    config = _make_config(db_path)
    app = create_app(config)
    client = TestClient(app)
    resp = client.get("/stats")
    assert resp.status_code == 200


def test_digest_detail_uses_application_shell():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()
    db.insert_digest("2026-W36", "# Useful evidence", 3)
    db.close()

    client = TestClient(create_app(_make_config(db_path)))
    resp = client.get("/digest/2026-W36")

    assert resp.status_code == 200
    assert 'class="brand-mark"' in resp.text
    assert "2026-W36 Digest — Distill" in resp.text
    assert "# Useful evidence" in resp.text


def test_article_not_found():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()
    db.close()

    config = _make_config(db_path)
    app = create_app(config)
    client = TestClient(app)
    resp = client.get("/article/99999")
    assert resp.status_code == 404
