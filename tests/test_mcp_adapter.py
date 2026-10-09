"""
Week 6 MCP interoperability compatibility tests -- University Student-
Support Case Agent.

Verifies the four acceptance criteria for the MCP adapter (src/mcp/):

1. Every external tool has a stable name and schema, and the MCP schemas are
   THE SAME objects as the internal Gemini function declarations.
2. Invalid tool calls are rejected before execution (unknown tool, schema
   violations) -- the mock store is never touched.
3. MCP errors map to the Week 4 error model (code/description/http_status).
4. The internal application does not depend on MCP transport: adapter tests
   use the adapter directly; transport behaviour is isolated in the server
   tests; the subprocess test proves a real MCP client over stdio works.

Run with:
    pytest tests/test_mcp_adapter.py -v
"""

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mcp.adapter import McpAdapter  # noqa: E402
from mcp.error_map import (  # noqa: E402
    JSONRPC_INVALID_PARAMS,
    McpToolProtocolError,
)
from mcp.schemas import (  # noqa: E402
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    mcp_tools,
    tool_by_name,
)
from mcp.server import process_message  # noqa: E402
from model_client import TOOLS  # noqa: E402
from tools import mock_store  # noqa: E402

SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")

STUDENT = "2300708510"

VALID_TICKET = {
    "student_id": STUDENT,
    "category": "GENERAL_QUERY",
    "summary": "Student portal login keeps failing",
    "details": "Since Monday the portal rejects my password even after a reset.",
    "urgency": "LOW",
}


@pytest.fixture(autouse=True)
def fresh_store():
    """Isolate every test from the shared in-memory mock store."""
    mock_store.reset_store()
    yield
    mock_store.reset_store()


@pytest.fixture
def adapter():
    return McpAdapter()


def internal_tools_by_name():
    return {declaration["function"]["name"]: declaration["function"] for declaration in TOOLS}


# ----------------------------------------------------------------------
# 1. Stable names + same schema for internal and MCP interfaces
# ----------------------------------------------------------------------


def test_external_catalogue_matches_internal_tool_surface(adapter):
    """The MCP surface is exactly the two Week 4 tools, nothing more."""
    names = [tool["name"] for tool in adapter.list_tools()]
    assert names == ["get_case_status", "create_support_ticket"]


def test_mcp_schema_is_exact_internal_parameters():
    """Published MCP inputSchema must be byte-for-byte the parameters object
    the orchestrator gives Gemini -- no second schema anywhere."""
    internal = internal_tools_by_name()
    for spec in mcp_tools():
        assert spec["inputSchema"] == internal[spec["name"]]["parameters"]


def test_mcp_description_is_internal_description():
    internal = internal_tools_by_name()
    for spec in mcp_tools():
        assert spec["description"] == internal[spec["name"]]["description"]


def test_catalogue_is_deterministic(adapter):
    """The same catalogue every time (clients cache tool lists)."""
    assert adapter.list_tools() == adapter.list_tools()


def test_stable_names_are_well_formed():
    """Machine-readable names, stable and non-empty, matching the Week 4
    Tool Catalogue section-4 names."""
    for spec in mcp_tools():
        assert spec["name"]
        assert spec["name"].isidentifier()


# ----------------------------------------------------------------------
# 2. Adapter: success results still carry exact consequences of Week 4 tools
# ----------------------------------------------------------------------


def test_get_case_status_success_result(adapter):
    result = adapter.call_tool("get_case_status", {"case_id": "CAS-2026-001"})
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    structured = result["structuredContent"]
    assert structured["case_id"] == "CAS-2026-001"
    assert structured["status"] == "IN_PROGRESS"


def test_create_support_ticket_hitl_gate_preserved(adapter):
    """Interop must never bypass the human-in-the-loop gate: EXAMINATION /
    HIGH tickets are still held for staff approval (Week 4 catalogue)."""
    args = dict(VALID_TICKET)
    args.update({"category": "EXAMINATION", "urgency": "HIGH"})
    result = adapter.call_tool("create_support_ticket", args)
    structured = result["structuredContent"]
    assert structured["status"] == "PENDING_STAFF_APPROVAL"
    assert structured["requires_human_approval"] is True
    assert "FACULTY_REGISTRAR_TRIAGE" in structured["routing_queue"]


