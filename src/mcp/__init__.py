"""Week 6 MCP interoperability adapter (src/mcp/).

Exposes the two Week 4 tools (get_case_status, create_support_ticket) to
external MCP clients using the SAME schemas as the internal Gemini function
declarations (src/model_client.py -- single source of truth) and the SAME
error model as the Week 4 Tool Catalogue (src/tools/handlers.py).

Layering (internal -> external): the application never imports this package.
The adapter lives between them:
    app (orchestrator/handlers/model_client)  -- zero MCP awareness
    src/mcp/adapter.py                         -- validates + dispatches
    src/mcp/server.py                          -- JSON-RPC / stdio transport
"""

from mcp.adapter import McpAdapter  # noqa: F401
from mcp.error_map import (  # noqa: F401
    McpToolProtocolError,
    JSONRPC_INVALID_PARAMS,
)
from mcp.schemas import (  # noqa: F401
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    mcp_tools,
    tool_by_name,
)
from mcp.validator import validate_schema  # noqa: F401