"""Week 7 observability: shared trace / stage-timing / redaction helpers.

One small module so the pipeline (main -> orchestrator -> model_client /
retriever -> tools) records timing, outcomes and correlation IDs the same
way, and so the redaction rules live in exactly one place.

How it works
------------
* A *trace* is one request (one /chat call, one CLI turn, one direct
  run_turn call). It has a correlation ID, a list of timed *stages* (spans)
  and a list of point-in-time *events*.
* The active trace lives in a ``contextvar``. Instrumented code never has
  to receive or pass a trace object, so no function signature in the
  pipeline changed, and when **no trace is active every helper here is a
  cheap no-op** (``stage()`` yields a null span). Code that is not being
  traced -- and the tests that fake the pipeline's functions -- behaves
  exactly as it did before.
* ``start_trace()`` is re-entrant: if a trace is already active (for
  example /chat started one and ``ask()`` starts another) the inner call
  joins the outer one instead of creating a second trace.

Privacy rules (never persist secrets or real student data)
----------------------------------------------------------
* Runtime spans are written by instrumented code and carry only lengths,
  counts, enum-like values, error codes, corpus doc IDs and latencies. They
  never carry student message text, free-text ticket fields or replies.
* Everything that leaves a trace (log line, JSONL sink, ``to_dict``) is run
  through ``redact()`` anyway, as a second line of defence: API keys and
  tokens, the *values* of secret-looking environment variables, emails,
  ``+`` phone numbers and 10-digit student numbers are masked. Student
  numbers become ``[STUDENT_ID:ab12cd]``, a keyed (HMAC) pseudonym that is
  stable inside one trace but cannot be brute-forced back to the number
  without the salt. Set ``TRACE_REDACTION_SALT`` for stable pseudonyms
  across runs (the Week 7 capture script does, so its output is
  reproducible); leave it unset in real use for a random per-process salt.
* Case / ticket IDs (CAS-..., TCK-...) are sandbox identifiers and are kept.

Reading a trace
---------------
Stage durations are wall-clock and **nested stages overlap**: an ``act``
span for retrieve_evidence contains the ``retrieval`` span, which contains
``retrieval.vector_query``. Use each span's ``parent`` to read it as a tree;
do not add all durations together. ``total_ms`` on the trace is the
end-to-end figure.

Outcome vocabulary
------------------
``ok``       stage finished normally
``error``    stage raised, or a tool returned an error payload
``refused``  the loop's contract refused the call (undeclared tool,
             repeated identical call, round cap) -- never executed
``retry``    (model_attempt events only) transient failure, will retry
A trace's own ``outcome`` is the agent loop's ``stop_reason`` when one is
set (goal_satisfied, approval_pending, round_limit_reached, tool_error),
``error`` if an exception escaped, otherwise ``ok``.

Configuration (all optional)
----------------------------
TRACE_LOG_PATH         append each finished (redacted) trace as one JSON
                       line to this file. Unset = no file is written.
TRACE_REDACTION_SALT   see above.
"""

import contextlib
import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

logger = logging.getLogger("observability")

CORRELATION_HEADER = "X-Correlation-ID"
SCHEMA_VERSION = "observability.trace.v1"

# Hard ceiling on spans+events kept per trace, so a runaway loop can never
# grow a trace without bound. Anything past it is counted, not stored.
MAX_RECORDS = 500

# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------

# Monotonic, so durations are immune to wall-clock adjustments. Looked up at
# call time through now()/elapsed_ms() so a test can replace it with a fake.
_clock: Callable[[], float] = time.perf_counter


def now() -> float:
    return _clock()


def elapsed_ms(start: float) -> float:
    return round((_clock() - start) * 1000.0, 3)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------
# Correlation IDs
# --------------------------------------------------------------------------

# An inbound ID is echoed into logs and a response header, so it is only
# accepted if it is short and boring. Anything else is replaced, never
# repaired -- this prevents log injection via the header.
_CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def new_correlation_id() -> str:
    return "cid-" + uuid.uuid4().hex[:16]


