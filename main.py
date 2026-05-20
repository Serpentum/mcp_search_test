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
    pass


@app.tool()
async def fetch_url(url: str, format: str = "markdown") -> str:
    """Fetch page content from a URL.

    Args:
        url: URL to fetch (http:// or https://)
        format: Output format - "markdown" (default) or "html"

    Returns:
        Page content in the specified format
    """
    pass


if __name__ == "__main__":
    asyncio.run(app.run_stdio_async())
