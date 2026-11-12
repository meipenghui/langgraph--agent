import sys
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.client.session import ClientSession

MCP_SERVER_NAME = 'ecommerce-order'
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_PATH = PROJECT_ROOT / 'mcp_server' / 'server.py'


def create_mcp_client() -> MultiServerMCPClient:
    return MultiServerMCPClient(
        {
            MCP_SERVER_NAME: {
                'transport': 'stdio',
                'command': sys.executable,
                'args': [str(MCP_SERVER_PATH)],
                'cwd': str(PROJECT_ROOT),
            }
        }
    )


async def load_tools(session: ClientSession) -> list[BaseTool]:
    return await load_mcp_tools(session)


async def list_mcp_tools() -> list[BaseTool]:
    client = create_mcp_client()
    async with client.session(MCP_SERVER_NAME) as session:
        return await load_tools(session)


async def invoke_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    client = create_mcp_client()
    async with client.session(MCP_SERVER_NAME) as session:
        tools = await load_tools(session)
        tool = next((item for item in tools if item.name == tool_name), None)
        if tool is None:
            raise ValueError(f'MCP tool not found: {tool_name}')
        return await tool.ainvoke(arguments)
