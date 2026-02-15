import os
from datetime import UTC, datetime

import httpx

from ai_pulse.models import CollectedArticle, Source

REDDIT_OAUTH_URL = "https://oauth.reddit.com"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"


class RedditCollector:
    source_name = "reddit"

    async def collect(self, config: dict) -> list[CollectedArticle]:
        reddit_config = config.get("sources", {}).get("reddit", {})
        if not reddit_config.get("enabled", False):
            return []

        client_id = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        user_agent = os.environ.get("REDDIT_USER_AGENT", "ai-pulse/0.1")
        if not client_id or not client_secret:
            return []

        subreddits = reddit_config.get("subreddits", ["MachineLearning"])
        min_score = reddit_config.get("min_score", 15)
        max_results = reddit_config.get("max_results", 30)

        token = await self._get_token(client_id, client_secret, user_agent)
        if not token:
            return []

        articles = []
        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent,
            },
        ) as client:
            for sub in subreddits:
                try:
                    posts = await self._fetch_subreddit(client, sub, min_score, max_results)
                    articles.extend(posts)
                except Exception:
                    continue

        return articles[:max_results]

    async def _get_token(
        self, client_id: str, client_secret: str, user_agent: str
    ) -> str | None:
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(
                    REDDIT_TOKEN_URL,
                    auth=(client_id, client_secret),
                    data={"grant_type": "client_credentials"},
                    headers={"User-Agent": user_agent},
                )
                resp.raise_for_status()
                return resp.json().get("access_token")
            except Exception:
                return None

    async def _fetch_subreddit(
        self,
        client: httpx.AsyncClient,
        subreddit: str,
        min_score: int,
        limit: int,
    ) -> list[CollectedArticle]:
        resp = await client.get(
            f"{REDDIT_OAUTH_URL}/r/{subreddit}/hot",
            params={"limit": limit, "t": "week"},
        )
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for post in data.get("data", {}).get("children", []):
            d = post.get("data", {})
            score = d.get("score", 0)
            if score < min_score:
                continue

            url = d.get("url", "")
            if url.startswith("/r/"):
                url = f"https://www.reddit.com{url}"
            if "reddit.com" in url and "/comments/" in url:
                url = d.get("url_overridden_by_dest", url)

            articles.append(
                CollectedArticle(
                    url=url or f"https://www.reddit.com{d.get('permalink', '')}",
                    title=d.get("title", ""),
                    author=d.get("author"),
                    source=Source.REDDIT,
                    source_id=d.get("id"),
                    published_at=datetime.fromtimestamp(d.get("created_utc", 0), tz=UTC),
                    points=score,
                    comment_count=d.get("num_comments", 0),
                    tags=[subreddit],
                )
            )
        return articles
