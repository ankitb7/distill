import pytest

from ai_pulse.collectors.hackernews import HackerNewsCollector
from ai_pulse.collectors.rss import RSSCollector


@pytest.mark.asyncio
async def test_hackernews_collector_disabled():
    collector = HackerNewsCollector()
    config = {"sources": {"hackernews": {"enabled": False}}}
    articles = await collector.collect(config)
    assert articles == []


@pytest.mark.asyncio
async def test_rss_collector_disabled():
    collector = RSSCollector()
    config = {"sources": {"rss": {"enabled": False}}}
    articles = await collector.collect(config)
    assert articles == []


@pytest.mark.asyncio
async def test_rss_collector_empty_feeds():
    collector = RSSCollector()
    config = {"sources": {"rss": {"enabled": True, "feeds": []}}}
    articles = await collector.collect(config)
    assert articles == []
