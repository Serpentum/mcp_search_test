import asyncio
import signal
import logging
import sys
import os
import time
import socket
import json
import ipaddress
import re
import unicodedata
from difflib import SequenceMatcher
from typing import List, Tuple, Optional
from urllib.parse import urlparse, quote_plus

from mcp.server.fastmcp import FastMCP

import httpx
from bs4 import BeautifulSoup
import trafilatura
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

# --- Language Detection & Engine Selection ---

CYRILLIC_PATTERN = re.compile(r'[\u0400-\u04FF]')
CHINESE_PATTERN = re.compile(r'[\u4E00-\u9FFF]')
LATIN_ALPHA_PATTERN = re.compile(r'[a-zA-Z]{3,}')


def detect_language(query: str) -> str:
    """Detect query language and return 'ru', 'zh', or 'en'."""
    if CYRILLIC_PATTERN.search(query):
        return "ru"
    if CHINESE_PATTERN.search(query):
        return "zh"
    if LATIN_ALPHA_PATTERN.search(query):
        return "en"
    return "en"


# --- Engine Config ---

# Default engine order (EN priority)
DEFAULT_ENGINE_ORDER = ["bing", "yandex", "baidu"]

# Engine priority by language
ENGINE_BY_LANG = {
    "ru": ["yandex", "bing", "baidu"],
    "zh": ["baidu", "bing", "yandex"],
    "en": ["bing", "yandex", "baidu"],
}

RESULTS_PER_ENGINE = int(os.getenv("MCP_RESULTS_PER_ENGINE", "12"))
EARLY_TERMINATION_THRESHOLD = int(os.getenv("MCP_EARLY_TERM", "8"))
BATCH_FETCH_SIZE = int(os.getenv("MCP_BATCH_FETCH", "4"))
SMART_FETCH_TOP_N = int(os.getenv("MCP_SMART_FETCH_TOP", "3"))
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("MCP_SEMANTIC_THRESHOLD", "0.6"))


def normalize_text(text: str) -> str:
    """Normalize text for semantic comparison."""
    text = text.lower().strip()
    text = unicodedata.normalize('NFKD', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def semantic_similarity(a: str, b: str) -> float:
    """Compute fuzzy similarity between two strings."""
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def relevance_score(title: str, snippet: str, query: str) -> float:
    """Score how relevant a result is to the query (0-1)."""
    q_words = set(normalize_text(query).split())
    if not q_words:
        return 0.0

    title_norm = normalize_text(title)
    snippet_norm = normalize_text(snippet)

    score = 0.0
    for word in q_words:
        if word in title_norm:
            score += 2.0
        if word in snippet_norm:
            score += 1.0

    # Bonus for exact phrase match
    if normalize_text(query) in normalize_text(title + " " + snippet):
        score += 3.0

    return score


def semantic_cache_get(cache: _Cache, key: str, fallback_keys: Optional[List[str]] = None) -> Optional[str]:
    """Try exact cache hit first, then try semantic match on fallback keys."""
    exact = cache.get(key)
    if exact is not None:
        return exact

    if fallback_keys:
        for fk in fallback_keys:
            cached_key = None
            for stored_key in list(cache._store.keys()):
                if stored_key.startswith("search:"):
                    stored_query = stored_key[len("search:"):]
                    if semantic_similarity(stored_query, fk) >= SEMANTIC_CACHE_THRESHOLD:
                        cached_key = stored_key
                        break
            if cached_key:
                logger.info("Semantic cache hit: '%s' matched '%s' (score=%.2f)", fk, stored_query,
                            semantic_similarity(stored_query, fk))
                return cache._store[cached_key].value
    return None

# --- Environment Config ---
CACHE_TTL_SECONDS = int(os.getenv("MCP_CACHE_TTL", "300"))
CACHE_MAX_SIZE = int(os.getenv("MCP_CACHE_MAX_SIZE", "100"))
UNSAFE_MODE = os.getenv("MCP_UNSAFE_MODE", "false").lower() == "true"
BROWSER_TIMEOUT = int(os.getenv("MCP_BROWSER_TIMEOUT", "15000"))

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

    def record_call(self, tool_type: str):
        self.total_count += 1
        if tool_type == "search":
            self.search_count += 1
        elif tool_type == "fetch":
            self.fetch_count += 1

    def reset(self):
        self.search_count = 0
        self.fetch_count = 0
        self.total_count = 0


_tracker = SessionTracker()


# --- Browser Manager ---

class _BrowserManager:
    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def init(self):
        async with self._lock:
            if self._initialized:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.firefox.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            self._context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
            )
            self._context.set_default_timeout(BROWSER_TIMEOUT)
            self._initialized = True
            logger.info("Playwright browser (firefox) initialized")

    async def new_page(self) -> Page:
        if not self._initialized:
            await self.init()
        return await self._context.new_page()

    async def close(self):
        async with self._lock:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._initialized = False
            logger.info("Playwright browser closed")


