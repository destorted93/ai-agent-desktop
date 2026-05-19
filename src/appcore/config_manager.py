"""Single configuration system for the app.

One True Config Root:
- Runtime config lives in app-data: <APPDATA>/ai-agent/Config/
- Entry point: Config/app.yaml
- Agent definitions: Config/agents/*.yaml (full YAML; prompt is a string field)

No other config files are consulted.
Secrets are still stored in OS keyring (SecureStorage).
"""

from __future__ import annotations

import copy
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

from ..storage.secure import get_app_data_dir



# -----------------------
# Agent model UI spec (defaults + allowed values)
# -----------------------

# Keep these near AgentModelSettings so UI can ask the backend for a single
# source of truth (no magic strings/numbers in the Studio).
AGENT_MODEL_NAME_OPTIONS: List[str] = [
    # Keep ordering consistent with our supported backend list.
    "gemini-2.5-pro",
    "claude-haiku-4-5-20251001",
    "claude-4-5-sonnet-v1:0",
    "claude-4-5-opus-v1:0",
    "claude-4-6-sonnet-v1:0",
    "claude-4-6-opus-v1:0",
    "claude-sonnet-4-6",
    "gpt-5",
    "gpt-5-chat",
    "gpt-5-codex",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5.1",
    "gpt-5.1-chat",
    "gpt-5.1-codex",
    "gpt-5.1-codex-max",
    "gpt-5.2",
    "gpt-5.2-chat",
    "gpt-5.2-codex",
    "gpt-5.4-nano",
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.5",
]

AGENT_REASONING_EFFORT_OPTIONS: List[str] = ["none", "minimal", "low", "medium", "high", "xhigh"]
AGENT_REASONING_SUMMARY_OPTIONS: List[str] = ["auto", "concise", "detailed"]
AGENT_TEXT_VERBOSITY_OPTIONS: List[str] = ["low", "medium", "high"]

AGENT_TEMPERATURE_MIN = 0.1
AGENT_TEMPERATURE_MAX = 1.0
AGENT_MAX_TURNS_MIN = 1
AGENT_MAX_TURNS_MAX = 1024

AGENT_MODEL_DEFAULTS: Dict[str, Any] = {
    "name": "gpt-5.2",
    "temperature": 1.0,
    "max_turns": 256,
    "reasoning_effort": "high",
    "reasoning_summary": "detailed",
    "text_verbosity": "high",
    "stream": True,
}

# -----------------------
# Models
# -----------------------

AgentRole = Literal["primary", "family", "subagent", "template"]


class AppAPISettings(BaseModel):
    # LLM API style for the primary run pipeline.
    # - 'responses' (default): OpenAI Responses API runner
    # - 'chat_completions': Chat Completions-style runner (OpenAI-compatible, for Claude/OpenRouter/etc.)
    mode: str = Field(default="responses")
    base_url: str = Field(default="")

    class Config:
        extra = "forbid"

class AppPathsSettings(BaseModel):
    project_root: Optional[str] = Field(default=None)

    class Config:
        extra = "forbid"


class AppUISettings(BaseModel):
    theme: str = Field(default="dark")
    widget_opacity: float = Field(default=0.95)
    widget_width: int = Field(default=60)
    widget_height: int = Field(default=60)
    chat_width: int = Field(default=600)
    chat_height: int = Field(default=700)
    always_on_top: bool = Field(default=True)
    font_size: int = Field(default=13)
    show_token_usage: bool = Field(default=True)

    class Config:
        extra = "forbid"


class AppTranscribeSettings(BaseModel):
    model: str = Field(default="gpt-4o-transcribe")
    language: str = Field(default="en")
    sample_rate: int = Field(default=16000)

    class Config:
        extra = "forbid"


class AppTTSSettings(BaseModel):
    model: str = Field(default="gpt-4o-mini-tts")
    voice: str = Field(default="coral")
    format: str = Field(default="mp3")

    class Config:
        extra = "forbid"


class AppEmbeddingSettings(BaseModel):
    model: str = Field(default="openai.text-embedding-3-large")

    class Config:
        extra = "forbid"


class AppToolsSettings(BaseModel):
    terminal_permission_required: bool = Field(default=False)
    filesystem_permission_required: bool = Field(default=False)

    class Config:
        extra = "forbid"


