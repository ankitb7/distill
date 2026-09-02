from unittest.mock import AsyncMock, patch

import pytest

from distill.models import CollectedArticle, Source
from distill.processing.pipeline import PipelineStage, collect_articles, run_pipeline


@pytest.mark.asyncio
async def test_run_pipeline_owns_order_and_database_lifetime(tmp_path):
    stages = []
    config = {
        "database": {"path": str(tmp_path / "pipeline.db")},
        "sources": {
            source: {"enabled": False}
            for source in ("hackernews", "rss", "devto", "arxiv", "slack")
        },
    }

    result = await run_pipeline(config, on_stage=stages.append)

    assert stages == list(PipelineStage)
    assert result.collection.inserted == 0
    assert result.extracted == 0
    assert result.deduplicated == 0
    assert result.scored == 0
    assert result.truncated == 0


@pytest.mark.asyncio
async def test_collect_articles_isolates_source_failures(tmp_db):
    good = AsyncMock()
    good.source_name = "good"
    good.collect.return_value = [
        CollectedArticle(url="https://example.com/post", title="Post", source=Source.RSS)
    ]
    bad = AsyncMock()
    bad.source_name = "bad"
    bad.collect.side_effect = RuntimeError("unavailable")

    with patch("distill.processing.pipeline._collectors", return_value=[bad, good]):
        result = await collect_articles(tmp_db, {})

    assert result.inserted == 1
    assert result.sources[0].error == "unavailable"
    assert result.sources[1].inserted == 1
