import asyncio
import signal
import logging
import sys
from typing import List

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


@app.tool()
async def fetch_url(url: str, format: str = "markdown") -> str:
    """Fetch page content from a URL.

    Args:
        url: URL to fetch (http:// or https://)
        format: Output format - "markdown" (default) or "html"

    Returns:
        Page content in the specified format
    """
    # TODO: T004, T005, T006 -- реализация fetch_url, валидация URL, обработка ошибок
    pass


if __name__ == "__main__":
    asyncio.run(app.run_stdio_async())
