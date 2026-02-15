"""Tests for FirecrawlScrapeTool."""

import json
from typing import Any

import httpx
import pytest

from nanobot.agent.tools.web import FirecrawlScrapeTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _firecrawl_response(
    success: bool = True,
    markdown: str = "# Hello\n\nWorld",
    title: str = "Hello Page",
    source_url: str = "https://example.com",
    status_code: int = 200,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a Firecrawl v2 API response body."""
    data: dict[str, Any] = {
        "success": success,
        "data": {
            "markdown": markdown,
            "metadata": {
                "title": title,
                "sourceURL": source_url,
                "statusCode": status_code,
            },
        },
    }
    if error:
        data["data"]["metadata"]["error"] = error
    return data


class _FakeResponse:
    """Minimal fake httpx.Response."""

    def __init__(self, status_code: int, data: dict[str, Any]):
        self.status_code = status_code
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://api.firecrawl.dev/v2/scrape"),
                response=self,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_api_key_returns_error():
    tool = FirecrawlScrapeTool(api_key="")
    result = await tool.execute(url="https://example.com")
    assert result == "Error: FIRECRAWL_API_KEY not configured"


@pytest.mark.asyncio
async def test_invalid_url_returns_validation_error():
    tool = FirecrawlScrapeTool(api_key="fc-test")
    result = await tool.execute(url="ftp://bad.example.com")
    obj = json.loads(result)
    assert "error" in obj
    assert "URL validation" in obj["error"]


@pytest.mark.asyncio
async def test_successful_scrape(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        captured["json"] = kwargs.get("json", {})
        return _FakeResponse(200, _firecrawl_response())

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tool = FirecrawlScrapeTool(api_key="fc-test-key")
    result = await tool.execute(url="https://example.com")
    obj = json.loads(result)

    assert obj["url"] == "https://example.com"
    assert obj["title"] == "Hello Page"
    assert obj["sourceURL"] == "https://example.com"
    assert obj["status"] == 200
    assert obj["truncated"] is False
    assert "# Hello" in obj["text"]

    # Verify request was formed correctly
    assert captured["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert captured["headers"]["Authorization"] == "Bearer fc-test-key"
    assert captured["json"]["onlyMainContent"] is True
    assert "markdown" in captured["json"]["formats"]


@pytest.mark.asyncio
async def test_api_returns_failure(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, _firecrawl_response(success=False, error="Blocked"))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tool = FirecrawlScrapeTool(api_key="fc-test")
    result = await tool.execute(url="https://example.com")
    obj = json.loads(result)
    assert "error" in obj


@pytest.mark.asyncio
async def test_http_error_returns_error_json(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return _FakeResponse(500, {})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tool = FirecrawlScrapeTool(api_key="fc-test")
    result = await tool.execute(url="https://example.com")
    obj = json.loads(result)
    assert "error" in obj
    assert obj["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_truncation_at_default_max_chars(monkeypatch):
    long_text = "x" * 60_000

    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, _firecrawl_response(markdown=long_text))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tool = FirecrawlScrapeTool(api_key="fc-test", max_chars=50_000)
    result = await tool.execute(url="https://example.com")
    obj = json.loads(result)
    assert obj["truncated"] is True
    assert obj["length"] == 50_000


@pytest.mark.asyncio
async def test_custom_max_chars_param(monkeypatch):
    long_text = "y" * 500

    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, _firecrawl_response(markdown=long_text))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tool = FirecrawlScrapeTool(api_key="fc-test")
    result = await tool.execute(url="https://example.com", maxChars=200)
    obj = json.loads(result)
    assert obj["truncated"] is True
    assert obj["length"] == 200


@pytest.mark.asyncio
async def test_no_truncation_when_within_limit(monkeypatch):
    short_text = "short content"

    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, _firecrawl_response(markdown=short_text))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tool = FirecrawlScrapeTool(api_key="fc-test")
    result = await tool.execute(url="https://example.com")
    obj = json.loads(result)
    assert obj["truncated"] is False
    assert obj["text"] == short_text


def test_should_cache_returns_false_for_errors():
    tool = FirecrawlScrapeTool(api_key="fc-test")
    error_result = json.dumps({"error": "something went wrong", "url": "https://example.com"})
    assert tool.should_cache(error_result) is False


def test_should_cache_returns_true_for_success():
    tool = FirecrawlScrapeTool(api_key="fc-test")
    ok_result = json.dumps({"url": "https://example.com", "text": "hello", "truncated": False})
    assert tool.should_cache(ok_result) is True


def test_tool_metadata():
    """Verify tool class attributes are set correctly."""
    tool = FirecrawlScrapeTool(api_key="fc-test")
    assert tool.name == "firecrawl_scrape"
    assert tool.parallel_safe is True
    assert tool.cacheable is True
    assert tool.cache_ttl_s == 600.0
    assert tool.max_retries == 1
    assert "url" in tool.parameters["properties"]
    assert "url" in tool.parameters["required"]


@pytest.mark.asyncio
async def test_env_var_fallback(monkeypatch):
    """API key falls back to FIRECRAWL_API_KEY env var."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-from-env")

    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, _firecrawl_response())

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tool = FirecrawlScrapeTool()  # no explicit api_key
    assert tool.api_key == "fc-from-env"
    result = await tool.execute(url="https://example.com")
    obj = json.loads(result)
    assert "text" in obj  # successful response
