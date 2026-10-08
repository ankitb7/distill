import asyncio
import re
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from rich.console import Console

from distill.config import get_db_path
from distill.db import Database
from distill.models import Digest
from distill.outputs.markdown import render_digest_markdown
from distill.outputs.podcast_providers import (
    DEFAULT_PODCAST_PROVIDER,
    PODCAST_PROVIDERS,
    configure_podcast,
)
from distill.outputs.podcastfy import SCRIPT_PROVIDERS, PodcastfyProvider, PodcastfySettings
from distill.processing.recommendation import (
    ReadingSlateRequest,
    meets_quality_gate,
    select_reading_slate,
)
from distill.processing.text import plain_text_excerpt

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STATIC_DIR = Path(__file__).parent.parent / "static"

_generating: bool = False
_last_error: str | None = None
_active_provider: str | None = None


def _archive_entry(digest: Digest) -> dict:
    weekly = re.fullmatch(r"(\d{4})-W(\d{2})", digest.week_label)
    kind = "Weekly" if weekly else "Comparison" if "comparison-" in digest.week_label else "Custom"
    fallback = f"Week {int(weekly[2])}, {weekly[1]}" if weekly else "Custom briefing"
    return {
        "digest": digest,
        "title": digest.podcast_title or fallback,
        "kind": kind,
        "date_label": datetime.fromisoformat(digest.created_at).strftime("%d %b %Y").lstrip("0"),
    }


def create_app(config: dict) -> FastAPI:
    app = FastAPI(title="Distill")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["plain_text_excerpt"] = plain_text_excerpt
    db_path = get_db_path(config)
    db = Database(db_path)
    try:
        db.init_schema()
    finally:
        db.close()

    def get_db() -> Database:
        return Database(db_path)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, source: str = "", limit: int = 50):
        from distill.outputs.digest import get_week_range

        db = get_db()
        _, week_start, week_end = get_week_range()
        articles = select_reading_slate(
            db,
            config,
            ReadingSlateRequest(limit=limit, week_start=week_start, week_end=week_end),
        )
        if not articles:
            articles = select_reading_slate(db, config, ReadingSlateRequest(limit=limit))

        if source:
            articles = [(a, s) for a, s in articles if a.source.value == source]

        recommendation_config = config.get("recommendation", {})
        quality_count = sum(
            meets_quality_gate(score, recommendation_config) for _, score in articles
        )

        sources = list(db.get_stats().get("by_source", {}).keys())
        db.close()

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "articles": articles,
                "sources": sources,
                "source_filter": source,
                "limit": limit,
                "quality_count": quality_count,
            },
        )

    @app.get("/article/{article_id}", response_class=HTMLResponse)
    async def article_detail(request: Request, article_id: int):
        db = get_db()
        result = db.get_article_with_score(article_id)
        if not result:
            db.close()
            return HTMLResponse("Article not found", status_code=404)
        article, score = result
        db.close()

        return templates.TemplateResponse(
            request,
            "article.html",
            {"article": article, "score": score},
        )

    @app.get("/digests", response_class=HTMLResponse)
    async def digests_list(request: Request):
        db = get_db()
        digests = db.list_digests()
        db.close()
        entries = [_archive_entry(digest) for digest in digests]
        groups = [
            {"name": name, "entries": [entry for entry in entries if entry["kind"] == kind]}
            for kind, name in [
                ("Weekly", "Weekly"),
                ("Comparison", "Comparisons"),
                ("Custom", "Custom runs"),
            ]
        ]
        return templates.TemplateResponse(
            request, "digest.html", {"digests": digests, "digest_groups": groups}
        )

    @app.get("/digest/{week_label}", response_class=HTMLResponse)
    async def digest_detail(request: Request, week_label: str):
        db = get_db()
        digest = db.get_digest(week_label)
        db.close()
        if not digest:
            return HTMLResponse("Digest not found", status_code=404)
        return templates.TemplateResponse(
            request,
            "digest_detail.html",
            {"digest": digest, "digest_html": render_digest_markdown(digest.markdown or "")},
        )

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_page(request: Request):
        db = get_db()
        stats = db.get_stats()
        db.close()
        return templates.TemplateResponse(request, "stats.html", {"stats": stats})

    @app.get("/podcasts", response_class=HTMLResponse)
    async def podcasts_page(request: Request):
        global _last_error
        db = get_db()
        try:
            episodes = [
                {
                    "podcast": podcast,
                    "articles": db.get_podcast_articles(podcast.week_label),
                    "kind": _archive_entry(podcast)["kind"],
                    "date_label": datetime.fromisoformat(podcast.created_at)
                    .strftime("%d %b %Y")
                    .lstrip("0"),
                }
                for podcast in db.list_podcasts()
            ]
        finally:
            db.close()
        provider = config.get("podcast", {}).get("provider", DEFAULT_PODCAST_PROVIDER)
        ctx = {
            "episodes": episodes,
            "generating": _generating,
            "provider": provider,
            "providers": PODCAST_PROVIDERS,
            "script_providers": SCRIPT_PROVIDERS,
            "script_provider": config.get("podcast", {})
            .get("podcastfy", {})
            .get("script_provider", "anthropic"),
        }
        if _generating:
            ctx["message"] = (
                f"Podcast generation in progress ({_active_provider or provider}). "
                "Refresh in a few minutes."
            )
        elif _last_error:
            ctx["error"] = _last_error
            _last_error = None
        return templates.TemplateResponse(request, "podcasts.html", ctx)

    @app.get("/podcast-articles")
    async def podcast_articles_page() -> RedirectResponse:
        return RedirectResponse("/podcasts", status_code=308)

    @app.post("/podcasts/generate")
    async def generate_podcast_now(
        article_ids: str = Form(""), provider: str = Form(""), script_provider: str = Form("")
    ):
        global _generating, _active_provider, _last_error
        if not _generating:
            from distill.config import get_output_dir
            from distill.outputs.podcast import generate_podcast

            try:
                selected_config = configure_podcast(config, provider, script_provider)
                selected_provider = selected_config["podcast"]["provider"]
                if selected_provider == "podcastfy-edge":
                    PodcastfyProvider(
                        PodcastfySettings(**selected_config["podcast"].get("podcastfy", {}))
                    ).validate_setup()
            except ValueError as exc:
                _last_error = str(exc)
                return RedirectResponse("/podcasts", status_code=303)

            ids = None
            if article_ids.strip():
                try:
                    ids = [int(x.strip()) for x in article_ids.split(",")]
                except ValueError as exc:
                    raise HTTPException(422, "Enter article IDs separated by commas.") from exc
                db = get_db()
                try:
                    found = {article.id for article, _ in db.get_articles_by_ids(ids)}
                finally:
                    db.close()
                if set(ids) != found:
                    raise HTTPException(422, "Some articles could not be found. Check the IDs.")

            _generating = True
            _active_provider = selected_provider
            _last_error = None

            async def _run():
                global _generating, _last_error, _active_provider
                db = get_db()
                try:
                    output_dir = get_output_dir(config)
                    await generate_podcast(db, selected_config, output_dir, article_ids=ids)
                except Exception as e:
                    _last_error = str(e)
                    Console().print(f"Podcast generation failed: {e}")
                finally:
                    _generating = False
                    _active_provider = None
                    db.close()

            asyncio.create_task(_run())

        return RedirectResponse("/podcasts", status_code=303)

    @app.get("/podcast-file/{week_label}")
    async def podcast_file(week_label: str):
        from fastapi.responses import FileResponse

        db = get_db()
        digest = db.get_digest(week_label)
        db.close()
        if not digest or not digest.podcast_path:
            return HTMLResponse("No podcast for this week", status_code=404)
        file_path = Path(digest.podcast_path)
        if not file_path.exists():
            return HTMLResponse("Podcast file not found", status_code=404)
        media = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
        }.get(file_path.suffix.lower(), "application/octet-stream")
        return FileResponse(file_path, media_type=media)

    @app.get("/add", response_class=HTMLResponse)
    async def add_links_page(request: Request):
        return templates.TemplateResponse(request, "add.html", {"results": None})

    @app.post("/add", response_class=HTMLResponse)
    async def add_links_submit(request: Request, urls: str = Form("")):
        from distill.processing.intake import ManualArticleRequest, add_manual_articles

        raw_urls = [u.strip() for u in urls.splitlines() if u.strip()]
        db = get_db()
        intake_results = await add_manual_articles(
            db, [ManualArticleRequest(url) for url in raw_urls]
        )
        db.close()
        results = [
            {"url": result.url, "status": result.status.value, "title": result.title}
            for result in intake_results
        ]
        template = (
            "_add_results.html" if request.headers.get("HX-Request") == "true" else "add.html"
        )
        return templates.TemplateResponse(request, template, {"results": results})

    @app.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request):
        return templates.TemplateResponse(request, "search.html", {"results": None, "query": ""})

    @app.post("/search", response_class=HTMLResponse)
    async def search_submit(request: Request, query: str = Form("")):
        results = await _search_articles(query.strip())
        return templates.TemplateResponse(
            request, "search.html", {"results": results, "query": query}
        )

    @app.post("/search/add", response_class=HTMLResponse)
    async def search_add_article(
        request: Request,
        url: str = Form(""),
        title: str = Form(""),
        query: str = Form(""),
    ):
        from distill.processing.intake import ManualArticleRequest, add_manual_articles

        db = get_db()
        await add_manual_articles(db, [ManualArticleRequest(url, title=title)])
        db.close()

        # Re-run search to show updated results
        results = await _search_articles(query)
        return templates.TemplateResponse(
            request,
            "search.html",
            {"results": results, "query": query, "added": title},
        )

    return app


