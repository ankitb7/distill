"""Article scoring: engagement normalization + Claude LLM-as-judge."""

import asyncio
import json
import os
from collections.abc import Callable
from hashlib import sha256
from statistics import mean, stdev
from urllib.parse import urlparse

import httpx

from distill.db import Database
from distill.models import Article, ScoreBreakdown

ASSESSMENT_RUBRIC_VERSION = "actionable-insight-v1"


def _extract_rss_domains(config: dict) -> set[str]:
    """Extract domains from RSS feed URLs in config as trusted author sources."""
    domains = set()
    feeds = config.get("sources", {}).get("rss", {}).get("feeds", [])
    for feed in feeds:
        url = feed.get("url", "")
        if url:
            domain = urlparse(url).netloc.removeprefix("www.")
            domains.add(domain)
    return domains


def _extract_rss_authors(config: dict) -> dict[str, str]:
    """Extract domain -> author name mapping from RSS feeds."""
    authors = {}
    feeds = config.get("sources", {}).get("rss", {}).get("feeds", [])
    for feed in feeds:
        url = feed.get("url", "")
        name = feed.get("name", "")
        if url and name:
            domain = urlparse(url).netloc.removeprefix("www.")
            authors[domain] = name
    return authors


def _get_trusted_curators(config: dict) -> dict[str, str]:
    """Get trusted Slack curators from config: {user_id: name}."""
    curators = {}
    slack_config = config.get("sources", {}).get("slack", {})
    for curator in slack_config.get("trusted_curators", []):
        curators[curator["id"]] = curator["name"]
    return curators


