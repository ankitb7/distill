import json

import pytest
from typer.testing import CliRunner

from distill.cli import app
from distill.db import Database

runner = CliRunner()


@pytest.fixture
def db_path(tmp_path):
    db = Database(tmp_path / "test.db")
    db.init_schema()
    db.close()
    return tmp_path / "test.db"


def test_ingest_from_file(db_path, tmp_path):
    articles = [
        {
            "url": "https://example.com/article",
            "title": "Test Article",
            "source": "slack",
            "tags": ["engineering"],
            "points": 3,
            "comment_count": 1,
        }
    ]
    article_file = tmp_path / "articles.json"
    article_file.write_text(json.dumps(articles))

    result = runner.invoke(app, ["ingest", str(article_file), "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Ingested 1" in result.output


def test_ingest_skips_duplicates(db_path, tmp_path):
    articles = [{"url": "https://example.com/article", "title": "Test", "source": "slack"}]
    article_file = tmp_path / "articles.json"
    article_file.write_text(json.dumps(articles))

    runner.invoke(app, ["ingest", str(article_file), "--db", str(db_path)])
    result = runner.invoke(app, ["ingest", str(article_file), "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Ingested 0" in result.output


def test_ingest_invalid_json(db_path, tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json")
    result = runner.invoke(app, ["ingest", str(bad_file), "--db", str(db_path)])
    assert result.exit_code != 0


def test_ingest_from_stdin(db_path):
    articles = [{"url": "https://example.com/stdin", "title": "Stdin Test", "source": "slack"}]
    result = runner.invoke(app, ["ingest", "-", "--db", str(db_path)], input=json.dumps(articles))
    assert result.exit_code == 0
    assert "Ingested 1" in result.output