def sanitize_correlation_id(raw: Any) -> Optional[str]:
    """Return ``raw`` if it is a safe correlation ID, else None."""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    return candidate if _CORRELATION_ID_RE.match(candidate) else None


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

_SECRET_MASK = "[REDACTED_SECRET]"

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_GOOGLE_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{12,}=*")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|authorization|"
    r"access[_-]?token|auth[_-]?token)\b(\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\w])\+\d{1,3}[\s\-]?\d{2,3}[\s\-]?\d{3}[\s\-]?\d{3,4}(?!\d)")
_STUDENT_ID_RE = re.compile(r"(?<!\d)\d{10}(?!\d)")

# Dict keys whose VALUE is never kept, whatever it looks like.
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|authorization|cookie|"
    r"session[_-]?id|access[_-]?token|auth[_-]?token|bearer)"
)
# Keys that hold a student number: pseudonymised, not dropped, so a reader
# can still see "the same student" across spans of one trace.
_STUDENT_KEY_RE = re.compile(r"(?i)^(student[_-]?(id|number|no)|reg(istration)?[_-]?(no|number))$")

_SECRET_ENV_NAME_RE = re.compile(r"(?i)(KEY|SECRET|TOKEN|PASSWORD|PASSWD|SALT)$")

_generated_salt: Optional[str] = None
_salt_lock = threading.Lock()


def _salt() -> bytes:
    configured = os.getenv("TRACE_REDACTION_SALT", "").strip()
    if configured:
        return configured.encode("utf-8")
    global _generated_salt
    if _generated_salt is None:
        with _salt_lock:
            if _generated_salt is None:
                _generated_salt = secrets.token_hex(16)
    return _generated_salt.encode("utf-8")


def pseudonymize(value: Any, label: str = "STUDENT_ID") -> str:
    digest = hmac.new(_salt(), str(value).strip().encode("utf-8"), hashlib.sha256)
    return f"[{label}:{digest.hexdigest()[:6]}]"


def _env_secret_values() -> List[str]:
    """Values of secret-looking environment variables currently set.

    Masking the *actual* values means a key that leaks into an error
    message is caught even if it has no recognisable prefix.
    """
    values = []
    for name, value in os.environ.items():
        if _SECRET_ENV_NAME_RE.search(name) and value and len(value.strip()) >= 8:
            values.append(value.strip())
    return sorted(set(values), key=len, reverse=True)


def _mask_assignment(match: "re.Match[str]") -> str:
    return f"{match.group(1)}{match.group(2)}{_SECRET_MASK}"


