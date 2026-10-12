"""MCP stdio transport: JSON-RPC 2.0 messages, newline-delimited, on a pipe.

This module is the ONLY place that knows about MCP framing and the stdio
transport. It implements the three surface methods MCP tool servers need
(2025-06-18): `initialize`, `tools/list`, `tools/call`, plus the
`notifications/initialized` handshake. Everything it returns is produced by
mcp/adapter.py, so the transport is a thin wire shim.

The application never imports this module. Run standalone (speak MCP over
stdio to any MCP client/LLM host that supports `command` servers):

    cd src
    python -m mcp.server

Framing: one JSON-RPC message per line (UTF-8). Batching is not supported; a
batch array is rejected as an invalid request, matching the 2025-06-18 spec
which removed JSON-RPC batching.
"""

import json
import sys

from mcp import adapter as mcp_adapter
from mcp.error_map import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    McpToolProtocolError,
)
from mcp.schemas import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)

# One adapter instance for the whole server process: the catalogue and the
# dispatcher are stateless, and the underlying mock store keeps the same
# seeded behaviour the agent sees in-process.
_adapter = mcp_adapter.create_adapter()


def _success(id, result):
    return json.dumps({"jsonrpc": "2.0", "id": id, "result": result}, ensure_ascii=False)


def _error(id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return json.dumps({"jsonrpc": "2.0", "id": id, "error": error}, ensure_ascii=False)


def _initialize_result(params):
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    chosen = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
    return {
        "protocolVersion": chosen,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def _handle_tools_call(params):
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise McpToolProtocolError(
            JSONRPC_INVALID_PARAMS,
            "tools/call requires a string 'name'.",
            data={"error": {"code": "VALIDATION_FAILED", "description": "Missing tool name.", "http_status": 422}},
        )
    return _adapter.call_tool(params["name"], params.get("arguments", {}))


def process_message(raw):
    """Handle one raw JSON-RPC line; return the response string, or None for
    notifications (which never get a response). Never raises."""
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        return _error(None, JSONRPC_PARSE_ERROR, "Parse error")

    if isinstance(message, list):
        return _error(None, JSONRPC_INVALID_REQUEST, "Batching is not supported")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, JSONRPC_INVALID_REQUEST, "Invalid Request")

    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message

    try:
        if method == "initialize" and not is_notification:
            result = _initialize_result(message.get("params") or {})
        elif method == "tools/list" and not is_notification:
            result = {"tools": _adapter.list_tools()}
        elif method == "tools/call" and not is_notification:
            result = _handle_tools_call(message.get("params") or {})
        elif is_notification:
            # notifications/initialized and any other notification is
            # fire-and-forget; never reply.
            return None
        else:
            return _error(request_id, JSONRPC_METHOD_NOT_FOUND, "Method not found")
    except McpToolProtocolError as error:
        return _error(request_id, error.code, error.message, error.data)
    except Exception as error:  # pragma: no cover - defensive health guard
        return _error(request_id, JSONRPC_INTERNAL_ERROR, f"Internal error: {error}")

    return _success(request_id, result)


def serve_stdio(read_stream, write_stream):
    """Read newline-delimited JSON-RPC from read_stream and write responses
    to write_stream until EOF. Streams are binary file objects."""
    for raw in iter(read_stream.readline, b""):
        if raw in (b"", b"\n", b"\r\n"):
            continue
        response = process_message(raw.decode("utf-8", errors="replace").strip())
        if response is None:
            continue
        write_stream.write(response.encode("utf-8") + b"\n")
        write_stream.flush()


def main():
    serve_stdio(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()