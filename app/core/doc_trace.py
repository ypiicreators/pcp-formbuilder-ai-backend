"""
Per-request tracing for the document flow.

Goal: when an upload is slow or fails, one grep of the console shows WHERE.

- `begin_trace()` / `ensure_trace()` give every log line of one upload a shared
  `[doc=ab12cd34]` prefix (installed once as a logging filter, so existing
  `logger.info(...)` calls in the providers, KB loader, httpx etc. get it for free).
- `scope("section 3/11")` adds a second tag, so lines from concurrent section tasks
  can be told apart.
- `stage(logger, "name", **ctx)` logs `start` and `took=..s`, records the timing, and
  logs the failing exception type when the block raises.
- `log_summary()` prints one line with every stage's duration and the slowest one.

Context variables are copied into asyncio tasks at creation, so tasks started after
`begin_trace()` share the same id and the same timings list.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_trace_id: ContextVar[str] = ContextVar("doc_trace_id", default="")
_scope: ContextVar[str] = ContextVar("doc_trace_scope", default="")
_started: ContextVar[float] = ContextVar("doc_trace_started", default=0.0)
# Shared (mutable) list: (label, seconds, status)
_timings: ContextVar[list[tuple[str, float, str]] | None] = ContextVar(
    "doc_trace_timings", default=None
)


def current_trace() -> str:
    return _trace_id.get()


def begin_trace(logger: logging.Logger, label: str, **ctx: Any) -> str:
    """Start a new trace in the current context and log what the request is."""
    tid = uuid.uuid4().hex[:8]
    _trace_id.set(tid)
    _scope.set("")
    _started.set(time.monotonic())
    _timings.set([])
    logger.info("TRACE begin %s%s", label, _fmt(ctx))
    return tid


def ensure_trace(logger: logging.Logger, label: str, **ctx: Any) -> str:
    """Reuse the active trace (route started it) or start one (direct callers/tests)."""
    return _trace_id.get() or begin_trace(logger, label, **ctx)


def _fmt(ctx: dict[str, Any]) -> str:
    if not ctx:
        return ""
    return " " + " ".join(f"{k}={_short(v)}" for k, v in ctx.items())


def _short(value: Any, limit: int = 200) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


@contextmanager
def scope(tag: str) -> Iterator[None]:
    """Tag every log line emitted inside the block (per-task safe)."""
    token = _scope.set(tag)
    try:
        yield
    finally:
        _scope.reset(token)


@contextmanager
def stage(logger: logging.Logger, name: str, **ctx: Any) -> Iterator[dict[str, Any]]:
    """
    Time one step. The yielded dict is for the caller to add results, which are
    printed on the closing line:

        with stage(logger, "kb_fetch") as st:
            kb = await fetch_live_kb()
            st["entries"] = len(kb)
    """
    info: dict[str, Any] = {}
    started = time.monotonic()
    label = f"{_scope.get()}:{name}" if _scope.get() else name
    logger.info("STAGE %s start%s", name, _fmt(ctx))
    status = "ok"
    try:
        yield info
    except GeneratorExit:
        status = "closed"  # consumer stopped iterating (e.g. client disconnected)
        raise
    except asyncio.CancelledError:
        status = "cancelled"  # e.g. a sibling section failed, or the client disconnected
        logger.warning("STAGE %s cancelled after %.2fs", name, time.monotonic() - started)
        raise
    except BaseException as exc:
        status = f"error:{type(exc).__name__}"
        logger.error(
            "STAGE %s FAILED after %.2fs: %s %s",
            name,
            time.monotonic() - started,
            type(exc).__name__,
            _short(exc, 300),
        )
        raise
    finally:
        took = time.monotonic() - started
        recorded = _timings.get()
        if recorded is not None:
            recorded.append((label, took, status))
        if status in ("ok", "closed"):
            logger.info("STAGE %s %s took=%.2fs%s", name, status, took, _fmt(info))


def log_summary(logger: logging.Logger, outcome: str) -> None:
    """One line: total wall time, every stage, and the slowest. Call once per request."""
    recorded = _timings.get() or []
    total = time.monotonic() - _started.get() if _started.get() else 0.0
    parts = [f"{label}={secs:.1f}s" + ("" if status == "ok" else f"({status})")
             for label, secs, status in recorded]
    slowest = max(recorded, key=lambda r: r[1], default=None)
    logger.info(
        "TRACE summary outcome=%s total=%.1fs slowest=%s | %s",
        outcome,
        total,
        f"{slowest[0]}({slowest[1]:.1f}s)" if slowest else "n/a",
        " ".join(parts) or "no stages",
    )


class TraceFilter(logging.Filter):
    """Prefix log messages with the active trace id (and scope tag)."""

    def filter(self, record: logging.LogRecord) -> bool:
        tid = _trace_id.get()
        if tid and not getattr(record, "_doc_traced", False):
            tag = f"[doc={tid}{' ' + _scope.get() if _scope.get() else ''}] "
            record.msg = tag + record.getMessage()
            record.args = ()
            record._doc_traced = True  # several handlers may see the same record
        return True


def install_trace_filter() -> None:
    """Attach the filter to every root handler (idempotent)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, TraceFilter) for f in handler.filters):
            handler.addFilter(TraceFilter())