class AppAgentsSettings(BaseModel):
    primary: str = Field(default="aria")
    family: str = Field(default="ariane")

    class Config:
        extra = "forbid"


class ConfluenceTokenEntry(BaseModel):
    """Non-secret reference entry for a Confluence PAT.

    The actual token is stored in the OS keychain under a derived secret name.
    """

    base_url: str = Field(default="")

    class Config:
        extra = "forbid"


class AppConfluenceSettings(BaseModel):
    """Confluence integration settings (non-secret)."""

    tokens: List[ConfluenceTokenEntry] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class AppSettings(BaseModel):
    schema_version: int = Field(default=1)
    api: AppAPISettings = Field(default_factory=AppAPISettings)
    paths: AppPathsSettings = Field(default_factory=AppPathsSettings)
    ui: AppUISettings = Field(default_factory=AppUISettings)
    transcribe: AppTranscribeSettings = Field(default_factory=AppTranscribeSettings)
    tts: AppTTSSettings = Field(default_factory=AppTTSSettings)
    embedding: AppEmbeddingSettings = Field(default_factory=AppEmbeddingSettings)
    tools: AppToolsSettings = Field(default_factory=AppToolsSettings)
    confluence: AppConfluenceSettings = Field(default_factory=AppConfluenceSettings)
    agents: AppAgentsSettings = Field(default_factory=AppAgentsSettings)

    class Config:
        extra = "forbid"


class AgentModelSettings(BaseModel):
    # Defaults are centralized in AGENT_MODEL_DEFAULTS (near the top of this file)
    # so Studio + schema stay in sync without scattered magic numbers.
    name: str = Field(default=str(AGENT_MODEL_DEFAULTS.get("name") or "gpt-5.2"))
    temperature: float = Field(default=float(AGENT_MODEL_DEFAULTS.get("temperature") or 1.0))
    max_turns: int = Field(default=int(AGENT_MODEL_DEFAULTS.get("max_turns") or 256))
    reasoning_effort: str = Field(default=str(AGENT_MODEL_DEFAULTS.get("reasoning_effort") or "high"))
    reasoning_summary: str = Field(default=str(AGENT_MODEL_DEFAULTS.get("reasoning_summary") or "detailed"))
    text_verbosity: str = Field(default=str(AGENT_MODEL_DEFAULTS.get("text_verbosity") or "high"))
    stream: bool = Field(default=bool(AGENT_MODEL_DEFAULTS.get("stream") if AGENT_MODEL_DEFAULTS.get("stream") is not None else True))

    class Config:
        extra = "forbid"


class AgentToolsSettings(BaseModel):
    # Tool selection per tool group.
    #
    # Example:
    #   groups:
    #     filesystem: [read_file, write_file]
    #     memory: [search_memories]
    #
    # A tool group is considered enabled iff it has at least one selected tool.
    groups: Dict[str, List[str]] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class AgentSettings(BaseModel):
    schema_version: int = Field(default=1)
    id: str
    display_name: str
    description: Optional[str] = None
    role: AgentRole = Field(default="subagent")
    model: AgentModelSettings = Field(default_factory=AgentModelSettings)
    tools: AgentToolsSettings = Field(default_factory=AgentToolsSettings)
    prompt: str = Field(default="")

    class Config:
        extra = "forbid"


class AgentRuntimeConfig(BaseModel):
    model_name: str = Field(default="gpt-5.2")
    temperature: float = Field(default=1.0)
    max_turns: int = Field(default=256)
    reasoning: Dict[str, Any] = Field(default_factory=lambda: {"effort": "high", "summary": "detailed"})
    text: Dict[str, Any] = Field(default_factory=lambda: {"verbosity": "high"})
    store: bool = Field(default=False)
    stream: bool = Field(default=True)
    tool_choice: str = Field(default="auto")
    include: List[str] = Field(default_factory=lambda: ["reasoning.encrypted_content"])
    instructions: str = Field(default="")

    class Config:
        extra = "forbid"


@dataclass
class ConfigError:
    path: str
    message: str


@dataclass
class ToolGroupSpec:
    id: str
    display_name: str
    tools: List[str]
    prompt_md: Optional[str] = None
    # Absolute path to the prompt markdown file (resolved at load time)
    prompt_path: Optional[Path] = None
    # Absolute path to the tool_group.yaml that defined this spec
    manifest_path: Optional[Path] = None


