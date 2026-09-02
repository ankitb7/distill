from pathlib import Path

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG_PATH = Path("config.yaml")
PROJECT_ROOT = Path(__file__).parent.parent.parent


def load_config(config_path: Path | None = None) -> dict:
    load_dotenv()
    path = config_path or PROJECT_ROOT / DEFAULT_CONFIG_PATH
    if not path.exists():
        return _default_config()
    with open(path) as f:
        return yaml.safe_load(f)


def _default_config() -> dict:
    return {
        "database": {"path": "distill.db"},
        "output": {"dir": "output"},
        "sources": {
            "hackernews": {"enabled": True, "keywords": ["AI", "LLM"], "min_points": 10},
            "rss": {"enabled": True, "feeds": []},
        },
        "scoring": {
            "model": "claude-sonnet-4-5-20250929",
            "batch_size": 12,
            "concurrency": 4,
            "max_retries": 3,
            "retry_base_seconds": 1,
            "max_output_tokens": 768,
            "assessment_max_age_days": 45,
            "content_preview_chars": 6000,
            "weights": {
                "engagement": 0.05,
                "relevance": 0.25,
                "technical_depth": 0.15,
                "novelty": 0.15,
                "applicability": 0.25,
                "evidence_quality": 0.15,
                "noise_penalty": 0.25,
            },
        },
        "reader_profile": {
            "mission": "Find evidence-backed, actionable AI engineering practices.",
            "priority_outcomes": [],
            "positive_signals": [],
            "noise_signals": [],
        },
        "recommendation": {
            "minimum_score": 0.35,
            "minimum_relevance": 0.6,
            "minimum_applicability": 0.5,
            "minimum_evidence_quality": 0.4,
            "maximum_noise_penalty": 0.45,
            "fill_to_limit": True,
            "fallback_minimum_relevance": 0.4,
            "candidate_multiplier": 5,
            "diversity_strength": 0.15,
            "max_per_domain": 2,
            "max_per_source": 12,
        },
        "dedup": {
            "title_similarity_threshold": 0.85,
            "embedding_similarity_threshold": 0.88,
        },
    }


def get_db_path(config: dict) -> Path:
    return PROJECT_ROOT / config["database"]["path"]


def get_output_dir(config: dict) -> Path:
    path = PROJECT_ROOT / config["output"]["dir"]
    path.mkdir(parents=True, exist_ok=True)
    return path