def redact_text(text: Any, max_len: Optional[int] = None) -> str:
    """Mask secrets and student/contact data in a string.

    ``max_len`` truncates the (already redacted) result and says how much
    was cut, so long retrieved passages do not bloat a trace.
    """
    if not isinstance(text, str):
        text = str(text)
    for secret in _env_secret_values():
        text = text.replace(secret, _SECRET_MASK)
    text = _PRIVATE_KEY_RE.sub(_SECRET_MASK, text)
    text = _JWT_RE.sub(_SECRET_MASK, text)
    text = _GOOGLE_KEY_RE.sub(_SECRET_MASK, text)
    text = _OPENAI_KEY_RE.sub(_SECRET_MASK, text)
    text = _BEARER_RE.sub("Bearer " + _SECRET_MASK, text)
    text = _ASSIGNMENT_RE.sub(_mask_assignment, text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _STUDENT_ID_RE.sub(lambda m: pseudonymize(m.group(0)), text)
    if max_len is not None and len(text) > max_len:
        text = text[:max_len] + f"...[truncated {len(text) - max_len} chars]"
    return text


def _redact_member(key: str, value: Any, max_str: Optional[int]) -> Any:
    if _SENSITIVE_KEY_RE.search(key):
        return value if value in (None, "") else "[REDACTED]"
    if _STUDENT_KEY_RE.match(key) and isinstance(value, (str, int)) and not isinstance(value, bool):
        return pseudonymize(value)
    return redact(value, max_str)


def redact(value: Any, max_str: Optional[int] = None) -> Any:
    """Deep-copy a JSON-like structure with every string redacted."""
    if isinstance(value, dict):
        return {str(k): _redact_member(str(k), v, max_str) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(v, max_str) for v in value]
    if value is None or isinstance(value, (bool, float)):
        return value
    if isinstance(value, int):
        # A bare 10-digit integer is a student/phone number, not a counter.
        return pseudonymize(value) if 10**9 <= abs(value) < 10**10 else value
    if isinstance(value, str):
        return redact_text(value, max_str)
    return redact_text(repr(value), max_str)


def find_sensitive(text: str) -> List[str]:
    """Names of the sensitive-data categories still present in ``text``.

    Used as a post-check on serialised traces: it should come back empty
    for anything that went through ``redact``. Already-masked values do
    not count.
    """
    found = []
    checks = (
        ("private_key", _PRIVATE_KEY_RE),
        ("jwt", _JWT_RE),
        ("google_api_key", _GOOGLE_KEY_RE),
        ("openai_api_key", _OPENAI_KEY_RE),
        ("bearer_token", _BEARER_RE),
        ("email", _EMAIL_RE),
        ("phone", _PHONE_RE),
        ("student_number", _STUDENT_ID_RE),
    )
    for name, pattern in checks:
        for match in pattern.finditer(text):
            if "REDACTED" in match.group(0):
                continue
            found.append(name)
            break
    for match in _ASSIGNMENT_RE.finditer(text):
        if "REDACTED" not in match.group(3):
            found.append("credential_assignment")
            break
    if any(secret in text for secret in _env_secret_values()):
        found.append("environment_secret")
    return found


# --------------------------------------------------------------------------
# Spans and traces
# --------------------------------------------------------------------------


class _NullSpan:
    """Returned by stage() when no trace is active: accepts and ignores."""

    id = None

    def set(self, **attrs: Any) -> "_NullSpan":
        return self

    def set_outcome(self, outcome: str) -> "_NullSpan":
        return self


NULL_SPAN = _NullSpan()


class Span:
    """One timed stage. Created by stage(); read it via Trace.to_dict()."""

    def __init__(self, span_id: str, name: str, parent_id: Optional[str],
                 attrs: Dict[str, Any], start: float, start_ms: float):
        self.id = span_id
        self.name = name
        self.parent_id = parent_id
        self.attrs = dict(attrs)
        self.outcome: Optional[str] = None
        self.error: Optional[Dict[str, str]] = None
        self.duration_ms: Optional[float] = None
        self._start = start
        self.start_ms = start_ms

    def set(self, **attrs: Any) -> "Span":
        self.attrs.update(attrs)
        return self

    def set_outcome(self, outcome: str) -> "Span":
        self.outcome = outcome
        return self

    def _fail(self, exc: BaseException) -> None:
        self.outcome = "error"
        self.error = {
            "type": type(exc).__name__,
            "message": redact_text(str(exc), 300),
        }

    def _close(self) -> None:
        self.duration_ms = elapsed_ms(self._start)
        if self.outcome is None:
            self.outcome = "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "parent": self.parent_id,
            "name": self.name,
            "start_ms": self.start_ms,
            "duration_ms": self.duration_ms,
            "outcome": self.outcome,
            "attrs": redact(self.attrs),
            "error": self.error,
        }


