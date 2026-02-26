from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Source(StrEnum):
    HACKERNEWS = "hackernews"
    RSS = "rss"
    DEVTO = "devto"
    ARXIV = "arxiv"


class CollectedArticle(BaseModel):
    """Raw article from a collector, before DB insertion."""

    url: str
    title: str
    author: str | None = None
    source: Source
    source_id: str | None = None
    published_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    points: int | None = None
    comment_count: int | None = None
    summary: str | None = None


class Article(BaseModel):
    """Article stored in the database."""

    id: int
    url: str
    normalized_url: str
    title: str
    author: str | None = None
    source: Source
    source_id: str | None = None
    content_text: str | None = None
    content_length: int | None = None
    published_at: datetime | None = None
    collected_at: datetime
    tags: list[str] = Field(default_factory=list)
    points: int | None = None
    comment_count: int | None = None
    summary: str | None = None
    is_duplicate: bool = False
    canonical_id: int | None = None


class ScoreBreakdown(BaseModel):
    """LLM scoring result for an article."""

    engagement_score: float = 0.0
    technical_depth: float = 0.0
    novelty: float = 0.0
    applicability: float = 0.0
    composite_score: float = 0.0
    reasoning: str = ""


class ScoredArticle(BaseModel):
    """Article with its score."""

    article: Article
    score: ScoreBreakdown
