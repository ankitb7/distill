"""Podcast generation with pluggable providers: notebooklm (default) or edge-tts."""

import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from ai_pulse.db import Database
from ai_pulse.models import Article, ScoreBreakdown
from ai_pulse.outputs.digest import get_week_range


def _collect_weekly_articles(
    db: Database, top_n: int
) -> tuple[str, list[tuple[Article, ScoreBreakdown]]]:
    label, week_start, week_end = get_week_range()
    top = db.get_top_articles(limit=top_n, week_start=week_start, week_end=week_end)
    if not top:
        top = db.get_top_articles(limit=top_n)
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


def _build_source_text(
    articles: list[tuple[Article, ScoreBreakdown]],
    article_texts: dict[int, str],
    label: str,
) -> str:
    lines = [
        f"# Distill Weekly Briefing — {label}",
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

        text = article_texts.get(article.id, "")
        if text:
            lines.append(text[:3000])
        elif article.content_text:
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


# ---------------------------------------------------------------------------
# Content fetching (shared by edge-tts and notebooklm providers)
# ---------------------------------------------------------------------------


async def _fetch_article_content(url: str) -> str | None:
    try:
        import trafilatura

        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True,
            headers={"User-Agent": "distill/0.1"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = trafilatura.extract(
                resp.text,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            return content if content and len(content) > 100 else None
    except Exception:
        return None


async def _gather_article_texts(
    articles: list[tuple[Article, ScoreBreakdown]],
) -> dict[int, str]:
    import asyncio

    texts = {}
    fetch_tasks = {}

    for article, _ in articles:
        if article.content_text and len(article.content_text) > 500:
            texts[article.id] = article.content_text[:3000]
        else:
            fetch_tasks[article.id] = article.url

    if fetch_tasks:
        results = await asyncio.gather(
            *[_fetch_article_content(url) for url in fetch_tasks.values()]
        )
        for (aid, _), content in zip(fetch_tasks.items(), results):
            if content:
                texts[aid] = content[:3000]

    return texts


# ---------------------------------------------------------------------------
# Provider: NotebookLM
# ---------------------------------------------------------------------------


async def _generate_notebooklm(
    articles: list[tuple[Article, ScoreBreakdown]],
    article_texts: dict[int, str],
    label: str,
    output_dir: Path,
    article_ids: list[int] | None,
) -> Path:
    from notebooklm import NotebookLMClient

    title = (
        f"Distill On-Demand — {label}" if article_ids
        else f"Distill — {label}"
    )

    source_text = _build_source_text(articles, article_texts, label)
    source_path = output_dir / f"podcast-source-{label}.md"
    source_path.write_text(source_text)

    audio_path = output_dir / f"podcast-{label}.mp3"

    async with await NotebookLMClient.from_storage() as client:
        notebook = await client.notebooks.create(title)
        nb_id = notebook.id

        await client.sources.add_text(
            nb_id,
            title=f"Distill Articles — {label}",
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

    return audio_path


# ---------------------------------------------------------------------------
# Provider: edge-tts (Claude script + free TTS)
# ---------------------------------------------------------------------------


async def _generate_script(
    articles: list[tuple[Article, ScoreBreakdown]],
    article_texts: dict[int, str],
    label: str,
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY required for podcast script generation. "
            "Set it in .env or environment."
        )

    article_summaries = []
    for rank, (article, score) in enumerate(articles, 1):
        manual_tag = " [MUST COVER]" if "manual" in (article.tags or []) else ""
        text = article_texts.get(article.id, article.summary or "")
        entry = (
            f"Article {rank}: {article.title}{manual_tag}\n"
            f"By: {article.author or 'Unknown'} | Source: {article.source.value}\n"
            f"Score: {score.composite_score:.2f}\n"
        )
        if text:
            entry += f"Content:\n{text[:2000]}\n"
        if score.reasoning:
            entry += f"Why it matters: {score.reasoning}\n"
        article_summaries.append(entry)

    articles_block = "\n---\n".join(article_summaries)

    prompt = f"""Write a podcast script for two hosts (Alex and Sarah) discussing this week's \
top AI and software engineering articles. The audience is senior software engineers who \
actively use AI coding agents (Claude Code, Cursor) in daily work.

Guidelines:
- Natural, conversational tone — like two knowledgeable friends catching up
- Cover EVERY article, but spend more time on higher-scored and [MUST COVER] articles
- Focus on practical takeaways: what can the listener apply at work Monday morning?
- Be direct, skip hype. Call out what's genuinely novel vs. rehashed.
- Target 10-15 minutes of content (roughly 2000-3000 words)
- Start with a brief intro, end with a quick wrap-up of key themes
- Use natural transitions between topics

Format each line as:
[Alex] dialogue here
[Sarah] dialogue here

Articles for {label}:

{articles_block}

Write the complete script now:"""

    model = os.environ.get("DISTILL_SCRIPT_MODEL", "claude-sonnet-4-5-20250929")

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 8192,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


def _parse_script(script: str) -> list[tuple[str, str]]:
    segments = []
    current_speaker = None
    current_text = []

    for line in script.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("[Alex]") or line.startswith("[ALEX]"):
            if current_speaker and current_text:
                segments.append((current_speaker, " ".join(current_text)))
            current_speaker = "alex"
            current_text = [line.split("]", 1)[1].strip()]
        elif line.startswith("[Sarah]") or line.startswith("[SARAH]"):
            if current_speaker and current_text:
                segments.append((current_speaker, " ".join(current_text)))
            current_speaker = "sarah"
            current_text = [line.split("]", 1)[1].strip()]
        elif current_speaker:
            current_text.append(line)

    if current_speaker and current_text:
        segments.append((current_speaker, " ".join(current_text)))

    return segments


async def _tts_edge(
    segments: list[tuple[str, str]],
    output_path: Path,
    voice_a: str = "en-US-GuyNeural",
    voice_b: str = "en-US-AriaNeural",
):
    import edge_tts

    voice_map = {"alex": voice_a, "sarah": voice_b}

    with tempfile.TemporaryDirectory() as tmpdir:
        segment_files = []
        for i, (speaker, text) in enumerate(segments):
            if not text.strip():
                continue
            voice = voice_map.get(speaker, voice_a)
            seg_path = Path(tmpdir) / f"seg_{i:04d}.mp3"
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(seg_path))
            segment_files.append(seg_path)

        with open(output_path, "wb") as out:
            for seg in segment_files:
                out.write(seg.read_bytes())


async def _generate_edge_tts(
    articles: list[tuple[Article, ScoreBreakdown]],
    article_texts: dict[int, str],
    label: str,
    output_dir: Path,
    podcast_config: dict,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    def _status(msg: str):
        print(msg)
        if on_status:
            on_status(msg)

    _status("Generating podcast script with Claude...")
    script = await _generate_script(articles, article_texts, label)

    script_path = output_dir / f"podcast-script-{label}.md"
    script_path.write_text(script)

    segments = _parse_script(script)
    if not segments:
        raise RuntimeError("Failed to parse script into speaker segments")
    _status(f"Script ready — {len(segments)} dialogue segments")

    audio_path = output_dir / f"podcast-{label}.mp3"
    _status("Synthesizing audio with edge-tts...")

    voice_a = podcast_config.get("voice_a", "en-US-GuyNeural")
    voice_b = podcast_config.get("voice_b", "en-US-AriaNeural")
    await _tts_edge(segments, audio_path, voice_a=voice_a, voice_b=voice_b)

    return audio_path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


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
    provider = podcast_config.get("provider", "notebooklm")

    _status("Collecting articles...")
    if article_ids:
        label, articles = _collect_ondemand_articles(db, article_ids)
    else:
        label, articles = _collect_weekly_articles(db, top_n)

    if not articles:
        return None

    manual_count = sum(1 for a, _ in articles if "manual" in (a.tags or []))
    _status(f"Found {len(articles)} articles ({manual_count} manually added)")

    _status("Fetching article content...")
    article_texts = await _gather_article_texts(articles)
    _status(f"Content ready for {len(article_texts)}/{len(articles)} articles")

    output_dir.mkdir(parents=True, exist_ok=True)

    if provider == "notebooklm":
        _status("Generating podcast with NotebookLM...")
        audio_path = await _generate_notebooklm(
            articles, article_texts, label, output_dir, article_ids
        )
    elif provider == "edge-tts":
        _status("Generating podcast script with Claude...")
        audio_path = await _generate_edge_tts(
            articles, article_texts, label, output_dir, podcast_config,
            on_status=on_status,
        )
    else:
        raise ValueError(f"Unknown podcast provider: {provider!r}. Use: notebooklm, edge-tts")

    # Link to digest
    if not article_ids:
        week_label = label
        row = db.conn.execute(
            "SELECT id FROM digests WHERE week_label = ?", (week_label,)
        ).fetchone()
        if row:
            db.conn.execute(
                """UPDATE digests
                   SET podcast_path = ?, article_count = ?, created_at = ?
                   WHERE week_label = ?""",
                (str(audio_path), len(articles), datetime.now().isoformat(), week_label),
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