_browser = _BrowserManager()


# --- Search Engines ---

async def _parse_yandex(page: Page, query: str) -> list[tuple[str, str, str]]:
    """Search via Yandex."""
    url = f"https://yandex.ru/search/?text={quote_plus(query)}"
    logger.info("yandex: navigating to %s", url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT)
        try:
            await page.wait_for_selector(".serp-item", timeout=10000)
        except Exception:
            pass
    except Exception as e:
        logger.warning("yandex: navigation error: %s", str(e))
        return []

    results = await page.evaluate("""() => {
        const items = document.querySelectorAll('.serp-item');
        const results = [];
        for (const item of items) {
            const titleEl = item.querySelector('.serp-item__title a, a[href]');
            const linkEl = item.querySelector('.path__text, .link');
            const snippetEl = item.querySelector('.serp-item__text, .serp-item__snippet, .organic__text');
            if (titleEl && titleEl.href) {
                results.push({
                    title: titleEl.textContent.trim(),
                    url: titleEl.href,
                    snippet: snippetEl ? snippetEl.textContent.trim() : ''
                });
            }
        }
        return results.slice(0, """ + str(RESULTS_PER_ENGINE) + """);
    }""")

    parsed = []
    for r in results:
        title = r.get("title", "").strip()
        url = r.get("url", "").strip()
        snippet = r.get("snippet", "").strip()
        if title and url:
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = "https://yandex.ru" + url
            parsed.append((title, url, snippet))
    return parsed


async def _parse_bing(page: Page, query: str) -> list[tuple[str, str, str]]:
    """Search via Bing."""
    url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=ru&count=5"
    logger.info("bing: navigating to %s", url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT)
        try:
            await page.wait_for_selector(".b_algo", timeout=10000)
        except Exception:
            pass
    except Exception as e:
        logger.warning("bing: navigation error: %s", str(e))
        return []

    results = await page.evaluate("""() => {
        const items = document.querySelectorAll('.b_algo');
        const results = [];
        for (const item of items) {
            const titleEl = item.querySelector('h2 a');
            const snippetEl = item.querySelector('.b_caption p, p');
            if (titleEl && titleEl.href) {
                results.push({
                    title: titleEl.textContent.trim(),
                    url: titleEl.href,
                    snippet: snippetEl ? snippetEl.textContent.trim() : ''
                });
            }
        }
        return results.slice(0, """ + str(RESULTS_PER_ENGINE) + """);
    }""")

    parsed = []
    for r in results:
        title = r.get("title", "").strip()
        url = r.get("url", "").strip()
        snippet = r.get("snippet", "").strip()
        if title and url:
            if url.startswith("//"):
                url = "https:" + url
            parsed.append((title, url, snippet))
    return parsed


async def _parse_baidu(page: Page, query: str) -> list[tuple[str, str, str]]:
    """Search via Baidu."""
    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn=5"
    logger.info("baidu: navigating to %s", url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT)
        try:
            await page.wait_for_selector(".result, .result-op", timeout=10000)
        except Exception:
            pass
    except Exception as e:
        logger.warning("baidu: navigation error: %s", str(e))
        return []

    results = await page.evaluate("""() => {
        const items = document.querySelectorAll('.result, .result-op, .c-container');
        const results = [];
        const seen = new Set();
        for (const item of items) {
            const link = item.querySelector('a');
            if (!link) continue;
            const href = link.href || '';
            if (href && !seen.has(href)) {
                seen.add(href);
                const title = link.textContent.trim();
                const content = item.querySelector('.c-abstract, .content-2Ve07, .moia1Vr') || item;
                const snippet = content.textContent.trim().substring(0, 200);
                results.push({ title: title, url: href, snippet: snippet });
                if (results.length >= """ + str(RESULTS_PER_ENGINE) + """) break;
            }
        }
        return results;
    }""")

    parsed = []
    for r in results:
        title = r.get("title", "").strip()
        url = r.get("url", "").strip()
        snippet = r.get("snippet", "").strip()
        if title and url:
            parsed.append((title, url, snippet))
    return parsed


ENGINES = {
    "bing": (_parse_bing, "Bing"),
    "yandex": (_parse_yandex, "Yandex"),
    "baidu": (_parse_baidu, "Baidu"),
}


