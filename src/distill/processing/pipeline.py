from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from distill.config import get_db_path
from distill.db import Database


class PipelineStage(StrEnum):
    COLLECT = "collect"
    EXTRACT = "extract"
    DEDUP = "dedup"
    SCORE = "score"
    TRUNCATE = "truncate"


@dataclass(frozen=True)
class SourceCollection:
    source: str
    found: int = 0
    inserted: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CollectionResult:
    sources: tuple[SourceCollection, ...]

    @property
    def inserted(self) -> int:
        return sum(source.inserted for source in self.sources)


@dataclass(frozen=True)
class PipelineResult:
    collection: CollectionResult
    extracted: int
    deduplicated: int
    scored: int
    truncated: int


def _collectors() -> list:
    from distill.collectors import ArxivCollector, DevToCollector, HackerNewsCollector, RSSCollector
    from distill.collectors.slack import SlackCollector

    return [
        HackerNewsCollector(),
        RSSCollector(),
        DevToCollector(),
        ArxivCollector(),
        SlackCollector(),
    ]


async def collect_articles(
    db: Database, config: dict, source_filter: str | None = None
) -> CollectionResult:
    collectors = _collectors()
    if source_filter:
        collectors = [
            collector for collector in collectors if collector.source_name == source_filter
        ]

    results = []
    for collector in collectors:
        try:
            articles = await collector.collect(config)
            inserted = sum(db.insert_article(article) is not None for article in articles)
            results.append(SourceCollection(collector.source_name, len(articles), inserted))
        except Exception as error:
            results.append(SourceCollection(collector.source_name, error=str(error)))
    return CollectionResult(tuple(results))


async def run_pipeline(
    config: dict,
    *,
    on_stage: Callable[[PipelineStage], None] | None = None,
) -> PipelineResult:
    """Run Collect → Extract → Dedup → Score → truncate with owned DB lifetime."""
    from distill.processing.dedup import run_title_dedup
    from distill.processing.extractor import extract_content
    from distill.processing.scorer import score_articles

    db = Database(get_db_path(config))
    try:
        db.init_schema()

        if on_stage:
            on_stage(PipelineStage.COLLECT)
        collection = await collect_articles(db, config)

        if on_stage:
            on_stage(PipelineStage.EXTRACT)
        extracted = await extract_content(db)

        if on_stage:
            on_stage(PipelineStage.DEDUP)
        threshold = config.get("dedup", {}).get("title_similarity_threshold", 0.85)
        deduplicated = run_title_dedup(db, threshold=threshold)

        if on_stage:
            on_stage(PipelineStage.SCORE)
        scored = await score_articles(db, config)

        if on_stage:
            on_stage(PipelineStage.TRUNCATE)
        evidence_chars = int(config.get("scoring", {}).get("content_preview_chars", 6000))
        truncated = db.truncate_content(excerpt_length=evidence_chars)

        return PipelineResult(collection, extracted, deduplicated, scored, truncated)
    finally:
        db.close()
