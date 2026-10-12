# MCP Interoperability Test Report

**Week 6 · Memory, state & interoperability**
**BSE4104 — Emerging Trends in Software Engineering**
**Date:** 9 October 2026 · **Relevant spec:** `docs/architecture/MCP_CAPABILITY_SPEC.md`

## 1. Summary

An MCP adapter (MCP **2025-06-18**, JSON-RPC 2.0 over stdio) now exposes the
two Week 4 tools (`get_case_status`, `create_support_ticket`) to external MCP
clients using **the same schemas** as the internal Gemini function
declarations and **the same error model** as the Week 4 Tool Catalogue. The
internal application was not modified and has no MCP dependency; core
behaviour (Human-in-the-Loop gate, disallowed topics, duplicates, error codes)
is unchanged across the boundary.

```
Result: 30/30 compatibility tests passed (plus 346 pre-existing = 376 total)
Defects found: 0 new; adapter–handler validation boundary documented (§4.3)
```

## 2. What was tested

| Layer | Test | Count | Outcome |
|---|---|---|---|
| **Schema parity** | MCP catalogue = internal `model_client.TOOLS` (names, inputSchema byte-for-byte, descriptions, deterministic order) | 5 | pass |
| **Adapter success** | case lookup returns the real seeded record; HITL gate; regular ticket flow — via the adapter (no transport) | 3 | pass |
| **Week 4 error mapping** | CASE_NOT_FOUND, INVALID_ID_FORMAT, DISALLOWED_TOPIC, handler VALIDATION_FAILED, DUPLICATE_TICKET → `isError: true` + `structuredContent` carrying the Week 4 payload | 5 | pass |
| **Rejection before execution** | unknown tool, missing required, wrong type, bad enum, unregistered property, non-object args → `-32602` with `UNKNOWN_TOOL`/`VALIDATION_FAILED` in `data`; store provably untouched | 6 | pass |
| **JSON-RPC transport** | initialize negotiation, tools/list, tools/call success + tool error, method-not-found, parse error, batch rejected, notifications silent | 10 | pass |
| **End-to-end MCP client** | subprocess stdio server driven like a real client: initialize → tools/list → tools/call → rejected call | 1 | pass |

## 3. Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| Every external tool has a stable name and schema | ✅ | `tools/list` deterministic; `test_external_catalogue_matches_internal_tool_surface`, `test_catalogue_is_deterministic`, `test_stable_names_are_well_formed` |
| Invalid tool calls are rejected before execution | ✅ | `test_unregistered_parameter_rejected_before_execution` asserts store `next_ticket_number` is unchanged; `-32602` rejections pre-dispatch |
| MCP errors map to the existing error model (Week 4 Tool Catalogue) | ✅ | `test_*_maps_to_week4` + `test_unknown_tool_returns_invalid_params_with_week4_data`; both interfaces carry `{code, description, http_status}` |
| Internal application does not depend directly on MCP transport details | ✅ | `src/mcp/` only; grep confirms `model_client.py`, `orchestrator.py`, `tools/`, `main.py` import no `mcp` module; transport isolated to `mcp/server.py` |
| Test an MCP client calling the agent without changing its core behaviour | ✅ | `test_subprocess_mcp_client_roundtrip`; behaviour pins in §6 of the spec (HITL, DISALLOWED_TOPIC, duplicates) all pass over MCP |

## 4. Findings

### 4.1 No changes to core files
The adapter shares the exact `dispatch()` the orchestrator uses, so the two
interfaces cannot drift in behaviour. Zero edits to internal application
source.

### 4.2 Schema identity is enforced, not assumed
`test_mcp_schema_is_exact_internal_parameters` compares `inputSchema` to the
`function.parameters` object from `model_client.TOOLS` on every run — the
"same schemas" guarantee is a test failure, not a convention.

### 4.3 Boundary between "rejected" and "tool error" (documented decision)
Constraints a plain JSON schema cannot express (summary length, student-ID
format, CASE-ID shape) live in the handlers, so they surface as tool-
execution errors (`isError: true`) rather than pre-execution `-32602`. Both
carry the same `VALIDATION_FAILED`/`INVALID_ID_FORMAT` Week 4 payload, so a
client sees one error vocabulary. This matches the MCP guidance that errors
a model can self-correct from belong in the result.

### 4.4 Security posture preserved
- HITL gate: EXAMINATION/HIGH tickets still `PENDING_STAFF_APPROVAL`.
- DISALLOWED_TOPIC rules unchanged (demand-verb + protected-subject detection).
- Session memory is not exposed over MCP.
- No new dependencies were added (`requirements.txt` untouched).

## 5. Limitations / follow-ups

- stdio transport only; HTTP streaming is future work.
- No auth at the MCP boundary — fine for the in-memory mock store, but a real
  registry needs the Week 6 session/student-identity check in `get_case_status`.
- `retrieve_evidence` (orchestrator-internal) intentionally not exposed.
- If the `mcp` Python SDK is adopted later, only `mcp/server.py` would change.

## 6. Reproduce

```bash
# full compatibility suite (no network)
python -m pytest tests/test_mcp_adapter.py -v

# full project suite
python -m pytest -q

# speak MCP over stdio
cd src && python -m mcp.server
```

## 7. Trace / evidence reference

- Compatibility tests: `tests/test_mcp_adapter.py` (30 cases)
- Capability spec: `docs/architecture/MCP_CAPABILITY_SPEC.md`
  (tool catalogue, error contract, adapter contract)