def assessment_version(config: dict, model: str) -> str:
    """Identify the model, Reader profile, rubric, and weights behind an assessment."""
    material = {
        "rubric": ASSESSMENT_RUBRIC_VERSION,
        "model": model,
        "reader_profile": config.get("reader_profile", {}),
        "weights": config.get("scoring", {}).get("weights", {}),
        "content_preview_chars": config.get("scoring", {}).get("content_preview_chars", 6000),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()[:16]


def _bounded(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def compute_composite_score(engagement: float, assessment: ScoreBreakdown, weights: dict) -> float:
    """Combine independent Article assessment dimensions and an explicit noise penalty."""
    dimensions = {
        "engagement": engagement,
        "relevance": assessment.relevance,
        "technical_depth": assessment.technical_depth,
        "novelty": assessment.novelty,
        "applicability": assessment.applicability,
        "evidence_quality": assessment.evidence_quality,
    }
    defaults = {
        "engagement": 0.05,
        "relevance": 0.25,
        "technical_depth": 0.15,
        "novelty": 0.15,
        "applicability": 0.25,
        "evidence_quality": 0.15,
    }
    positive_weights = {
        name: max(0.0, float(weights.get(name, default))) for name, default in defaults.items()
    }
    total_weight = sum(positive_weights.values()) or 1.0
    positive = sum(dimensions[name] * weight for name, weight in positive_weights.items())
    noise_weight = max(0.0, float(weights.get("noise_penalty", 0.25)))
    return _bounded(positive / total_weight - noise_weight * assessment.noise_penalty)


def compute_engagement_score(article: Article, all_articles: list[Article]) -> float:
    if article.points is None and article.comment_count is None:
        return 0.5

    cohort = [a for a in all_articles if a.source == article.source]
    points = [a.points for a in cohort if a.points is not None]
    comments = [a.comment_count for a in cohort if a.comment_count is not None]

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


async def _fetch_web_signals(url: str, trusted_domains: set[str]) -> dict:
    """Search HN Algolia API + check URL domain for reputation signals."""
    signals: dict = {"hn_points": 0, "hn_comments": 0, "hn_stories": 0, "domain_note": ""}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": url, "tags": "story", "hitsPerPage": 5},
            )
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", [])
                signals["hn_stories"] = len(hits)
                if hits:
                    signals["hn_points"] = max(h.get("points", 0) or 0 for h in hits)
                    signals["hn_comments"] = max(h.get("num_comments", 0) or 0 for h in hits)
    except Exception:
        pass

    domain = urlparse(url).netloc.removeprefix("www.")
    if domain in trusted_domains:
        signals["domain_note"] = f"Trusted source (in user's RSS subscriptions): {domain}"

    return signals


def _build_assessment_prompt(article: Article, config: dict, external_signals: list[str]) -> str:
    """Build one evidence-first rubric for every Article source."""
    profile = config.get("reader_profile", {})
    content = article.content_text or article.summary or "No extracted content available."
    content_limit = int(config.get("scoring", {}).get("content_preview_chars", 6000))
    profile_text = json.dumps(profile, indent=2, sort_keys=True)
    signals = "\n".join(f"- {signal}" for signal in external_signals) or "- none"
    return f"""Assess whether this Article is worth this specific reader's limited attention.

Reader profile (goals are semantic, never literal keyword requirements):
{profile_text}

Article:
- title: {article.title}
- url: {article.url}
- source: {article.source.value}
- author/context: {article.author or "unknown"}
- external signals:\n{signals}

Representative content (may be truncated):
{content[:content_limit]}

Score each dimension independently from 0.0 to 1.0:
- relevance: directly advances one or more Reader profile outcomes. Mere AI mention is irrelevant.
- technical_depth: exposes mechanisms, architecture, constraints, trade-offs, or implementation.
- novelty: contains a transferable insight not merely a new event, release, or familiar claim.
- applicability: yields a concrete experiment, decision, workflow, migration tactic, or artifact the
  reader could use within weeks. Predictions and awareness alone are not actions.
- evidence_quality: first-hand implementation, code, measurements, evaluation, incident data, or a
  detailed case study. Unsupported assertions, vendor claims, and second-hand summaries score low.
- noise_penalty: repackaging, hype, vague futurism, unevidenced prediction, broad news, listicles,
  or commentary that provides neither a new insight nor an executable action.

Important calibration:
- A polished or long article is not necessarily deep.
- A cutting-edge topic is not novel unless the article contributes a specific new insight.
- Reputation and engagement are discovery signals only; never substitute them for substance.
- A strong case study may be actionable through transferable decisions even without a tutorial.
- Be strict. Most articles should score below 0.6 overall.

Return JSON only:
{{"relevance": 0.X, "technical_depth": 0.X, "novelty": 0.X,
  "applicability": 0.X, "evidence_quality": 0.X, "noise_penalty": 0.X,
  "recommended_action": "one specific action or empty string",
  "reasoning": "brief evidence-grounded explanation"}}"""


async def score_with_llm(
    articles: list[Article],
    model: str = "claude-sonnet-4-5-20250929",
    config: dict | None = None,
    concurrency: int = 5,
    on_error: Callable[[int, str], None] | None = None,
) -> dict[int, ScoreBreakdown]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {}

    config = config or {}
    rss_authors = _extract_rss_authors(config)
    trusted_curators = _get_trusted_curators(config)
    trusted_domains = _extract_rss_domains(config)

    results: dict[int, ScoreBreakdown] = {}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    scoring_config = config.get("scoring", {})
    max_retries = max(0, int(scoring_config.get("max_retries", 3)))
    retry_base_seconds = max(0.0, float(scoring_config.get("retry_base_seconds", 1)))
    max_output_tokens = max(256, int(scoring_config.get("max_output_tokens", 768)))

    async def assess(article: Article, client: httpx.AsyncClient) -> None:
        external_signals = []
        if article.source.value == "slack":
            web_signals = await _fetch_web_signals(article.url, trusted_domains)
            if web_signals["hn_stories"]:
                external_signals.append(
                    f"HN coverage: {web_signals['hn_stories']} stories; "
                    f"maximum {web_signals['hn_points']} points"
                )
            if web_signals["domain_note"]:
                external_signals.append(web_signals["domain_note"])

        domain = urlparse(article.url).netloc.removeprefix("www.")
        if domain in rss_authors:
            external_signals.append(f"Reader subscribes to {rss_authors[domain]}")
        if article.author in trusted_curators:
            external_signals.append(f"Shared by trusted curator {trusted_curators[article.author]}")

        prompt = _build_assessment_prompt(article, config, external_signals)

        last_error = "unknown assessment failure"
        for attempt in range(max_retries + 1):
            try:
                async with semaphore:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": model,
                            "max_tokens": max_output_tokens,
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
                    relevance=_bounded(parsed.get("relevance", 0)),
                    technical_depth=_bounded(parsed.get("technical_depth", 0)),
                    novelty=_bounded(parsed.get("novelty", 0)),
                    applicability=_bounded(parsed.get("applicability", 0)),
                    evidence_quality=_bounded(parsed.get("evidence_quality", 0)),
                    noise_penalty=_bounded(parsed.get("noise_penalty", 0)),
                    reasoning=parsed.get("reasoning", ""),
                    recommended_action=parsed.get("recommended_action", ""),
                )
                return
            except httpx.HTTPStatusError as error:
                last_error = f"HTTP {error.response.status_code}"
                retryable = error.response.status_code == 429 or error.response.status_code >= 500
            except (httpx.TransportError, json.JSONDecodeError, KeyError, IndexError) as error:
                last_error = type(error).__name__
                retryable = True
            except Exception as error:
                last_error = type(error).__name__
                retryable = False

            if not retryable or attempt == max_retries:
                break
            await asyncio.sleep(retry_base_seconds * (2**attempt))

        if on_error:
            on_error(article.id, last_error)

    async with httpx.AsyncClient(timeout=60) as client:
        await asyncio.gather(*(assess(article, client) for article in articles))

    return results


