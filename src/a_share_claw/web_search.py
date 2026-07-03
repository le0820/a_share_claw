from __future__ import annotations

import html
import json
import os
import re
import urllib.parse

from .http_client import fetch_json, fetch_text
from .utils import truncate


async def search_web(query: str, max_results: int = 5) -> str:
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if api_key:
        return await _search_brave(query, max_results, api_key)
    return await _search_duckduckgo(query, max_results)


async def fetch_url_summary(url: str, max_chars: int = 8000) -> str:
    text = await fetch_text(url, timeout=20, max_bytes=max_chars * 4)
    text = _html_to_text(text)
    return truncate(text.strip(), max_chars)


async def _search_brave(query: str, max_results: int, api_key: str) -> str:
    encoded = urllib.parse.quote(query)
    url = f"https://api.search.brave.com/res/v1/web/search?q={encoded}&count={max_results}"
    data = await fetch_json(url, timeout=15)
    results = []
    for item in data.get("web", {}).get("results", [])[:max_results]:
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "description": item.get("description"),
            }
        )
    return json.dumps(results, ensure_ascii=False)


async def _search_duckduckgo(query: str, max_results: int) -> str:
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    page = await fetch_text(url, timeout=20, max_bytes=1_000_000)
    pattern = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    results = []
    for raw_url, raw_title in pattern.findall(page):
        if len(results) >= max_results:
            break
        href = html.unescape(raw_url)
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
            qs = urllib.parse.parse_qs(parsed.query)
            href = qs.get("uddg", [href])[0]
        title = _html_to_text(raw_title)
        results.append({"title": title, "url": href})
    return json.dumps(results, ensure_ascii=False)


def _html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()
