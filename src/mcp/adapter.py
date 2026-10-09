"""The MCP adapter: the external integration interface for the Week 4 tools.

This is the only place (in src/mcp/) that touches the internal application's
tool dispatcher. It is deliberately transport-agnostic -- no JSON-RPC, no
stdio. It produces MCP-typed `tools/call` results and raises structured
`McpToolProtocolError`s; only mcp/server.py knows about framing/transport,
and the application (model_client, orchestrator, handlers, main) never
imports this package at all. That is the decoupling the acceptance criteria
require: "internal application does not depend directly on MCP transport
details".

Call contract (per MCP 2025-06-18):
  * list_tools() -> the stable tool catalogue (same schemas as internal).
  * call_tool(name, arguments) ->
        on success : {"content": [...], "structuredContent": <output>,
                      "isError": false}
        on tool EXECUTION error (Week 4 ToolError): {"content": [...],
                      "structuredContent": <Week 4 error payload>,
                      "isError": true}
        on INVALID call (unknown tool / schema violation): raises
                      McpToolProtocolError(JSONRPC_INVALID_PARAMS, ...) so it
                      is rejected before execution, never dispatched.

Rejection happens first, in validate-and-check order, so an invalid call can
never mutate the mock store or reach a handler.
"""

import json

from mcp.error_map import (
    JSONRPC_INVALID_PARAMS,
    McpToolProtocolError,
    unknown_tool_payload,
    validation_failed_payload,
)
from mcp.schemas import mcp_tools, strict_input_schema, tool_by_name
from mcp.validator import validate_schema
from tools.handlers import dispatch


def _error_result(payload):
    """MCP CallToolResult for a tool that ran but reported a Week 4 error.
    The error model is visible to the calling model (isError + structured
    content), which is what lets an MCP model self-correct."""
    error = payload["error"]
    return {
        "content": [{"type": "text", "text": error["description"]}],
        "structuredContent": payload,
        "isError": True,
    }


class McpAdapter:
    def __init__(self, dispatcher=dispatch, tool_specs=None):
        """dispatcher: callable(name, arguments) -> dict, defaulting to the
        exact dispatch() the orchestrator uses. Inject for tests, never for
        production. tool_specs: override the catalogue (default: derived
        from the internal declarations)."""
        self._dispatcher = dispatcher or dispatch
        self._specs = {
            spec["name"]: spec for spec in (tool_specs if tool_specs is not None else mcp_tools())
        }
        self._strict = {
            name: strict_input_schema(name) for name in self._specs
        }

    def list_tools(self):
        """The stable catalogue, in deterministic TOOLS order."""
        names = [spec["name"] for spec in mcp_tools() if spec["name"] in self._specs]
        if not names:
            return []
        return [self._specs[name] for name in names]

    def call_tool(self, name, arguments):
        """Validate-then-execute one MCP tool call against the SAME dispatch
        the bounded agent loop uses, so core behaviour is unchanged."""
        if name not in self._specs:
            raise McpToolProtocolError(
                JSONRPC_INVALID_PARAMS,
                f"Unknown tool '{name}'.",
                data=unknown_tool_payload(name, self._specs),
            )
        if not isinstance(arguments, dict):
            raise McpToolProtocolError(
                JSONRPC_INVALID_PARAMS,
                "Tool arguments must be a JSON object.",
                data=validation_failed_payload("Tool arguments must be a JSON object."),
            )

        problems = validate_schema(self._strict[name], arguments)
        if problems:
            message = "Invalid arguments for '{}': {}".format(name, "; ".join(problems))
            raise McpToolProtocolError(
                JSONRPC_INVALID_PARAMS,
                message,
                data=validation_failed_payload(message),
            )

        # Executed through the internal dispatcher: same success payloads,
        # same Week 4 error payloads, same HITL gate, same mock store.
        result = self._dispatcher(name, arguments)
        if "error" in result:
            return _error_result(result)
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "structuredContent": result,
            "isError": False,
        }


def create_adapter():
    """Convenience factory; the catalogue and dispatcher are the defaults."""
    return McpAdapter()