"""Agent factory to keep runner selection centralized.

We support multiple LLM API styles while keeping the app/UI event contract stable.

Current modes:
- responses (default): src/core/agent.Agent (Responses API)
- chat_completions: src/core/chat_completions_agent.ChatCompletionsAgent

This is intentionally small so app.py, group_session, and bus_agent_runtime don't drift.
"""

from __future__ import annotations

from typing import Any, Optional


def get_api_mode_from_app(app: Any) -> str:
    try:
        md = getattr(getattr(getattr(app, "config", None), "app", None), "api", None)
        mode = getattr(md, "mode", None)
        mode = str(mode).strip().lower() if isinstance(mode, str) else ""
        return mode or "responses"
    except Exception:
        return "responses"


def create_agent(
    *,
    api_key: Optional[str],
    base_url: Optional[str],
    name: str,
    tools: list,
    user_id: str,
    config: Any,
    agent_id: Optional[str] = None,
    api_mode: Optional[str] = None,
) -> Any:
    mode = str(api_mode or "").strip().lower() if isinstance(api_mode, str) else ""
    if not mode:
        mode = "responses"

    if mode in ("chat", "chat_completions", "chat-completions", "chatcompletion", "chatcompletions"):
        from ..core.chat_completions_agent import ChatCompletionsAgent

        return ChatCompletionsAgent(
            api_key=api_key,
            base_url=base_url,
            name=name,
            tools=tools,
            user_id=user_id,
            config=config,
            agent_id=agent_id,
        )

    # Default: Responses API
    from ..core.agent import Agent

    return Agent(
        api_key=api_key,
        base_url=base_url,
        name=name,
        tools=tools,
        user_id=user_id,
        config=config,
        agent_id=agent_id,
    )
