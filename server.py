import asyncio
import signal
import logging
import sys
import os
import time
import socket
import json
import ipaddress
from typing import List
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

import httpx
from googlesearch import search as google_search
from bs4 import BeautifulSoup
import trafilatura

# --- Environment Config ---
MAX_SEARCH_SOFT = int(os.getenv("MCP_MAX_SEARCH_SOFT", "5"))
MAX_SEARCH_HARD = int(os.getenv("MCP_MAX_SEARCH_HARD", "7"))
MAX_FETCH_SOFT = int(os.getenv("MCP_MAX_FETCH_SOFT", "8"))
MAX_FETCH_HARD = int(os.getenv("MCP_MAX_FETCH_HARD", "10"))
MAX_TOTAL_REQUESTS = int(os.getenv("MCP_MAX_TOTAL", "30"))
CACHE_TTL_SECONDS = int(os.getenv("MCP_CACHE_TTL", "300"))
CACHE_MAX_SIZE = int(os.getenv("MCP_CACHE_MAX_SIZE", "100"))

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("web-tools-mcp")

app = FastMCP("web-tools-mcp")


# --- Cache ---

class _CacheEntry:
    __slots__ = ("value", "created_at")

    def __init__(self, value, created_at: float):
        self.value = value
        self.created_at = created_at


class _Cache:
    def __init__(self, ttl: int = CACHE_TTL_SECONDS, max_size: int = CACHE_MAX_SIZE):
        self._store: dict[str, _CacheEntry] = {}
        self._order: list[str] = []
        self.ttl = ttl
        self.max_size = max_size

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() - entry.created_at > self.ttl:
            self._remove(key)
            return None
        return entry.value

    def put(self, key: str, value):
        if key in self._store:
            self._remove(key)
        if len(self._store) >= self.max_size:
            self._evict()
        self._store[key] = _CacheEntry(value, time.time())
        self._order.append(key)

    def _remove(self, key: str):
        self._store.pop(key, None)
        if key in self._order:
            self._order.remove(key)

    def _evict(self):
        if self._order:
            oldest = self._order.pop(0)
            self._store.pop(oldest, None)


# --- Session Tracker ---

class SessionTracker:
    def __init__(self):
        self.search_count = 0
        self.fetch_count = 0
        self.total_count = 0
        self.cache = _Cache()

    def can_call(self, tool_type: str) -> bool:
        if self.total_count >= MAX_TOTAL_REQUESTS:
            return False
        if tool_type == "search":
            return self.search_count < MAX_SEARCH_HARD
        if tool_type == "fetch":
            return self.fetch_count < MAX_FETCH_HARD
        return True

    def record_call(self, tool_type: str):
        self.total_count += 1
        if tool_type == "search":
            self.search_count += 1
        elif tool_type == "fetch":
            self.fetch_count += 1

    def get_progress(self) -> dict:
        return {
            "searches": self.search_count,
            "searches_soft": MAX_SEARCH_SOFT,
            "searches_hard": MAX_SEARCH_HARD,
            "fetches": self.fetch_count,
            "fetches_soft": MAX_FETCH_SOFT,
            "fetches_hard": MAX_FETCH_HARD,
            "total": self.total_count,
            "total_hard": MAX_TOTAL_REQUESTS,
            "remaining_searches": max(0, MAX_SEARCH_HARD - self.search_count),
            "remaining_fetches": max(0, MAX_FETCH_HARD - self.fetch_count),
            "remaining_total": max(0, MAX_TOTAL_REQUESTS - self.total_count),
        }

    def get_search_advisory(self) -> str | None:
        if self.search_count >= MAX_SEARCH_HARD:
            return None
        if self.search_count >= MAX_SEARCH_SOFT:
            remaining = MAX_SEARCH_HARD - self.search_count
            if remaining == 1:
                return (
                    "\n---\n"
                    "⚠️ Достигнут мягкий лимит поисков (5/7).\n"
                    "Пожалуйста, завершите поиск и перейдите к суммаризации найденных данных.\n"
                    "Прочитайте найденные страницы через fetch_url для углубления в тему."
                )
            else:
                return (
                    "\n---\n"
                    f"⚠️ Достигнут мягкий лимит поисков ({self.search_count}/{MAX_SEARCH_HARD}).\n"
                    f"Рекомендую: 1) Прочитать найденные страницы через fetch_url 2) "
                    f"Сформировать ответ на основе имеющихся данных"
                )
        return None

    def get_fetch_advisory(self) -> str | None:
        if self.fetch_count >= MAX_FETCH_HARD:
            return None
        if self.fetch_count >= MAX_FETCH_SOFT:
            return (
                "\n---\n"
                f"⚠️ Достигнут мягкий лимит чтений ({self.fetch_count}/{MAX_FETCH_HARD}).\n"
                "Пожалуйста, завершите чтение и сформируйте ответ на основе имеющихся данных."
            )
        return None

    def get_progress_banner(self) -> str:
        p = self.get_progress()
        return (
            f"\n---\n"
            f"📊 Прогресс: поиски {p['searches']}/{p['searches_hard']}, "
            f"чтения {p['fetches']}/{p['fetches_hard']}, "
            f"всего {p['total']}/{p['total_hard']}\n"
            f"Осталось: {p['remaining_searches']} поисков, "
            f"{p['remaining_fetches']} чтений, "
            f"{p['remaining_total']} всего"
        )


