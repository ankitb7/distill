import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import httpx

from distill.models import Article, ScoreBreakdown

StatusCallback = Callable[[str], None]
PODCAST_BRIEF = (
    "You are briefing a senior software engineer who uses AI coding agents daily. "
    "Cover practical takeaways, genuine novelty, and what can be applied Monday "
    "morning. Be direct, skip hype, and stay conversational but information-dense."
)


@dataclass(frozen=True)
class PodcastSource:
    label: str
    articles: list[tuple[Article, ScoreBreakdown]]
    article_texts: dict[int, str]
    on_demand: bool = False


class PodcastProvider(Protocol):
    async def generate(
        self, source: PodcastSource, output_dir: Path, on_status: StatusCallback
    ) -> Path: ...


def get_podcast_provider(name: str, config: dict) -> PodcastProvider:
    if name == "notebooklm":
        return NotebookLMProvider()
    if name == "gemini-api-tts":
        from distill.outputs.gemini_api_tts import ALEX_STYLE, SARAH_STYLE, GeminiAPITTSProvider

        return GeminiAPITTSProvider(
            model=config.get("gemini_api_model", "gemini-3.8-flash-tts"),
            voice_a=config.get("gemini_voice_a", "Puck"),
            voice_b=config.get("gemini_voice_b", "Aoede"),
            style_a=config.get("gemini_style_a", ALEX_STYLE),
            style_b=config.get("gemini_style_b", SARAH_STYLE),
        )
    if name == "gemini-tts":
        from distill.outputs.gemini_tts import GeminiTTSProvider

        return GeminiTTSProvider(
            project=config.get("gemini_project", ""),
            model=config.get("gemini_model", "gemini-2.5-pro-tts"),
            voice_a=config.get("gemini_voice_a", "Charon"),
            voice_b=config.get("gemini_voice_b", "Kore"),
        )
    if name == "edge-tts":
        return EdgeTTSProvider(
            voice_a=config.get("voice_a", "en-US-GuyNeural"),
            voice_b=config.get("voice_b", "en-US-AriaNeural"),
        )
    raise ValueError(
        f"Unknown podcast provider: {name!r}. Use: notebooklm, gemini-api-tts, gemini-tts, edge-tts"
    )


class NotebookLMProvider:
    async def generate(
        self, source: PodcastSource, output_dir: Path, on_status: StatusCallback
    ) -> Path:
        from notebooklm import NotebookLMClient

        title = (
            f"Distill On-Demand — {source.label}"
            if source.on_demand
            else f"Distill — {source.label}"
        )
        source_text = _build_source_text(source)
        (output_dir / f"podcast-source-{source.label}.md").write_text(source_text)
        audio_path = output_dir / f"podcast-{source.label}.mp3"

        async with await NotebookLMClient.from_storage() as client:
            notebook = await client.notebooks.create(title)
            await client.sources.add_text(
                notebook.id,
                title=f"Distill Articles — {source.label}",
                content=source_text,
                wait=True,
            )
            status = await client.artifacts.generate_audio(notebook.id, instructions=PODCAST_BRIEF)
            on_status("Waiting for NotebookLM audio (this takes a few minutes)...")
            await client.artifacts.wait_for_completion(notebook.id, status.task_id, timeout=600.0)
            await client.artifacts.download_audio(notebook.id, str(audio_path))

        return audio_path


@dataclass(frozen=True)
class EdgeTTSProvider:
    voice_a: str
    voice_b: str

    async def generate(
        self, source: PodcastSource, output_dir: Path, on_status: StatusCallback
    ) -> Path:
        on_status("Generating podcast script with Claude...")
        script = await _generate_script(source)
        (output_dir / f"podcast-script-{source.label}.md").write_text(script)

        segments = _parse_script(script)
        if not segments:
            raise RuntimeError("Failed to parse script into speaker segments")
        on_status(f"Script ready — {len(segments)} dialogue segments")

        audio_path = output_dir / f"podcast-{source.label}.mp3"
        on_status("Synthesizing audio with edge-tts...")
        await _synthesize_edge_tts(segments, audio_path, voice_a=self.voice_a, voice_b=self.voice_b)
        return audio_path


