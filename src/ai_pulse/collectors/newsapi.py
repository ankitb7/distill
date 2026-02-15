import os
from datetime import UTC, datetime, timedelta

import httpx

from ai_pulse.models import CollectedArticle, Source

NEWSAPI_URL = "https://newsapi.org/v2/everything"


class NewsAPICollector:
    source_name = "newsapi"

    async def collect(self, config: dict) -> list[CollectedArticle]:
        newsapi_config = config.get("sources", {}).get("newsapi", {})
        if not newsapi_config.get("enabled", False):
            return []

        api_key = os.environ.get("NEWSAPI_KEY")
        if not api_key:
            return []

        queries = newsapi_config.get(
            "queries", ["artificial intelligence developer", "LLM programming"]
        )
        max_results = newsapi_config.get("max_results", 20)
        from_date = (datetime.now(tz=UTC) - timedelta(days=7)).strftime("%Y-%m-%d")

        articles = []
        async with httpx.AsyncClient(timeout=30) as client:
            for query in queries:
                try:
                    resp = await client.get(
                        NEWSAPI_URL,
                        params={
                            "q": query,
                            "from": from_date,
                            "sortBy": "relevancy",
                            "pageSize": max_results,
                            "language": "en",
                            "apiKey": api_key,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    for item in data.get("articles", []):
                        article = self._parse_item(item)
                        if article:
                            articles.append(article)
                except Exception:
                    continue

        seen: set[str] = set()
        unique = []
        for a in articles:
            if a.url not in seen:
                seen.add(a.url)
                unique.append(a)
        return unique[:max_results]

    def _parse_item(self, item: dict) -> CollectedArticle | None:
        url = item.get("url")
        title = item.get("title")
        if not url or not title or "[Removed]" in title:
            return None

        published = None
        if item.get("publishedAt"):
            try:
                published = datetime.fromisoformat(
                    item["publishedAt"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        source_name = item.get("source", {}).get("name", "")

        return CollectedArticle(
            url=url,
            title=title,
            author=item.get("author"),
            source=Source.NEWSAPI,
            source_id=url,
            published_at=published,
            tags=[source_name] if source_name else [],
            summary=item.get("description", ""),
        )
