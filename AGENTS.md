# AGENTS.md — Distill

AI article curation and podcast generator for senior engineers focused on AI adoption.
Pipeline: **Collect → Extract → Dedup → Score → Digest → Podcast**.

## Agent skills

### Issue tracker

Issues and PRDs are local Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage states use the five default Matt Pocock role strings. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.

## Tech Stack

- Python 3.12+, package manager: `uv`
- CLI: Typer + Rich
- Web: FastAPI + Jinja2 + Pico CSS + HTMX
- Database: SQLite (WAL mode)
- LLM: Anthropic Claude (scoring + podcast scripts)
- TTS: NotebookLM or edge-tts
- Embeddings: sentence-transformers (all-MiniLM-L6-v2)
- Content extraction: trafilatura → readability-lxml → Jina Reader fallback

## Commands

```bash
# Setup
uv sync                          # Install production deps
uv sync --group dev              # Install dev deps (pytest, ruff)
uv run distill init              # Create DB schema

# Full pipeline
uv run distill run               # collect → extract → dedup → score → truncate

# Individual steps
uv run distill collect            # Fetch articles from enabled sources
uv run distill extract            # Extract full content for non-RSS articles
uv run distill dedup              # Title-based deduplication
uv run distill dedup --embeddings # Title + embedding deduplication
uv run distill score              # Score articles (engagement + Claude)

# Outputs
uv run distill digest             # Generate markdown digest
uv run distill podcast            # Generate weekly podcast
uv run distill archive            # Digest + podcast + cache top articles + prune old articles
uv run distill serve              # Web dashboard at http://localhost:8585

# Utilities
uv run distill stats              # Print DB statistics
uv run distill ingest FILE        # Ingest articles from JSON file (Slack MCP backend)
uv run distill qa                 # Visual QA screenshots (requires rodney + showboat)

# Testing and linting
uv run pytest                     # Run all tests
uv run pytest tests/test_db.py    # Run a single test file
uv run ruff check src/ tests/     # Lint
uv run ruff format src/ tests/    # Format
```

## Project Layout

```
src/distill/
├── cli.py              # Typer CLI entry point (all commands)
├── config.py           # YAML config + env loading
├── models.py           # Pydantic models (Article, CollectedArticle, Score, Source)
├── db.py               # SQLite wrapper, schema, CRUD
├── collectors/         # Article sources (hackernews, rss, devto, arxiv, slack)
├── processing/         # extractor, dedup, scorer
├── outputs/            # digest, podcast, web (FastAPI dashboard)
└── templates/          # Jinja2 HTML templates (8 files)
tests/                  # pytest suite with tmp_db fixture
config.yaml             # Main configuration (sources, scoring, dedup, web, podcast)
```

## Key Behaviours to Know

**RSS collector** fetches article content at collection time (not in the extract step). This ensures curated feed articles are never deprioritised by engagement-based queues. `distill extract` is still needed for HN and other sources.

**Content extraction** uses a three-tier fallback: trafilatura → readability-lxml → Jina Reader (`r.jina.ai`). The Jina fallback handles JS-rendered and bot-blocked sites (e.g. openai.com). Both the RSS collector and the extractor use `fetch_article_content()` from `processing/extractor.py`.

**Slack scoring** uses a separate LLM prompt and composite formula (engagement weight 0.05 vs 0.15 for other sources). It fetches HN virality signals live via Algolia and applies trusted curator boosts. Slack articles are always LLM-scored even without content (scored on URL + context alone).

**Scoring eligibility**: non-Slack articles need `content_text` to qualify for LLM scoring. Articles that fail content extraction get an engagement-only score capped at 0.4.

**`get_top_articles`** applies a 15-day recency filter and excludes URLs from the `weekly_cache` table (populated by `distill archive`) to prevent repeat surfacing across weeks.

**`CollectedArticle`** (the pre-DB model) now includes `content_text` and `content_length`. `insert_article` persists these if present.

**Database tables**: `articles`, `scores`, `dedup_groups`, `digests`, `weekly_cache`.

## Environment

- Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`
- Never commit `.env` files or API keys
- Config lives in `config.yaml` — all CLI commands accept `--config PATH` to override

## Code Style

- Ruff: `target-version = "py312"`, `line-length = 100`
- Lint rules: `E, F, I, N, W, UP`
- Use type hints on all function signatures
- Prefer `async`/`await` with `httpx` for HTTP calls
- Use Pydantic models for data validation (see `models.py`)
- No semicolons at end of lines

## Testing

- Framework: pytest + pytest-asyncio
- Fixtures: `conftest.py` provides `tmp_db` (temporary SQLite DB)
- Test paths: `tests/`
- Always run `uv run pytest` before considering a change complete
- When modifying a module, run its corresponding test: `uv run pytest tests/test_<module>.py`

## When Writing Code

- Run `uv run ruff check src/ tests/` after every file change
- Run `uv run ruff format src/ tests/` to auto-format
- Add tests for new functionality in `tests/`
- Use `rich.console.Console` for CLI output (not bare `print`)
- Collectors must implement the protocol in `collectors/base.py`
- Database operations go through `db.py` — never use raw SQL outside that module
- Content fetching always goes through `fetch_article_content()` in `processing/extractor.py` — do not inline httpx + trafilatura calls in collectors

## When Blocked

- If tests fail after 2 attempts: stop and report the failing test with full output
- If a dependency is missing: check `pyproject.toml` first, then suggest `uv add <pkg>`
- Never delete the database file to resolve errors
- Never force push or skip tests

## Definition of Done

A task is complete when ALL pass:
1. `uv run ruff check src/ tests/` exits 0
2. `uv run ruff format --check src/ tests/` exits 0
3. `uv run pytest` exits 0 with no failures
4. Changed files are staged and committed
