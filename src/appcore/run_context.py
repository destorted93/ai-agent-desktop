"""RunContext: ambient per-run/per-task context.

This is a small, general-purpose context carrier intended for:
- routing per-agent resources (e.g. memory stores)
- correlation IDs for logging/telemetry
- future per-run policy decisions (without threading args everywhere)

Design constraints:
- Must stay lightweight and import-safe (no Runtime imports, no storage imports).
- Must be safe under concurrency: ContextVar provides task/thread-local context.
- Context is immutable (frozen) to avoid spooky mutation side-effects.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Optional


@dataclass(frozen=True)
class RunContext:
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None

    session_id: Optional[str] = None
    run_id: Optional[str] = None

    parent_session_id: Optional[str] = None
    parent_run_id: Optional[str] = None
    parent_call_id: Optional[str] = None

    mode: Optional[str] = None  # e.g. persistent/run for sub-agents


_current_ctx: ContextVar[RunContext] = ContextVar("appcore.run_context.current", default=RunContext())


def get_run_context() -> RunContext:
    return _current_ctx.get()


def set_run_context(ctx: RunContext) -> Any:
    """Set the current RunContext. Returns a token for reset_run_context()."""
    return _current_ctx.set(ctx if isinstance(ctx, RunContext) else RunContext())


def reset_run_context(token: Any) -> None:
    try:
        _current_ctx.reset(token)
    except Exception:
        pass


def patch_run_context(**kwargs) -> Any:
    """Patch the current RunContext immutably; returns a token for reset_run_context()."""
    cur = get_run_context()
    try:
        nxt = replace(cur, **kwargs)
    except Exception:
        nxt = cur
    return set_run_context(nxt)
