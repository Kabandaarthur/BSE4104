"""MCP tool schemas derived from the internal Gemini function declarations.

The two Week 4 tools live in ONE place: the OpenAI/wire-format function
declarations in src/model_client.py (GET_CASE_STATUS_DECLARATION and
CREATE_SUPPORT_TICKET_DECLARATION), which are what Gemini itself is given by
the orchestrator. We do not maintain a second copy for MCP: each MCP tool's
`inputSchema` IS the exact `function.parameters` object from those internal
declarations. That is what "the same tool schemas are used by both internal
and MCP interfaces" means -- if the Week 4 declarations change, tools/list
changes, and there is no third place to forget.

The stable external contract (capability specification) is:
    name         = the Week 4 tool name, unchanged (get_case_status,
                   create_support_ticket)
    inputSchema  = the internal declarations' JSON-Schema parameters
    description  = the internal declaration description

A strict variant is derived per tool for PRE-EXECUTION rejection: it is the
same object with `additionalProperties: false` so a client that sends an
unregistered parameter is rejected before any handler or store code runs
(handlers.py already rejects unregistered parameters, but MCP must reject
before *execution*, and the rejection must carry the Week 4 error model).
The strict variant is used only for validation; the published inputSchema is
byte-for-byte the internal parameters.
"""

import copy

from model_client import TOOLS

# MCP 2025-06-18 is the current stable revision of the protocol (the draft
# revisions are not advertised). The server echoes a client's protocolVersion
# when it is in SUPPORTED_PROTOCOL_VERSIONS, otherwise it answers with ours.
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")

# Stable identity advertised during initialize. Version bumps when the tool
# surface changes, so MCP clients can detect a breaking schema change.
SERVER_NAME = "makerere-student-support-case-agent"
SERVER_VERSION = "0.1.0"

# Human-readable titles for tools/list (optional per the MCP spec, added
# only as a display aid; the name is always the machine-readable identity).
_TITLES = {
    "get_case_status": "Case Status Lookup",
    "create_support_ticket": "Create Support Ticket",
}


def _function(declaration):
    return declaration["function"]


def internal_tools():
    """Deep copies of the internal Gemini function declarations (TOOLS).

    Exposed so the compatibility tests can assert MCP parity directly against
    the same objects the orchestrator hands to the model provider."""
    return copy.deepcopy(TOOLS)


def mcp_tools():
    """The stable external tool catalogue: one spec per internal tool, in the
    same deterministic order as TOOLS (get_case_status first)."""
    specs = []
    for declaration in TOOLS:
        function = _function(declaration)
        specs.append(
            {
                "name": function["name"],
                "title": _TITLES.get(function["name"], function["name"]),
                "description": function["description"],
                # The SAME JSON-Schema object Gemini is given. No re-typing.
                "inputSchema": copy.deepcopy(function["parameters"]),
            }
        )
    return specs


def tool_by_name(name):
    """The MCP spec for one tool, or None if the name is not a stable tool."""
    for spec in mcp_tools():
        if spec["name"] == name:
            return spec
    return None


def strict_input_schema(name):
    """The tool's published inputSchema with `additionalProperties: false`.

    Used only by the adapter to reject unknown parameters BEFORE execution.
    Published schemas (tools/list) keep the exact internal parameters."""
    spec = tool_by_name(name)
    if spec is None:
        return None
    schema = copy.deepcopy(spec["inputSchema"])
    schema["additionalProperties"] = False
    return schema