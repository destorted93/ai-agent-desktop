"""Helpers for hot-reloading agent definitions from ConfigRoot.

Goal: keep hot-reload behavior correct WITHOUT duplicating logic across:
- bus_agents.py (Agents Studio save)
- agent_run.py (primary run)
- group_session.py / bus_agent_runtime.py (spawned agents)

Agent core remains agnostic: we only rebuild AgentRuntimeConfig + tools in the app layer.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from ..tools import get_default_tools
from .agent_factory import create_agent, get_api_mode_from_app


def ensure_config_loaded(app: Any) -> None:
    try:
        app.config.load()
    except Exception:
        pass


def _build_runtime_config_and_tools_for_spec(
    app: Any,
    spec: Any,
    *,
    allow_memory: bool,
    allow_session_meta: bool,
    allow_recursion: bool,
) -> Tuple[Any, list]:
    agent_config = app.config.build_runtime_config(
        spec,
        allow_memory=bool(allow_memory),
        allow_session_meta=bool(allow_session_meta),
        allow_recursion=bool(allow_recursion),
    )
    all_tools = get_default_tools()
    tools = app.config.filter_tools(
        all_tools,
        spec,
        allow_memory=bool(allow_memory),
        allow_session_meta=bool(allow_session_meta),
        allow_recursion=bool(allow_recursion),
    )
    return agent_config, tools


def recreate_primary_agent_instance(
    app: Any,
    *,
    api_key: Optional[str],
    base_url: Optional[str],
) -> None:
    """Recreate the live primary agent instance.

    Needed when app-level runner settings change (e.g. api.mode), because switching
    between Agent implementations requires a new instance.

    Agent core remains agnostic; this is pure app orchestration.
    """

    ensure_config_loaded(app)

    aria_spec = None
    try:
        aria_spec = app.config.get_primary_agent()
    except Exception:
        aria_spec = None

    if aria_spec is None:
        return

    agent_config, tools = _build_runtime_config_and_tools_for_spec(
        app,
        aria_spec,
        allow_memory=True,
        allow_session_meta=True,
        allow_recursion=True,
    )

    aria_name = str(getattr(aria_spec, "display_name", None) or getattr(aria_spec, "id", "Aria"))
    aria_user_id = f"default_user:{str(getattr(aria_spec, 'id', 'aria'))}"

    api_mode = get_api_mode_from_app(app)

    app.agent = create_agent(
        api_key=api_key,
        base_url=base_url,
        name=aria_name,
        tools=tools,
        user_id=aria_user_id,
        config=agent_config,
        agent_id=str(getattr(aria_spec, "id", "aria")),
        api_mode=api_mode,
    )


def hot_apply_primary_agent(
    app: Any,
    *,
    allow_during_inference: bool,
) -> None:
    """Reload config and apply updated primary agent config/tools to the live app.agent.

    allow_during_inference:
      - False when called from UI save actions (don't mutate live agent mid-run)
      - True when called at the start of a run (safe; we want newest config)
    """

    if getattr(app, "agent", None) is None:
        return

    if not allow_during_inference:
        try:
            if getattr(app, "_is_inference_running", lambda: False)():
                return
        except Exception:
            # If we can't determine, be conservative.
            return

    ensure_config_loaded(app)

    try:
        aria_spec = app.config.get_primary_agent()
    except Exception:
        aria_spec = None

    if aria_spec is None:
        return

    agent_config, tools = _build_runtime_config_and_tools_for_spec(
        app,
        aria_spec,
        allow_memory=True,
        allow_session_meta=True,
        allow_recursion=True,
    )

    try:
        app.agent.name = str(getattr(aria_spec, "display_name", None) or getattr(aria_spec, "id", "Aria"))
    except Exception:
        pass

    try:
        app.agent.update_config(agent_config)
    except Exception:
        pass

    try:
        app.agent.update_tools(tools)
    except Exception:
        pass


def hot_apply_primary_agent_if_matches_saved_id(app: Any, saved_agent_id: Optional[str]) -> None:
    """Called after saving an agent definition.

    If the saved id is the primary agent id, update the live app.agent (best-effort).
    """
    sid = str(saved_agent_id or "").strip().lower()
    if not sid:
        return

    try:
        pid = str(app.config.app.agents.primary or "").strip().lower()
    except Exception:
        pid = "aria"

    if sid != pid:
        return

    hot_apply_primary_agent(app, allow_during_inference=False)
