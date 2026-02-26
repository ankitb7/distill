import asyncio
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ai_pulse.config import get_db_path
from ai_pulse.db import Database
from ai_pulse.models import CollectedArticle, ScoreBreakdown, Source

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_gen_state: dict = {"running": False, "message": "", "done": False, "error": False}


def create_app(config: dict) -> FastAPI:
    app = FastAPI(title="AI Pulse")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    db_path = get_db_path(config)

    def get_db() -> Database:
        return Database(db_path)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, source: str = "", limit: int = 50):
        from ai_pulse.outputs.digest import get_week_range

        db = get_db()
        _, week_start, week_end = get_week_range()
        articles = db.get_top_articles(
            limit=limit, week_start=week_start, week_end=week_end
        )

        if source:
            articles = [(a, s) for a, s in articles if a.source.value == source]

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
            },
        )

    @app.get("/article/{article_id}", response_class=HTMLResponse)
    async def article_detail(request: Request, article_id: int):
        db = get_db()
        rows = db.conn.execute(
            """SELECT a.*, s.engagement_score, s.technical_depth, s.novelty,
                      s.applicability, s.composite_score, s.reasoning
               FROM articles a
               LEFT JOIN scores s ON a.id = s.article_id
               WHERE a.id = ?""",
            (article_id,),
        ).fetchall()

        if not rows:
            db.close()
            return HTMLResponse("Article not found", status_code=404)

        r = rows[0]
        article = db._row_to_article(r)
        score = ScoreBreakdown(
            engagement_score=r["engagement_score"] or 0,
            technical_depth=r["technical_depth"] or 0,
            novelty=r["novelty"] or 0,
            applicability=r["applicability"] or 0,
            composite_score=r["composite_score"] or 0,
            reasoning=r["reasoning"] or "",
        )
        db.close()

        return templates.TemplateResponse(
            request,
            "article.html",
            {"article": article, "score": score},
        )

    @app.get("/digests", response_class=HTMLResponse)
    async def digests_list(request: Request):
        db = get_db()
        rows = db.conn.execute(
            "SELECT * FROM digests ORDER BY created_at DESC"
        ).fetchall()
        digests = [dict(r) for r in rows]
        db.close()
        return templates.TemplateResponse(
            request, "digest.html", {"digests": digests}
        )

    @app.get("/digest/{week_label}", response_class=HTMLResponse)
    async def digest_detail(request: Request, week_label: str):
        db = get_db()
        row = db.conn.execute(
            "SELECT * FROM digests WHERE week_label = ?", (week_label,)
        ).fetchone()
        db.close()
        if not row:
            return HTMLResponse("Digest not found", status_code=404)
        markdown = row["markdown"] or ""
        return HTMLResponse(f"<pre>{markdown}</pre>")

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_page(request: Request):
        db = get_db()
        stats = db.get_stats()
        db.close()
        return templates.TemplateResponse(
            request, "stats.html", {"stats": stats}
        )

    @app.get("/podcasts", response_class=HTMLResponse)
    async def podcasts_page(request: Request):
        db = get_db()
        rows = db.conn.execute(
            "SELECT * FROM digests ORDER BY created_at DESC"
        ).fetchall()
        podcasts = [dict(r) for r in rows]
        db.close()
        return templates.TemplateResponse(
            request, "podcasts.html",
            {"podcasts": podcasts, "gen_state": _gen_state},
        )

    @app.post("/podcasts/generate", response_class=HTMLResponse)
    async def generate_podcast_now(request: Request, article_ids: str = Form("")):
        if _gen_state["running"]:
            return HTMLResponse(
                '<div class="gen-status gen-status-running">'
                '<span class="gen-spinner"></span>'
                f'{_gen_state["message"]}'
                "</div>",
            )

        from ai_pulse.config import get_output_dir
        from ai_pulse.outputs.podcast import generate_podcast

        ids = None
        if article_ids.strip():
            ids = [int(x.strip()) for x in article_ids.split(",") if x.strip()]

        _gen_state.update(running=True, message="Starting...", done=False, error=False)

        async def _run():
            db = get_db()
            try:
                output_dir = get_output_dir(config)
                path = await generate_podcast(
                    db, config, output_dir, article_ids=ids,
                    on_status=lambda msg: _gen_state.update(message=msg),
                )
                if path:
                    _gen_state.update(
                        running=False, done=True, error=False,
                        message=f"Podcast ready: {path.name}",
                    )
                else:
                    _gen_state.update(
                        running=False, done=True, error=True,
                        message="No articles found to generate podcast",
                    )
            except Exception as e:
                _gen_state.update(
                    running=False, done=True, error=True,
                    message=f"Error: {e}",
                )
            finally:
                db.close()

        asyncio.create_task(_run())

        return HTMLResponse(
            '<div id="gen-poll" hx-get="/podcasts/status" '
            'hx-trigger="every 10s" hx-swap="outerHTML">'
            '<div class="gen-status gen-status-running">'
            '<span class="gen-spinner"></span>Starting...'
            "</div></div>",
        )

    @app.get("/podcasts/status", response_class=HTMLResponse)
    async def podcast_status():
        if _gen_state["running"]:
            return HTMLResponse(
                '<div id="gen-poll" hx-get="/podcasts/status" '
                'hx-trigger="every 10s" hx-swap="outerHTML">'
                '<div class="gen-status gen-status-running">'
                f'<span class="gen-spinner"></span>{_gen_state["message"]}'
                "</div></div>",
            )

        if _gen_state["done"]:
            is_error = _gen_state["error"]
            css = "gen-status-error" if is_error else "gen-status-done"
            msg = _gen_state["message"]
            refresh = "" if is_error else (
                ' <a href="/podcasts" style="margin-left: 0.5rem; '
                'color: inherit; text-decoration: underline;">Refresh page</a>'
            )
            _gen_state.update(running=False, done=False, message="", error=False)
            return HTMLResponse(
                f'<div id="gen-poll"><div class="gen-status {css}">'
                f"{msg}{refresh}</div></div>",
            )

        return HTMLResponse('<div id="gen-poll"></div>')

    @app.get("/podcast-file/{week_label}")
    async def podcast_file(week_label: str):
        from fastapi.responses import FileResponse

        db = get_db()
        row = db.conn.execute(
            "SELECT podcast_path FROM digests WHERE week_label = ?",
            (week_label,),
        ).fetchone()
        db.close()
        if not row or not row["podcast_path"]:
            return HTMLResponse("No podcast for this week", status_code=404)
        file_path = Path(row["podcast_path"])
        if not file_path.exists():
            return HTMLResponse("Podcast file not found", status_code=404)
        media = "audio/mpeg" if file_path.suffix == ".mp3" else "text/markdown"
        return FileResponse(file_path, media_type=media)

    @app.get("/add", response_class=HTMLResponse)
    async def add_links_page(request: Request):
        return templates.TemplateResponse(
            request, "add.html", {"results": None}
        )

    @app.post("/add", response_class=HTMLResponse)
    async def add_links_submit(request: Request, urls: str = Form("")):
        import httpx
        import trafilatura

        raw_urls = [u.strip() for u in urls.splitlines() if u.strip()]
        results = []
        db = get_db()

        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "ai-pulse/0.1"},
        ) as client:
            for url in raw_urls:
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                entry = {"url": url, "status": "error", "title": None}
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    html = resp.text

                    title = _extract_title(html, url)
                    entry["title"] = title

                    article = CollectedArticle(
                        url=url,
                        title=title,
                        source=Source.RSS,
                        source_id=f"manual:{url}",
                        tags=["manual"],
                    )
                    aid = db.insert_article(article)
                    if aid is None:
                        entry["status"] = "exists"
                    else:
                        content = trafilatura.extract(
                            html,
                            include_comments=False,
                            include_tables=True,
                            favor_precision=True,
                        )
                        if content and len(content) > 100:
                            db.update_content(aid, content)
                            entry["status"] = "extracted"
                        else:
                            entry["status"] = "added"
                except Exception as e:
                    entry["status"] = f"error: {e}"
                results.append(entry)

        db.close()
        return templates.TemplateResponse(
            request, "add.html", {"results": results}
        )

    @app.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request):
        return templates.TemplateResponse(
            request, "search.html", {"results": None, "query": ""}
        )

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
        import httpx
        import trafilatura

        db = get_db()
        article = CollectedArticle(
            url=url,
            title=title,
            source=Source.RSS,
            source_id=f"manual:{url}",
            tags=["manual"],
        )
        aid = db.insert_article(article)
        if aid is not None:
            try:
                async with httpx.AsyncClient(
                    timeout=20, follow_redirects=True,
                    headers={"User-Agent": "ai-pulse/0.1"},
                ) as client:
                    resp = await client.get(url)
                    content = trafilatura.extract(
                        resp.text,
                        include_comments=False,
                        include_tables=True,
                        favor_precision=True,
                    )
                    if content and len(content) > 100:
                        db.update_content(aid, content)
            except Exception:
                pass
        db.close()

        # Re-run search to show updated results
        results = await _search_articles(query)
        return templates.TemplateResponse(
            request, "search.html",
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
                results.append({
                    "title": hit.get("title", ""),
                    "url": url,
                    "points": hit.get("points", 0),
                    "comments": hit.get("num_comments", 0),
                    "author": hit.get("author", ""),
                    "date": (hit.get("created_at", ""))[:10],
                    "source": "Hacker News",
                })
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
                results.append({
                    "title": item.get("title", ""),
                    "url": url,
                    "points": item.get("positive_reactions_count", 0),
                    "comments": item.get("comments_count", 0),
                    "author": item.get("user", {}).get("name", ""),
                    "date": (item.get("published_at", ""))[:10],
                    "source": "dev.to",
                })
        except Exception:
            pass

    # Sort by points descending, take top N
    results.sort(key=lambda x: x.get("points", 0), reverse=True)
    return results[:limit]


def _extract_title(html: str, fallback: str) -> str:
    import re

    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
    if match:
        return match.group(1).strip()
    return fallback
