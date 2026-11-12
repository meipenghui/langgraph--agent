import asyncio
import sys
from pathlib import Path

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.mcp_client.client import list_mcp_tools


async def main() -> None:
    tools = await list_mcp_tools()
    print(f'Connected to MCP server through stdio. Tools: {len(tools)}')
    for tool in tools:
        print(f'- {tool.name}: {tool.description}')
        print(f'  args: {tool.args}')


if __name__ == '__main__':
    asyncio.run(main())