class Trace:
    """Everything recorded for one request. Not thread-safe by design: a
    trace belongs to one request, which runs in one thread."""

    def __init__(self, correlation_id: str, name: str, attrs: Dict[str, Any]):
        self.correlation_id = correlation_id
        self.name = name
        self.attrs: Dict[str, Any] = dict(attrs)
        self.outcome = "ok"
        self.error: Optional[Dict[str, str]] = None
        self.started_at = _utc_iso()
        self.finished_at: Optional[str] = None
        self.total_ms: Optional[float] = None
        self.stages: List[Span] = []
        self.events: List[Dict[str, Any]] = []
        self.dropped_records = 0
        self._t0 = now()
        self._stack: List[Span] = []
        self._next_id = 1

    # -- recording ---------------------------------------------------------

    def set(self, **attrs: Any) -> None:
        self.attrs.update(attrs)

    def set_outcome(self, outcome: str) -> None:
        self.outcome = outcome

    def _has_room(self) -> bool:
        if len(self.stages) + len(self.events) >= MAX_RECORDS:
            self.dropped_records += 1
            return False
        return True

    def _open(self, name: str, attrs: Dict[str, Any]) -> Span:
        parent = self._stack[-1].id if self._stack else None
        span = Span(f"s{self._next_id}", name, parent, attrs, now(),
                    elapsed_ms(self._t0))
        self._next_id += 1
        self._stack.append(span)
        if self._has_room():
            self.stages.append(span)
        return span

    def _close_span(self, span: Span) -> None:
        span._close()
        if self._stack and self._stack[-1] is span:
            self._stack.pop()
        elif span in self._stack:
            self._stack.remove(span)

    def event(self, name: str, **attrs: Any) -> None:
        if not self._has_room():
            return
        self.events.append({
            "name": name,
            "t_ms": elapsed_ms(self._t0),
            "stage": self._stack[-1].id if self._stack else None,
            "attrs": attrs,
        })

    def annotate(self, **attrs: Any) -> None:
        """Attach attributes to the innermost open stage (else the trace)."""
        if self._stack:
            self._stack[-1].set(**attrs)
        else:
            self.set(**attrs)

    def fail(self, exc: BaseException) -> None:
        self.outcome = "error"
        self.error = {"type": type(exc).__name__, "message": redact_text(str(exc), 300)}

    def finish(self) -> None:
        self.total_ms = elapsed_ms(self._t0)
        self.finished_at = _utc_iso()

    # -- reading -------------------------------------------------------------

    def stage_summary(self) -> Dict[str, Dict[str, Any]]:
        """Per stage name: count, total/max duration, error and refusal
        counts. Nested stages overlap -- see the module docstring."""
        summary: Dict[str, Dict[str, Any]] = {}
        for span in self.stages:
            if span.duration_ms is None:
                continue
            entry = summary.setdefault(
                span.name,
                {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "errors": 0, "refused": 0},
            )
            entry["count"] += 1
            entry["total_ms"] = round(entry["total_ms"] + span.duration_ms, 3)
            entry["max_ms"] = max(entry["max_ms"], span.duration_ms)
            if span.outcome == "error":
                entry["errors"] += 1
            elif span.outcome == "refused":
                entry["refused"] += 1
        return summary

    def snapshot(self) -> Dict[str, Any]:
        """Timing so far -- what TurnResult.timings carries."""
        total = self.total_ms if self.total_ms is not None else elapsed_ms(self._t0)
        return {"total_ms": total, "stages": self.stage_summary()}

    def summary(self) -> Dict[str, Any]:
        """Compact, redacted form for the one-line log record."""
        return redact({
            "correlation_id": self.correlation_id,
            "name": self.name,
            "outcome": self.outcome,
            "total_ms": self.total_ms,
            "started_at": self.started_at,
            "attrs": self.attrs,
            "error": self.error,
            "stages": self.stage_summary(),
        })

    def to_dict(self, redacted: bool = True) -> Dict[str, Any]:
        data = {
            "schema_version": SCHEMA_VERSION,
            "correlation_id": self.correlation_id,
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_ms": self.total_ms if self.total_ms is not None else elapsed_ms(self._t0),
            "outcome": self.outcome,
            "error": self.error,
            "attrs": self.attrs,
            "stage_summary": self.stage_summary(),
            "stages": [span.to_dict() for span in self.stages],
            "events": self.events,
            "dropped_records": self.dropped_records,
        }
        return redact(data) if redacted else data


# --------------------------------------------------------------------------
# Context management
# --------------------------------------------------------------------------