def _build_source_text(source: PodcastSource) -> str:
    lines = [
        f"# Distill Weekly Briefing — {source.label}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "Top AI and engineering articles curated for senior software developers using AI "
        "coding agents. Cover practical implications, genuine novelty, and key technical "
        "insights that can be applied immediately.",
        "",
    ]
    for rank, (article, score) in enumerate(source.articles, 1):
        manual_tag = " [MUST COVER]" if "manual" in (article.tags or []) else ""
        lines.extend([f"## {rank}. {article.title}{manual_tag}", ""])
        if article.author:
            lines.append(f"By: {article.author}")
        lines.append(f"Source: {article.source.value}")
        if score.composite_score > 0:
            lines.append(f"Quality Score: {score.composite_score:.2f}")
        lines.extend(["", _article_text(source, article)[:3000]])
        if score.reasoning:
            lines.extend(["", f"Why this matters: {score.reasoning}"])
        lines.extend(["", "---", ""])
    return "\n".join(lines)


def _article_text(source: PodcastSource, article: Article) -> str:
    return source.article_texts.get(article.id) or article.content_text or article.summary or ""


async def _generate_script(source: PodcastSource) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY required for podcast script generation")

    summaries = []
    for rank, (article, score) in enumerate(source.articles, 1):
        manual_tag = " [MUST COVER]" if "manual" in (article.tags or []) else ""
        entry = (
            f"Article {rank}: {article.title}{manual_tag}\n"
            f"By: {article.author or 'Unknown'} | Source: {article.source.value}\n"
            f"Score: {score.composite_score:.2f}\n"
        )
        text = _article_text(source, article)
        if text:
            entry += f"Content:\n{text[:2000]}\n"
        if score.reasoning:
            entry += f"Why it matters: {score.reasoning}\n"
        summaries.append(entry)

    articles_block = "\n---\n".join(summaries)
    prompt = f"""{PODCAST_BRIEF}

Write a two-host deep-dive discussion for Alex and Sarah, ready to speak aloud.

Guidelines:
- Build a shared argument across the sources, not a sequence of article summaries.
- Alex explores how things work; Sarah probes implications and challenges assumptions.
  Both bring insight, ask real follow-up questions, and sometimes revise their view.
- Make each turn respond to the previous one: answer, question, qualify, or build on it.
  Mix brief reactions with explanations; most turns should be 10-45 words. Occasionally
  use a longer explanation when needed. Avoid alternating equal-length mini-lectures.
- Use contractions, concrete examples, varied sentence lengths, and a little dry humor
  when it arises naturally. Do not manufacture excitement, laughter, filler, agreement,
  personal experiences, or claims that the hosts tested or watched something.
- Cover EVERY article, but spend more time on higher-scored and [MUST COVER] articles
- Focus on practical takeaways: what can the listener apply at work Monday morning?
- Attribute specific findings, numbers, and vendor claims naturally. Make a material
  limitation clear where it matters, without repeating a disclaimer after every source.
  Keep hypotheses and suggested experiments distinct from reported results.
- Target 12-16 minutes (roughly 1800-2400 words). Open with an interesting tension or
  concrete question, then orient the listener. Connect topics through ideas and callbacks.
  End with a useful conclusion and mention the original article links in Distill.

Output only spoken dialogue. Keep delivery directions out of the transcript.
Format each turn as:
[Alex] dialogue here
[Sarah] dialogue here

Articles for {source.label}:

{articles_block}

Write the complete script now:"""
    model = os.environ.get("DISTILL_SCRIPT_MODEL", "claude-sonnet-4-5-20250929")
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
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
        response.raise_for_status()
        return response.json()["content"][0]["text"]


def _parse_script(script: str) -> list[tuple[str, str]]:
    segments = []
    current_speaker = None
    current_text = []
    for line in script.splitlines():
        line = line.strip()
        if not line:
            continue
        speaker = next(
            (name for name in ("alex", "sarah") if line.lower().startswith(f"[{name}]")),
            None,
        )
        if speaker:
            if current_speaker and current_text:
                segments.append((current_speaker, " ".join(current_text)))
            current_speaker = speaker
            current_text = [line.split("]", 1)[1].strip()]
        elif current_speaker:
            current_text.append(line)
    if current_speaker and current_text:
        segments.append((current_speaker, " ".join(current_text)))
    return segments


async def _synthesize_edge_tts(
    segments: list[tuple[str, str]], output_path: Path, *, voice_a: str, voice_b: str
) -> None:
    import edge_tts

    voice_map = {"alex": voice_a, "sarah": voice_b}
    with tempfile.TemporaryDirectory() as tmpdir:
        segment_files = []
        for index, (speaker, text) in enumerate(segments):
            if not text.strip():
                continue
            segment_path = Path(tmpdir) / f"seg_{index:04d}.mp3"
            await edge_tts.Communicate(text, voice_map.get(speaker, voice_a)).save(
                str(segment_path)
            )
            segment_files.append(segment_path)
        with output_path.open("wb") as output:
            for segment_path in segment_files:
                output.write(segment_path.read_bytes())