async def score_articles(
    db: Database,
    config: dict,
    *,
    force: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    scoring_config = config.get("scoring", {})
    model = scoring_config.get("model", "claude-sonnet-4-5-20250929")
    weights = scoring_config.get("weights", {})
    batch_size = scoring_config.get("batch_size", 5)
    concurrency = scoring_config.get("concurrency", 5)
    max_age_days = scoring_config.get("assessment_max_age_days", 45)
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    version = assessment_version(config, model)

    pending = db.get_articles_for_assessment(version, force=force, max_age_days=max_age_days)
    all_articles = db.get_all_articles(non_duplicate_only=True)
    scored_count = 0

    if has_api_key:
        llm_eligible = [
            a
            for a in pending
            if a.content_text
            or a.summary
            or a.source.value == "slack"
            or "manual" in (a.tags or [])
        ]
        llm_ids = {a.id for a in llm_eligible}
        engagement_only = [a for a in pending if a.id not in llm_ids]
    else:
        llm_eligible = []
        engagement_only = pending

    for article in engagement_only:
        eng = compute_engagement_score(article, all_articles)
        score = ScoreBreakdown(
            engagement_score=eng,
            composite_score=min(eng * 0.4, 0.4),
            reasoning=(
                "Article assessment unavailable: ANTHROPIC_API_KEY is not configured"
                if not has_api_key
                else "Article assessment deferred until content is available"
            ),
            score_version=version,
            status="unavailable" if not has_api_key else "incomplete",
        )
        db.insert_score(article.id, score)
        scored_count += 1
    if on_progress and engagement_only:
        on_progress(scored_count, len(pending))

    for i in range(0, len(llm_eligible), batch_size):
        batch = llm_eligible[i : i + batch_size]
        failures: dict[int, str] = {}
        llm_scores = await score_with_llm(
            batch,
            model=model,
            config=config,
            concurrency=concurrency,
            on_error=failures.__setitem__,
        )

        for article in batch:
            eng = compute_engagement_score(article, all_articles)
            llm = llm_scores.get(article.id)

            if llm:
                llm.engagement_score = eng
                llm.composite_score = compute_composite_score(eng, llm, weights)
                llm.score_version = version
                llm.status = "success"
                score = llm
            else:
                score = ScoreBreakdown(
                    engagement_score=eng,
                    composite_score=min(eng * 0.4, 0.4),
                    reasoning=(
                        f"Article assessment failed ({failures.get(article.id, 'unknown')}); "
                        "retained for retry"
                    ),
                    score_version=version,
                    status="failed",
                )
            db.insert_score(article.id, score)
            scored_count += 1
        if on_progress:
            on_progress(scored_count, len(pending))

    return scored_count
