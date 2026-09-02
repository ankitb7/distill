from datetime import datetime

import httpx

from distill.models import CollectedArticle, Source

DEVTO_API_URL = "https://dev.to/api/articles"


class DevToCollector:
    source_name = "devto"

    async def collect(self, config: dict) -> list[CollectedArticle]:
        devto_config = config.get("sources", {}).get("devto", {})
        if not devto_config.get("enabled", False):
            return []

        tags = devto_config.get("tags", ["ai", "machinelearning", "llm"])
        min_reactions = devto_config.get("min_reactions", 10)
        max_results = devto_config.get("max_results", 30)

        articles = []
        async with httpx.AsyncClient(timeout=30) as client:
            for tag in tags:
                try:
                    resp = await client.get(
                        DEVTO_API_URL,
                        params={"tag": tag, "per_page": max_results, "top": 7},
                    )
                    resp.raise_for_status()
                    for item in resp.json():
                        if item.get("public_reactions_count", 0) >= min_reactions:
                            article = self._parse_item(item, tag)
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

    def _parse_item(self, item: dict, tag: str) -> CollectedArticle | None:
        url = item.get("url")
        title = item.get("title")
        if not url or not title:
            return None

        published = None
        if item.get("published_at"):
            try:
                published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        tags = item.get("tag_list", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        return CollectedArticle(
            url=url,
            title=title,
            author=item.get("user", {}).get("name"),
            source=Source.DEVTO,
            source_id=str(item.get("id", "")),
            published_at=published,
            points=item.get("public_reactions_count", 0),
            comment_count=item.get("comments_count", 0),
            tags=tags,
            summary=item.get("description", ""),
        )
