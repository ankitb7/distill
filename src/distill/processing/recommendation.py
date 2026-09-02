import re
from dataclasses import dataclass
from urllib.parse import urlparse

from distill.db import Database
from distill.models import Article, ScoreBreakdown

ScoredArticle = tuple[Article, ScoreBreakdown]


@dataclass(frozen=True)
class ReadingSlateRequest:
    limit: int = 20
    week_start: str | None = None
    week_end: str | None = None
    exclude_last_week: bool = True


def select_reading_slate(
    db: Database, config: dict, request: ReadingSlateRequest
) -> list[ScoredArticle]:
    """Select a high-quality, non-redundant Reading slate from assessed Articles."""
    slate_config = config.get("recommendation", {})
    candidate_multiplier = max(1, int(slate_config.get("candidate_multiplier", 5)))
    candidates = db.get_top_articles(
        limit=request.limit * candidate_multiplier,
        week_start=request.week_start,
        week_end=request.week_end,
        exclude_last_week=request.exclude_last_week,
    )
    qualified = [item for item in candidates if meets_quality_gate(item[1], slate_config)]
    selected = _diversify(qualified, request.limit, slate_config)
    if not slate_config.get("fill_to_limit", False) or len(selected) >= request.limit:
        return selected

    selected_ids = {article.id for article, _ in selected}
    fallback_minimum_relevance = float(slate_config.get("fallback_minimum_relevance", 0.4))
    fallback = [
        item
        for item in candidates
        if item[0].id not in selected_ids
        and item[1].status == "success"
        and item[1].relevance >= fallback_minimum_relevance
    ]
    return _diversify(
        fallback,
        request.limit,
        slate_config,
        selected=selected,
        relax_caps=True,
    )


def meets_quality_gate(score: ScoreBreakdown, config: dict) -> bool:
    """Return whether an assessment qualifies for the primary Reading slate."""
    return (
        score.status == "success"
        and score.composite_score >= float(config.get("minimum_score", 0.35))
        and score.relevance >= float(config.get("minimum_relevance", 0))
        and score.applicability >= float(config.get("minimum_applicability", 0))
        and score.evidence_quality >= float(config.get("minimum_evidence_quality", 0))
        and score.noise_penalty <= float(config.get("maximum_noise_penalty", 1))
    )


def _diversify(
    candidates: list[ScoredArticle],
    limit: int,
    config: dict,
    *,
    selected: list[ScoredArticle] | None = None,
    relax_caps: bool = False,
) -> list[ScoredArticle]:
    diversity_strength = float(config.get("diversity_strength", 0.15))
    max_per_domain = max(1, int(config.get("max_per_domain", 2)))
    max_per_source = max(1, int(config.get("max_per_source", max(2, limit * 3 // 5))))
    remaining = list(candidates)
    selected = list(selected or [])
    domain_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for article, _ in selected:
        domain = _domain(article)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        source = article.source.value
        source_counts[source] = source_counts.get(source, 0) + 1

    while remaining and len(selected) < limit:
        eligible = [
            item
            for item in remaining
            if domain_counts.get(_domain(item[0]), 0) < max_per_domain
            and source_counts.get(item[0].source.value, 0) < max_per_source
        ]
        if not eligible:
            eligible = [
                item
                for item in remaining
                if domain_counts.get(_domain(item[0]), 0) < max_per_domain
            ]
        if not eligible and relax_caps:
            eligible = remaining
        if not eligible:
            break
        best = max(
            eligible,
            key=lambda item: (
                item[1].composite_score
                - diversity_strength * _maximum_similarity(item[0], selected)
            ),
        )
        selected.append(best)
        remaining.remove(best)
        domain = _domain(best[0])
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        source = best[0].source.value
        source_counts[source] = source_counts.get(source, 0) + 1

    return selected


def _domain(article: Article) -> str:
    return urlparse(article.url).netloc.lower().removeprefix("www.")


def _maximum_similarity(article: Article, selected: list[ScoredArticle]) -> float:
    if not selected:
        return 0.0
    tokens = _tokens(article)
    return max(_jaccard(tokens, _tokens(other)) for other, _ in selected)


def _tokens(article: Article) -> set[str]:
    text = f"{article.title} {article.summary or ''} {article.content_text or ''}"
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