# -----------------------
# Parsing helpers
# -----------------------

_ID_RE = re.compile(r"^[a-z][a-z0-9_\-]{1,63}$")


class _YamlLiteralDumper(yaml.SafeDumper):
    pass


def _yaml_str_representer(dumper: yaml.SafeDumper, data: str):
    # Use block scalars for multi-line strings (nice for prompts).
    if isinstance(data, str) and "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_YamlLiteralDumper.add_representer(str, _yaml_str_representer)


def _yaml_dump(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_YamlLiteralDumper,
        sort_keys=False,
        allow_unicode=True,
    )


def _split_frontmatter(md: str) -> Tuple[Optional[str], str]:
    if not isinstance(md, str):
        return None, ""
    if not (md.startswith("---\n") or md.startswith("---\r\n")):
        return None, md
    lines = md.splitlines(True)
    if not lines or not re.match(r"^---\s*$", lines[0]):
        return None, md
    end_idx = None
    for i in range(1, len(lines)):
        if re.match(r"^---\s*$", lines[i]):
            end_idx = i
            break
    if end_idx is None:
        return None, md
    fm = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    return fm, body.lstrip("\n")


def slugify(name: str) -> str:
    s = (name or "").strip().lower()
    out = []
    prev_us = False
    for ch in s:
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
            prev_us = False
        elif ch.isspace() or ch in ("/", "\\", "."):
            if not prev_us:
                out.append("_")
                prev_us = True
        else:
            continue
    s2 = re.sub(r"_+", "_", "".join(out).strip("_-") or "")
    if not s2:
        return "agent"
    if not s2[0].isalpha():
        s2 = f"a_{s2}"
    return s2[:64]


# -----------------------
# ConfigManager
# -----------------------


