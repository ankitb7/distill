from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from distill.db import Database
from distill.models import CollectedArticle, Source
from distill.processing.extractor import extract_articles


class IntakeStatus(StrEnum):
    ADDED = "added"
    EXTRACTED = "extracted"
    EXISTS = "exists"


@dataclass(frozen=True)
class ManualArticleRequest:
    url: str
    title: str | None = None


@dataclass(frozen=True)
class IntakeResult:
    url: str
    title: str
    status: IntakeStatus
    article_id: int | None


def ingest_articles(db: Database, articles: Iterable[CollectedArticle]) -> list[IntakeResult]:
    """Persist validated articles without applying Manual article conventions."""
    results = []
    for article in articles:
        article_id = db.insert_article(article)
        status = IntakeStatus.ADDED if article_id is not None else IntakeStatus.EXISTS
        results.append(IntakeResult(article.url, article.title, status, article_id))
    return results


async def add_manual_articles(
    db: Database, requests: Iterable[ManualArticleRequest]
) -> list[IntakeResult]:
    """Enrich and persist Manual articles with consistent source and tag semantics."""
    normalized = [
        ManualArticleRequest(_normalize_url(request.url), request.title) for request in requests
    ]
    extracted = await extract_articles([request.url for request in normalized])
    results = []
    for request, extraction in zip(normalized, extracted, strict=True):
        title = request.title or extraction.title or extraction.url
        article = CollectedArticle(
            url=extraction.url,
            title=title,
            source=Source.RSS,
            source_id=f"manual:{request.url}",
            tags=["manual"],
            content_text=extraction.content,
            content_length=extraction.content_length or None,
        )
        article_id = db.insert_article(article)
        if article_id is None:
            status = IntakeStatus.EXISTS
        elif extraction.content:
            status = IntakeStatus.EXTRACTED
        else:
            status = IntakeStatus.ADDED
        results.append(IntakeResult(extraction.url, title, status, article_id))
    return results


def _normalize_url(url: str) -> str:
    stripped = url.strip()
    return stripped if stripped.startswith(("http://", "https://")) else f"https://{stripped}"
