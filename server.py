import asyncio
import signal
import logging
import sys
import socket
import ipaddress
from typing import List
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

import httpx
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup
import trafilatura

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


# --- URL Validation (T005) ---

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
    # Check for localhost variants
    if "localhost" in host:
        return True
    # Resolve hostname and check IPs
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

    # Check scheme
    if not (url.startswith("http://") or url.startswith("https://")):
        return f"Error: URL must start with http:// or https://. Got: {url}"

    parsed = urlparse(url)
    host = parsed.hostname

    if not host:
        return f"Error: Could not extract hostname from URL: {url}"

    if is_private_or_blocked(host):
        return f"Error: Access to '{host}' is blocked for security reasons (private IP or localhost)."

    return None


# --- web_search (T003) ---

@app.tool()
async def web_search(query: str) -> List[str]:
    """Search the web via DuckDuckGo.

    Args:
        query: Search query string

    Returns:
        List of up to 5 search results (title, URL, snippet)
    """
    try:
        if not query or not query.strip():
            return ["Error: query parameter is required and cannot be empty."]

        ddgs = DDGS()
        results = list(ddgs.text(query.strip(), max_results=5))

        if not results:
            return [f"No results found for query: {query}"]

        formatted_results = []
        for i, result in enumerate(results, 1):
            title = result.get("title", "No title")
            url = result.get("href", "No URL")
            snippet = result.get("body", "No snippet")
            formatted_results.append(
                f"Result {i}:\n"
                f"  Title: {title}\n"
                f"  URL: {url}\n"
                f"  Snippet: {snippet}\n"
                f"{'-' * 40}"
            )

        logger.info("web_search: found %d results for '%s'", len(results), query)
        return formatted_results

    except Exception as e:
        logger.error("web_search error: %s", str(e), exc_info=True)
        return [f"Error searching for '{query}': {str(e)}"]


# --- fetch_url (T004 + T006) ---

@app.tool()
async def fetch_url(url: str, format: str = "markdown") -> str:
    """Fetch page content from a URL.

    Args:
        url: URL to fetch (http:// or https://)
        format: Output format - "markdown" (default) or "html"

    Returns:
        Page content in the specified format
    """
    try:
        # Validate URL (T005)
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

            # markdown extraction
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
                    # Fallback to bs4
                    soup = BeautifulSoup(response.text, "html.parser")
                    content = soup.get_text(separator="\n", strip=True)[:10000]
                    if not content.strip():
                        content = f"Could not extract text content from {url}"
            except Exception:
                soup = BeautifulSoup(response.text, "html.parser")
                content = soup.get_text(separator="\n", strip=True)[:10000]
                if not content.strip():
                    content = f"Could not extract text content from {url}"

        logger.info("fetch_url: extracted %d chars of markdown from %s", len(content), url)
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