def test_create_support_ticket_regular_flows_through(adapter):
    result = adapter.call_tool("create_support_ticket", dict(VALID_TICKET))
    structured = result["structuredContent"]
    assert structured["status"] == "OPEN"
    assert structured["requires_human_approval"] is False


# ----------------------------------------------------------------------
# 3. Tool EXECUTION errors mapped to the Week 4 error model (isError: true)
# ----------------------------------------------------------------------


def test_case_not_found_is_tool_error(adapter):
    result = adapter.call_tool("get_case_status", {"case_id": "CAS-2099-999"})
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "CASE_NOT_FOUND"
    assert error["http_status"] == 404


def test_invalid_case_id_format_maps_to_week4(adapter):
    result = adapter.call_tool("get_case_status", {"case_id": "not-a-case-id"})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "INVALID_ID_FORMAT"
    assert result["structuredContent"]["error"]["http_status"] == 400


def test_disallowed_topic_maps_to_week4(adapter):
    args = dict(VALID_TICKET)
    args.update({
        "summary": "Please raise my marks on CS301",
        "details": "I need a better grade to keep my scholarship",
    })
    result = adapter.call_tool("create_support_ticket", args)
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "DISALLOWED_TOPIC"
    assert error["http_status"] == 403


def test_handler_validation_failure_maps_to_week4(adapter):
    """Constraints the JSON schema cannot express (summary length) are
    enforced by the Week 4 handler and surface as a tool-execution error
    carrying VALIDATION_FAILED."""
    args = dict(VALID_TICKET)
    args["summary"] = "x"  # below the handler's 5-char minimum
    result = adapter.call_tool("create_support_ticket", args)
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "VALIDATION_FAILED"
    assert result["structuredContent"]["error"]["http_status"] == 422


def test_duplicate_ticket_maps_to_week4(adapter):
    adapter.call_tool("create_support_ticket", dict(VALID_TICKET))
    result = adapter.call_tool("create_support_ticket", dict(VALID_TICKET))
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "DUPLICATE_TICKET"
    assert result["structuredContent"]["error"]["http_status"] == 409


# ----------------------------------------------------------------------
# 4. Invalid calls REJECTED before execution (-32602 + Week 4 data)
# ----------------------------------------------------------------------


def test_unknown_tool_rejected_before_execution(adapter):
    with pytest.raises(McpToolProtocolError) as excinfo:
        adapter.call_tool("fabricate_case", {"case_id": "CAS-2026-001"})
    assert excinfo.value.code == JSONRPC_INVALID_PARAMS
    assert excinfo.value.data["error"]["code"] == "UNKNOWN_TOOL"
    assert excinfo.value.data["error"]["http_status"] == 404


def test_missing_required_field_rejected_before_execution(adapter):
    with pytest.raises(McpToolProtocolError) as excinfo:
        adapter.call_tool("get_case_status", {})
    assert excinfo.value.code == JSONRPC_INVALID_PARAMS
    assert excinfo.value.data["error"]["code"] == "VALIDATION_FAILED"
    assert "case_id" in excinfo.value.message


def test_wrong_type_rejected_before_execution(adapter):
    with pytest.raises(McpToolProtocolError) as excinfo:
        adapter.call_tool("get_case_status", {"case_id": 123})
    assert excinfo.value.code == JSONRPC_INVALID_PARAMS


def test_enum_violation_rejected_before_execution(adapter):
    args = dict(VALID_TICKET)
    args["category"] = "FEE_RELIEF"
    with pytest.raises(McpToolProtocolError) as excinfo:
        adapter.call_tool("create_support_ticket", args)
    assert excinfo.value.data["error"]["code"] == "VALIDATION_FAILED"
    assert "category" in excinfo.value.message


def test_unregistered_parameter_rejected_before_execution(adapter, fresh_store):
    """additionalProperties:false -- an extra key never reaches the handler,
    so the store is untouched (nothing created)."""
    before = mock_store.get_store()["next_ticket_number"]
    args = dict(VALID_TICKET)
    args["malicious_extra"] = "injected"
    with pytest.raises(McpToolProtocolError):
        adapter.call_tool("create_support_ticket", args)
    assert mock_store.get_store()["next_ticket_number"] == before


