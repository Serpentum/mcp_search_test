import asyncio
import signal
import logging
import sys
import os
import socket
import ipaddress
from typing import List
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

import httpx
from googlesearch import search as google_search
from bs4 import BeautifulSoup
import trafilatura

MAX_SEARCH_CALLS = 2
MAX_FETCH_CALLS = 5
_search_call_count = 0
_fetch_call_count = 0

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("web-tools-mcp")

app = FastMCP("web-tools-mcp")


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
    """Validate URL. Returns error message if invalid, None if valid."""
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
    """Search the web via Google.

    Args:
        query: Search query string

    Returns:
        List of up to 5 search results (title, URL, snippet)
    """
    global _search_call_count
    _search_call_count += 1
    if _search_call_count > MAX_SEARCH_CALLS:
        logger.warning("Search call limit reached (%d), shutting down", MAX_SEARCH_CALLS)
        sys.stderr.write(f"ERROR: Search limit reached ({MAX_SEARCH_CALLS}). No more searches allowed.\n")
        sys.stderr.flush()
        os._exit(1)

    try:
        if not query or not query.strip():
            return ["Error: query parameter is required and cannot be empty."]

        results = list(google_search(query.strip(), num=5, stop=5, pause=2))

        if not results:
            return [f"No results found for query: {query}"]

        formatted_results = []
        for i, url in enumerate(results, 1):
            formatted_results.append(
                f"Result {i}:\n"
                f"  URL: {url}\n"
                f"  Snippet: Use fetch_url to read this page\n"
                f"{'-' * 40}"
            )

        logger.info("web_search: found %d results for '%s' (call %d/%d)", len(results), query, _search_call_count, MAX_SEARCH_CALLS)
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
    global _fetch_call_count
    _fetch_call_count += 1
    if _fetch_call_count > MAX_FETCH_CALLS:
        logger.warning("Fetch call limit reached (%d), shutting down", MAX_FETCH_CALLS)
        sys.stderr.write(f"ERROR: Fetch limit reached ({MAX_FETCH_CALLS}). No more page reads allowed.\n")
        sys.stderr.flush()
        os._exit(1)

    try:
        error = validate_url(url)
        if error:
            return error

        fmt = format.lower() if format else "markdown"
        if fmt not in ("markdown", "html"):
            return f"Error: format must be 'markdown' or 'html'. Got: {format}"

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
                logger.info("fetch_url: returned raw HTML (%d chars) from %s", len(content), url)
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

        logger.info("fetch_url: extracted %d chars from %s (call %d/%d)", len(content), url, _fetch_call_count, MAX_FETCH_CALLS)
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


if __name__ == "__main__":
    asyncio.run(app.run_stdio_async())
