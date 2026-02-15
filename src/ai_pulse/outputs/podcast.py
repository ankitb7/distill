"""Podcast generation using Google NotebookLM via notebooklm-py."""

from datetime import datetime
from pathlib import Path

from ai_pulse.db import Database
from ai_pulse.models import Article, ScoreBreakdown
from ai_pulse.outputs.digest import get_week_range


def _collect_weekly_articles(
    db: Database, top_n: int
) -> tuple[str, list[tuple[Article, ScoreBreakdown]]]:
    """Get top articles + all manual articles for the current week."""
    label, week_start, week_end = get_week_range()

    top = db.get_top_articles(limit=top_n, week_start=week_start, week_end=week_end)
    if not top:
        top = db.get_top_articles(limit=top_n)

    manual = db.get_manual_articles(week_start=week_start, week_end=week_end)

    # Merge: manual articles first (guaranteed), then top articles (deduped)
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
    """Get specific articles by ID for an on-demand podcast."""
    label = datetime.now().strftime("%Y-%m-%d-%H%M")
    articles = db.get_articles_by_ids(article_ids)
    return label, articles


async def generate_podcast(
    db: Database,
    config: dict,
    output_dir: Path,
    article_ids: list[int] | None = None,
) -> Path | None:
    from notebooklm import NotebookLMClient

    podcast_config = config.get("podcast", {})
    top_n = podcast_config.get("top_n", 20)

    if article_ids:
        label, articles = _collect_ondemand_articles(db, article_ids)
        title = f"AI Pulse On-Demand — {label}"
    else:
        label, articles = _collect_weekly_articles(db, top_n)
        title = f"AI Pulse — {label}"

    if not articles:
        return None

    manual_count = sum(1 for a, _ in articles if "manual" in (a.tags or []))
    print(f"Podcast: {len(articles)} articles ({manual_count} manually added)")

    source_text = _build_source_text(articles, label)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_path = output_dir / f"podcast-source-{label}.md"
    source_path.write_text(source_text)

    audio_path = output_dir / f"podcast-{label}.mp3"

    async with await NotebookLMClient.from_storage() as client:
        notebook = await client.notebooks.create(title)
        nb_id = notebook.id

        await client.sources.add_text(
            nb_id,
            title=f"AI Pulse Articles — {label}",
            content=source_text,
            wait=True,
        )

        instructions = (
            "You are briefing a senior software engineer who uses AI coding "
            "agents (Claude Code, Cursor) daily. Cover each article focusing "
            "on practical takeaways, what's genuinely novel, and what the "
            "listener can apply at work Monday morning. Be direct, skip hype. "
            "Keep the tone conversational but information-dense."
        )
        status = await client.artifacts.generate_audio(
            nb_id, instructions=instructions
        )

        print(f"Podcast generation started: {title}")
        print("Waiting for NotebookLM audio (this takes a few minutes)...")
        await client.artifacts.wait_for_completion(
            nb_id, status.task_id, timeout=600.0
        )

        await client.artifacts.download_audio(nb_id, str(audio_path))

    # Link to digest if it's a weekly podcast
    if not article_ids:
        week_label = label
        row = db.conn.execute(
            "SELECT id FROM digests WHERE week_label = ?", (week_label,)
        ).fetchone()
        if row:
            db.conn.execute(
                "UPDATE digests SET podcast_path = ? WHERE week_label = ?",
                (str(audio_path), week_label),
            )
        else:
            db.conn.execute(
                """INSERT INTO digests (week_label, podcast_path, article_count, created_at)
                   VALUES (?, ?, ?, ?)""",
                (week_label, str(audio_path), len(articles), datetime.now().isoformat()),
            )
        db.conn.commit()

    size_mb = audio_path.stat().st_size / 1024 / 1024
    print(f"Podcast saved: {audio_path} ({size_mb:.1f} MB)")
    return audio_path


def _build_source_text(
    articles: list[tuple[Article, ScoreBreakdown]], label: str
) -> str:
    lines = [
        f"# AI Pulse Weekly Briefing — {label}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "This document contains the top AI and engineering articles "
        "curated this week for senior software developers who heavily use "
        "AI coding agents (Claude Code, Cursor, etc.) in daily work. "
        "Cover each article focusing on practical implications, what's "
        "genuinely novel, and key technical insights a practitioner can "
        "apply immediately.",
        "",
    ]

    for rank, (article, score) in enumerate(articles, 1):
        manual_tag = " [MUST COVER]" if "manual" in (article.tags or []) else ""
        lines.append(f"## {rank}. {article.title}{manual_tag}")
        lines.append("")
        if article.author:
            lines.append(f"By: {article.author}")
        lines.append(f"Source: {article.source.value}")
        if score.composite_score > 0:
            lines.append(f"Quality Score: {score.composite_score:.2f}")
        lines.append("")

        if article.content_text:
            lines.append(article.content_text[:3000])
        elif article.summary:
            lines.append(article.summary)

        if score.reasoning:
            lines.append("")
            lines.append(f"Why this matters: {score.reasoning}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