_tracker = SessionTracker()


async def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    logger.info("Received signal %s, shutting down...", sig_name)
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# --- URL Validation ---

PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_private_or_blocked(host: str) -> bool:
    host = host.strip().lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in net for net in PRIVATE_RANGES)
    except ValueError:
        pass
    if "localhost" in host:
        return True
    try:
        addr_info = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if any(ip_obj in net for net in PRIVATE_RANGES):
                    return True
            except ValueError:
                continue
    except socket.gaierror:
        pass
    return False


def validate_url(url: str) -> str | None:
    if not url or not url.strip():
        return "Error: url parameter is required and cannot be empty."

    url = url.strip()

    if not (url.startswith("http://") or url.startswith("https://")):
        return f"Error: URL must start with http:// or https://. Got: {url}"

    parsed = urlparse(url)
    host = parsed.hostname

    if not host:
        return f"Error: Could not extract hostname from URL: {url}"

    if is_private_or_blocked(host):
        return f"Error: Access to '{host}' is blocked for security reasons (private IP or localhost)."

    return None


# --- web_search ---

@app.tool()
async def web_search(query: str) -> List[str]:
    """Search the web via Google. Soft limit: 5, Hard limit: 7 per session.

    Args:
        query: Search query string

    Returns:
        List of up to 5 search results (title, URL, snippet)
    """
    if not query or not query.strip():
        return ["Error: query parameter is required and cannot be empty."]

    query = query.strip()

    cached = _tracker.cache.get(f"search:{query}")
    if cached is not None:
        logger.info("web_search: cache hit for '%s'", query)
        banner = _tracker.get_progress_banner()
        return [*cached, banner]

    if not _tracker.can_call("search"):
        logger.warning("Search hard limit reached (%d/%d)", _tracker.search_count, MAX_SEARCH_HARD)
        return [f"ERROR: Лимит поисков исчерпан ({MAX_SEARCH_HARD}/{MAX_SEARCH_HARD}). Дальнейший поиск недоступен. Используйте已有的 результаты."]

    try:
        results = list(google_search(query, num=5, stop=5, pause=2))

        if not results:
            _tracker.record_call("search")
            banner = _tracker.get_progress_banner()
            advisory = _tracker.get_search_advisory()
            msg = f"No results found for query: {query}"
            if advisory:
                return [msg, advisory]
            return [msg, banner]

        formatted_results = []
        for i, url in enumerate(results, 1):
            formatted_results.append(
                f"Result {i}:\n"
                f"  URL: {url}\n"
                f"  Snippet: Use fetch_url to read this page\n"
                f"{'-' * 40}"
            )

        _tracker.record_call("search")
        _tracker.cache.put(f"search:{query}", formatted_results)

        logger.info("web_search: %d results (call %d/%d)", len(results), _tracker.search_count, MAX_SEARCH_HARD)

        banner = _tracker.get_progress_banner()
        advisory = _tracker.get_search_advisory()

        if advisory:
            return [*formatted_results, advisory]
        return [*formatted_results, banner]

    except Exception as e:
        logger.error("web_search error: %s", str(e), exc_info=True)
        return [f"Error searching for '{query}': {str(e)}"]