def test_non_object_arguments_rejected_before_execution(adapter):
    with pytest.raises(McpToolProtocolError) as excinfo:
        adapter.call_tool("get_case_status", ["CAS-2026-001"])
    assert excinfo.value.code == JSONRPC_INVALID_PARAMS


# ----------------------------------------------------------------------
# 5. JSON-RPC transport behaviour (isolated, no subprocess)
# ----------------------------------------------------------------------


def test_initialize_negotiates_protocol():
    response = json.loads(process_message(
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"x","version":"1"}}}'
    ))
    assert response["id"] == 1
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["capabilities"]["tools"] == {"listChanged": False}


def test_initialize_defaults_to_latest_when_client_version_unknown():
    response = json.loads(process_message(
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2020-01-01"}}'
    ))
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS


def test_tools_list_over_jsonrpc():
    response = json.loads(process_message('{"jsonrpc":"2.0","id":2,"method":"tools/list"}'))
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == ["get_case_status", "create_support_ticket"]


def test_tools_call_success_over_jsonrpc():
    response = json.loads(process_message(
        '{"jsonrpc":"2.0","id":3,"method":"tools/call",'
        '"params":{"name":"get_case_status","arguments":{"case_id":"CAS-2026-002"}}}'
    ))
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["case_id"] == "CAS-2026-002"


def test_tools_call_tool_error_over_jsonrpc():
    response = json.loads(process_message(
        '{"jsonrpc":"2.0","id":4,"method":"tools/call",'
        '"params":{"name":"create_support_ticket","arguments":{"student_id":"2300708510",'
        '"category":"EXAMINATION","summary":"I want my suspension lifted today",'
        '"details":"Please overturn the disciplinary decision",'
        '"urgency":"HIGH"}}}'
    ))
    assert response["id"] == 4
    result = response["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "DISALLOWED_TOPIC"


def test_unknown_method_returns_method_not_found():
    response = json.loads(process_message('{"jsonrpc":"2.0","id":5,"method":"resources/list"}'))
    assert response["error"]["code"] == -32601


def test_unknown_tool_returns_invalid_params_with_week4_data():
    response = json.loads(process_message(
        '{"jsonrpc":"2.0","id":6,"method":"tools/call",'
        '"params":{"name":"nope","arguments":{}}}'
    ))
    assert response["error"]["code"] == JSONRPC_INVALID_PARAMS
    assert response["error"]["data"]["error"]["code"] == "UNKNOWN_TOOL"


def test_parse_error_for_malformed_json():
    response = json.loads(process_message("{ this is not json"))
    assert response["error"]["code"] == -32700


def test_batch_is_invalid_request():
    response = json.loads(process_message('[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]'))
    assert response["error"]["code"] == -32600


def test_notification_gets_no_response():
    assert process_message('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None


# ----------------------------------------------------------------------
# 6. End-to-end: a real MCP client speaking stdio JSON-RPC to the server
# ----------------------------------------------------------------------

_REQUEST_TIMEOUT = 10


def _client_request(proc, payload):
    """Send one JSON-RPC request line, wait for the single response line."""
    proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
    proc.stdin.flush()
    ready, _, _ = select.select([proc.stdout], [], [], _REQUEST_TIMEOUT)
    assert ready, "MCP server did not respond in time"
    line = proc.stdout.readline()
    assert line, "MCP server closed stdout unexpectedly"
    return json.loads(line)


def test_subprocess_mcp_client_roundtrip():
    """Launch src/mcp/server.py as an MCP stdio server and drive it like an
    external MCP client: initialize -> tools/list -> tools/call -> error."""
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp.server"],
        cwd=SRC_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        init = _client_request(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "compat-test-client", "version": "0.0.1"},
            },
        })
        assert init["result"]["protocolVersion"] == PROTOCOL_VERSION

        listed = _client_request(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in listed["result"]["tools"]]
        assert names == ["get_case_status", "create_support_ticket"]

        called = _client_request(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_case_status", "arguments": {"case_id": "CAS-2026-003"}},
        })
        assert called["result"]["isError"] is False
        assert called["result"]["structuredContent"]["status"] == "RESOLVED"

        rejected = _client_request(proc, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "create_support_ticket", "arguments": {"student_id": "123"}},
        })
        assert rejected["error"]["code"] == JSONRPC_INVALID_PARAMS
        assert rejected["error"]["data"]["error"]["code"] == "VALIDATION_FAILED"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()