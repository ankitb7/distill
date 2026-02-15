import asyncio
from datetime import UTC, datetime

import httpx

from ai_pulse.models import CollectedArticle, Source

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{}"


class HackerNewsCollector:
    source_name = "hackernews"

    async def collect(self, config: dict) -> list[CollectedArticle]:
        hn_config = config.get("sources", {}).get("hackernews", {})
        if not hn_config.get("enabled", True):
            return []

        keywords = hn_config.get("keywords", ["AI", "LLM"])
        min_points = hn_config.get("min_points", 10)
        max_results = hn_config.get("max_results", 50)

        articles = []
        async with httpx.AsyncClient(timeout=30) as client:
            tasks = [
                self._search_keyword(client, kw, min_points, max_results)
                for kw in keywords
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        seen_urls: set[str] = set()
        for result in results:
            if isinstance(result, Exception):
                continue
            for article in result:
                if article.url not in seen_urls:
                    seen_urls.add(article.url)
                    articles.append(article)

        articles.sort(key=lambda a: a.points or 0, reverse=True)
        return articles[:max_results]

    async def _search_keyword(
        self, client: httpx.AsyncClient, keyword: str, min_points: int, max_results: int
    ) -> list[CollectedArticle]:
        params = {
            "query": keyword,
            "tags": "story",
            "numericFilters": f"points>{min_points}",
            "hitsPerPage": max_results,
        }
        resp = await client.get(ALGOLIA_SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for hit in data.get("hits", []):
            url = hit.get("url")
            if not url:
                url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
            articles.append(
                CollectedArticle(
                    url=url,
                    title=hit.get("title", ""),
                    author=hit.get("author"),
                    source=Source.HACKERNEWS,
                    source_id=hit.get("objectID"),
                    published_at=datetime.fromtimestamp(
                        hit["created_at_i"], tz=UTC
                    )
                    if hit.get("created_at_i")
                    else None,
                    points=hit.get("points", 0),
                    comment_count=hit.get("num_comments", 0),
                    tags=hit.get("_tags", []),
                )
            )
        return articles
