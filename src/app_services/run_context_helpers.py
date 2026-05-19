"""Run-context helpers.

Purpose
- Provide a single shared implementation of `_iter_with_run_context`.
- Used by agent runners + bus runtime handlers.

This was originally duplicated in several modules during the extraction phase to
avoid import/cycle risk. Now that the seams are stable, we centralize it.

Design constraints
- No UI imports.
- Preserve behavior (move-first).
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, TypeVar

T = TypeVar("T")


def iter_with_run_context(ctx: Any, iterable: Iterable[T]) -> Iterator[T]:
    """Yield from an iterable while a RunContext is active (ContextVar).

    Accepts:
      - RunContext instance
      - dict of RunContext fields
    """
    token = None
    try:
        from ..appcore.run_context import RunContext, set_run_context, reset_run_context

        if isinstance(ctx, dict):
            try:
                ctx = RunContext(**ctx)
            except Exception:
                ctx = RunContext()
        elif not isinstance(ctx, RunContext):
            ctx = RunContext()

        token = set_run_context(ctx)
    except Exception:
        token = None

    try:
        for x in iterable:
            yield x
    finally:
        if token is not None:
            try:
                reset_run_context(token)
            except Exception:
                pass


# Back-compat alias (older modules used the private-ish name).
_iter_with_run_context = iter_with_run_context
