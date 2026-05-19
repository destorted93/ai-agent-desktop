"""Chat Completions-backed agent that preserves the app's Responses-style event contract.

Design goal: compatibility, not migration.

- UI + storage are built around Responses API-style event names and canonical session item shapes.
- This agent calls Chat Completions (streaming) and *translates* chunks into the same event stream.

Notes:
- Reasoning summary events are not emitted (Chat Completions doesn't provide them reliably).
- Tool schemas are adapted from our existing Responses-style tool defs to Chat Completions tool format.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Generator, List, Optional

from .agent import Agent, make_serializable


def _adapt_tool_schema_for_chat(schema: Any) -> Any:
    """Adapt our existing tool schema shape to Chat Completions `tools` format.

    Existing (Responses-style in this app):
      {"type":"function","name":...,"description":...,"parameters":{...},"strict":True}

    Chat Completions expects:
      {"type":"function","function":{"name":...,"description":...,"parameters":{...}}}

    If the schema already contains a `function` key, we pass it through unchanged.
    """
    if not isinstance(schema, dict):
        return schema
    if schema.get("type") != "function":
        return schema
    if isinstance(schema.get("function"), dict):
        return schema

    fn = {
        "name": schema.get("name") or "",
        "description": schema.get("description") or "",
        "parameters": schema.get("parameters") or {"type": "object", "properties": {}, "additionalProperties": False},
    }
    # Drop any Responses-only keys like `strict`.
    return {"type": "function", "function": fn}


def _parts_to_chat_content(parts: Any) -> Any:
    """Convert our canonical content-part list to Chat Completions message content.

    Our canonical parts:
      - {"type":"input_text","text":...}
      - {"type":"output_text","text":...}
      - {"type":"input_image","image_url": "data:image/..."}

    Chat parts:
      - {"type":"text","text":...}
      - {"type":"image_url","image_url": {"url": ...}}

    We return either a list of parts or a simple string when safe.
    """
    if parts is None:
        return ""
    if isinstance(parts, str):
        return parts

    if not isinstance(parts, list):
        return str(parts)

    out: List[Dict[str, Any]] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        t = p.get("type")
        if t in ("input_text", "output_text"):
            txt = p.get("text")
            if isinstance(txt, str) and txt:
                out.append({"type": "text", "text": txt})
        elif t == "input_image":
            url = p.get("image_url")
            if isinstance(url, str) and url:
                out.append({"type": "image_url", "image_url": {"url": url}})
        else:
            # Best-effort: stringify unknown parts so we don't silently drop context.
            try:
                out.append({"type": "text", "text": json.dumps(p)})
            except Exception:
                out.append({"type": "text", "text": str(p)})

    if not out:
        return ""
    if len(out) == 1 and out[0].get("type") == "text":
        # Chat accepts a raw string; keep it simple.
        return out[0].get("text") or ""
    return out


def _canonical_items_to_chat_messages(canonical_items: List[Dict[str, Any]], instructions: str) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []

    ins = (instructions or "").strip()
    if ins:
        # Keep it boring: use `system` for broad compatibility.
        messages.append({
            "role": "system", 
            "content": [
                {
                    "type": "text",
                    "text": ins,
                    "cache_control": {"type": "ephemeral"} # enables caching
                }
            ]
        })

    for it in canonical_items or []:
        if not isinstance(it, dict):
            continue

        # Message-like items
        role = it.get("role")
        if isinstance(role, str) and role:
            # Some providers don't accept OpenAI's newer roles; keep compatibility here.
            if role.strip().lower() == "developer":
                role = "system"
            content = _parts_to_chat_content(it.get("content"))
            messages.append({"role": role, "content": content})
            continue

        # Responses-style message output item: {type:"message", role:"assistant", content:[{type:"output_text"...}]}
        if it.get("type") == "message" and isinstance(it.get("role"), str):
            role2 = it.get("role")
            content = _parts_to_chat_content(it.get("content"))
            messages.append({"role": role2, "content": content})
            continue

        # Tool call item
        if it.get("type") == "function_call":
            call_id = str(it.get("call_id") or "") or f"call_{uuid.uuid4().hex}"
            fn_name = str(it.get("name") or "")
            args = it.get("arguments")
            args_str = args if isinstance(args, str) else (json.dumps(args) if args is not None else "{}")
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": fn_name, "arguments": args_str},
                        }
                    ],
                }
            )
            continue

        # Tool output item
        if it.get("type") == "function_call_output":
            call_id = str(it.get("call_id") or "")
            out = it.get("output")
            out_str = out if isinstance(out, str) else json.dumps(out)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": out_str})
            continue

        # Ignore other internal-only items (reasoning/run_summary/system_notice/etc.)

    return messages


class ChatCompletionsAgent(Agent):
    """Agent that uses Chat Completions under the hood but emits Responses-style events."""

    def _process_turn(self, input_messages: List[Dict]) -> Generator[Dict[str, Any], None, None]:
        model = self.config.model_name
        temperature = self.config.temperature

        # Runner marker (debug): this path uses the Chat Completions API.
        try:
            aid = str(getattr(self, "agent_id", "") or "").strip()
            nm = str(getattr(self, "name", "") or "").strip() or "Agent"
            print(f"[DEBUG Agent] Runner: chat_completions | agent={nm}{(' ('+aid+')') if aid else ''}")
            ins = str(getattr(self, "instructions", "") or "")
            ins_one = " ".join(ins.split())
            if ins_one:
                print(f"[DEBUG Agent] Instructions (first 100): {ins_one[:100]}{'...' if len(ins_one) > 100 else ''}")
        except Exception:
            pass

        # Provider request: translate canonical history into Chat Completions messages.
        canonical_history = (input_messages or []) + (self.session_items_during_run or [])
        chat_messages = _canonical_items_to_chat_messages(canonical_history, self.instructions)

        create_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            # Force streaming: the app/UI event contract is stream-first.
            "stream": True,
            "temperature": temperature,
        }

        # Provider compatibility profile (Claude OpenAI-SDK compatibility layer has known limitations).
        # We keep the agent core agnostic by only branching on the declared model name.
        m0 = str(model or "").strip().lower()
        is_claude = m0.startswith("claude")

        if is_claude:
            # Anthropic docs: OpenAI-SDK compat is meant for evaluation; prompt caching not supported.
            # Many OpenAI-specific knobs are ignored or unsupported.
            # We still try usage-in-stream via stream_options.include_usage (if ignored, no harm).
            try:
                print("[DEBUG Agent] Provider profile: claude_openai_sdk_compat (drop prompt_cache_key, reasoning_effort, verbosity; keep stream_options include_usage)")
            except Exception:
                pass
        else:
            try:
                print("[DEBUG Agent] Provider profile: openai_chat_completions (enable prompt_cache_key, reasoning_effort, verbosity, stream_options include_usage)")
            except Exception:
                pass

        # Prompt caching key
        if not is_claude:
            try:
                if isinstance(getattr(self, "user_id", None), str) and str(self.user_id).strip():
                    create_kwargs["prompt_cache_key"] = str(self.user_id).strip()
            except Exception:
                pass

        # Reasoning + verbosity
        if not is_claude:
            try:
                eff = None
                if isinstance(getattr(self.config, "reasoning", None), dict):
                    eff = self.config.reasoning.get("effort")
                if isinstance(eff, str) and eff.strip():
                    create_kwargs["reasoning_effort"] = eff.strip()
            except Exception:
                pass

            try:
                vb = None
                if isinstance(getattr(self.config, "text", None), dict):
                    vb = self.config.text.get("verbosity")
                if isinstance(vb, str) and vb.strip():
                    create_kwargs["verbosity"] = vb.strip()
            except Exception:
                pass

        # Tools
        if self.tool_schemas:
            try:
                # Chat Completions only supports function tools. Drop Responses-only tool types
                # (e.g., web_search/image_generation) for compatibility.
                fn_tools = []
                for s in (self.tool_schemas or []):
                    if isinstance(s, dict) and s.get("type") != "function":
                        continue
                    fn_tools.append(_adapt_tool_schema_for_chat(s))
                if fn_tools:
                    create_kwargs["tools"] = fn_tools
                    create_kwargs["tool_choice"] = self.config.tool_choice
            except Exception:
                pass

        # Best-effort usage in stream: request usage tokens in the streaming response.
        # OpenAI supports this; some gateways may ignore it.
        create_kwargs_with_usage = dict(create_kwargs)
        create_kwargs_with_usage["stream_options"] = {"include_usage": True}

        # Debug print request params (similar style to Responses runner).
        try:
            def _pv(k: str, v: Any) -> str:
                if k == "messages" and isinstance(v, list):
                    return f"<{len(v)} messages>"
                if k == "tools" and isinstance(v, list):
                    return f"<{len(v)} tools>"
                if isinstance(v, str):
                    if len(v) > 100:
                        return f"{v[:100]}... (truncated, total length {len(v)})"
                    return v
                if isinstance(v, (list, dict)):
                    # Avoid dumping huge histories; show a short summary.
                    try:
                        s = json.dumps(v)
                        return f"{s[:100]}{'...' if len(s) > 100 else ''}"
                    except Exception:
                        return f"<{type(v).__name__}>"
                return str(v)

            for k, v in create_kwargs_with_usage.items():
                print(f"[DEBUG Agent] Request param: {k} = {_pv(str(k), v)}")
        except Exception:
            pass

        try:
            print(f"[DEBUG Agent] Input messages count: {len(chat_messages)}")
        except Exception:
            pass

        # Debug: count image parts in outgoing messages.
        try:
            img_parts = 0
            for m in (chat_messages or []):
                c = m.get("content") if isinstance(m, dict) else None
                if isinstance(c, list):
                    for p in c:
                        if isinstance(p, dict) and str(p.get("type") or "") == "image_url":
                            img_parts += 1
            print(f"[DEBUG Agent] Image parts in request: {img_parts}")

            # If images are present, print a tiny structural preview of the most recent user message
            # so we can verify we are sending the correct Chat Completions schema:
            # {"type":"image_url","image_url":{"url":"data:image/..."}}
            if img_parts:
                try:
                    last_user = None
                    for mm in reversed(list(chat_messages or [])):
                        if isinstance(mm, dict) and mm.get("role") == "user":
                            last_user = mm
                            break
                    if isinstance(last_user, dict):
                        prev = {"role": "user", "content": []}
                        c = last_user.get("content")
                        if isinstance(c, list):
                            for p in c:
                                if not isinstance(p, dict):
                                    continue
                                if p.get("type") == "text":
                                    t = p.get("text")
                                    prev["content"].append({"type": "text", "text": (t[:80] + "...") if isinstance(t, str) and len(t) > 80 else t})
                                elif p.get("type") == "image_url":
                                    iu = p.get("image_url")
                                    if isinstance(iu, dict) and isinstance(iu.get("url"), str):
                                        u = iu.get("url")
                                        prev["content"].append({"type": "image_url", "image_url": {"url": (u[:60] + "...") if len(u) > 60 else u}})
                                    else:
                                        prev["content"].append({"type": "image_url", "image_url": iu})
                                else:
                                    prev["content"].append({"type": p.get("type")})
                        print(f"[DEBUG Agent] Image request preview (last user msg):\n{json.dumps(prev, indent=2)}")
                except Exception:
                    pass

            if is_claude and img_parts:
                print("[DEBUG Agent] NOTE: Claude OpenAI-SDK compatibility may ignore image inputs; use native Claude Messages API for vision.")
        except Exception:
            pass

        events = None
        try:
            # Claude compat: we already removed the unsupported knobs; still request usage-in-stream.
            events = self.client.chat.completions.create(**create_kwargs_with_usage)
        except TypeError:
            # SDK signature mismatch (older client). Retry without stream_options.
            events = self.client.chat.completions.create(**create_kwargs)
        except Exception as e:
            # Provider rejected an optional field (common with OpenAI-compatible gateways).
            # Fallback is still useful for random proxies, but we keep it narrow.
            msg = str(e).lower()
            if any(k in msg for k in ("prompt_cache_key", "reasoning_effort", "verbosity", "stream_options", "unknown", "unrecognized", "unexpected")):
                try:
                    print("[DEBUG Agent] Retry: minimal chat.completions kwargs")
                except Exception:
                    pass
                retry = dict(create_kwargs)
                retry.pop("prompt_cache_key", None)
                retry.pop("reasoning_effort", None)
                retry.pop("verbosity", None)
                retry.pop("stream_options", None)
                events = self.client.chat.completions.create(**retry)
            else:
                raise

        # Debug: confirm we got a stream iterator.
        try:
            print(f"[DEBUG Agent] Got events iterator: {type(events)}")
        except Exception:
            pass

        text_accum: List[str] = []
        tool_calls_by_index: Dict[int, Dict[str, Any]] = {}
        finish_reason = None
        last_usage = None

        debug_raw = str(os.environ.get("AI_AGENT_DEBUG_CHAT_RAW", "") or "").strip().lower() in ("1", "true", "yes", "y", "on")
        debug_max_chunks = None
        try:
            v = str(os.environ.get("AI_AGENT_DEBUG_CHAT_RAW_MAX_CHUNKS", "") or "").strip()
            if v:
                debug_max_chunks = int(v)
        except Exception:
            debug_max_chunks = None

        raw_chunk_i = 0
        last_chunk_dump = None

        for chunk in events:
            if self._stop_requested:
                break

            # Optional heavy debug: dump raw stream chunks.
            if debug_raw:
                try:
                    if debug_max_chunks is None or raw_chunk_i < int(debug_max_chunks):
                        dump = make_serializable(chunk)
                        last_chunk_dump = dump
                        print(f"[DEBUG Agent] RAW chat chunk #{raw_chunk_i}:\n{json.dumps(dump, indent=2)}")
                    raw_chunk_i += 1
                except Exception as e:
                    try:
                        print(f"[DEBUG Agent] RAW chat chunk dump failed: {e}")
                    except Exception:
                        pass

            # Chunk usage may appear only on the final chunk.
            try:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    last_usage = u
            except Exception:
                pass

            choices = getattr(chunk, "choices", None)
            if not choices:
                continue

            choice0 = choices[0]
            try:
                fr = getattr(choice0, "finish_reason", None)
                if fr is not None:
                    finish_reason = fr
            except Exception:
                pass

            delta = getattr(choice0, "delta", None)
            if delta is None:
                continue

            # Text delta
            d_content = getattr(delta, "content", None)
            if isinstance(d_content, str) and d_content:
                text_accum.append(d_content)
                yield {
                    "type": "response.output_text.delta",
                    "agent_name": self.name,
                    "content": {"delta": d_content},
                }

            # Tool call deltas
            d_tool_calls = getattr(delta, "tool_calls", None)
            if isinstance(d_tool_calls, list) and d_tool_calls:
                for tc in d_tool_calls:
                    try:
                        idx = getattr(tc, "index", None)
                        idx_i = int(idx) if idx is not None else 0
                    except Exception:
                        idx_i = 0

                    st = tool_calls_by_index.get(idx_i)
                    if not isinstance(st, dict):
                        st = {
                            "call_id": None,
                            "name": None,
                            "args": "",
                            "provider_id": None,
                        }

                    # id
                    try:
                        pid = getattr(tc, "id", None)
                        if isinstance(pid, str) and pid:
                            st["provider_id"] = pid
                            st["call_id"] = pid
                    except Exception:
                        pass

                    # function fields
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        try:
                            nm = getattr(fn, "name", None)
                            if isinstance(nm, str) and nm:
                                st["name"] = nm
                        except Exception:
                            pass
                        try:
                            argd = getattr(fn, "arguments", None)
                            if isinstance(argd, str) and argd:
                                st["args"] = str(st.get("args") or "") + argd
                        except Exception:
                            pass

                    tool_calls_by_index[idx_i] = st

        # Optional heavy debug: dump the last chunk we saw and (if available) the final response object.
        if debug_raw:
            try:
                print(f"[DEBUG Agent] RAW chat stream ended. chunks_seen={raw_chunk_i}")
            except Exception:
                pass
            try:
                if last_chunk_dump is not None:
                    print(f"[DEBUG Agent] RAW last chat chunk:\n{json.dumps(last_chunk_dump, indent=2)}")
            except Exception:
                pass
            try:
                final_resp = None
                if hasattr(events, "get_final_response"):
                    final_resp = events.get_final_response()
                elif hasattr(events, "response"):
                    final_resp = getattr(events, "response")
                if final_resp is not None:
                    print(f"[DEBUG Agent] RAW final chat response object:\n{json.dumps(make_serializable(final_resp), indent=2)}")
            except Exception as e:
                try:
                    print(f"[DEBUG Agent] RAW final chat response object unavailable: {e}")
                except Exception:
                    pass

        # If stop requested mid-stream, do not force-complete partial tool calls.
        if self._stop_requested:
            return

        # Token usage (best-effort)
        try:
            if last_usage is not None:
                # Support both SDK objects and dicts.
                def _g(obj, key, default=0):
                    try:
                        if isinstance(obj, dict):
                            return obj.get(key, default)
                        return getattr(obj, key, default)
                    except Exception:
                        return default

                prompt_details = _g(last_usage, "prompt_tokens_details", None)
                completion_details = _g(last_usage, "completion_tokens_details", None)

                cached_tokens = int(_g(prompt_details, "cached_tokens", 0) or 0) if prompt_details is not None else 0
                reasoning_tokens = int(_g(completion_details, "reasoning_tokens", 0) or 0) if completion_details is not None else 0

                self.token_usage = {
                    "turn": self.turn,
                    "input_tokens": int(_g(last_usage, "prompt_tokens", 0) or 0),
                    "cached_tokens": cached_tokens,
                    "output_tokens": int(_g(last_usage, "completion_tokens", 0) or 0),
                    "reasoning_tokens": reasoning_tokens,
                    "total_tokens": int(_g(last_usage, "total_tokens", 0) or 0),
                }
                self.token_usage_history[self.turn] = dict(self.token_usage)
        except Exception:
            pass

        # Finalize: tool calls vs assistant text
        full_text = "".join(text_accum)

        # If we got tool calls (or finish_reason says tool_calls), execute tools.
        if tool_calls_by_index:
            # Persist any assistant text that preceded tool calls.
            if isinstance(full_text, str) and full_text.strip():
                self.session_items_during_run.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": full_text}],
                    }
                )
                yield {
                    "type": "response.output_text.done",
                    "agent_name": self.name,
                    "content": {"text": full_text},
                }

            # Execute each tool call in index order.
            for idx in sorted(tool_calls_by_index.keys()):
                st = tool_calls_by_index.get(idx) or {}
                call_id = str(st.get("call_id") or "")
                if not call_id:
                    call_id = f"call_{uuid.uuid4().hex}"
                func_name = str(st.get("name") or "")
                func_args_str = str(st.get("args") or "{}")

                # Emit a tool-call boundary event for UI.
                yield {
                    "type": "response.output_item.done",
                    "agent_name": self.name,
                    "content": {
                        "item": {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": func_name,
                            "arguments": func_args_str,
                        }
                    },
                }

                # Execute the tool (same semantics as the Responses agent).
                self.function_call_detected = True

                # Parse args + execute tool fail-closed (match Responses runner semantics).
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}

                    result = None
                    for tool in self.tools:
                        tool_name = tool.schema.get("name") if isinstance(tool.schema, dict) else getattr(tool.schema, "name", None)
                        if tool_name == func_name:
                            # Internal-only call context for tools that need to stream UI events
                            # or attach subhistory links (e.g., consult_ariane).
                            try:
                                if str(func_name) in ("consult_ariane", "run_subagent"):
                                    func_args["_call_id"] = str(call_id or "")
                                    func_args["_parent_stream_topic"] = getattr(self, "_active_stream_topic", None)
                                    func_args["_parent_run_id"] = getattr(self, "_active_run_id", None)
                                    func_args["_parent_session_id"] = getattr(self, "_active_session_id", None)
                            except Exception:
                                pass

                            result = tool.run(**func_args)
                            break

                    if result is None:
                        result = {"error": f"Tool '{func_name}' not found"}
                except Exception as e:
                    result = {"error": f"Tool execution error: {str(e)}"}

                # Wrapper-only meta
                wrap_meta = None
                if isinstance(result, dict) and "__wrap_meta__" in result:
                    try:
                        wrap_meta = result.pop("__wrap_meta__")
                    except Exception:
                        wrap_meta = None

                if wrap_meta is None and isinstance(result, list):
                    try:
                        idx_to_drop = None
                        for i2, it in enumerate(result):
                            if isinstance(it, dict) and "__wrap_meta__" in it:
                                wrap_meta = it.pop("__wrap_meta__")
                                if len(it.keys()) == 0:
                                    idx_to_drop = i2
                                break
                        if idx_to_drop is not None:
                            try:
                                result.pop(idx_to_drop)
                            except Exception:
                                pass
                    except Exception:
                        wrap_meta = None

                # Injected message
                inject_message = None
                if isinstance(result, dict) and "__inject_message__" in result:
                    try:
                        inject_message = result.pop("__inject_message__")
                    except Exception:
                        inject_message = None

                if str(func_name) == "inner_loop" and isinstance(result, dict) and "error" not in result:
                    result = {"status": "success"}

                if isinstance(wrap_meta, dict) and call_id:
                    self.wrap_meta_by_call_id[str(call_id)] = make_serializable(wrap_meta)

                # Telemetry: remember what happened this turn.
                try:
                    if call_id:
                        self._auto_telemetry_tool_call_ids_this_turn.append(str(call_id))
                    if isinstance(wrap_meta, dict):
                        su = wrap_meta.get("subagent_usage")
                        if isinstance(su, dict):
                            self._auto_telemetry_subagents_this_turn.append(make_serializable(su))
                except Exception:
                    pass

                # Emit tool output UI event.
                try:
                    payload = {
                        "call_id": call_id,
                        "name": func_name,
                        "arguments": func_args_str,
                        "output": make_serializable(result),
                    }
                    if isinstance(wrap_meta, dict):
                        payload["wrap_meta"] = make_serializable(wrap_meta)
                    yield {
                        "type": "response.tool_output",
                        "agent_name": self.name,
                        "content": payload,
                    }
                except Exception:
                    pass

                # Persist canonical items to the run transcript.
                self.session_items_during_run.append(
                    {"type": "function_call", "call_id": call_id, "name": func_name, "arguments": func_args_str}
                )
                self.session_items_during_run.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(make_serializable(result)),
                    }
                )

                if inject_message is not None:
                    try:
                        self.session_items_during_run.append(make_serializable(inject_message))
                    except Exception:
                        pass

                    # Wrapper-only meta for injected messages + live UI injection event.
                    try:
                        inj_role = str(inject_message.get("role") or "").lower() if isinstance(inject_message, dict) else ""
                        inj_types = []
                        try:
                            for p in (inject_message.get("content") or []):
                                if isinstance(p, dict) and isinstance(p.get("type"), str):
                                    inj_types.append(p.get("type"))
                        except Exception:
                            inj_types = []

                        inj_meta = {
                            "injected": True,
                            "origin_tool_call_id": (str(call_id) if call_id else None),
                            "origin_tool_name": str(func_name),
                            "injected_role": inj_role,
                            "content_types": inj_types,
                        }

                        inj_idx = len(self.session_items_during_run) - 1
                        self.wrap_meta_by_item_index[int(inj_idx)] = make_serializable(inj_meta)

                        if inj_role == "user":
                            yield {
                                "type": "response.injected_message",
                                "agent_name": self.name,
                                "content": {
                                    "origin_call_id": (str(call_id) if call_id else None),
                                    "origin_tool_name": str(func_name),
                                    "message": make_serializable(inject_message),
                                    "wrap_meta": make_serializable(inj_meta),
                                },
                            }

                        # Parity with Responses runner: stream assistant-role injections live too.
                        if inj_role == "assistant":
                            try:
                                txt_parts = []
                                for p in (inject_message.get("content") or []):
                                    if isinstance(p, dict) and isinstance(p.get("text"), str):
                                        txt_parts.append(p.get("text"))
                                injected_text = "".join(txt_parts).strip()
                                if injected_text:
                                    yield {
                                        "type": "response.content_part.added",
                                        "agent_name": self.name,
                                        "content": {},
                                    }
                                    yield {
                                        "type": "response.output_text.delta",
                                        "agent_name": self.name,
                                        "content": {"delta": injected_text},
                                    }
                                    yield {
                                        "type": "response.output_text.done",
                                        "agent_name": self.name,
                                        "content": {"text": injected_text},
                                    }
                            except Exception:
                                pass
                    except Exception:
                        pass

            # Auto-inject telemetry after tool turns (POC: blunt, full payload).
            try:
                if bool(getattr(self, "_auto_telemetry_inject_enabled", False)) and bool(self.function_call_detected):
                    inj_msg, inj_meta, ui_ev = self._build_turn_telemetry_injection()
                    if isinstance(inj_msg, dict):
                        self.session_items_during_run.append(make_serializable(inj_msg))
                        inj_idx = len(self.session_items_during_run) - 1
                        if isinstance(inj_meta, dict):
                            self.wrap_meta_by_item_index[int(inj_idx)] = make_serializable(inj_meta)
                        if isinstance(ui_ev, dict):
                            yield ui_ev
            except Exception:
                pass

            # Usage event (if any)
            if self.token_usage:
                yield {"type": "response.usage", "agent_name": self.name, "content": self.token_usage}

            return

        # No tool calls: finalize assistant message.
        if isinstance(full_text, str) and full_text.strip():
            self.session_items_during_run.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": full_text}],
                }
            )
            yield {
                "type": "response.output_text.done",
                "agent_name": self.name,
                "content": {"text": full_text},
            }

        if self.token_usage:
            yield {"type": "response.usage", "agent_name": self.name, "content": self.token_usage}
