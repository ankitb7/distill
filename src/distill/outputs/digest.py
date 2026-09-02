from datetime import datetime, timedelta
from pathlib import Path

from distill.db import Database
from distill.models import ScoreBreakdown
from distill.processing.recommendation import ReadingSlateRequest, select_reading_slate


def get_week_range(week_label: str | None = None) -> tuple[str, str, str]:
    if week_label:
        year, week = week_label.split("-W")
        dt = datetime.strptime(f"{year} {week} 1", "%G %V %u")
    else:
        dt = datetime.now()
        dt -= timedelta(days=dt.weekday())  # Monday

    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    label = f"{start.year}-W{start.isocalendar()[1]:02d}"
    return label, start.isoformat(), end.isoformat()


def generate_digest(
    db: Database,
    output_dir: Path,
    week_label: str | None = None,
    top_n: int = 20,
    config: dict | None = None,
) -> Path:
    label, week_start, week_end = get_week_range(week_label)
    config = config or {}
    articles = select_reading_slate(
        db,
        config,
        ReadingSlateRequest(limit=top_n, week_start=week_start, week_end=week_end),
    )

    if not articles:
        articles = select_reading_slate(db, config, ReadingSlateRequest(limit=top_n))

    lines = [
        f"# Distill Digest — {label}",
        f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        f"*{len(articles)} top articles*",
        "",
        "---",
        "",
    ]

    for rank, (article, score) in enumerate(articles, 1):
        score_display = _format_score(score)
        lines.append(f"## {rank}. {article.title}")
        lines.append("")
        meta_parts = []
        if article.author:
            meta_parts.append(f"**Author**: {article.author}")
        meta_parts.append(f"**Source**: {article.source.value}")
        if article.points:
            meta_parts.append(f"**Points**: {article.points}")
        if score.composite_score > 0:
            meta_parts.append(f"**Score**: {score.composite_score:.2f}")
        lines.append(" | ".join(meta_parts))
        lines.append("")
        lines.append(f"[Read article]({article.url})")
        lines.append("")

        if score_display:
            lines.append(score_display)
            lines.append("")

        if article.content_text:
            preview = article.content_text[:500].rsplit(" ", 1)[0]
            lines.append(f"> {preview}...")
            lines.append("")

        lines.append("---")
        lines.append("")

    markdown = "\n".join(lines)

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"digest-{label}.md"
    path.write_text(markdown)

    db.insert_digest(label, markdown, len(articles))
    return path


def _format_score(score: ScoreBreakdown) -> str:
    if score.composite_score == 0:
        return ""
    parts = [
        f"Engagement: {score.engagement_score:.2f}",
        f"Relevance: {score.relevance:.2f}",
        f"Depth: {score.technical_depth:.2f}",
        f"Novelty: {score.novelty:.2f}",
        f"Applicability: {score.applicability:.2f}",
        f"Evidence: {score.evidence_quality:.2f}",
        f"Noise: {score.noise_penalty:.2f}",
    ]
    line = " | ".join(parts)
    result = f"*{line}*"
    if score.reasoning:
        result += f"\n*{score.reasoning}*"
    if score.recommended_action:
        result += f"\n**Recommended action:** {score.recommended_action}"
    return result