class ConfigManager:
    """The single config manager for the entire app."""

    def __init__(self, config_root: Optional[Path] = None):
        self.config_root = config_root or (get_app_data_dir() / "Config")
        self.app_path = self.config_root / "app.yaml"
        self.agents_dir = self.config_root / "agents"
        self.one_shot_path = self.config_root / "one_shot.yaml"

        self.app: AppSettings = AppSettings()
        self.agents: Dict[str, AgentSettings] = {}
        # agent_id -> file path that defined it (for edit/delete without guessing names)
        self.agent_paths: Dict[str, Path] = {}

        # one-shot subagent template (Config/one_shot.yaml)
        self.one_shot_template = None

        # Tool groups are discovered from src/tools/*/tool_group.yaml
        self.tool_groups: Dict[str, ToolGroupSpec] = {}

        self.errors: List[ConfigError] = []

    def ensure_bootstrap(self) -> None:
        """Ensure app-data ConfigRoot exists. If missing, seed from src/config_seed/."""
        self.config_root.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)

        if self.app_path.exists() and any(self.agents_dir.glob("*.yaml")) and self.one_shot_path.exists():
            return

        # Seed defaults live in repo at src/config_seed/ (one level above appcore/).
        seed_root = Path(__file__).resolve().parent.parent / "config_seed"
        try:
            # Copy app.yaml
            src_app = seed_root / "app.yaml"
            if src_app.exists() and not self.app_path.exists():
                shutil.copyfile(src_app, self.app_path)

            # Copy agents
            src_agents = seed_root / "agents"
            if src_agents.exists() and src_agents.is_dir():
                for p in src_agents.glob("*.yaml"):
                    dst = self.agents_dir / p.name
                    if not dst.exists():
                        shutil.copyfile(p, dst)

            # Copy one_shot.yaml (one-shot subagent template)
            src_one_shot = seed_root / "one_shot.yaml"
            if src_one_shot.exists():
                dst_one_shot = self.one_shot_path
                if not dst_one_shot.exists():
                    shutil.copyfile(src_one_shot, dst_one_shot)
        except Exception as e:
            self.errors.append(ConfigError(path=str(seed_root), message=f"Bootstrap copy failed: {e}"))

    def load(self) -> None:
        self.errors = []
        self.ensure_bootstrap()

        # app.yaml
        try:
            data = yaml.safe_load(self.app_path.read_text(encoding="utf-8")) if self.app_path.exists() else {}
            data = data or {}
            if not isinstance(data, dict):
                raise ValueError("app.yaml must be a YAML object")
            self.app = AppSettings(**data)
        except Exception as e:
            self.errors.append(ConfigError(path=str(self.app_path), message=str(e)))
            self.app = AppSettings()

        # tool groups
        try:
            self._load_tool_groups()
        except Exception as e2:
            self.errors.append(ConfigError(path="<tool_groups>", message=str(e2)))

        # agents
        self.agents = {}
        self.agent_paths = {}
        for path in sorted(self.agents_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
                data = data or {}
                if not isinstance(data, dict):
                    raise ValueError("Agent file must be a YAML object")

                data = dict(data)
                # Legacy cleanup: allow older fields without failing load.
                data.pop("user_id", None)
                data.pop("lifecycle", None)

                spec = AgentSettings(**data)
                if not _ID_RE.match(spec.id):
                    raise ValueError(f"Invalid id '{spec.id}' (expected {_ID_RE.pattern})")

                self.agents[str(spec.id)] = spec
                self.agent_paths[str(spec.id)] = path
            except Exception as e:
                self.errors.append(ConfigError(path=str(path), message=str(e)))

        # one_shot.yaml (one-shot subagent template)
        self.one_shot_template = None
        try:
            if self.one_shot_path.exists():
                data = yaml.safe_load(self.one_shot_path.read_text(encoding="utf-8")) if self.one_shot_path.exists() else {}
                data = data or {}
                if not isinstance(data, dict):
                    raise ValueError("one_shot.yaml must be a YAML object")

                data = dict(data)
                # Legacy cleanup: allow older fields without failing load.
                data.pop("user_id", None)
                data.pop("lifecycle", None)

                spec = AgentSettings(**data)
                # Force template role for safety.
                try:
                    spec.role = "template"
                except Exception:
                    pass
                self.one_shot_template = spec
        except Exception as e:
            self.errors.append(ConfigError(path=str(self.one_shot_path), message=str(e)))
            self.one_shot_template = None

    def save_app(self) -> None:
        """Atomic write of app.yaml."""
        data = self.app.model_dump() if hasattr(self.app, "model_dump") else self.app.dict()
        blob = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        tmp = self.app_path.with_suffix(self.app_path.suffix + ".tmp")
        tmp.write_text(blob, encoding="utf-8")
        tmp.replace(self.app_path)

    def resolve_agent_id(self, name_or_id: str) -> Optional[str]:
        raw = (name_or_id or "").strip()
        if not raw:
            return None
        if raw in self.agents:
            return raw
        s = slugify(raw)
        if s in self.agents:
            return s
        matches = [aid for aid, a in self.agents.items() if (a.display_name or "").strip().lower() == raw.lower()]
        return matches[0] if len(matches) == 1 else None

    def get_agent(self, agent_id: str) -> Optional[AgentSettings]:
        return self.agents.get(str(agent_id))

    def get_primary_agent(self) -> Optional[AgentSettings]:
        return self.get_agent(self.app.agents.primary)

    def get_family_agent(self) -> Optional[AgentSettings]:
        return self.get_agent(self.app.agents.family)

    def _load_tool_groups(self) -> None:
        """Discover tool group specs from src/tools/*/tool_group.yaml.

        This avoids hardcoding tool-group membership in config_manager.py.
        """
        self.tool_groups = {}
        tools_root = Path(__file__).resolve().parent.parent / "tools"
        if not tools_root.exists() or not tools_root.is_dir():
            return

        for d in sorted(tools_root.iterdir(), key=lambda p: p.name):
            if not d.is_dir():
                continue
            mf = d / "tool_group.yaml"
            if not mf.exists():
                continue

            raw = yaml.safe_load(mf.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                continue

            gid = str(raw.get("id") or d.name).strip().lower()
            if not gid:
                continue

            display_name = str(raw.get("display_name") or gid)
            prompt_md = raw.get("prompt_md")
            prompt_md = str(prompt_md).strip() if isinstance(prompt_md, str) and prompt_md.strip() else None
            tools = raw.get("tools") if isinstance(raw.get("tools"), list) else []
            tool_names = [str(t).strip() for t in tools if isinstance(t, str) and t.strip()]

            prompt_path = (d / prompt_md) if prompt_md else None
            if isinstance(prompt_path, Path) and not prompt_path.exists():
                prompt_path = None

            self.tool_groups[gid] = ToolGroupSpec(
                id=gid,
                display_name=display_name,
                tools=tool_names,
                prompt_md=prompt_md,
                prompt_path=prompt_path,
                manifest_path=mf,
            )

    def list_tool_groups_meta(self) -> List[Dict[str, Any]]:
        """Return tool group metadata for UI (Agents Studio).

        Includes each tool's description (from tool schema) so the UI can display it.
        """
        # Map tool_name -> description from the canonical tool registry.
        tool_desc: Dict[str, str] = {}
        try:
            from ..tools import get_default_tools  # local import to avoid import-order traps

            for t in (get_default_tools() or []):
                try:
                    sch = getattr(t, "schema", None)
                    if not isinstance(sch, dict):
                        continue
                    nm = sch.get("name")
                    if not isinstance(nm, str) or not nm.strip():
                        continue
                    desc = sch.get("description")
                    if isinstance(desc, str) and desc.strip():
                        tool_desc[nm.strip()] = desc.strip()
                    else:
                        tool_desc[nm.strip()] = ""
                except Exception:
                    continue
        except Exception:
            tool_desc = {}

        out: List[Dict[str, Any]] = []
        for gid, spec in (self.tool_groups or {}).items():
            tools_meta: List[Dict[str, Any]] = []
            for tn in (spec.tools or []):
                if not isinstance(tn, str) or not tn.strip():
                    continue
                name = tn.strip()
                tools_meta.append({"name": name, "description": tool_desc.get(name, "")})

            out.append(
                {
                    "id": str(gid),
                    "display_name": str(spec.display_name or gid),
                    "tools": tools_meta,
                }
            )

        out.sort(key=lambda x: str(x.get("id") or ""))
        return out

    def _get_tool_group_prompt_text(self, group_id: str, agent_spec: AgentSettings) -> str:
        tg = (self.tool_groups or {}).get(str(group_id).strip().lower())
        if not tg or not isinstance(tg.prompt_path, Path) or not tg.prompt_path.exists():
            return ""
        try:
            txt = tg.prompt_path.read_text(encoding="utf-8")
        except Exception:
            return ""

        # Tiny templating (keep it boring).
        try:
            txt = txt.replace("{AGENT_NAME}", str(agent_spec.display_name or ""))
            txt = txt.replace("{AGENT_ID}", str(agent_spec.id or ""))
        except Exception:
            pass

        return (txt or "").strip()

    def build_effective_instructions(
        self,
        spec: AgentSettings,
        *,
        allow_memory: bool,
        allow_session_meta: bool,
        allow_recursion: bool,
    ) -> str:
        base = (spec.prompt or "").strip()

        # Tool-group selection is stored as group_id -> [tool_name...].
        sel = getattr(getattr(spec, "tools", None), "groups", None)
        sel_map: Dict[str, List[str]] = sel if isinstance(sel, dict) else {}

        effective: List[str] = []
        for raw_gid, raw_tools in (sel_map or {}).items():
            gid = str(raw_gid).strip().lower()
            if not gid:
                continue

            # Runner-level gating (context truth).
            if gid == "memory" and not allow_memory:
                continue
            if gid == "session" and not allow_session_meta:
                continue
            if gid in ("subagents", "consult_inner_voice") and not allow_recursion:
                continue

            tg = (self.tool_groups or {}).get(gid)
            if not tg or not isinstance(getattr(tg, "tools", None), list):
                continue

            # Group is enabled iff at least one selected tool is valid for that group.
            desired = [str(x).strip() for x in (raw_tools or []) if isinstance(x, str) and x.strip()]
            if not desired:
                continue

            valid = {str(x).strip() for x in tg.tools if isinstance(x, str) and x.strip()}
            if not (set(desired) & valid):
                continue

            if gid not in effective:
                effective.append(gid)

        parts: List[str] = [base] if base else []
        for g in sorted(effective):
            ch = self._get_tool_group_prompt_text(g, spec)
            if ch:
                parts.append(ch)

        return "\n\n".join([p for p in parts if isinstance(p, str) and p.strip()]).strip()

    def build_runtime_config(
        self,
        spec: AgentSettings,
        *,
        allow_memory: bool,
        allow_session_meta: bool,
        allow_recursion: bool,
    ) -> AgentRuntimeConfig:
        instr = self.build_effective_instructions(
            spec,
            allow_memory=bool(allow_memory),
            allow_session_meta=bool(allow_session_meta),
            allow_recursion=bool(allow_recursion),
        )
        return AgentRuntimeConfig(
            model_name=str(spec.model.name),
            temperature=float(spec.model.temperature),
            max_turns=int(spec.model.max_turns),
            reasoning={"effort": str(spec.model.reasoning_effort), "summary": str(spec.model.reasoning_summary)},
            text={"verbosity": str(spec.model.text_verbosity)},
            stream=bool(spec.model.stream),
            instructions=instr,
        )


    # Tool filtering (policy + groups)

    def filter_tools(self, all_tools: List[Any], spec: AgentSettings, *, allow_memory: bool, allow_session_meta: bool, allow_recursion: bool) -> List[Any]:
        # Tool-group selection is stored as group_id -> [tool_name...].
        sel = getattr(getattr(spec, "tools", None), "groups", None)
        sel_map: Dict[str, List[str]] = sel if isinstance(sel, dict) else {}

        allowed: set[str] = set()
        for raw_gid, raw_tools in (sel_map or {}).items():
            gid = str(raw_gid).strip().lower()
            if not gid:
                continue

            # Runner-level gating (context truth).
            if gid == "memory" and not allow_memory:
                continue
            if gid == "session" and not allow_session_meta:
                continue
            if gid in ("subagents", "consult_inner_voice") and not allow_recursion:
                continue

            tg = (self.tool_groups or {}).get(gid)
            if not tg or not isinstance(getattr(tg, "tools", None), list):
                continue

            desired = [str(x).strip() for x in (raw_tools or []) if isinstance(x, str) and x.strip()]
            if not desired:
                continue

            valid = {str(x).strip() for x in tg.tools if isinstance(x, str) and x.strip()}
            allowed |= {x for x in desired if x in valid}

        # Extra runner-level gating (tool-level truth).
        if not allow_recursion:
            allowed -= {"consult_ariane", "run_subagent"}
        if not allow_memory:
            allowed -= {"get_memories", "search_memories", "create_memory", "update_memory", "delete_memory"}
        if not allow_session_meta:
            allowed -= {"set_session_meta"}

        def _name(t: Any) -> Optional[str]:
            try:
                schema = getattr(t, "schema", None)
                if isinstance(schema, dict):
                    nm = schema.get("name")
                    return str(nm) if isinstance(nm, str) and nm else None
            except Exception:
                return None
            return None

        out: List[Any] = []
        for t in all_tools:
            nm = _name(t)
            if nm and nm in allowed:
                # Web search is an OpenAI built-in tool: its tool schema must not include `name`.
                # But DO NOT mutate the shared tool instance (get_default_tools() returns reusable objects).
                try:
                    if t.schema.get("type") == "web_search":
                        t2 = copy.copy(t)
                        sch = getattr(t, "schema", None)
                        t2.schema = dict(sch) if isinstance(sch, dict) else {"type": "web_search"}
                        t2.schema.pop("name", None)
                        out.append(t2)
                    else:
                        out.append(t)
                except Exception:
                    out.append(t)

        return out

    # -----------------------------------------------------------------
    # One-shot template (Config/one_shot.yaml)
    # -----------------------------------------------------------------

    def get_one_shot_template(self) -> Optional[AgentSettings]:
        return getattr(self, "one_shot_template", None)

    def save_one_shot_template(self, spec: AgentSettings) -> Path:
        """Create/update Config/one_shot.yaml (atomic)."""
        # Enforce a stable id + role.
        try:
            spec.id = "one_shot"
        except Exception:
            pass
        try:
            spec.role = "template"
        except Exception:
            pass

        try:
            data = spec.model_dump()  # pydantic v2
        except Exception:
            data = spec.dict()  # pydantic v1

        # Keep prompts tidy.
        try:
            p = data.get("prompt")
            if isinstance(p, str) and p and not p.endswith("\n"):
                data["prompt"] = p + "\n"
        except Exception:
            pass

        blob = _yaml_dump(data)
        tmp = self.one_shot_path.with_suffix(self.one_shot_path.suffix + ".tmp")
        tmp.write_text(blob, encoding="utf-8")
        tmp.replace(self.one_shot_path)

        # Keep in-memory copy in sync.
        try:
            self.one_shot_template = AgentSettings(**data)
            try:
                self.one_shot_template.role = "template"
            except Exception:
                pass
        except Exception:
            self.one_shot_template = spec

        return self.one_shot_path

    # -----------------------------------------------------------------
    # Agent file CRUD (for Agents Studio hot-reload)
    # -----------------------------------------------------------------

    def _agent_to_yaml(self, spec: AgentSettings) -> str:
        """Serialize an AgentSettings into Config/agents/*.yaml format."""
        try:
            data = spec.model_dump()  # pydantic v2
        except Exception:
            data = spec.dict()  # pydantic v1

        # Keep prompts tidy.
        try:
            p = data.get("prompt")
            if isinstance(p, str) and p and not p.endswith("\n"):
                data["prompt"] = p + "\n"
        except Exception:
            pass

        return _yaml_dump(data)

    def save_agent(self, spec: AgentSettings) -> Path:
        """Create or update an agent definition under Config/agents/ (atomic)."""
        if not _ID_RE.match(str(spec.id)):
            raise ValueError(f"Invalid id '{spec.id}'")

        # Prefer the original defining file if present (preserves custom filenames),
        # otherwise use <id>.yaml.
        path = self.agent_paths.get(str(spec.id))
        if not isinstance(path, Path):
            path = self.agents_dir / f"{spec.id}.yaml"

        blob = self._agent_to_yaml(spec)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(blob, encoding="utf-8")
        tmp.replace(path)
        return path

    def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent definition file by id (best-effort)."""
        aid = str(agent_id or "").strip()
        if not aid:
            return False

        path = self.agent_paths.get(aid)
        if not isinstance(path, Path):
            path = self.agents_dir / f"{aid}.yaml"

        try:
            if path.exists():
                path.unlink()
                return True
        except Exception:
            return False
        return False


    def get_agent_model_ui_spec(self) -> Dict[str, Any]:
        """Return defaults + allowed values for the Agents Studio model editor."""
        try:
            defaults = AgentModelSettings().model_dump()  # pydantic v2
        except Exception:
            try:
                defaults = AgentModelSettings().dict()  # pydantic v1
            except Exception:
                defaults = dict(AGENT_MODEL_DEFAULTS)

        return {
            "defaults": defaults,
            "options": {
                "model_name": list(AGENT_MODEL_NAME_OPTIONS),
                "reasoning_effort": list(AGENT_REASONING_EFFORT_OPTIONS),
                "reasoning_summary": list(AGENT_REASONING_SUMMARY_OPTIONS),
                "text_verbosity": list(AGENT_TEXT_VERBOSITY_OPTIONS),
            },
            "ranges": {
                "temperature": {"min": float(AGENT_TEMPERATURE_MIN), "max": float(AGENT_TEMPERATURE_MAX), "step": 0.1},
                "max_turns": {"min": int(AGENT_MAX_TURNS_MIN), "max": int(AGENT_MAX_TURNS_MAX), "step": 1},
            },
        }

    def list_agents_meta(self) -> List[Dict[str, Any]]:
        """Return a lightweight list of agents for UI dropdowns/pickers."""
        out: List[Dict[str, Any]] = []
        for aid, a in (self.agents or {}).items():
            if not isinstance(a, AgentSettings):
                continue
            try:
                out.append(
                    {
                        "id": str(a.id),
                        "display_name": str(a.display_name),
                        "description": (str(a.description) if a.description is not None else None),
                        "role": str(a.role),
                        "tools": {"groups": list(a.tools.groups or [])},
                    }
                )
            except Exception:
                pass

        def _k(m: Dict[str, Any]) -> tuple:
            role = str(m.get("role") or "")
            # primary first, then family, then the rest.
            pr = 0 if role == "primary" else 1 if role == "family" else 2
            return (pr, str(m.get("display_name") or ""))

        out.sort(key=_k)
        return out
