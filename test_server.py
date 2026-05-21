import asyncio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main():
    server_params = StdioServerParameters(
        command="python3",
        args=["server.py"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print("Available tools:")
            for tool in tools.tools:
                print(f"  - {tool.name}")

            # Test web_search
            print("\n--- Testing web_search ---")
            result = await session.call_tool("web_search", {"query": "MCP server Python"})
            print(f"Status: {result.isError}")
            for content in result.content:
                text = content.text if hasattr(content, 'text') else str(content)
                print(f"Result: {text[:1000]}")

            # Test fetch_url
            print("\n--- Testing fetch_url ---")
            if not result.isError and result.content:
                text = result.content[0].text if hasattr(result.content[0], 'text') else str(result.content[0])
                # Extract first URL if possible
                import re
                urls = re.findall(r'https?://[^\s,)]+', text)
                if urls:
                    url = urls[0][:200]  # truncate
                    print(f"Fetching: {url}")
                    fetch_result = await session.call_tool("fetch_url", {"url": url, "format": "markdown"})
                    print(f"Fetch status: {fetch_result.isError}")
                    for content in fetch_result.content:
                        ftext = content.text if hasattr(content, 'text') else str(content)
                        print(f"Fetched: {ftext[:500]}")

            # Test reset_limits
            print("\n--- Testing reset_limits ---")
            reset = await session.call_tool("reset_limits", {})
            print(f"Reset status: {reset.isError}")
            for content in reset.content:
                print(f"Reset: {content.text if hasattr(content, 'text') else content}")


asyncio.run(main())
