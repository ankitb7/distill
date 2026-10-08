import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
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


def test_index_falls_back_to_recent_articles_when_week_has_no_qualified_articles(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "fallback.db"
    db = Database(db_path)
    db.init_schema()
    aid = db.insert_article(
        CollectedArticle(
            url="https://example.com/recent",
            title="Recent useful article",
            source=Source.RSS,
            content_text="Useful engineering evidence.",
        )
    )
    db.insert_score(aid, ScoreBreakdown(composite_score=0.8))
    db.close()
    # Advance the weekly window beyond collection while retaining real recency filtering.
    monkeypatch.setattr(
        "distill.outputs.digest.get_week_range",
        lambda: ("2099-W01", "2099-01-01T00:00:00", "2099-01-08T00:00:00"),
    )
    response = TestClient(create_app(_make_config(db_path))).get("/")
    assert "Recent useful article" in response.text


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


def test_add_links_htmx_response_is_results_fragment():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()
    db.close()

    client = TestClient(create_app(_make_config(db_path)))
    resp = client.post("/add", data={"urls": ""}, headers={"HX-Request": "true"})

    assert resp.status_code == 200
    assert "<!DOCTYPE html>" not in resp.text
    assert "<form" not in resp.text


def test_podcasts_empty_state(tmp_db):
    client = TestClient(create_app(_make_config(tmp_db.db_path)))
    response = client.get("/podcasts")
    assert response.status_code == 200
    assert "No podcasts yet." in response.text
    assert 'href="/podcasts" class="active" aria-current="page"' in response.text
    assert 'href="/podcast-articles"' not in response.text
    assert "Current provider: <strong>gemini-api-tts</strong>" in response.text
    assert "NotebookLM" not in response.text
    assert 'value="podcastfy-edge"' in response.text
    assert 'for="weekly-provider"' in response.text
    assert 'for="custom-script-provider"' in response.text
    assert 'aria-describedby="custom-script-help"' in response.text


def test_podcasts_group_sources_and_keep_legacy_episodes(tmp_db, tmp_path):
    audio = tmp_path / "custom.mp3"
    audio.write_bytes(b"audio")
    for i in range(2):
        aid = tmp_db.insert_article(
            CollectedArticle(
                url=f"https://example.com/article-{i}",
                title=f"Article <{i}>",
                source=Source.RSS,
            )
        )
        article = tmp_db.get_article_with_score(aid)[0]
        tmp_db.save_podcast(f"custom-{i}", audio, 1, articles=[article])
    tmp_db.save_podcast("legacy", audio, 5)
    tmp_db.insert_digest("digest-only", "# Digest", 20)
    tmp_db.delete_old_articles("2099-01-01")
    client = TestClient(create_app(_make_config(tmp_db.db_path)))
    response = client.get("/podcasts")
    assert response.status_code == 200
    for i in range(2):
        section = response.text.split(f'id="episode-custom-{i}"')[1].split("</section>")[0]
        assert f'href="https://example.com/article-{i}"' in section
        assert f"Article &lt;{i}&gt;" in section
        assert f"article-{1 - i}" not in section
        assert f'src="/podcast-file/custom-{i}"' in section
        disclosure = section.split("<details>", 1)[1].split("</details>", 1)[0]
        assert "<summary>1 related article</summary>" in disclosure
        assert f'href="https://example.com/article-{i}"' in disclosure
        assert "<audio" not in disclosure
        assert "<details open" not in section
    assert "Article links weren’t saved for this episode." in response.text
    assert "digest-only" not in response.text
    assert 'action="/podcasts/generate"' in response.text
    playback = client.get("/podcast-file/custom-0")
    assert playback.content == b"audio"
    assert playback.headers["content-type"] == "audio/mpeg"


@pytest.mark.parametrize("ids", ["abc", ",", "1,", "99999", "-1"])
def test_invalid_custom_episode_selection_does_not_start_generation(tmp_db, ids):
    client = TestClient(create_app(_make_config(tmp_db.db_path)))
    with patch("distill.outputs.podcast.generate_podcast", new_callable=AsyncMock) as generate:
        response = client.post("/podcasts/generate", data={"article_ids": ids})
    assert response.status_code == 422
    generate.assert_not_called()


def test_custom_episode_generated_through_web_has_source_links(tmp_db, tmp_path):
    aid = tmp_db.insert_article(
        CollectedArticle(
            url="https://example.com/custom",
            title="Custom article",
            source=Source.RSS,
            content_text="content " * 100,
        )
    )
    audio = tmp_path / "web.mp3"
    audio.write_bytes(b"audio")
    provider = AsyncMock()
    provider.generate.return_value = audio
    with patch("distill.outputs.podcast.get_podcast_provider", return_value=provider):
        with TestClient(create_app(_make_config(tmp_db.db_path))) as client:
            response = client.post("/podcasts/generate", data={"article_ids": str(aid)})
            assert response.status_code == 200
            sources = client.get("/podcasts")
    assert 'href="https://example.com/custom"' in sources.text


def test_web_provider_override_does_not_change_defaults(tmp_db):
    config = _make_config(tmp_db.db_path)
    config["podcast"] = {
        "provider": "gemini-api-tts",
        "podcastfy": {"script_provider": "anthropic"},
    }
    generate = AsyncMock(return_value=None)
    with (
        patch("distill.outputs.web.PodcastfyProvider.validate_setup"),
        patch("distill.outputs.podcast.generate_podcast", generate),
    ):
        with TestClient(create_app(config)) as client:
            response = client.post(
                "/podcasts/generate",
                data={
                    "provider": "podcastfy-edge",
                    "script_provider": "openai",
                },
            )
    assert response.status_code == 200
    selected = generate.await_args.args[1]["podcast"]
    assert selected["provider"] == "podcastfy-edge"
    assert selected["podcastfy"]["script_provider"] == "openai"
    assert config["podcast"]["provider"] == "gemini-api-tts"
    assert config["podcast"]["podcastfy"]["script_provider"] == "anthropic"


@pytest.mark.parametrize(
    "data",
    [
        {"provider": "notebooklm"},
        {"provider": "podcastfy-edge", "script_provider": "invalid"},
        {"provider": "podcastfy-edge", "script_provider": "openai"},
    ],
)
def test_invalid_or_unconfigured_provider_shows_error_without_starting(tmp_db, monkeypatch, data):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch("distill.outputs.podcast.generate_podcast", new_callable=AsyncMock) as generate:
        response = TestClient(create_app(_make_config(tmp_db.db_path))).post(
            "/podcasts/generate", data=data
        )
    assert response.status_code == 200
    assert 'role="alert"' in response.text
    generate.assert_not_called()


def test_old_podcast_articles_url_redirects_to_podcasts(tmp_db):
    client = TestClient(create_app(_make_config(tmp_db.db_path)))
    response = client.get("/podcast-articles", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/podcasts"


def test_gemini_wav_episode_has_inline_player_and_correct_media_type(tmp_db, tmp_path):
    audio = tmp_path / "gemini.wav"
    audio.write_bytes(b"RIFF-test-audio")
    tmp_db.save_podcast("gemini-sample", audio, 0)
    config = _make_config(tmp_db.db_path)
    config["podcast"] = {"provider": "gemini-tts"}
    client = TestClient(create_app(config))
    response = client.get("/podcasts")
    assert 'src="/podcast-file/gemini-sample"' in response.text
    assert "official Cloud Text-to-Speech API" in response.text
    audio_response = client.get("/podcast-file/gemini-sample")
    assert audio_response.headers["content-type"] == "audio/wav"
    assert audio_response.content == audio.read_bytes()
