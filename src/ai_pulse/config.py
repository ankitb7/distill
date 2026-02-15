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
        "database": {"path": "ai_pulse.db"},
        "output": {"dir": "output"},
        "sources": {
            "hackernews": {"enabled": True, "keywords": ["AI", "LLM"], "min_points": 10},
            "rss": {"enabled": True, "feeds": []},
        },
        "scoring": {
            "model": "claude-sonnet-4-5-20250929",
            "batch_size": 5,
            "weights": {
                "engagement": 0.20,
                "technical_depth": 0.25,
                "novelty": 0.25,
                "applicability": 0.30,
            },
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
