import asyncio, nest_asyncio
import json
from typing import Sequence
from langchain_core.messages import BaseMessage
from langchain_core.tools.structured import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient


def _get_tools_sync(client: MultiServerMCPClient):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        nest_asyncio.apply(loop)
        return loop.run_until_complete(client.get_tools())
    else:
        return asyncio.run(client.get_tools())

def _render_tool_schema(tool: StructuredTool) -> str:
    schema = getattr(tool, "args_schema", None)
    try:
        if schema is None:
            return json.dumps({"properties": {}, "required": []}, indent=2)
        if hasattr(schema, "model_json_schema"):
            return json.dumps(schema.model_json_schema(), indent=2)
        if isinstance(schema, (dict, list)):
            return json.dumps(schema, indent=2)
        return json.dumps({"schema": str(schema)}, indent=2)
    except Exception:
        return json.dumps({"schema": str(schema)}, indent=2)


def tools_to_text(tools: list[StructuredTool]) -> str:
    formatted_tools = []
    for t in tools:
        formatted_tools.append(
            f"-> name: {t.name}\n"
            f"   -> description: {t.description}\n"
            f"   -> args_schema:\n{_render_tool_schema(t)}"
        )
    return "\n\n".join(formatted_tools)


def print_messages(messages: Sequence[BaseMessage]):
    for m in messages:
        m.pretty_print()