_current: "contextvars.ContextVar[Optional[Trace]]" = contextvars.ContextVar(
    "observability_trace", default=None
)


def current_trace() -> Optional[Trace]:
    return _current.get()


def current_correlation_id() -> Optional[str]:
    trace = _current.get()
    return trace.correlation_id if trace else None


@contextlib.contextmanager
def start_trace(correlation_id: Optional[str] = None, name: str = "request",
                **attrs: Any) -> Iterator[Trace]:
    """Open a trace for the duration of the block (or join the active one).

    The outermost call owns the trace: when it exits, the trace is
    finished (total_ms stamped) and emitted. An inner call just yields the
    existing trace so attributes can be added to it.
    """
    existing = _current.get()
    if existing is not None:
        existing.set(**{k: v for k, v in attrs.items() if k not in existing.attrs})
        yield existing
        return

    trace = Trace(sanitize_correlation_id(correlation_id) or new_correlation_id(), name, attrs)
    token = _current.set(trace)
    try:
        yield trace
    except BaseException as exc:
        trace.fail(exc)
        raise
    finally:
        try:
            _current.reset(token)
        except ValueError:  # reset from a different context; fall back safely
            _current.set(None)
        trace.finish()
        _emit(trace)


@contextlib.contextmanager
def stage(name: str, **attrs: Any) -> Iterator[Any]:
    """Time a block as one stage of the active trace.

    Yields a span with ``.set(**attrs)`` and ``.set_outcome(...)``. If the
    block raises, the stage is marked ``error`` with the exception type and
    redacted message, its latency is still recorded, and the exception
    propagates unchanged. With no active trace this is a no-op.
    """
    trace = _current.get()
    if trace is None:
        yield NULL_SPAN
        return
    span = trace._open(name, attrs)
    try:
        yield span
    except BaseException as exc:
        span._fail(exc)
        raise
    finally:
        trace._close_span(span)


def annotate(**attrs: Any) -> None:
    """Attach attributes to the innermost open stage. No-op without a trace."""
    trace = _current.get()
    if trace is not None:
        trace.annotate(**attrs)


def annotate_trace(**attrs: Any) -> None:
    trace = _current.get()
    if trace is not None:
        trace.set(**attrs)


def set_outcome(outcome: str) -> None:
    trace = _current.get()
    if trace is not None:
        trace.set_outcome(outcome)


def record_event(name: str, **attrs: Any) -> None:
    trace = _current.get()
    if trace is not None:
        trace.event(name, **attrs)


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------

_listeners: List[Callable[[Trace], None]] = []
_sink_lock = threading.Lock()


def add_listener(listener: Callable[[Trace], None]) -> None:
    _listeners.append(listener)


def remove_listener(listener: Callable[[Trace], None]) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


@contextlib.contextmanager
def collecting() -> Iterator[List[Trace]]:
    """Collect every trace that finishes inside the block (tests, evidence
    capture). The yielded list is filled as traces complete."""
    finished: List[Trace] = []
    listener = finished.append
    add_listener(listener)
    try:
        yield finished
    finally:
        remove_listener(listener)


def _emit(trace: Trace) -> None:
    """Hand a finished trace to listeners, the log and the optional file.

    Observability must never break a request, so each step is isolated and
    any failure is swallowed after a warning.
    """
    for listener in list(_listeners):
        try:
            listener(trace)
        except Exception:  # noqa: BLE001 - a bad listener must not matter
            logger.warning("trace listener failed", exc_info=True)
    try:
        logger.info("trace %s", json.dumps(trace.summary(), ensure_ascii=False, sort_keys=True))
    except Exception:  # noqa: BLE001
        logger.warning("could not log trace summary", exc_info=True)
    path = os.getenv("TRACE_LOG_PATH", "").strip()
    if path:
        try:
            line = json.dumps(trace.to_dict(redacted=True), ensure_ascii=False)
            with _sink_lock:
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:  # noqa: BLE001
            logger.warning("could not write trace to TRACE_LOG_PATH", exc_info=True)