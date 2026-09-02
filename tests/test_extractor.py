from unittest.mock import AsyncMock, patch

import httpx
import pytest

from distill.processing.extractor import (
    ExtractedArticle,
    ExtractionMethod,
    ExtractionRequest,
    extract_article,
    extract_articles,
)


def _response(url: str, text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("GET", url))


def _mock_client(responses: dict[str, httpx.Response | Exception]) -> AsyncMock:
    client = AsyncMock()

    async def get(url: str, **kwargs) -> httpx.Response:
        response = responses[url]
        if isinstance(response, Exception):
            raise response
        return response

    client.get = get
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None
    return context


@pytest.mark.asyncio
async def test_extract_article_returns_title_content_and_method():
    url = "https://example.com/article"
    html = (
        "<html><head><title>Deep &amp; Useful</title></head><body>"
        + "word " * 80
        + "</body></html>"
    )
    with patch(
        "distill.processing.extractor.httpx.AsyncClient",
        return_value=_mock_client({url: _response(url, html)}),
    ):
        result = await extract_article(url)

    assert result.requested_url == url
    assert result.url == url
    assert result.title == "Deep & Useful"
    assert result.content
    assert result.method in {ExtractionMethod.TRAFILATURA, ExtractionMethod.READABILITY}


@pytest.mark.asyncio
async def test_extract_article_uses_jina_fallback():
    url = "https://example.com/blocked"
    jina_url = f"https://r.jina.ai/{url}"
    with patch(
        "distill.processing.extractor.httpx.AsyncClient",
        return_value=_mock_client(
            {
                url: _response(url, "blocked", status=403),
                jina_url: _response(jina_url, "Useful fallback content. " * 10),
            }
        ),
    ):
        result = await extract_article(url)

    assert result.content == ("Useful fallback content. " * 10).strip()
    assert result.method is ExtractionMethod.JINA


@pytest.mark.asyncio
async def test_extract_articles_preserves_order_and_isolates_no_content():
    first = "https://example.com/one"
    second = "https://example.com/two"
    responses = {
        first: _response(first, "<title>One</title>" + "content " * 30),
        second: _response(second, "no content", status=500),
        f"https://r.jina.ai/{second}": _response(
            f"https://r.jina.ai/{second}", "missing", status=404
        ),
    }
    with patch(
        "distill.processing.extractor.httpx.AsyncClient",
        return_value=_mock_client(responses),
    ):
        results = await extract_articles([ExtractionRequest(first), second], concurrency=2)

    assert [result.requested_url for result in results] == [first, second]
    assert results[0].content
    assert results[1] == ExtractedArticle(second, second, None, None, None)


@pytest.mark.asyncio
async def test_extract_article_rejects_non_http_url():
    with pytest.raises(ValueError, match="Unsupported article URL"):
        await extract_article("example.com/article")


@pytest.mark.asyncio
async def test_extract_articles_isolates_invalid_urls():
    valid = "https://example.com/valid"
    html = "<title>Valid</title>" + "content " * 30
    with patch(
        "distill.processing.extractor.httpx.AsyncClient",
        return_value=_mock_client({valid: _response(valid, html)}),
    ):
        results = await extract_articles(["not-a-url", valid])

    assert results[0] == ExtractedArticle("not-a-url", "not-a-url", None, None, None)
    assert results[1].title == "Valid"
    assert results[1].content
