"""Podcast generation with pluggable providers: notebooklm (default) or edge-tts."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from distill.db import Database
from distill.models import Article, ScoreBreakdown
from distill.outputs.digest import get_week_range
from distill.outputs.podcast_providers import PodcastSource, get_podcast_provider
from distill.processing.extractor import extract_articles
from distill.processing.recommendation import ReadingSlateRequest, select_reading_slate


def _collect_weekly_articles(
    db: Database, config: dict, top_n: int
) -> tuple[str, list[tuple[Article, ScoreBreakdown]]]:
    label, week_start, week_end = get_week_range()
    top = select_reading_slate(
        db,
        config,
        ReadingSlateRequest(limit=top_n, week_start=week_start, week_end=week_end),
    )
    if not top:
        top = select_reading_slate(db, config, ReadingSlateRequest(limit=top_n))
    manual = db.get_manual_articles(week_start=week_start, week_end=week_end)

    seen_ids = set()
    merged = []
    for item in manual:
        if item[0].id not in seen_ids:
            seen_ids.add(item[0].id)
            merged.append(item)
    for item in top:
        if item[0].id not in seen_ids:
            seen_ids.add(item[0].id)
            merged.append(item)

    return label, merged


def _collect_ondemand_articles(
    db: Database, article_ids: list[int]
) -> tuple[str, list[tuple[Article, ScoreBreakdown]]]:
    label = datetime.now().strftime("%Y-%m-%d-%H%M")
    articles = db.get_articles_by_ids(article_ids)
    return label, articles


async def _gather_article_texts(
    articles: list[tuple[Article, ScoreBreakdown]],
) -> dict[int, str]:
    texts = {}
    fetch_tasks = {}

    for article, _ in articles:
        if article.content_text and len(article.content_text) > 500:
            texts[article.id] = article.content_text[:3000]
        else:
            fetch_tasks[article.id] = article.url

    if fetch_tasks:
        results = await extract_articles(fetch_tasks.values())
        for (aid, _), result in zip(fetch_tasks.items(), results, strict=True):
            if result.content:
                texts[aid] = result.content[:3000]

    return texts


async def generate_podcast(
    db: Database,
    config: dict,
    output_dir: Path,
    article_ids: list[int] | None = None,
    on_status: "Callable[[str], None] | None" = None,
) -> Path | None:
    def _status(msg: str):
        print(msg)
        if on_status:
            on_status(msg)

    podcast_config = config.get("podcast", {})
    top_n = podcast_config.get("top_n", 20)
    provider_name = podcast_config.get("provider", "notebooklm")

    _status("Collecting articles...")
    if article_ids:
        label, articles = _collect_ondemand_articles(db, article_ids)
    else:
        label, articles = _collect_weekly_articles(db, config, top_n)

    if not articles:
        return None

    manual_count = sum(1 for a, _ in articles if "manual" in (a.tags or []))
    _status(f"Found {len(articles)} articles ({manual_count} manually added)")

    _status("Fetching article content...")
    article_texts = await _gather_article_texts(articles)
    _status(f"Content ready for {len(article_texts)}/{len(articles)} articles")

    output_dir.mkdir(parents=True, exist_ok=True)

    provider = get_podcast_provider(provider_name, podcast_config)
    audio_path = await provider.generate(
        PodcastSource(label, articles, article_texts, on_demand=bool(article_ids)),
        output_dir,
        _status,
    )

    # Link to digest
    if not article_ids:
        db.save_podcast(label, audio_path, len(articles))

    size_mb = audio_path.stat().st_size / 1024 / 1024
    print(f"Podcast saved: {audio_path} ({size_mb:.1f} MB)")
    return audio_path