async def _search_articles(query: str, limit: int = 10) -> list[dict]:
    """Search HN Algolia + Google for best articles on a topic."""
    import httpx

    results = []
    seen_urls = set()

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        # HN Algolia — sorted by relevance, high quality
        try:
            resp = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": query, "tags": "story", "hitsPerPage": limit * 2},
            )
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append(
                    {
                        "title": hit.get("title", ""),
                        "url": url,
                        "points": hit.get("points", 0),
                        "comments": hit.get("num_comments", 0),
                        "author": hit.get("author", ""),
                        "date": (hit.get("created_at", ""))[:10],
                        "source": "Hacker News",
                    }
                )
        except Exception:
            pass

        # dev.to search — practitioner-focused
        try:
            resp = await client.get(
                "https://dev.to/api/articles",
                params={"tag": query.replace(" ", ","), "per_page": limit},
            )
            resp.raise_for_status()
            for item in resp.json():
                url = item.get("url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append(
                    {
                        "title": item.get("title", ""),
                        "url": url,
                        "points": item.get("positive_reactions_count", 0),
                        "comments": item.get("comments_count", 0),
                        "author": item.get("user", {}).get("name", ""),
                        "date": (item.get("published_at", ""))[:10],
                        "source": "dev.to",
                    }
                )
        except Exception:
            pass

    # Sort by points descending, take top N
    results.sort(key=lambda x: x.get("points", 0), reverse=True)
    return results[:limit]
