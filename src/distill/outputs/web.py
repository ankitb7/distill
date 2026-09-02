import asyncio
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from distill.config import get_db_path
from distill.db import Database
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


def _build_slack_channel_map(config: dict) -> dict[str, str]:
    """Build channel_name -> channel_id mapping from config."""
    channels = config.get("sources", {}).get("slack", {}).get("channels", [])
    return {ch["name"]: ch["id"] for ch in channels if "name" in ch and "id" in ch}


def create_app(config: dict) -> FastAPI:
    app = FastAPI(title="Distill")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["plain_text_excerpt"] = plain_text_excerpt
    db_path = get_db_path(config)
    slack_channel_map = _build_slack_channel_map(config)

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
                "slack_channel_map": slack_channel_map,
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
        return templates.TemplateResponse(request, "digest.html", {"digests": digests})

    @app.get("/digest/{week_label}", response_class=HTMLResponse)
    async def digest_detail(request: Request, week_label: str):
        db = get_db()
        digest = db.get_digest(week_label)
        db.close()
        if not digest:
            return HTMLResponse("Digest not found", status_code=404)
        return templates.TemplateResponse(request, "digest_detail.html", {"digest": digest})

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
        podcasts = db.list_digests()
        db.close()
        provider = config.get("podcast", {}).get("provider", "notebooklm")
        ctx = {"podcasts": podcasts, "generating": _generating, "provider": provider}
        if _generating:
            time_est = "10-15 minutes" if provider == "notebooklm" else "3-5 minutes"
            ctx["message"] = f"Podcast generation in progress ({provider}). Refresh in {time_est}."
        elif _last_error:
            ctx["error"] = _last_error
            _last_error = None
        return templates.TemplateResponse(request, "podcasts.html", ctx)

    @app.post("/podcasts/generate")
    async def generate_podcast_now(article_ids: str = Form("")):
        global _generating
        if not _generating:
            from distill.config import get_output_dir
            from distill.outputs.podcast import generate_podcast

            ids = None
            if article_ids.strip():
                ids = [int(x.strip()) for x in article_ids.split(",") if x.strip()]

            _generating = True

            async def _run():
                global _generating, _last_error
                db = get_db()
                try:
                    output_dir = get_output_dir(config)
                    await generate_podcast(db, config, output_dir, article_ids=ids)
                except Exception as e:
                    _last_error = str(e)
                    print(f"Podcast generation failed: {e}")
                finally:
                    _generating = False
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
        media = "audio/mpeg" if file_path.suffix == ".mp3" else "text/markdown"
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
        return templates.TemplateResponse(request, "add.html", {"results": results})

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
