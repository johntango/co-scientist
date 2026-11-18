"""Trusted MCP connections and bounded, schema-checked tool discovery/execution."""

import asyncio
import json
import re
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


class ToolRegistry:
    def __init__(self, configuration, timeout=30):
        self.configuration = configuration
        self.timeout = timeout
        self.stack = AsyncExitStack()
        self.tools = {}

    async def __aenter__(self):
        try:
            for name, spec in self.configuration.items():
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,24}", name):
                    raise ValueError("MCP server names must be 1–24 alphanumeric, dash or underscore characters")
                if spec["transport"] == "stdio":
                    transport = stdio_client(StdioServerParameters(command=spec["command"], args=spec.get("args", [])))
                elif spec["transport"] == "streamable-http":
                    transport = streamablehttp_client(spec["url"])
                else:
                    raise ValueError("Unsupported MCP transport")
                streams = await self.stack.enter_async_context(transport)
                session = await self.stack.enter_async_context(
                    ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=self.timeout))
                )
                await session.initialize()
                cursor = None
                seen = set()
                while True:
                    page = await session.list_tools(cursor=cursor)
                    for tool in page.tools:
                        if tool.name not in spec.get("allow_tools", []):
                            continue
                        qualified = f"{name}__{tool.name}"
                        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", qualified) or qualified in self.tools:
                            raise ValueError("Duplicate or invalid qualified MCP tool name")
                        Draft202012Validator.check_schema(tool.inputSchema)
                        self.tools[qualified] = (session, tool)
                    cursor = page.nextCursor
                    if not cursor:
                        break
                    if cursor in seen or len(seen) >= 100:
                        raise ValueError("Invalid MCP discovery pagination")
                    seen.add(cursor)
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *exc):
        return await self.stack.__aexit__(*exc)

    def definitions(self):
        return [
            {
                "type": "function",
                "function": {"name": name, "description": tool.description or "", "parameters": tool.inputSchema},
            }
            for name, (_, tool) in self.tools.items()
        ]

    async def call(self, name, arguments):
        # Emit execution activity only after local allowlist/schema validation.
        if name not in self.tools:
            raise ValueError("Tool is not in the configured allowlist")
        Draft202012Validator(self.tools[name][1].inputSchema).validate(arguments)
        observer = getattr(self, "observer", None)
        if observer:
            observer("started", name)
        try:
            result = await self._call(name, arguments)
        except BaseException:
            if observer:
                observer("failed", name)
            raise
        if observer:
            observer("completed", name)
        return result

    async def _call(self, name, arguments):
        if name not in self.tools:
            raise ValueError("Tool is not in the configured allowlist")
        session, tool = self.tools[name]
        Draft202012Validator(tool.inputSchema).validate(arguments)
        result = await session.call_tool(tool.name, arguments)
        if result.isError:
            raise ValueError("MCP tool failed: " + " ".join(getattr(c, "text", "") for c in result.content))
        data = result.structuredContent
        if data is None:
            text = "\n".join(getattr(c, "text", "") for c in result.content)
            try:
                data = json.loads(text)
            except ValueError:
                data = {"text": text}
        if tool.outputSchema is not None:
            Draft202012Validator(tool.outputSchema).validate(data)
        if len(json.dumps(data)) > 180_000:
            raise ValueError("Tool response exceeds 180 KB; request a smaller page or use artifacts")
        return data


def load_servers(path):
    """Configuration is operator-owned; model output never chooses commands or servers."""
    return json.loads(Path(path).read_text())["servers"]


async def discover(path):
    async with ToolRegistry(load_servers(path)) as registry:
        return registry.definitions()


if __name__ == "__main__":
    import sys

    print(json.dumps(asyncio.run(discover(sys.argv[1])), indent=2))
