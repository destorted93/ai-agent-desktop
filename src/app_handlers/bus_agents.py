"""Agents (Agents Studio) bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.

NOTE: This module mirrors the original logic from Application._bus_agents_*.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ..appcore.config_manager import AgentSettings
from ..appcore.runtime_context import Runtime
from ..app_services.agent_reload import hot_apply_primary_agent_if_matches_saved_id


def register_agents_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    """Register agents CRUD + model UI spec endpoints. Returns unsubscribe callables."""
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("agents.cmd.list", lambda ev: bus_agents_list(app, ev)))
    unsubs.append(bus.subscribe("agents.cmd.get", lambda ev: bus_agents_get(app, ev)))
    unsubs.append(bus.subscribe("agents.cmd.save", lambda ev: bus_agents_save(app, ev)))
    unsubs.append(bus.subscribe("agents.cmd.delete", lambda ev: bus_agents_delete(app, ev)))
    unsubs.append(bus.subscribe("agents.cmd.model_ui_spec", lambda ev: bus_agents_model_ui_spec(app, ev)))
    unsubs.append(bus.subscribe("agents.cmd.tool_groups.list", lambda ev: bus_agents_tool_groups_list(app, ev)))
    unsubs.append(bus.subscribe("agents.cmd.prompt.preview", lambda ev: bus_agents_prompt_preview(app, ev)))

    # One-shot subagent template (Config/one_shot.yaml)
    unsubs.append(bus.subscribe("agents.cmd.one_shot.get", lambda ev: bus_agents_one_shot_get(app, ev)))
    unsubs.append(bus.subscribe("agents.cmd.one_shot.save", lambda ev: bus_agents_one_shot_save(app, ev)))
    unsubs.append(bus.subscribe("agents.cmd.one_shot.prompt.preview", lambda ev: bus_agents_one_shot_prompt_preview(app, ev)))

    return unsubs


def bus_agents_list(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass
            agents = app.config.list_agents_meta()
            app._bus_reply(reply_topic, {"status": "success", "agents": agents})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agents_get(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    agent_id = payload.get("agent_id")
    if not reply_topic:
        return
    if not isinstance(agent_id, str) or not agent_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "agent_id is required"})
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            spec = app.config.get_agent(str(agent_id).strip())
            if spec is None:
                app._bus_reply(reply_topic, {"status": "error", "message": f"Unknown agent '{agent_id}'"})
                return

            try:
                agent = spec.model_dump()  # pydantic v2
            except Exception:
                agent = spec.dict()  # pydantic v1

            app._bus_reply(reply_topic, {"status": "success", "agent": agent})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agents_save(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    agent = payload.get("agent")
    if not reply_topic:
        return
    if not isinstance(agent, dict):
        app._bus_reply(reply_topic, {"status": "error", "message": "agent must be an object"})
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            spec = AgentSettings(**agent)

            primary_id = str(app.config.app.agents.primary or "").strip().lower()
            family_id = str(app.config.app.agents.family or "").strip().lower()
            sid = str(spec.id or "").strip().lower()

            existing = None
            try:
                existing = app.config.get_agent(str(spec.id).strip())
            except Exception:
                existing = None

            # Allow editing existing primary/family agents (user is trusted), but prevent
            # creating NEW protected agents or changing roles.
            if existing is not None:
                try:
                    spec.role = str(getattr(existing, "role", "subagent") or "subagent")
                except Exception:
                    pass
            else:
                # New agent creation: must not claim primary/family ids.
                if sid in (primary_id, family_id):
                    app._bus_reply(reply_topic, {"status": "error", "message": "Cannot create/overwrite protected agent id"})
                    return
                # New agents must be normal subagents.
                if str(spec.role or "").strip().lower() in ("primary", "family"):
                    app._bus_reply(reply_topic, {"status": "error", "message": "Cannot create an agent with role primary/family"})
                    return
                spec.role = "subagent"

            # Save + reload (hot reload)
            app.config.save_agent(spec)
            try:
                app.config.load()
            except Exception:
                pass

            # Best-effort hot-apply to the live primary agent instance (no app restart required).
            # Other agents (subagents, group participants) are instantiated per-run and will pick up
            # the updated spec automatically as long as ConfigManager is reloaded.
            try:
                hot_apply_primary_agent_if_matches_saved_id(app, str(spec.id))
            except Exception:
                pass

            Runtime.get_event_bus().publish(
                "agents.list.changed",
                {"action": "saved", "agent_id": str(spec.id)},
            )
            app._bus_reply(reply_topic, {"status": "success", "agent_id": str(spec.id)})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agents_delete(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    agent_id = payload.get("agent_id")
    if not reply_topic:
        return
    if not isinstance(agent_id, str) or not agent_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "agent_id is required"})
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            primary_id = str(app.config.app.agents.primary or "").strip().lower()
            family_id = str(app.config.app.agents.family or "").strip().lower()
            sid = str(agent_id).strip().lower()

            if sid in (primary_id, family_id):
                app._bus_reply(reply_topic, {"status": "error", "message": "This agent is protected"})
                return

            ok = bool(app.config.delete_agent(str(agent_id).strip()))
            try:
                app.config.load()
            except Exception:
                pass

            Runtime.get_event_bus().publish(
                "agents.list.changed",
                {"action": "deleted", "agent_id": str(agent_id).strip()},
            )
            app._bus_reply(reply_topic, {"status": "success", "deleted": ok, "agent_id": str(agent_id).strip()})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agents_model_ui_spec(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            spec = None
            try:
                if hasattr(app.config, "get_agent_model_ui_spec"):
                    spec = app.config.get_agent_model_ui_spec()
            except Exception:
                spec = None

            app._bus_reply(reply_topic, {"status": "success", "spec": spec or {}})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agents_tool_groups_list(app: Any, event) -> None:
    """List available tool groups (discovered from src/tools/*/tool_group.yaml)."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            groups = []
            try:
                groups = app.config.list_tool_groups_meta()
            except Exception:
                groups = []

            app._bus_reply(reply_topic, {"status": "success", "tool_groups": groups})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agents_prompt_preview(app: Any, event) -> None:
    """Return the effective system prompt for an agent (base + tool-group chapters).

    Context matters because runners apply gates (memory/session_meta/recursion).

    payload:
      - agent_id: str
      - context_mode: 'auto'|'primary'|'subagent_persistent'|'subagent_run' (optional)
      - reply_topic: str
    """
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    agent_id = payload.get("agent_id")
    context_mode = payload.get("context_mode")

    if not reply_topic:
        return
    if not isinstance(agent_id, str) or not agent_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "agent_id is required"})
        return

    cm = str(context_mode or "auto").strip().lower()
    if cm not in ("auto", "primary", "subagent_persistent", "subagent_run"):
        cm = "auto"

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            spec = app.config.get_agent(str(agent_id).strip())
            if spec is None:
                app._bus_reply(reply_topic, {"status": "error", "message": f"Unknown agent '{agent_id}'"})
                return

            role = str(getattr(spec, "role", "subagent") or "subagent").strip().lower()

            # Default preview mode: match how the agent is most commonly run.
            mode_eff = cm
            if mode_eff == "auto":
                if role == "primary":
                    mode_eff = "primary"
                else:
                    # family + subagents are typically invoked as persistent subagents
                    mode_eff = "subagent_persistent"

            allow_memory = True
            allow_session_meta = False
            allow_recursion = False

            if mode_eff == "primary":
                allow_memory = True
                allow_session_meta = True
                allow_recursion = True
            elif mode_eff == "subagent_persistent":
                allow_memory = True
                allow_session_meta = False
                allow_recursion = False
            elif mode_eff == "subagent_run":
                allow_memory = False
                allow_session_meta = False
                allow_recursion = False

            prompt = app.config.build_effective_instructions(
                spec,
                allow_memory=bool(allow_memory),
                allow_session_meta=bool(allow_session_meta),
                allow_recursion=bool(allow_recursion),
            )

            app._bus_reply(
                reply_topic,
                {
                    "status": "success",
                    "agent_id": str(getattr(spec, "id", agent_id)),
                    "display_name": str(getattr(spec, "display_name", agent_id)),
                    "role": role,
                    "context_mode": mode_eff,
                    "allow_memory": bool(allow_memory),
                    "allow_session_meta": bool(allow_session_meta),
                    "allow_recursion": bool(allow_recursion),
                    "prompt": str(prompt or ""),
                },
            )

        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


# -----------------------------------------------------------------------------
# One-shot subagent template (Config/one_shot.yaml)
# -----------------------------------------------------------------------------


def bus_agents_one_shot_get(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            spec = None
            try:
                spec = app.config.get_one_shot_template()
            except Exception:
                spec = None

            if spec is None:
                app._bus_reply(reply_topic, {"status": "error", "message": "Missing Config/one_shot.yaml"})
                return

            try:
                agent = spec.model_dump()  # pydantic v2
            except Exception:
                agent = spec.dict()  # pydantic v1

            app._bus_reply(reply_topic, {"status": "success", "agent": agent})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agents_one_shot_save(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    agent = payload.get("agent")
    if not reply_topic:
        return
    if not isinstance(agent, dict):
        app._bus_reply(reply_topic, {"status": "error", "message": "agent must be an object"})
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            spec = AgentSettings(**agent)
            try:
                spec.role = "template"
            except Exception:
                pass
            try:
                spec.id = "one_shot"
            except Exception:
                pass

            app.config.save_one_shot_template(spec)
            try:
                app.config.load()
            except Exception:
                pass

            Runtime.get_event_bus().publish(
                "agents.one_shot.changed",
                {"action": "saved"},
            )
            app._bus_reply(reply_topic, {"status": "success", "agent_id": "one_shot"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agents_one_shot_prompt_preview(app: Any, event) -> None:
    """Prompt preview for the one-shot template."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            spec = None
            try:
                spec = app.config.get_one_shot_template()
            except Exception:
                spec = None

            if spec is None:
                app._bus_reply(reply_topic, {"status": "error", "message": "Missing Config/one_shot.yaml"})
                return

            prompt = app.config.build_effective_instructions(
                spec,
                allow_memory=True,
                allow_session_meta=True,
                allow_recursion=False,
            )

            app._bus_reply(
                reply_topic,
                {
                    "status": "success",
                    "agent_id": "one_shot",
                    "display_name": str(getattr(spec, "display_name", "one_shot")),
                    "role": "template",
                    "context_mode": "subagent_run",
                    "allow_memory": True,
                    "allow_session_meta": True,
                    "allow_recursion": False,
                    "prompt": str(prompt or ""),
                },
            )

        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