async def _search_all_engines(query: str) -> list[tuple[str, str, str]]:
    """Search using language-aware engine selection with early termination."""
    lang = detect_language(query)
    engine_order = ENGINE_BY_LANG.get(lang, DEFAULT_ENGINE_ORDER)

    all_results = []
    seen_urls = set()

    for engine_key in engine_order:
        engine_func, engine_name = ENGINES[engine_key]
        page = await _browser.new_page()
        try:
            await Stealth().apply_stealth_async(page)
            results = await engine_func(page, query)
            if results:
                logger.info("%s (lang=%s) returned %d results", engine_name, lang, len(results))

                for title, url, snippet in results:
                    if url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append((title, url, snippet))

                if len(all_results) >= EARLY_TERMINATION_THRESHOLD:
                    logger.info("Early termination: %d results collected", len(all_results))
                    break
        except Exception as e:
            logger.warning("%s search failed: %s", engine_name, str(e))
        finally:
            try:
                await page.close()
            except Exception:
                pass

    return all_results


# --- web_search ---

@app.tool()
async def web_search(query: str) -> List[str]:
    """Search the web via multiple search engines with smart fetch.
    
    Args:
        query: Search query string
        
    Returns:
        List of search results with relevance scoring and top-N fetch
    """
    if not query or not query.strip():
        return ["Error: query parameter is required and cannot be empty."]

    query = query.strip()

    # Check cache (exact + semantic)
    cached = semantic_cache_get(_tracker.cache, f"search:{query}", fallback_keys=[query])
    if cached is not None:
        logger.info("web_search: cache hit for '%s'", query)
        return [*cached]

    logger.info("web_search: cache miss for '%s'", query)

    try:
        results = await _search_all_engines(query)

        if not results:
            _tracker.record_call("search")
            return [f"No results found for query: {query}"]

        # Score and sort by relevance
        scored_results = []
        for title, url, snippet in results:
            score = relevance_score(title, snippet, query)
            scored_results.append((score, title, url, snippet))

        scored_results.sort(key=lambda x: x[0], reverse=True)

        # Smart fetch: only fetch top-N most relevant results
        fetch_limit = min(SMART_FETCH_TOP_N, len(scored_results))
        top_results = scored_results[:fetch_limit]

        # Batch fetch top results
        fetched_contents = []
        for i in range(0, len(top_results), BATCH_FETCH_SIZE):
            batch = top_results[i:i + BATCH_FETCH_SIZE]

            tasks = []
            for score, title, url, snippet in batch:
                tasks.append(fetch_url(url))

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for (score, title, url, snippet), content in zip(batch, batch_results):
                if isinstance(content, Exception):
                    logger.warning(f"Batch fetch failed for {url}: {content}")
                    continue
                if content and not content.startswith("Error") and not content.startswith("ERROR"):
                    fetched_contents.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "content": content,
                        "relevance_score": score
                    })

        # Format results
        if not fetched_contents:
            formatted_results = []
            for i, (score, title, url, snippet) in enumerate(top_results, 1):
                formatted_results.append(
                    f"Result {i}:\n"
                    f"  Title: {title}\n"
                    f"  URL: {url}\n"
                    f"  Snippet: {snippet}\n"
                    f"  Relevance: {score:.2f}\n"
                    f"{'-' * 40}"
                )
        else:
            formatted_results = []
            for i, item in enumerate(fetched_contents, 1):
                formatted_results.append(
                    f"Result {i}:\n"
                    f"  Title: {item['title']}\n"
                    f"  URL: {item['url']}\n"
                    f"  Snippet: {item['snippet']}\n"
                    f"  Relevance: {item['relevance_score']:.2f}\n"
                    f"  Content:\n{item['content'][:800]}\n"
                    f"{'-' * 40}"
                )

        _tracker.record_call("search")
        _tracker.cache.put(f"search:{query}", formatted_results)

        logger.info("web_search: %d results, %d fetched",
                    len(top_results), len(fetched_contents))

        return formatted_results

    except Exception as e:
        logger.error("web_search error: %s", str(e), exc_info=True)
        return [f"Error searching for '{query}': {str(e)}"]


# --- fetch_url ---

@app.tool()
async def fetch_url(url: str, format: str = "markdown") -> str:
    """Fetch page content from a URL.
    
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
        return cached

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
                logger.info("fetch_url: HTML %d chars", len(content))
                return content

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
        logger.info("fetch_url: %d chars", len(content))
        return content

    except httpx.RequestError as e:
        logger.error("fetch_url network error: %s", str(e), exc_info=True)
        return f"Error fetching '{url}': Network error - {str(e)}"
    except httpx.HTTPStatusError as e:
        logger.error("fetch_url HTTP error: %s", str(e), exc_info=True)
        return f"Error fetching '{url}': HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.error("fetch_url error: %s", str(e), exc_info=True)
        return f"Error fetching '{url}': {str(e)}"



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
    if UNSAFE_MODE:
        return False
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


if __name__ == "__main__":
    async def main():
        try:
            await app.run_stdio_async()
        except asyncio.CancelledError:
            pass
        finally:
            await _browser.close()

    asyncio.run(main())
