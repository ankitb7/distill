import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from distill import scheduled
from distill.scheduled import validate_result


def test_claude_failure_reports_captured_error(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".claude.json").write_text(
        json.dumps({"mcpServers": {"slack": {"type": "http", "url": "https://example.com/mcp"}}})
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(scheduled.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(
        scheduled.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="Authentication expired"
        ),
    )
    with pytest.raises(RuntimeError, match="Authentication expired"):
        scheduled.collect_slack({"channels": []})


def test_failed_mcp_read_is_not_treated_as_empty_success() -> None:
    with pytest.raises(RuntimeError, match="collection failed"):
        validate_result({"is_error": True}, {}, datetime.now(UTC))
    with pytest.raises(RuntimeError, match="Incomplete"):
        validate_result(
            {
                "subtype": "success",
                "structured_output": {
                    "complete": False,
                    "error": "Slack authentication expired",
                    "articles": [],
                },
            },
            {},
            datetime.now(UTC),
        )


def test_filters_metadata_and_keeps_most_reacted_duplicate() -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    article = {
        "url": "https://example.com/article",
        "title": "Article",
        "source": "slack",
        "source_id": str(now.timestamp()),
        "tags": ["ai_coding_guild"],
        "points": 2,
    }
    candidates = [
        article,
        {**article, "points": 5},
        {**article, "url": "https://example.com/low", "points": 1},
        {**article, "url": "https://example.com/old", "source_id": "1"},
        {**article, "url": "https://example.com/other", "tags": ["other"]},
        {**article, "url": "https://miro.slack.com/archives/123"},
        {**article, "url": "https://docs.google.com/document/123"},
    ]
    result = validate_result(
        {
            "subtype": "success",
            "structured_output": {"complete": True, "error": "", "articles": candidates},
        },
        {"channels": [{"name": "ai_coding_guild"}], "min_reactions": 2},
        now,
    )
    assert len(result) == 1
    assert result[0].points == 5
    assert result[0].published_at == now


def test_pipeline_only_starts_after_successful_slack_ingestion(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        scheduled,
        "load_config",
        lambda: {"sources": {"slack": {"enabled": True, "backend": "mcp"}}},
    )
    monkeypatch.setattr(scheduled, "collect_slack", lambda config: [])
    monkeypatch.setattr(scheduled.subprocess, "run", lambda args, **kwargs: calls.append(args[1:]))
    scheduled.main()
    assert calls == [["ingest", "-"], ["run"]]

    calls.clear()

    def fail(config: dict) -> list:
        raise RuntimeError("Slack unavailable")

    monkeypatch.setattr(scheduled, "collect_slack", fail)
    with pytest.raises(RuntimeError, match="Slack unavailable"):
        scheduled.main()
    assert calls == []