# --- fetch_url ---

@app.tool()
async def fetch_url(url: str, format: str = "markdown") -> str:
    """Fetch page content from a URL. Soft limit: 8, Hard limit: 10 per session.

    Args:
        url: URL to fetch (http:// or https://)
        format: Output format - "markdown" (default) or "html"

    Returns:
        Page content in the specified format
    """
    error = validate_url(url)
    if error:
        return error

    fmt = format.lower() if format else "markdown"
    if fmt not in ("markdown", "html"):
        return f"Error: format must be 'markdown' or 'html'. Got: {format}"

    cached = _tracker.cache.get(f"fetch:{url}:{fmt}")
    if cached is not None:
        logger.info("fetch_url: cache hit for '%s' (%s)", url, fmt)
        banner = _tracker.get_progress_banner()
        return f"{cached}\n{banner}"

    if not _tracker.can_call("fetch"):
        logger.warning("Fetch hard limit reached (%d/%d)", _tracker.fetch_count, MAX_FETCH_HARD)
        return f"ERROR: Лимит чтений исчерпан ({MAX_FETCH_HARD}/{MAX_FETCH_HARD}). Дальнейшее чтение невозможно. Используйте已有的 результаты."

    try:
        headers = {
            "User-Agent": "WebToolsMCP/1.0 (AI Assistant Tool)"
        }

        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            headers=headers,
            verify=False
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            if fmt == "html":
                content = response.text[:10000]
                _tracker.record_call("fetch")
                _tracker.cache.put(f"fetch:{url}:{fmt}", content)
                logger.info("fetch_url: HTML %d chars (call %d/%d)", len(content), _tracker.fetch_count, MAX_FETCH_HARD)
                banner = _tracker.get_progress_banner()
                advisory = _tracker.get_fetch_advisory()
                result = f"{content}\n{banner}"
                if advisory:
                    result = f"{content}\n{advisory}"
                return result

            try:
                markdown = trafilatura.extract(
                    response.text,
                    include_links=False,
                    include_tables=True,
                    encoding="utf-8"
                )
                if markdown:
                    content = markdown[:10000]
                else:
                    soup = BeautifulSoup(response.text, "html.parser")
                    content = soup.get_text(separator="\n", strip=True)[:10000]
                    if not content.strip():
                        content = f"Could not extract text content from {url}"
            except Exception:
                soup = BeautifulSoup(response.text, "html.parser")
                content = soup.get_text(separator="\n", strip=True)[:10000]
                if not content.strip():
                    content = f"Could not extract text content from {url}"

        _tracker.record_call("fetch")
        _tracker.cache.put(f"fetch:{url}:{fmt}", content)
        logger.info("fetch_url: %d chars (call %d/%d)", len(content), _tracker.fetch_count, MAX_FETCH_HARD)

        banner = _tracker.get_progress_banner()
        advisory = _tracker.get_fetch_advisory()

        if advisory:
            return f"{content}\n{advisory}"
        return f"{content}\n{banner}"

    except httpx.RequestError as e:
        logger.error("fetch_url network error: %s", str(e), exc_info=True)
        return f"Error fetching '{url}': Network error - {str(e)}"
    except httpx.HTTPStatusError as e:
        logger.error("fetch_url HTTP error: %s", str(e), exc_info=True)
        return f"Error fetching '{url}': HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.error("fetch_url error: %s", str(e), exc_info=True)
        return f"Error fetching '{url}': {str(e)}"


if __name__ == "__main__":
    asyncio.run(app.run_stdio_async())
