from datetime import UTC, datetime
from time import mktime

import feedparser
import httpx

from ai_pulse.models import CollectedArticle, Source


class RSSCollector:
    source_name = "rss"

    async def collect(self, config: dict) -> list[CollectedArticle]:
        rss_config = config.get("sources", {}).get("rss", {})
        if not rss_config.get("enabled", True):
            return []

        feeds = rss_config.get("feeds", [])
        articles = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for feed_info in feeds:
                url = feed_info["url"]
                name = feed_info.get("name", url)
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.text)
                    limit = feed_info.get("max_results", 20)
                    for entry in parsed.entries[:limit]:
                        article = self._parse_entry(entry, name)
                        if article:
                            articles.append(article)
                except Exception:
                    continue

        return articles

    def _parse_entry(self, entry: dict, feed_name: str) -> CollectedArticle | None:
        url = entry.get("link")
        title = entry.get("title")
        if not url or not title:
            return None

        published = None
        for date_field in ("published_parsed", "updated_parsed"):
            ts = entry.get(date_field)
            if ts:
                try:
                    published = datetime.fromtimestamp(mktime(ts), tz=UTC)
                except (TypeError, ValueError, OverflowError):
                    pass
                break

        author = entry.get("author", feed_name)
        summary = entry.get("summary", "")
        if len(summary) > 500:
            summary = summary[:497] + "..."

        tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]

        return CollectedArticle(
            url=url,
            title=title,
            author=author,
            source=Source.RSS,
            source_id=entry.get("id", url),
            published_at=published,
            tags=tags,
            summary=summary,
        )
