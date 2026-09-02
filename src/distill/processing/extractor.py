import asyncio
import html as html_lib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import httpx
import trafilatura
from readability import Document

from distill.db import Database
from distill.models import Article

_TWEET_URL = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/\w+/status/\d+")
_URL_IN_TEXT = re.compile(r"https?://[^\s\)\]>\"\']+")


class ExtractionMethod(StrEnum):
    TRAFILATURA = "trafilatura"
    READABILITY = "readability"
    JINA = "jina"


@dataclass(frozen=True)
class ExtractionRequest:
    url: str
    hint_text: str | None = None


@dataclass(frozen=True)
class ExtractedArticle:
    requested_url: str
    url: str
    title: str | None
    content: str | None
    method: ExtractionMethod | None

    @property
    def content_length(self) -> int:
        return len(self.content) if self.content else 0


def extract_article_urls_from_text(text: str) -> list[str]:
    """Extract non-tweet HTTP URLs from plain text (tweet body, feed summary, etc.)."""
    urls = []
    for match in _URL_IN_TEXT.finditer(text):
        url = match.group().rstrip(".,;)'\"")
        if _TWEET_URL.match(url):
            continue
        if "jina.ai" in url:
            continue
        urls.append(url)
    return urls


async def resolve_tweet_url(
    client: httpx.AsyncClient, url: str, hint_text: str | None = None
) -> str:
    """
    For a tweet URL, return the article URL embedded in the tweet.
    Tries hint_text (feedparser summary/content) first — no HTTP call needed.
    Falls back to Jina Reader to fetch the tweet and extract the link.
    Returns the original URL if no article link is found.
    """
    if not _TWEET_URL.match(url):
        return url

    if hint_text:
        candidates = extract_article_urls_from_text(hint_text)
        if candidates:
            return candidates[0]

    try:
        resp = await client.get(
            f"https://r.jina.ai/{url}",
            headers={"Accept": "text/plain", "X-Return-Format": "text"},
        )
        resp.raise_for_status()
        candidates = extract_article_urls_from_text(resp.text)
        if candidates:
            return candidates[0]
    except Exception:
        pass

    return url


async def extract_content(db: Database, limit: int = 50) -> int:
    articles = db.get_articles_without_content(limit=limit)
    extracted = 0

    results = await extract_articles(article.url for article in articles)
    for article, result in zip(articles, results, strict=True):
        if result.url != article.url:
            db.update_url(article.id, result.url)
        if result.content:
            db.update_content(article.id, result.content)
            extracted += 1

    return extracted


async def extract_article(url: str, *, hint_text: str | None = None) -> ExtractedArticle:
    """Resolve and extract one article without persisting it."""
    if not _is_supported_url(url):
        raise ValueError(f"Unsupported article URL: {url!r}")
    return (await extract_articles([ExtractionRequest(url=url, hint_text=hint_text)]))[0]


async def extract_articles(
    requests: Iterable[str | ExtractionRequest], *, concurrency: int = 8
) -> list[ExtractedArticle]:
    """Extract articles in input order using one shared HTTP client."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    normalized = [
        request if isinstance(request, ExtractionRequest) else ExtractionRequest(url=request)
        for request in requests
    ]
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "distill/0.1 (article curation bot)"},
    ) as client:

        async def extract_one(request: ExtractionRequest) -> ExtractedArticle:
            if not _is_supported_url(request.url):
                return ExtractedArticle(request.url, request.url, None, None, None)
            async with semaphore:
                return await _extract_article(client, request)

        return await asyncio.gather(*(extract_one(request) for request in normalized))


def _is_supported_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


async def _extract_article(
    client: httpx.AsyncClient, request: ExtractionRequest
) -> ExtractedArticle:
    resolved_url = await resolve_tweet_url(client, request.url, hint_text=request.hint_text)
    title: str | None = None

    try:
        resp = await client.get(resolved_url)
        resp.raise_for_status()
        html = _sanitize_html(resp.text)
        title = extract_html_title(html)
        content, method = _parse_html(html)
        if content:
            return ExtractedArticle(request.url, resolved_url, title, content, method)
    except Exception:
        pass

    try:
        resp = await client.get(
            f"https://r.jina.ai/{resolved_url}",
            headers={"Accept": "text/plain", "X-Return-Format": "text"},
        )
        resp.raise_for_status()
        content = resp.text.strip()
        if content and len(content) > 100:
            return ExtractedArticle(
                request.url, resolved_url, title, content, ExtractionMethod.JINA
            )
    except Exception:
        pass

    return ExtractedArticle(request.url, resolved_url, title, None, None)


async def fetch_article_content(client: httpx.AsyncClient, url: str) -> str | None:
    """Compatibility helper for callers that already own an HTTP client."""
    return (await _extract_article(client, ExtractionRequest(url=url))).content


async def _extract_single(client: httpx.AsyncClient, article: Article) -> str | None:
    return await fetch_article_content(client, article.url)


def parse_html_to_text(html: str) -> str | None:
    return _parse_html(_sanitize_html(html))[0]


def _parse_html(html: str) -> tuple[str | None, ExtractionMethod | None]:
    content = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if content and len(content) > 100:
        return content, ExtractionMethod.TRAFILATURA

    try:
        doc = Document(html)
        content = trafilatura.extract(doc.summary()) or _strip_tags(doc.summary())
        if content and len(content) > 100:
            return content, ExtractionMethod.READABILITY
    except Exception:
        pass

    return None, None


def extract_html_title(html: str) -> str | None:
    og_title = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        html,
        re.IGNORECASE,
    )
    if og_title:
        return html_lib.unescape(og_title.group(1).strip())
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if title:
        return html_lib.unescape(re.sub(r"\s+", " ", title.group(1)).strip())
    return None


def _sanitize_html(html: str) -> str:
    return html.translate({i: None for i in range(32) if i not in (9, 10, 13)})


def _strip_tags(html: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean
