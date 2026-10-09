"""Mapping between the Week 4 error model and MCP/JSON-RPC error codes.

The Week 4 Tool Catalogue defines the application's one error vocabulary:
`ToolError` (and `dispatch`'s UNKNOWN_TOOL/VALIDATION_FAILED fallbacks) always
produce `{"error": {code, description, http_status}}`. MCP must not invent a
second vocabulary, so:

  * JSON-RPC protocol errors (standard codes below) -- used for "finding the
    tool" and argument-schem validation failures rejected BEFORE execution --
    carry the same `{"error": {code, description, http_status}}` payload in
    their `data` field;
  * tool EXECUTION errors (CASE_NOT_FOUND, DISALLOWED_TOPIC, ...) are returned
    inside the `tools/call` result with `isError: true` and `structuredContent`
    set to the Week 4 payload, exactly as the MCP 2025-06-18 spec recommends.

Standard JSON-RPC 2.0 error codes (RFC 9745 family). -32000..-32099 are
reserved for implementation-defined server errors.
"""

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_SERVER_ERROR = -32000


class McpToolProtocolError(Exception):
    """Raised by the adapter for a call that must be REJECTED before any
    handler/store code runs (unknown tool, or arguments failing the derived
    JSON schema). server.py turns it into a JSON-RPC error response whose
    `data` carries the Week 4 error-model payload."""

    def __init__(self, code, message, data):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def week4_payload(code, description, http_status):
    """Build the Week 4 error-object shape used by tools/handlers.py."""
    return {"error": {"code": code, "description": description, "http_status": http_status}}


def unknown_tool_payload(name, available):
    """Mirror of the UNKNOWN_TOOL payload `dispatch` builds in handlers.py,
    kept so a rejected unknown tool reads identically on both interfaces."""
    return week4_payload(
        "UNKNOWN_TOOL",
        f"No tool named '{name}'. Available: {sorted(available)}.",
        404,
    )


def validation_failed_payload(description):
    """Mirror of the VALIDATION_FAILED payload `dispatch` builds."""
    return week4_payload("VALIDATION_FAILED", description, 422)