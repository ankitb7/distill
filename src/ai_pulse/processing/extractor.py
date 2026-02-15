import httpx
import trafilatura
from readability import Document

from ai_pulse.db import Database
from ai_pulse.models import Article


async def extract_content(db: Database, limit: int = 50) -> int:
    articles = db.get_articles_without_content(limit=limit)
    extracted = 0

    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "ai-pulse/0.1 (article curation bot)"},
    ) as client:
        for article in articles:
            content = await _extract_single(client, article)
            if content and len(content) > 100:
                db.update_content(article.id, content)
                extracted += 1

    return extracted


async def _extract_single(client: httpx.AsyncClient, article: Article) -> str | None:
    try:
        resp = await client.get(article.url)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return None

    # Primary: trafilatura
    content = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if content and len(content) > 100:
        return content

    # Fallback: readability-lxml
    try:
        doc = Document(html)
        content = doc.summary()
        # Strip HTML tags from readability output
        content = trafilatura.extract(content) or _strip_tags(content)
        if content and len(content) > 100:
            return content
    except Exception:
        pass

    return None


def _strip_tags(html: str) -> str:
    import re

    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean
