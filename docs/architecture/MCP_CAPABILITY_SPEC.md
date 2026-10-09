# MCP Capability Specification — Student-Support Case Agent

**Week 6 · Memory, state & interoperability**
**BSE4104 — Emerging Trends in Software Engineering**
**Status: prototype** · Protocol: **MCP 2025-06-18** (current stable revision)

This specification defines the external integration interface for the two
Week 4 tools, the adapter contract, and the stable tool catalogue an MCP
client can rely on.

---

## 1. Purpose and scope

The application exposes four capabilities in total:

| Capability | Where | External? |
|---|---|---|
| Grounded Q&A via RAG | `POST /chat` (`src/main.py`) | HTTP |
| Bounded agent loop | `src/orchestrator.py` | internal only |
| **get_case_status** | `src/tools/handlers.py` | **MCP (this spec)** |
| **create_support_ticket** | `src/tools/handlers.py` | **MCP (this spec)** |

MCP exposes the two Week 4 tools so an external LLM host (Claude Desktop,
Cursor, a custom router, another agent) can invoke the *same* tools the
bounded agent uses — with the *same* schemas and the *same* error model, and
without changing any core behaviour.

**Hard boundary (unchanged):** the adapter can only do what the tools can do.
It can make or influence **no** admissions, grading, disciplinary or fee
decisions; examinations and high-urgency tickets are still held for staff
approval (Human-in-the-Loop gate). Session memory is deliberately **not**
exposed through MCP.

---

## 2. External integration interface

The server speaks **JSON-RPC 2.0 over stdio**, one message per line (UTF-8),
which is the MCP transport an `mcpServers.command` entry point uses:

```jsonc
"mcpServers": {
  "makerere-case-agent": {
    "command": "python",
    "args": ["-m", "mcp.server"],
    "cwd": "<repo>/src"
  }
}
```

### 2.1 Handshake

| Message | Client → Server | Server → Client |
|---|---|---|
| `initialize` | `{protocolVersion, capabilities, clientInfo}` | `{protocolVersion, capabilities.tools.listChanged, serverInfo{name, version}}` |
| `notifications/initialized` | notification (no reply) | — |

Version negotiation: if the client's `protocolVersion` is `2024-11-05`,
`2025-03-26` or `2025-06-18`, the server echoes it; otherwise it answers with
`2025-06-18`. Server identity: `makerere-student-support-case-agent` v`0.1.0`.

### 2.2 Methods

| Method | Params | Result |
|---|---|---|
| `tools/list` | `{cursor?}` | `{tools: [...]}` — deterministic order, see §3 |
| `tools/call` | `{name, arguments?}` | `{content, structuredContent?, isError?}` |

Batching is rejected (`-32600`) per the 2025-06-18 spec.

---

## 3. Adapter contract

Packages under `src/mcp/` are the entire integration layer:

```
src/mcp/
  __init__.py     surface exports
  schemas.py      MCP tool catalogue DERIVED from src/model_client.py TOOLS
  validator.py    minimal JSON-Schema subset validator (pre-execution)
  error_map.py    Week 4 error model <-> MCP/JSON-RPC error codes
  adapter.py      transport-agnostic core: validate -> dispatch -> result
  server.py       JSON-RPC 2.0 stdio transport (the only MCP-aware module)
```

**Decoupling guarantee:** `server.py` is the only module that knows about
JSON-RPC framing and stdio. The application (`model_client`, `orchestrator`,
`handlers`, `main`) never imports `src/mcp/`. The adapter calls the exact
`dispatch()` the orchestrator uses, so an MCP call and an in-loop tool call
share one code path.

### 3.1 Single source of truth for schemas

There is no second schema file. Each MCP tool's `inputSchema` is the deep
copy of the `function.parameters` object from the Gemini function
declarations in `src/model_client.py` (`TOOLS`). If the Week 4 declaration
changes, both the orchestrator's tool-calling and `tools/list` change
together. The compatibility tests assert this equality directly.

A **strict** variant (same object + `additionalProperties: false`) is derived
for pre-execution validation only and is never published.

---

## 4. Stable tool catalogue

Every external tool has a stable name and schema. Names match the Week 4
Tool Catalogue section 4 exactly.

### 4.1 `get_case_status`

| Field | Value |
|---|---|
| name | `get_case_status` |
| title | Case Status Lookup |
| description | *Look up an existing student support case by its Case ID to get its current status, category, and official notes.* |
| inputSchema | `{type: object, properties: {case_id: {type: string}}, required: ["case_id"]}` |
| internal handler | `src/tools/handlers.get_case_status` |
| side effects | none (read-only) |

### 4.2 `create_support_ticket`

| Field | Value |
|---|---|
| name | `create_support_ticket` |
| title | Create Support Ticket |
| description | *Create a new support ticket for a student issue when procedural guidance cannot resolve it. Tickets with category EXAMINATION or urgency HIGH are held for staff approval.* |
| required | `student_id`, `category`, `summary`, `details`, `urgency` |
| `category` enum | `REGISTRATION`, `EXAMINATION`, `TIMETABLE`, `GENERAL_QUERY` |
| `urgency` enum | `LOW`, `MEDIUM`, `HIGH` |
| internal handler | `src/tools/handlers.create_support_ticket` |
| side effects | writes to the mock case/ticket store; HITL gate for EXAMINATION/HIGH |

---

## 5. Error contract

One error vocabulary everywhere: **the Week 4 Tool Catalogue error model**
(`{error: {code, description, http_status}}`). MCP adds no second vocabulary.

### 5.1 Protocol errors — call rejected **before execution**

Standard JSON-RPC codes; the Week 4 payload travels in `data`:

| Condition | JSON-RPC code | `data.error.code` | `http_status` |
|---|---|---|---|
| unknown tool name | `-32602` Invalid params | `UNKNOWN_TOOL` | 404 |
| non-object / schema-invalid arguments (missing required, wrong type, bad enum, unregistered property) | `-32602` Invalid params | `VALIDATION_FAILED` | 422 |
| unknown method | `-32601` Method not found | — | — |
| malformed JSON | `-32700` Parse error | — | — |
| batch array | `-32600` Invalid request | — | — |

Schema-violating calls raise **before** `dispatch()` runs, so they can never
touch the store or a handler.

### 5.2 Tool execution errors — Week 4 payload in the result

Errors that originate from *running* a tool are returned inside the
`tools/call` result, per the MCP 2025-06-18 recommendation:

```json
{
  "content": [{"type": "text", "text": "No case with that ID exists in the registry."}],
  "structuredContent": {
    "error": {"code": "CASE_NOT_FOUND", "description": "...", "http_status": 404}
  },
  "isError": true
}
```

Mapped codes: `INVALID_ID_FORMAT` (400), `CASE_NOT_FOUND` (404),
`CASE_ACCESS_DENIED` (403), `VALIDATION_FAILED` (422), `DISALLOWED_TOPIC`
(403), `DUPLICATE_TICKET` (409), `DATABASE_TIMEOUT` (503).

---

## 6. Behaviour preserved (do not regress)

Interop must not change core behaviour. The tests pin these across the MCP
boundary:

1. `get_case_status` returns the real seeded record, never an invented one.
2. EXAMINATION / HIGH tickets → `PENDING_STAFF_APPROVAL`,
   `FACULTY_REGISTRAR_TRIAGE`, `requires_human_approval: true`.
3. Grade/fee/disciplinary demands → `DISALLOWED_TOPIC` (403).
4. Duplicate unresolved tickets → `DUPLICATE_TICKET` (409).
5. Validation that a schema cannot express (summary length, student-number
   format) stays in the handlers and surfaces as `isError: true`.
6. The internal application has no MCP import anywhere.

---

## 7. Out of scope (prototype limits)

- Transport: **stdio only**. HTTP/Streamable-HTTP is future work.
- **No authentication or per-request scoping**: the sandbox mock store is
  open (the Week 6 session work is a separate track). A deployment to a real
  registry needs the session/student-identity check wired into `get_case_status`.
- `retrieve_evidence` (orchestrator-internal RAG tool) is **not** exposed.
- No `tools/list_changed` notifications (tool set is static — `listChanged: false`).

## 8. Run it

```bash
cd src
python -m mcp.server            # block on stdin/stdout, speak MCP
# compatibility tests (no network)
python -m pytest tests/test_mcp_adapter.py -v
```