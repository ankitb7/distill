"""Article scoring: engagement normalization + Claude LLM-as-judge."""

import json
import os
from statistics import mean, stdev

import httpx

from ai_pulse.db import Database
from ai_pulse.models import Article, ScoreBreakdown


def compute_engagement_score(article: Article, all_articles: list[Article]) -> float:
    if article.points is None and article.comment_count is None:
        return 0.5

    points = [a.points for a in all_articles if a.points is not None]
    comments = [a.comment_count for a in all_articles if a.comment_count is not None]

    if not points or len(points) < 2:
        return 0.5

    p_mean = mean(points)
    p_std = stdev(points) if len(points) > 1 else 1.0
    c_mean = mean(comments) if comments else 0
    c_std = stdev(comments) if len(comments) > 1 else 1.0

    p_z = ((article.points or 0) - p_mean) / max(p_std, 1)
    c_z = ((article.comment_count or 0) - c_mean) / max(c_std, 1)

    raw = 0.6 * p_z + 0.4 * c_z
    return max(0.0, min(1.0, (raw + 2) / 4))


async def score_with_llm(
    articles: list[Article], model: str = "claude-sonnet-4-5-20250929"
) -> dict[int, ScoreBreakdown]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {}

    results = {}
    for article in articles:
        content_preview = (article.content_text or "")[:3000]
        if not content_preview:
            continue

        prompt = f"""Rate this article for a senior software engineer who heavily uses AI \
(Claude Code, coding agents, agentic workflows) in daily work.

The ideal article is about: practical AI adoption patterns, agentic engineering, \
AI-augmented development workflows, coding agents, LLM integration in production, \
career strategy for AI-era engineers, or deep technical dives with actionable takeaways.

Low-value: pure hype/funding news, surface-level overviews, theoretical ML papers \
without practical application, listicles.

Title: {article.title}
Source: {article.source.value}
Content (preview):
{content_preview}

Rate on three axes from 0.0 to 1.0:
1. technical_depth: 0=news rewrite/summary, 1=deep analysis with code, architecture, or methodology
2. novelty: 0=widely covered/obvious, 1=fresh perspective, new technique, or contrarian insight
3. applicability: 0=theoretical/hype, 1=I could apply this at work Monday morning

Respond in JSON only:
{{"technical_depth": 0.X, "novelty": 0.X, "applicability": 0.X, "reasoning": "brief"}}"""

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 256,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["content"][0]["text"]
                text = text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0]
                parsed = json.loads(text)
                results[article.id] = ScoreBreakdown(
                    technical_depth=float(parsed.get("technical_depth", 0)),
                    novelty=float(parsed.get("novelty", 0)),
                    applicability=float(parsed.get("applicability", 0)),
                    reasoning=parsed.get("reasoning", ""),
                )
        except Exception:
            continue

    return results


async def score_articles(db: Database, config: dict) -> int:
    scoring_config = config.get("scoring", {})
    model = scoring_config.get("model", "claude-sonnet-4-5-20250929")
    weights = scoring_config.get("weights", {})
    min_engagement = scoring_config.get("min_engagement_for_llm", 5)
    batch_size = scoring_config.get("batch_size", 5)

    w_eng = weights.get("engagement", 0.20)
    w_td = weights.get("technical_depth", 0.25)
    w_nov = weights.get("novelty", 0.25)
    w_app = weights.get("applicability", 0.30)

    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    unscored = db.get_unscored_articles(min_engagement=0, require_content=False)
    all_articles = db.get_all_articles(non_duplicate_only=True)

    scored_count = 0

    # Manually added articles get the highest score automatically
    manual = [a for a in unscored if "manual" in (a.tags or [])]
    manual_ids = {a.id for a in manual}
    for article in manual:
        score = ScoreBreakdown(
            engagement_score=1.0,
            technical_depth=1.0,
            novelty=1.0,
            applicability=1.0,
            composite_score=1.0,
            reasoning="Manually added — auto-scored highest",
        )
        db.insert_score(article.id, score)
        scored_count += 1

    unscored = [a for a in unscored if a.id not in manual_ids]

    if has_api_key:
        llm_eligible = [
            a for a in unscored
            if a.content_text
            and ((a.points or 0) >= min_engagement or a.points is None)
        ]
        llm_ids = {a.id for a in llm_eligible}
        engagement_only = [a for a in unscored if a.id not in llm_ids]
    else:
        llm_eligible = []
        engagement_only = unscored

    for article in engagement_only:
        eng = compute_engagement_score(article, all_articles)
        # Cap engagement-only at 0.4 so they never outrank LLM-scored articles
        composite = min(eng * w_eng / max(w_eng, 0.01), 0.4)
        score = ScoreBreakdown(
            engagement_score=eng,
            composite_score=composite,
            reasoning="Engagement-only (no LLM scoring)",
        )
        db.insert_score(article.id, score)
        scored_count += 1

    for i in range(0, len(llm_eligible), batch_size):
        batch = llm_eligible[i : i + batch_size]
        llm_scores = await score_with_llm(batch, model=model)

        for article in batch:
            eng = compute_engagement_score(article, all_articles)
            llm = llm_scores.get(article.id)

            if llm:
                composite = (
                    w_eng * eng
                    + w_td * llm.technical_depth
                    + w_nov * llm.novelty
                    + w_app * llm.applicability
                )
                score = ScoreBreakdown(
                    engagement_score=eng,
                    technical_depth=llm.technical_depth,
                    novelty=llm.novelty,
                    applicability=llm.applicability,
                    composite_score=composite,
                    reasoning=llm.reasoning,
                )
            else:
                score = ScoreBreakdown(
                    engagement_score=eng,
                    composite_score=eng,
                    reasoning="LLM scoring failed, engagement-only",
                )
            db.insert_score(article.id, score)
            scored_count += 1

    return scored_count
