"""Filesystem tools for the agent."""


import base64
import json
from io import BytesIO
import os
import re
import shutil
import fnmatch
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from ...appcore.runtime_context import Runtime
from ...appcore.run_context import get_run_context
from ...storage.transactions_manager import TransactionsManager


from ...storage.fs_revisions import FsRevisionStore, FsRevisionError
from PIL import Image, UnidentifiedImageError
from ...storage.fs_diff import compute_transaction_diff_preview


def _resolve_scope_root(project_root: str, scope: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a filesystem root by scope.

    Returns (root_path, error_message). If error_message is not None, root_path is None.

    scope:
      - None / '' / 'project' -> project_root
      - 'sandbox' -> app-data Sandbox root (created on first use)

    NOTE: sandbox root is resolved via appcore Runtime PathsManager.
    """
    sc = (scope or "project").strip().lower()
    if sc in ("", "project"):
        return str(project_root), None
    if sc == "sandbox":
        try:
            return str(Runtime.get_paths().get_sandbox_root(ensure_exists=True)), None
        except Exception as e:
            return None, f"Sandbox unavailable: {e}"
    return None, "Invalid scope (expected 'project' or 'sandbox')"


def _scope_schema_prop() -> Dict[str, Any]:
    return {
        "type": "string",
        "enum": ["project", "sandbox"],
        "description": (
            "Filesystem scope (required). Must be 'project' or 'sandbox'. "
            "Do not omit; defaulting is not allowed."
        ),
    }


def _is_safe_path(root: str, path: str) -> bool:
    """Return True if `path` stays within `root` after resolving.

    This is defensive against common path-prefix tricks and symlink escapes.
    """
    try:
        root_p = Path(root).resolve()

        # Allow non-existent targets (writes/creates) while still resolving any
        # existing parents/symlinks.
        try:
            target_p = (root_p / path).resolve(strict=False)
        except TypeError:
            # Older Python: resolve() may not accept strict.
            target_p = (root_p / path).resolve()

        root_s = os.path.normcase(str(root_p))
        target_s = os.path.normcase(str(target_p))

        return os.path.commonpath([root_s, target_s]) == root_s
    except Exception:
        return False


class ReadFolderTool:
    """Tool to read folder contents."""

    def __init__(self):
        self.schema = {
            "type": "function",
            "name": "read_folder",
            "description": "List contents of one or more directories relative to the selected scope root.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of paths relative to the selected scope root. Use '.' for root.",
                    },
                    "scope": _scope_schema_prop(),
                    "survive": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                    },
                },
                "required": ["relative_path", "scope", "survive"],
                "additionalProperties": False,
            },
        }

    def run(self, relative_path, scope: Optional[str] = None, survive: Optional[bool] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)

        # Accept a list of paths.
        if not isinstance(relative_path, list):
            return {"status": "error", "message": "relative_path must be a list of strings"}

        rel_paths_raw: List[Any] = list(relative_path)
        if not rel_paths_raw:
            return {"status": "error", "message": "relative_path must be non-empty"}

        results: List[Dict[str, Any]] = []
        ok = 0
        bad = 0

        for rp in rel_paths_raw:
            if not isinstance(rp, str):
                results.append({"relative_path": rp, "status": "error", "message": "relative_path entries must be strings"})
                bad += 1
                continue

            rp2 = rp.strip()
            if not rp2:
                results.append({"relative_path": rp, "status": "error", "message": "Empty path"})
                bad += 1
                continue

            if not _is_safe_path(root, rp2):
                results.append({"relative_path": rp2, "status": "error", "message": "Path outside scope"})
                bad += 1
                continue

            full_path = os.path.join(root, rp2)
            if not os.path.exists(full_path):
                results.append({"relative_path": rp2, "status": "error", "message": "Folder not found"})
                bad += 1
                continue

            if not os.path.isdir(full_path):
                results.append({"relative_path": rp2, "status": "error", "message": "Not a directory"})
                bad += 1
                continue

            try:
                items = os.listdir(full_path)
                results.append({"relative_path": rp2, "status": "success", "items": items})
                ok += 1
            except Exception as e:
                results.append({"relative_path": rp2, "status": "error", "message": str(e)})
                bad += 1

        out = {
            "status": "success" if ok > 0 else "error",
            "results": results,
            "count": len(results),
            "success_count": ok,
            "error_count": bad,
            "partial": (ok > 0 and bad > 0),
        }
        if survive is False:
            out["__wrap_meta__"] = {"survive": False}
        return out


class ReadFileTool:
    """Tool to read file contents."""

    # Hard guardrails against accidental context nukes.
    _MAX_TOTAL_CHARS = 200_000
    _MAX_FILE_CHARS = 100_000

    def __init__(self):
        self.schema = {
            "type": "function",
            "name": "read_file",
            "description": (
                "Read contents of one or more files. Optionally specify line ranges. "
                "If multiple files are requested, returns per-file results (partial success allowed)."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "requests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "relative_path": {"type": "string", "description": "File path relative to the selected scope root."},
                                "start_line": {
                                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                                    "description": "Start line (1-based, optional). Use null to read from start.",
                                },
                                "end_line": {
                                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                                    "description": "End line (1-based, inclusive, optional). Use null to read to end.",
                                },
                            },
                            "required": ["relative_path", "start_line", "end_line"],
                            "additionalProperties": False,
                        },
                        "description": "Per-file requests (1 or more). You may repeat the same relative_path to read multiple slices from the same file.",
                    },
                    "scope": _scope_schema_prop(),
                    "survive": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                    },
                },
                "required": ["requests", "scope", "survive"],
                "additionalProperties": False,
            },
        }

    def run(
        self,
        requests: List[Dict[str, Any]],
        scope: Optional[str] = None,
        survive: Optional[bool] = None,
    ) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)

        # Build per-file tasks.
        tasks: List[Dict[str, Any]] = []
        pre_errors: List[Dict[str, Any]] = []

        if not isinstance(requests, list) or not requests:
            return {"status": "error", "message": "requests must be a non-empty list"}

        for req in requests:
            if not isinstance(req, dict):
                pre_errors.append({"relative_path": req, "status": "error", "message": "Invalid request (expected object)"})
                continue
            tasks.append(
                {
                    "relative_path": req.get("relative_path"),
                    "start_line": req.get("start_line"),
                    "end_line": req.get("end_line"),
                }
            )

        results: List[Dict[str, Any]] = []
        ok = 0
        bad = 0

        # Track total returned chars across ALL files.
        remaining_total = int(self._MAX_TOTAL_CHARS)

        # Pre-errors count as errors.
        for pe in pre_errors:
            results.append(pe)
            bad += 1

        for t in tasks:
            rp = t.get("relative_path")
            sl = t.get("start_line")
            el = t.get("end_line")

            if not isinstance(rp, str):
                results.append({"relative_path": rp, "status": "error", "message": "relative_path must be a string"})
                bad += 1
                continue

            rp2 = rp.strip()
            if not rp2:
                results.append({"relative_path": rp, "status": "error", "message": "Empty path"})
                bad += 1
                continue

            if not _is_safe_path(root, rp2):
                results.append({"relative_path": rp2, "status": "error", "message": "Path outside scope"})
                bad += 1
                continue

            full_path = os.path.join(root, rp2)
            if not os.path.isfile(full_path):
                results.append({"relative_path": rp2, "status": "error", "message": "File not found"})
                bad += 1
                continue

            # Parse start/end.
            try:
                si = 1 if sl is None else int(sl)
                ei = None if el is None else int(el)
                if si < 1:
                    raise ValueError("start_line must be >= 1")
                if ei is not None and ei < 1:
                    raise ValueError("end_line must be >= 1")
                if ei is not None and ei < si:
                    raise ValueError("end_line must be >= start_line")
            except Exception:
                results.append({"relative_path": rp2, "status": "error", "message": "start_line/end_line must be integers >= 1 or null, and end_line must be >= start_line"})
                bad += 1
                continue

            # Read slice with output caps.
            per_file_cap = min(int(self._MAX_FILE_CHARS), int(remaining_total))
            if per_file_cap <= 0:
                results.append({"relative_path": rp2, "status": "error", "message": "Output cap reached; refusing to return more content"})
                bad += 1
                continue

            content_parts: List[str] = []
            returned_chars = 0
            truncated = False
            total_lines = 0
            collecting = True

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    for ln_no, line in enumerate(f, 1):
                        total_lines = int(ln_no)

                        if not collecting:
                            continue

                        # Skip before slice.
                        if ln_no < si:
                            continue

                        # Past slice end: stop collecting but keep scanning for total_lines.
                        if ei is not None and ln_no > ei:
                            collecting = False
                            continue

                        # Append with caps.
                        avail = per_file_cap - returned_chars
                        if avail <= 0:
                            truncated = True
                            collecting = False
                            continue

                        if len(line) > avail:
                            content_parts.append(line[:avail])
                            returned_chars += len(line[:avail])
                            truncated = True
                            collecting = False
                            continue

                        content_parts.append(line)
                        returned_chars += len(line)

                content = "".join(content_parts)

                remaining_total -= int(returned_chars)

                results.append(
                    {
                        "relative_path": rp2,
                        "status": "success",
                        "content": content,
                        "returned_chars": int(returned_chars),
                        "truncated": bool(truncated),
                        "total_lines": int(total_lines),
                        "slice": {"start_line": (None if sl is None else int(si)), "end_line": (None if ei is None else int(ei))},
                    }
                )
                ok += 1

            except Exception as e:
                results.append({"relative_path": rp2, "status": "error", "message": str(e)})
                bad += 1

        out = {
            "status": "success" if ok > 0 else "error",
            "results": results,
            "count": len(results),
            "success_count": ok,
            "error_count": bad,
            "partial": (ok > 0 and bad > 0),
        }
        if survive is False:
            out["__wrap_meta__"] = {"survive": False}
        return out




class ImagesGetTool:
    """Load one or more local images so you can see them (inject as input_image).

    This is the filesystem equivalent of canvas_get(inject=true): it keeps the tool output
    small, and uses __inject_message__ to attach the actual image payload.

    Security:
    - scope: project|sandbox
    - path-safe (must stay within scope root)
    - allow only PNG/JPEG/WEBP/non-animated GIF
    - max 10 images per call
    - max total injected base64 payload: 50MB
    - decompression-bomb guard: cap pixel count
    """

    _MAX_IMAGES_PER_CALL = 10
    _MAX_TOTAL_B64_BYTES = 50 * 1024 * 1024
    _MAX_PIXELS = 120_000_000  # 120 MP (defensive)

    _MIME_BY_FORMAT = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "JPG": "image/jpeg",
        "WEBP": "image/webp",
        "GIF": "image/gif",
    }

    def __init__(self):
        self.schema = {
            "type": "function",
            "name": "images_get",
            "description": (
                "Load one or more local image files so you can SEE them (injects as user-role input_image into the next model turn). "
                "Allowed formats: PNG, JPEG, WEBP, non-animated GIF. "
                "Security caps: max 10 images per call, total injected payload <= 50MB."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of image paths relative to the selected scope root.",
                    },
                    "scope": _scope_schema_prop(),
                    "caption": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional caption text to include alongside the injected images.",
                    },
                    "survive": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "If false, this call/output (and its injected image message) will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                    },
                },
                "required": ["paths", "scope", "caption", "survive"],
                "additionalProperties": False,
            },
        }

    def _b64_len(self, n_bytes: int) -> int:
        # base64 expands to 4 * ceil(n/3)
        try:
            n = int(n_bytes)
        except Exception:
            n = 0
        return 4 * ((max(0, n) + 2) // 3)

    def run(self, paths: List[str], scope: Optional[str] = None, caption: Optional[str] = None, survive: Optional[bool] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)

        if not isinstance(paths, list) or not paths:
            return {"status": "error", "message": "paths must be a non-empty list"}

        if len(paths) > self._MAX_IMAGES_PER_CALL:
            return {"status": "error", "message": f"Too many images (max {self._MAX_IMAGES_PER_CALL} per call)"}

        # Validate paths + preflight sizes.
        abs_paths: List[str] = []
        rel_paths: List[str] = []
        st_sizes: List[int] = []

        # Conservative preflight: if raw bytes exceed 3/4 of 50MB, base64 will exceed 50MB.
        raw_ceiling = int((self._MAX_TOTAL_B64_BYTES * 3) // 4)
        raw_sum = 0

        for p in paths:
            if not isinstance(p, str) or not p.strip():
                return {"status": "error", "message": "paths must contain only non-empty strings"}
            rp = p.strip()
            if not _is_safe_path(root, rp):
                return {"status": "error", "message": f"Path outside scope: {rp}"}
            ap = os.path.join(root, rp)
            if not os.path.isfile(ap):
                return {"status": "error", "message": f"File not found: {rp}"}

            try:
                st = os.stat(ap)
                sz = int(st.st_size)
            except Exception:
                return {"status": "error", "message": f"Could not stat file: {rp}"}

            raw_sum += max(0, sz)
            if raw_sum > raw_ceiling:
                return {"status": "error", "message": "Total image bytes too large (would exceed 50MB payload limit)"}

            rel_paths.append(rp)
            abs_paths.append(ap)
            st_sizes.append(sz)

        # Read + validate image formats, compute exact b64 size.
        items = []
        total_b64 = 0

        # Pillow decompression-bomb guard.
        try:
            Image.MAX_IMAGE_PIXELS = int(self._MAX_PIXELS)
        except Exception:
            pass

        for rp, ap, sz in zip(rel_paths, abs_paths, st_sizes):
            # Small per-image overhead for the data URL prefix.
            # We'll compute the exact prefix after we know MIME.
            try:
                raw = open(ap, "rb").read()
            except Exception as e:
                return {"status": "error", "message": f"Failed to read {rp}: {e}"}

            # Validate image.
            try:
                img = Image.open(BytesIO(raw))
                fmt = (img.format or "").upper().strip()

                # Reject animated images (GIF/WEBP/APNG, etc.)
                try:
                    if bool(getattr(img, "is_animated", False)):
                        return {"status": "error", "message": f"Animated images are not allowed: {rp}"}
                    if int(getattr(img, "n_frames", 1) or 1) > 1:
                        return {"status": "error", "message": f"Animated images are not allowed: {rp}"}
                except Exception:
                    pass

                try:
                    w, h = img.size
                    if int(w) * int(h) > int(self._MAX_PIXELS):
                        return {"status": "error", "message": f"Image too large (pixel count cap): {rp}"}
                except Exception:
                    pass

            except UnidentifiedImageError:
                return {"status": "error", "message": f"Not a supported image: {rp}"}
            except Exception as e:
                return {"status": "error", "message": f"Failed to parse image {rp}: {e}"}

            if fmt not in self._MIME_BY_FORMAT:
                return {"status": "error", "message": f"Unsupported image format ({fmt or 'unknown'}): {rp}"}

            mime = self._MIME_BY_FORMAT[fmt]
            prefix = f"data:{mime};base64,"
            b64_len = self._b64_len(len(raw))

            # Enforce exact base64 cap.
            total_b64 += (len(prefix) + b64_len)
            if total_b64 > self._MAX_TOTAL_B64_BYTES:
                return {"status": "error", "message": "Total injected payload exceeds 50MB limit"}

            b64 = base64.b64encode(raw).decode("utf-8")
            items.append({"rel_path": rp, "mime": mime, "b64": b64})

        # Build injected message.
        cap = (caption or "").strip()
        header_lines = []
        if cap:
            header_lines.append(cap)
        header_lines.append("Injected images:")
        for rp in rel_paths:
            header_lines.append(f"- {rp}")

        injected_content: List[Dict[str, Any]] = [{"type": "input_text", "text": "\n".join(header_lines)}]
        for it in items:
            injected_content.append({"type": "input_image", "image_url": f"data:{it['mime']};base64,{it['b64']}"})

        out = {
            "status": "success",
            "count": len(items),
            "total_payload_bytes": int(total_b64),
            "__inject_message__": {
                "role": "user",
                "content": injected_content,
            },
        }
        if survive is False:
            out["__wrap_meta__"] = {"survive": False}
        return out


class WriteFileTool:
    """Tool to write file contents."""
    
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "write_file",
            "description": "Write content to a file (creates/overwrites; append=true appends). Normalizes line endings to avoid CRLF-doubling.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "File path relative to the selected scope root.",
                    },
                    "scope": _scope_schema_prop(),
                    "content": {
                        "type": "string",
                        "description": "Content to write.",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "Append to file if true, overwrite if false (default: false).",
                    },
                },
                "required": ["relative_path", "scope", "content", "append"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str, content: str, append: bool = False, scope: Optional[str] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        if not _is_safe_path(str(root), relative_path):
            return {"status": "error", "message": "Path outside scope"}
        
        full_path = os.path.join(str(root), relative_path)
        
        txn_id: Optional[str] = None
        before: Optional[Dict[str, Any]] = None
        manifest: Optional[Dict[str, Any]] = None
        try:
            if self.revision_store:
                txn_id = self.revision_store.begin_transaction(
                    "write_file",
                    {"relative_path": relative_path, "append": append, "content_len": len(content), "scope": (scope or "project")},
                )
                before = self.revision_store.snapshot_path(str(root), relative_path)
                if before is None:
                    raise FsRevisionError("Missing pre-snapshot; refusing to execute")
                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "write_file",
                    "args": {"relative_path": relative_path, "append": append, "content_len": len(content), "scope": (scope or "project")},
                    "status": "prepared",
                    "changes": [
                        {"op": "write", "before": before},
                    ],
                }
                # Non-negotiable: if this fails, we do NOT write.
                self.revision_store.commit_transaction(txn_id, manifest)


            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            mode = "a" if append else "w"

            # Avoid CRLF-doubling on Windows when caller passes explicit "\r\n".
            # In text mode with newline translation, writing "\r\n" can become "\r\r\n".
            # Normalize all line breaks to "\n" first, then let Python translate "\n" to the OS newline.
            content_to_write = content
            try:
                content_to_write = str(content).replace("\r\n", "\n").replace("\r", "\n")
            except Exception:
                content_to_write = content

            with open(full_path, mode, encoding="utf-8") as f:
                f.write(content_to_write)

            if self.revision_store and txn_id and manifest:
                after = self.revision_store.snapshot_path(str(root), relative_path)
                manifest["status"] = "applied"
                # Single-change manifest for write_file.
                if manifest.get("changes") and isinstance(manifest["changes"], list):
                    try:
                        manifest["changes"][0]["after"] = after
                    except Exception:
                        pass
                try:
                    self.revision_store.commit_transaction(txn_id, manifest)
                except Exception:
                    pass

            out = {"status": "success", "message": f"Written to {relative_path}", "transaction_id": txn_id}

            # Provide line count after write (best-effort, useful for later line-range ops).
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    out["total_lines"] = int(sum(1 for _ in f))
            except Exception:
                pass

            # Diff preview for UI (best-effort).
            if self.revision_store and txn_id:
                try:
                    prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                    if isinstance(prev, dict) and prev.get("status") == "success":
                        out["__wrap_meta__"] = {"diff_preview": prev}
                except Exception:
                    pass
            return out

        except FsRevisionError as e:
            return {"status": "error", "message": f"Revision snapshot failed; write aborted: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CreateFolderTool:
    """Tool to create folders."""
    
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "create_folder",
            "description": "Create a directory (and parents if needed).",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "Directory path relative to the selected scope root.",
                    },
                    "scope": _scope_schema_prop(),
                },
                "required": ["relative_path", "scope"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str, scope: Optional[str] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        if not _is_safe_path(str(root), relative_path):
            return {"status": "error", "message": "Path outside scope"}
        
        full_path = os.path.join(str(root), relative_path)
        
        txn_id: Optional[str] = None
        before: Optional[Dict[str, Any]] = None
        manifest: Optional[Dict[str, Any]] = None

        try:
            if self.revision_store:
                txn_id = self.revision_store.begin_transaction(
                    "create_folder",
                    {"relative_path": relative_path, "scope": (scope or "project")},
                )
                before = self.revision_store.snapshot_path(str(root), relative_path)
                if before is None:
                    raise FsRevisionError("Missing pre-snapshot; refusing to execute")
                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "create_folder",
                    "args": {"relative_path": relative_path, "scope": (scope or "project")},
                    "status": "prepared",
                    "changes": [
                        {"op": "mkdir", "before": before},
                    ],
                }
                # Non-negotiable: if this fails, we do NOT create.
                self.revision_store.commit_transaction(txn_id, manifest)

            os.makedirs(full_path, exist_ok=True)

            if self.revision_store and txn_id and manifest:
                after = self.revision_store.snapshot_path(str(root), relative_path)
                manifest["status"] = "applied"
                if manifest.get("changes") and isinstance(manifest["changes"], list):
                    try:
                        manifest["changes"][0]["after"] = after
                    except Exception:
                        pass
                try:
                    self.revision_store.commit_transaction(txn_id, manifest)
                except Exception:
                    pass

            out = {"status": "success", "message": f"Created {relative_path}", "transaction_id": txn_id}
            if self.revision_store and txn_id:
                try:
                    prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                    if isinstance(prev, dict) and prev.get("status") == "success":
                        out["__wrap_meta__"] = {"diff_preview": prev}
                except Exception:
                    pass
            return out

        except FsRevisionError as e:
            return {"status": "error", "message": f"Revision snapshot failed; create_folder aborted: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class DeletePathsTool:
    """Tool to delete files or folders."""
    
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "delete_paths",
            "description": "Delete files or directories.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of paths relative to the selected scope root.",
                    },
                    "scope": _scope_schema_prop(),
                },
                "required": ["paths", "scope"],
                "additionalProperties": False,
            },
        }
    
    def run(self, paths: List[str], scope: Optional[str] = None) -> List[Dict[str, Any]]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------
        
        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return [{"path": p, "status": "error", "message": str(err)} for p in (paths or [])]
        root = str(root)

        # Keep output order stable.
        results: List[Optional[Dict[str, Any]]] = [None] * len(paths)
        deletable: List[Tuple[int, str]] = []  # (index, relative_path)

        for i, path in enumerate(paths):
            if not _is_safe_path(root, path):
                results[i] = {"path": path, "status": "error", "message": "Outside scope"}
                continue

            full_path = os.path.join(root, path)
            if not os.path.exists(full_path):
                results[i] = {"path": path, "status": "error", "message": "Not found"}
                continue

            deletable.append((i, path))

        txn_id: Optional[str] = None
        changes: List[Dict[str, Any]] = []

        # Snapshot FIRST (non-negotiable). If this fails, nothing gets deleted.
        manifest: Optional[Dict[str, Any]] = None
        if self.revision_store and deletable:
            try:
                txn_id = self.revision_store.begin_transaction(
                    "delete_paths",
                    {"paths": [p for _, p in deletable], "scope": (scope or "project")},
                )
                for _, p in deletable:
                    snap = self.revision_store.snapshot_path(root, p)
                    changes.append({"op": "delete", "before": snap})

                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "delete_paths",
                    "args": {"paths": [p for _, p in deletable], "scope": (scope or "project")},
                    "status": "prepared",
                    "changes": changes,
                }
                # Non-negotiable: if this fails, we do NOT delete.
                self.revision_store.commit_transaction(txn_id, manifest)

            except FsRevisionError as e:
                for i, p in deletable:
                    results[i] = {
                        "path": p,
                        "status": "error",
                        "message": f"Revision snapshot failed; delete aborted: {str(e)}",
                        "transaction_id": txn_id,
                    }

                out_list = [
                    r or {"path": paths[idx], "status": "error", "message": "Unknown"}
                    for idx, r in enumerate(results)
                ]
                # Diff preview for UI (best-effort).
                if self.revision_store and txn_id:
                    try:
                        prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                        if isinstance(prev, dict) and prev.get("status") == "success":
                            out_list.append({"__wrap_meta__": {"diff_preview": prev}})
                    except Exception:
                        pass
                return out_list

        # Execute deletions.
        for i, p in deletable:
            full_path = os.path.join(root, p)
            try:
                if os.path.isdir(full_path):
                    shutil.rmtree(full_path)
                else:
                    os.remove(full_path)
                results[i] = {"path": p, "status": "success", "transaction_id": txn_id}
            except Exception as e:
                results[i] = {"path": p, "status": "error", "message": str(e), "transaction_id": txn_id}

        # Mark applied.
        if self.revision_store and txn_id and manifest:
            try:
                # Capture post-state snapshots for diff receipts (best-effort).
                try:
                    for idx, (_, p) in enumerate(deletable):
                        if idx < len(changes):
                            try:
                                changes[idx]["after"] = self.revision_store.snapshot_path(root, p)
                            except Exception:
                                pass
                except Exception:
                    pass

                manifest["status"] = "applied"
                self.revision_store.commit_transaction(txn_id, manifest)
            except Exception:
                pass

        out_list = [
            r or {"path": paths[idx], "status": "error", "message": "Unknown"}
            for idx, r in enumerate(results)
        ]

        # Diff preview for UI (best-effort).
        if self.revision_store and txn_id:
            try:
                prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                if isinstance(prev, dict) and prev.get("status") == "success":
                    out_list.append({"__wrap_meta__": {"diff_preview": prev}})
            except Exception:
                pass

        return out_list


class InsertTextTool:
    """Tool to insert text at a specific location in a file."""
    
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "insert_text",
            "description": "Insert text at a specific line in a file.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "File path relative to the selected scope root."},
                    "scope": _scope_schema_prop(),
                    "line": {"type": "integer", "description": "Line number (1-based) to insert before."},
                    "text": {"type": "string", "description": "Text to insert."},
                },
                "required": ["relative_path", "scope", "line", "text"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str, line: int, text: str, scope: Optional[str] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        if not _is_safe_path(str(root), relative_path):
            return {"status": "error", "message": "Path outside scope"}

        full_path = os.path.join(str(root), relative_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": "File not found"}
        
        txn_id: Optional[str] = None
        before: Optional[Dict[str, Any]] = None
        manifest: Optional[Dict[str, Any]] = None
        try:
            if self.revision_store:
                txn_id = self.revision_store.begin_transaction(
                    "insert_text",
                    {"relative_path": relative_path, "line": line, "text_len": len(text), "scope": (scope or "project")},
                )
                before = self.revision_store.snapshot_path(str(root), relative_path)
                if before is None:
                    raise FsRevisionError("Missing pre-snapshot; refusing to execute")
                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "insert_text",
                    "args": {"relative_path": relative_path, "line": line, "text_len": len(text), "scope": (scope or "project")},
                    "status": "prepared",
                    "changes": [
                        {"op": "write", "before": before},
                    ],
                }
                self.revision_store.commit_transaction(txn_id, manifest)


            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            insert_idx = max(0, min(len(lines), line - 1))
            lines.insert(insert_idx, text if text.endswith("\n") else text + "\n")

            with open(full_path, "w", encoding="utf-8") as f:
                f.writelines(lines)

            if self.revision_store and txn_id and manifest:
                after = self.revision_store.snapshot_path(str(root), relative_path)
                manifest["status"] = "applied"
                if manifest.get("changes") and isinstance(manifest["changes"], list):
                    try:
                        manifest["changes"][0]["after"] = after
                    except Exception:
                        pass
                try:
                    self.revision_store.commit_transaction(txn_id, manifest)
                except Exception:
                    pass

            out = {"status": "success", "message": f"Inserted at line {line}", "transaction_id": txn_id}
            if self.revision_store and txn_id:
                try:
                    prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                    if isinstance(prev, dict) and prev.get("status") == "success":
                        out["__wrap_meta__"] = {"diff_preview": prev}
                except Exception:
                    pass
            return out

        except FsRevisionError as e:
            return {"status": "error", "message": f"Revision snapshot failed; insert aborted: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class ReplaceTextTool:
    """Tool to replace text in a file."""
    
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "replace_text",
            "description": "Replace occurrences of old_text with new_text in a file.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "File path relative to the selected scope root."},
                    "scope": _scope_schema_prop(),
                    "old_text": {"type": "string", "description": "Text to find."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                    "count": {"type": "integer", "description": "Max replacements (default: all)."},
                    "max_ranges": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "description": (
                            "Maximum number of replacement line ranges to return in the result. "
                            "Null uses a safe default cap (prevents huge tool outputs). "
                            "0 returns no ranges (just the count)."
                        ),
                    },
                },
                "required": ["relative_path", "scope", "old_text", "new_text", "count", "max_ranges"],
                "additionalProperties": False,
            },
        }
    
    def run(
        self,
        relative_path: str,
        old_text: str,
        new_text: str,
        count: Optional[int] = None,
        max_ranges: Optional[int] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)
        if not _is_safe_path(root, relative_path):
            return {"status": "error", "message": "Path outside scope"}
        
        full_path = os.path.join(root, relative_path)
        
        txn_id: Optional[str] = None
        before: Optional[Dict[str, Any]] = None
        manifest: Optional[Dict[str, Any]] = None
        try:
            if not isinstance(old_text, str) or old_text == "":
                return {"status": "error", "message": "old_text must be a non-empty string"}

            if not os.path.isfile(full_path):
                return {"status": "error", "message": "File not found"}

            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            # No-op: treat as error (prevents silent thrash / repeated attempts).
            if old_text not in content:
                return {"status": "error", "message": "No changes (text not found)", "replacements": 0}

            if count is not None:
                try:
                    count_i = int(count)
                except Exception:
                    count_i = None
            else:
                count_i = None

            # Guard: count=0 means do nothing.
            max_repl: Optional[int] = None
            if count_i is not None:
                max_repl = max(0, int(count_i))
                if max_repl == 0:
                    return {"status": "success", "message": "No changes", "replacements": 0}

            # Perform the replace ourselves so we can also report where replacements landed
            # (line ranges in the *post* image).
            new_text_s = str(new_text)
            out_parts: List[str] = []
            replacement_ranges: List[Dict[str, int]] = []
            replacement_ranges_truncated = False
            replacements_count = 0

            # Cap returned ranges (not the actual replacement) to keep tool output bounded.
            # null => safe default
            try:
                max_ranges_i = 200 if max_ranges is None else int(max_ranges)
            except Exception:
                return {"status": "error", "message": "max_ranges must be an integer or null"}
            if max_ranges_i < 0:
                return {"status": "error", "message": "max_ranges must be >= 0 (or null)"}

            i = 0
            out_line = 1  # 1-based
            while True:
                j = content.find(old_text, i)
                if j == -1 or (max_repl is not None and replacements_count >= max_repl):
                    out_parts.append(content[i:])
                    break

                # Emit pre-match segment.
                seg = content[i:j]
                out_parts.append(seg)
                out_line += seg.count("\n")

                # Replacement starts at the current output line.
                start_line = int(out_line)

                try:
                    span_lines = len(new_text_s.splitlines())
                except Exception:
                    span_lines = 1
                if span_lines <= 0:
                    span_lines = 1
                end_line = int(start_line + span_lines - 1)

                if max_ranges_i == 0:
                    # Caller asked for no ranges.
                    pass
                elif len(replacement_ranges) < max_ranges_i:
                    replacement_ranges.append({"start_line": start_line, "end_line": end_line})
                else:
                    replacement_ranges_truncated = True

                # Emit replacement.
                out_parts.append(new_text_s)
                out_line += new_text_s.count("\n")

                replacements_count += 1
                i = j + len(old_text)

            new_content = "".join(out_parts)

            # No-op cases: old_text==new_text, etc.
            if replacements_count == 0 or new_content == content:
                return {"status": "success", "message": "No changes", "replacements": 0}

            # Create a revision txn only when we will actually write.
            if self.revision_store:
                txn_id = self.revision_store.begin_transaction(
                    "replace_text",
                    {
                        "relative_path": relative_path,
                        "old_len": len(old_text),
                        "new_len": len(new_text),
                        "count": count,
                        "scope": (scope or "project"),
                    },
                )
                before = self.revision_store.snapshot_path(str(root), relative_path)
                if before is None:
                    raise FsRevisionError("Missing pre-snapshot; refusing to execute")
                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "replace_text",
                    "args": {
                        "relative_path": relative_path,
                        "old_len": len(old_text),
                        "new_len": len(new_text),
                        "count": count,
                        "scope": (scope or "project"),
                    },
                    "status": "prepared",
                    "changes": [
                        {"op": "write", "before": before},
                    ],
                }
                # Non-negotiable: if this fails, we do NOT write.
                self.revision_store.commit_transaction(txn_id, manifest)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            if self.revision_store and txn_id and manifest:
                after = self.revision_store.snapshot_path(str(root), relative_path)
                manifest["status"] = "applied"
                if manifest.get("changes") and isinstance(manifest["changes"], list):
                    try:
                        manifest["changes"][0]["after"] = after
                    except Exception:
                        pass
                try:
                    self.revision_store.commit_transaction(txn_id, manifest)
                except Exception:
                    pass

            out: Dict[str, Any] = {
                "status": "success",
                "message": "Text replaced",
                "transaction_id": txn_id,
                "replacements": int(replacements_count),
                "replacement_ranges": replacement_ranges,
                "replacement_ranges_truncated": bool(replacement_ranges_truncated),
            }
            if self.revision_store and txn_id:
                try:
                    prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                    if isinstance(prev, dict) and prev.get("status") == "success":
                        out["__wrap_meta__"] = {"diff_preview": prev}
                except Exception:
                    pass
            return out

        except FsRevisionError as e:
            return {"status": "error", "message": f"Revision snapshot failed; replace aborted: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class DeleteLinesTool:
    """Delete a line range from a text file (1-based, inclusive).

    This is a higher-level "cheap edit" tool to avoid token-heavy read/replace cycles.

    Notes:
    - Preserves original line endings (does not normalize CRLF/LF) by using newline="".
    - Refuses invalid ranges loudly (start < 1, end < start, end > total_lines, etc.).
    """

    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "delete_lines",
            "description": "Delete a range of lines from a file (1-based, inclusive).",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "File path relative to the selected scope root."},
                    "scope": _scope_schema_prop(),
                    "start_line": {"type": "integer", "description": "Start line (1-based, inclusive)."},
                    "end_line": {"type": "integer", "description": "End line (1-based, inclusive)."},
                },
                "required": ["relative_path", "scope", "start_line", "end_line"],
                "additionalProperties": False,
            },
        }

    def run(self, relative_path: str, start_line: int, end_line: int, scope: Optional[str] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)
        if not _is_safe_path(root, relative_path):
            return {"status": "error", "message": "Path outside scope"}

        full_path = os.path.join(root, relative_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": "File not found"}

        try:
            sl = int(start_line)
            el = int(end_line)
        except Exception:
            return {"status": "error", "message": "start_line and end_line must be integers"}

        if sl < 1:
            return {"status": "error", "message": "start_line must be >= 1"}
        if el < sl:
            return {"status": "error", "message": "end_line must be >= start_line"}

        txn_id: Optional[str] = None
        before: Optional[Dict[str, Any]] = None
        manifest: Optional[Dict[str, Any]] = None

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                lines = f.readlines()

            total_lines = len(lines)
            if sl > total_lines:
                return {"status": "error", "message": f"start_line ({sl}) exceeds total_lines ({total_lines})"}
            if el > total_lines:
                return {"status": "error", "message": f"end_line ({el}) exceeds total_lines ({total_lines})"}

            # Compute output.
            start_idx = sl - 1
            end_idx_excl = el
            new_lines = lines[:start_idx] + lines[end_idx_excl:]

            # If no change (should be impossible with strict validation), treat as no-op.
            if new_lines == lines:
                return {"status": "success", "message": "No changes", "deleted_lines": 0}

            if self.revision_store:
                txn_id = self.revision_store.begin_transaction(
                    "delete_lines",
                    {
                        "relative_path": relative_path,
                        "start_line": sl,
                        "end_line": el,
                        "scope": (scope or "project"),
                    },
                )
                before = self.revision_store.snapshot_path(root, relative_path)
                if before is None:
                    raise FsRevisionError("Missing pre-snapshot; refusing to execute")
                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "delete_lines",
                    "args": {
                        "relative_path": relative_path,
                        "start_line": sl,
                        "end_line": el,
                        "scope": (scope or "project"),
                    },
                    "status": "prepared",
                    "changes": [
                        {"op": "write", "before": before},
                    ],
                }
                # Non-negotiable: if this fails, we do NOT write.
                self.revision_store.commit_transaction(txn_id, manifest)

            with open(full_path, "w", encoding="utf-8", newline="") as f:
                f.writelines(new_lines)

            if self.revision_store and txn_id and manifest:
                after = self.revision_store.snapshot_path(root, relative_path)
                manifest["status"] = "applied"
                if manifest.get("changes") and isinstance(manifest["changes"], list):
                    try:
                        manifest["changes"][0]["after"] = after
                    except Exception:
                        pass
                try:
                    self.revision_store.commit_transaction(txn_id, manifest)
                except Exception:
                    pass

            out: Dict[str, Any] = {
                "status": "success",
                "message": f"Deleted lines {sl}-{el}",
                "deleted_lines": int(el - sl + 1),
                "transaction_id": txn_id,
            }
            if self.revision_store and txn_id:
                try:
                    prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                    if isinstance(prev, dict) and prev.get("status") == "success":
                        out["__wrap_meta__"] = {"diff_preview": prev}
                except Exception:
                    pass
            return out

        except FsRevisionError as e:
            return {"status": "error", "message": f"Revision snapshot failed; delete_lines aborted: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class TransferLinesTool:
    """Copy or move a line range from one file to another.

    This is the missing "cheap" primitive for cut/copy/paste by line numbers.

    Semantics:
    - `src_start_line`/`src_end_line` are 1-based inclusive.
    - `dst_insert_at_line` is the line number (1-based) to insert *before*.
      - null means append.
      - values outside [1..dst_total+1] are clamped.
    - If `src_path == dst_path` and `delete_from_source == True`, then
      `dst_insert_at_line` is interpreted in the original (pre-op) coordinate system.
      If the destination is inside the moved block (src_start..src_end+1), the op
      is treated as a no-op success.

    Line endings are preserved (no newline normalization) by using newline="".
    """

    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "transfer_lines",
            "description": (
                "Move or copy a range of lines from a source file into a destination file, "
                "based on 1-based line numbers (token-cheap cut/copy/paste)."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "src_path": {"type": "string", "description": "Source file path relative to the selected scope root."},
                    "dst_path": {"type": "string", "description": "Destination file path relative to the selected scope root."},
                    "scope": _scope_schema_prop(),
                    "src_start_line": {"type": "integer", "description": "Source start line (1-based, inclusive)."},
                    "src_end_line": {"type": "integer", "description": "Source end line (1-based, inclusive)."},
                    "dst_insert_at_line": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "description": "Destination line (1-based) to insert before; null = append.",
                    },
                    "delete_from_source": {
                        "type": "boolean",
                        "description": "If true, remove the lines from source (cut). If false, source is unchanged (copy).",
                    },
                    "ensure_newline_boundary": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": (
                            "Optional: when inserting after a line that is not newline-terminated, "
                            "insert a newline boundary so the transferred block starts on a fresh line. "
                            "Default false (preserve exact line endings)."
                        ),
                    },
                },
                "required": [
                    "src_path",
                    "dst_path",
                    "scope",
                    "src_start_line",
                    "src_end_line",
                    "dst_insert_at_line",
                    "delete_from_source",
                    "ensure_newline_boundary",
                ],
                "additionalProperties": False,
            },
        }

    def _clamp(self, v: int, lo: int, hi: int) -> int:
        try:
            return max(int(lo), min(int(hi), int(v)))
        except Exception:
            return int(lo)

    def _choose_boundary(self, lines: List[str]) -> str:
        """Choose '\n' vs '\r\n' based on observed destination line endings."""
        crlf = 0
        lf = 0
        for ln in (lines or []):
            if not isinstance(ln, str):
                continue
            if ln.endswith("\r\n"):
                crlf += 1
            elif ln.endswith("\n"):
                lf += 1
        if crlf > lf and crlf > 0:
            return "\r\n"
        return "\n"

    def _maybe_insert_newline_boundary(self, *, lines: List[str], insert_idx: int) -> int:
        """If the previous line is not newline-terminated, insert a boundary before insert_idx."""
        try:
            idx = int(insert_idx)
        except Exception:
            idx = 0

        if idx <= 0:
            return idx
        if idx > len(lines):
            idx = len(lines)

        try:
            prev = lines[idx - 1]
        except Exception:
            return idx

        if not isinstance(prev, str):
            return idx

        # Treat LF, CRLF, or CR as newline-terminated.
        if prev.endswith("\n") or prev.endswith("\r"):
            return idx

        boundary = self._choose_boundary(lines)
        lines.insert(idx, boundary)
        return idx + 1

    def run(
        self,
        src_path: str,
        dst_path: str,
        src_start_line: int,
        src_end_line: int,
        dst_insert_at_line: Optional[int] = None,
        delete_from_source: bool = False,
        ensure_newline_boundary: Optional[bool] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)

        if not _is_safe_path(root, src_path) or not _is_safe_path(root, dst_path):
            return {"status": "error", "message": "Path outside scope"}

        src_full = os.path.join(root, src_path)
        dst_full = os.path.join(root, dst_path)

        if not os.path.isfile(src_full):
            return {"status": "error", "message": "Source file not found"}

        try:
            sl = int(src_start_line)
            el = int(src_end_line)
        except Exception:
            return {"status": "error", "message": "src_start_line and src_end_line must be integers"}

        if sl < 1:
            return {"status": "error", "message": "src_start_line must be >= 1"}
        if el < sl:
            return {"status": "error", "message": "src_end_line must be >= src_start_line"}

        # Read source (preserve line endings).
        try:
            with open(src_full, "r", encoding="utf-8", errors="replace", newline="") as f:
                src_lines = f.readlines()
        except Exception as e:
            return {"status": "error", "message": f"Failed to read source file: {e}"}

        src_total = len(src_lines)
        if sl > src_total:
            return {"status": "error", "message": f"src_start_line ({sl}) exceeds total_lines ({src_total})"}
        if el > src_total:
            return {"status": "error", "message": f"src_end_line ({el}) exceeds total_lines ({src_total})"}

        chunk = src_lines[sl - 1 : el]
        k = len(chunk)
        if k <= 0:
            return {"status": "error", "message": "Selected range is empty"}

        # Read destination (allow missing => create).
        dst_lines: List[str] = []
        dst_exists = os.path.exists(dst_full)
        if dst_exists:
            if not os.path.isfile(dst_full):
                return {"status": "error", "message": "Destination exists but is not a file"}
            try:
                with open(dst_full, "r", encoding="utf-8", errors="replace", newline="") as f:
                    dst_lines = f.readlines()
            except Exception as e:
                return {"status": "error", "message": f"Failed to read destination file: {e}"}

        # Compute insertion line.
        if dst_insert_at_line is None:
            dst_line_raw = (len(dst_lines) + 1) if src_path != dst_path else (src_total + 1)
        else:
            try:
                dst_line_raw = int(dst_insert_at_line)
            except Exception:
                return {"status": "error", "message": "dst_insert_at_line must be an integer or null"}

        if dst_line_raw < 1:
            return {"status": "error", "message": "dst_insert_at_line must be >= 1 (or null)"}

        ensure_nb = bool(ensure_newline_boundary) if ensure_newline_boundary is not None else False

        txn_id: Optional[str] = None
        manifest: Optional[Dict[str, Any]] = None
        changes: List[Dict[str, Any]] = []

        # Same-file cases.
        if src_path == dst_path:
            orig_lines = src_lines
            orig_total = src_total
            dst_line = self._clamp(dst_line_raw, 1, orig_total + 1)

            if delete_from_source:
                # No-op if "move inside itself".
                if (dst_line >= sl) and (dst_line <= (el + 1)):
                    return {
                        "status": "success",
                        "message": "No changes (destination inside moved block)",
                        "lines_transferred": 0,
                    }

                new_lines = orig_lines[: sl - 1] + orig_lines[el:]
                dst_line_adj = dst_line
                if dst_line > (el + 1):
                    dst_line_adj = dst_line - k
                insert_idx = self._clamp(dst_line_adj - 1, 0, len(new_lines))
                if ensure_nb:
                    insert_idx = self._maybe_insert_newline_boundary(lines=new_lines, insert_idx=insert_idx)
                new_lines[insert_idx:insert_idx] = chunk
            else:
                new_lines = list(orig_lines)
                dst_line = self._clamp(dst_line, 1, len(new_lines) + 1)
                insert_idx = self._clamp(dst_line - 1, 0, len(new_lines))
                if ensure_nb:
                    insert_idx = self._maybe_insert_newline_boundary(lines=new_lines, insert_idx=insert_idx)
                new_lines[insert_idx:insert_idx] = chunk

            if new_lines == orig_lines:
                return {"status": "success", "message": "No changes", "lines_transferred": 0}

            try:
                if self.revision_store:
                    txn_id = self.revision_store.begin_transaction(
                        "transfer_lines",
                        {
                            "src_path": src_path,
                            "dst_path": dst_path,
                            "src_start_line": sl,
                            "src_end_line": el,
                            "dst_insert_at_line": (dst_insert_at_line if dst_insert_at_line is not None else None),
                            "delete_from_source": bool(delete_from_source),
                            "ensure_newline_boundary": bool(ensure_nb),
                            "scope": (scope or "project"),
                        },
                    )
                    before = self.revision_store.snapshot_path(root, src_path)
                    if before is None:
                        raise FsRevisionError("Missing pre-snapshot; refusing to execute")
                    changes = [{"op": "write", "before": before}]
                    manifest = {
                        "id": txn_id,
                        "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                        "tool": "transfer_lines",
                        "args": {
                            "src_path": src_path,
                            "dst_path": dst_path,
                            "src_start_line": sl,
                            "src_end_line": el,
                            "dst_insert_at_line": (dst_insert_at_line if dst_insert_at_line is not None else None),
                            "delete_from_source": bool(delete_from_source),
                            "ensure_newline_boundary": bool(ensure_nb),
                            "scope": (scope or "project"),
                        },
                        "status": "prepared",
                        "changes": changes,
                    }
                    self.revision_store.commit_transaction(txn_id, manifest)

                os.makedirs(os.path.dirname(src_full), exist_ok=True)
                with open(src_full, "w", encoding="utf-8", newline="") as f:
                    f.writelines(new_lines)

                if self.revision_store and txn_id and manifest:
                    try:
                        changes[0]["after"] = self.revision_store.snapshot_path(root, src_path)
                    except Exception:
                        pass
                    manifest["status"] = "applied"
                    try:
                        self.revision_store.commit_transaction(txn_id, manifest)
                    except Exception:
                        pass

                out: Dict[str, Any] = {
                    "status": "success",
                    "message": ("Moved" if delete_from_source else "Copied") + f" {k} lines within {src_path}",
                    "lines_transferred": int(k),
                    "transaction_id": txn_id,
                }
                if self.revision_store and txn_id:
                    try:
                        prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                        if isinstance(prev, dict) and prev.get("status") == "success":
                            out["__wrap_meta__"] = {"diff_preview": prev}
                    except Exception:
                        pass
                return out

            except FsRevisionError as e:
                return {"status": "error", "message": f"Revision snapshot failed; transfer aborted: {str(e)}"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        # Cross-file cases.
        dst_total = len(dst_lines)
        dst_line = self._clamp(dst_line_raw, 1, dst_total + 1)

        new_dst_lines = list(dst_lines)
        insert_idx = self._clamp(dst_line - 1, 0, len(new_dst_lines))
        if ensure_nb:
            insert_idx = self._maybe_insert_newline_boundary(lines=new_dst_lines, insert_idx=insert_idx)
        new_dst_lines[insert_idx:insert_idx] = chunk

        new_src_lines = src_lines
        will_write_src = bool(delete_from_source)
        if delete_from_source:
            new_src_lines = src_lines[: sl - 1] + src_lines[el:]

        # Guard: if no actual changes (shouldn't happen), do not create a txn.
        if (not will_write_src) and (new_dst_lines == dst_lines):
            return {"status": "success", "message": "No changes", "lines_transferred": 0}

        try:
            if self.revision_store:
                txn_id = self.revision_store.begin_transaction(
                    "transfer_lines",
                    {
                        "src_path": src_path,
                        "dst_path": dst_path,
                        "src_start_line": sl,
                        "src_end_line": el,
                        "dst_insert_at_line": (dst_insert_at_line if dst_insert_at_line is not None else None),
                        "delete_from_source": bool(delete_from_source),
                        "ensure_newline_boundary": bool(ensure_nb),
                        "scope": (scope or "project"),
                    },
                )

                # Snapshot only what we will mutate.
                if will_write_src:
                    changes.append({"op": "write", "before": self.revision_store.snapshot_path(root, src_path)})
                changes.append({"op": "write", "before": self.revision_store.snapshot_path(root, dst_path)})

                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "transfer_lines",
                    "args": {
                        "src_path": src_path,
                        "dst_path": dst_path,
                        "src_start_line": sl,
                        "src_end_line": el,
                        "dst_insert_at_line": (dst_insert_at_line if dst_insert_at_line is not None else None),
                        "delete_from_source": bool(delete_from_source),
                        "ensure_newline_boundary": bool(ensure_nb),
                        "scope": (scope or "project"),
                    },
                    "status": "prepared",
                    "changes": changes,
                }
                self.revision_store.commit_transaction(txn_id, manifest)

            # Write destination (ensure directory).
            os.makedirs(os.path.dirname(dst_full), exist_ok=True)
            with open(dst_full, "w", encoding="utf-8", newline="") as f:
                f.writelines(new_dst_lines)

            # Write source if moving.
            if will_write_src:
                with open(src_full, "w", encoding="utf-8", newline="") as f:
                    f.writelines(new_src_lines)

            # Mark applied.
            if self.revision_store and txn_id and manifest:
                try:
                    # after snapshots in the same order as changes.
                    for idx, ch in enumerate(changes):
                        try:
                            p = ch.get("before", {}).get("path") if isinstance(ch.get("before"), dict) else None
                            if isinstance(p, str) and p:
                                changes[idx]["after"] = self.revision_store.snapshot_path(root, p)
                        except Exception:
                            pass
                except Exception:
                    pass

                manifest["status"] = "applied"
                try:
                    self.revision_store.commit_transaction(txn_id, manifest)
                except Exception:
                    pass

            out2: Dict[str, Any] = {
                "status": "success",
                "message": ("Moved" if delete_from_source else "Copied")
                + f" {k} lines from {src_path} to {dst_path} (insert before line {dst_line})",
                "lines_transferred": int(k),
                "transaction_id": txn_id,
            }
            if self.revision_store and txn_id:
                try:
                    prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                    if isinstance(prev, dict) and prev.get("status") == "success":
                        out2["__wrap_meta__"] = {"diff_preview": prev}
                except Exception:
                    pass
            return out2

        except FsRevisionError as e:
            return {"status": "error", "message": f"Revision snapshot failed; transfer aborted: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class FsSearchTool:
    """Tool to search the filesystem tree.

    One tool, two modes:

    - mode='names': find files/directories by *name* under start_path
    - mode='content': find files whose *contents* match the pattern under start_path

    Notes:
    - All paths are constrained to the configured project root.
    - Results are returned as paths *relative to the project root*.
    """

    def __init__(self):
        self.schema = {
            "type": "function",
            "name": "fs_search",
            "description": (
                "Search under a start directory. Use mode='names' to find files/dirs by basename; "
                "use mode='content' to search inside files and return matching file paths. "
                "Set is_regex=true to treat pattern as a regex; otherwise it is a case-insensitive substring match by default."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["names", "content"],
                        "description": "names = match basenames of files/dirs; content = search within file contents.",
                    },
                    "start_path": {
                        "type": "string",
                        "description": "Directory OR file to start from, relative to the selected scope root. Use '.' for root.",
                    },
                    "scope": _scope_schema_prop(),
                    "pattern": {
                        "type": "string",
                        "description": "Substring or regex pattern to search for.",
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "Treat pattern as a regular expression.",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "If false, do case-insensitive matching.",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["any", "file", "dir"],
                        "description": "(names mode only) What to include in results.",
                    },
                    "file_globs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "(content mode) Optional include globs for files relative to start_path, e.g. ['**/*.py','**/*.md']. Empty = all files.",
                    },
                    "ignore_globs": {
                        "anyOf": [
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "null"},
                        ],
                        "description": "Ignore globs applied to paths relative to start_path (both modes). Null = tool defaults.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Stop after this many results.",
                    },
                    "max_file_size_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "(content mode) Skip files larger than this size.",
                    },
                    "follow_symlinks": {
                        "type": "boolean",
                        "description": "If true, follow symlinks while walking.",
                    },
                    "survive": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                    },
                },
                "required": ["mode", "start_path", "scope", "pattern", "is_regex", "case_sensitive", "target", "file_globs", "ignore_globs", "max_results", "max_file_size_bytes", "follow_symlinks", "survive"],
                "additionalProperties": False,
            },
        }

    def _path_matches_any_glob(self, rel_posix: str, globs: List[str]) -> bool:
        """Return True if rel_posix matches any glob.

        We test both the path as-is and a synthetic "dir child" path so patterns like
        "**/.git/**" will also match the directory entry itself.
        """
        if not globs:
            return False

        # Normalize to posix-ish relative path (but do NOT strip leading dots like '.git')
        rel_posix = (rel_posix or "").replace("\\", "/")
        while rel_posix.startswith("./"):
            rel_posix = rel_posix[2:]
        rel_posix = rel_posix.lstrip("/")

        synthetic_child = rel_posix.rstrip("/") + "/_"

        # NOTE: Python's stdlib fnmatch does NOT treat '**' as recursive.
        # We implement a small glob matcher that does, because ignore patterns like
        # "**/.git/**" are the whole point.

        def glob_to_regex(pat: str) -> re.Pattern:
            pat = (pat or "").replace("\\", "/")
            while pat.startswith("./"):
                pat = pat[2:]
            pat = pat.lstrip("/")
            i = 0
            out = "^"
            while i < len(pat):
                # special-case "**/" so it can match "" or "a/b/c/"
                if pat[i : i + 3] == "**/":
                    out += "(?:.*/)?"
                    i += 3
                    continue
                if pat[i : i + 2] == "**":
                    out += ".*"
                    i += 2
                    continue

                c = pat[i]
                if c == "*":
                    out += "[^/]*"
                elif c == "?":
                    out += "[^/]"
                elif c == "[":
                    # character class: copy through closing bracket (or treat as literal '[')
                    j = i + 1
                    while j < len(pat) and pat[j] != "]":
                        j += 1
                    if j < len(pat) and pat[j] == "]":
                        out += pat[i : j + 1]
                        i = j
                    else:
                        out += re.escape(c)
                else:
                    out += re.escape(c)
                i += 1

            out += "$"
            return re.compile(out)

        # Small per-call cache (globs list is tiny)
        compiled: List[re.Pattern] = []
        for g in globs:
            if not g:
                continue
            g = (g or "").replace("\\", "/")
            while g.startswith("./"):
                g = g[2:]
            g = g.lstrip("/")

            # If the glob has no '/', treat it as a basename match against any segment.
            if "/" not in g and "**" not in g:
                parts = rel_posix.split("/")
                if any(fnmatch.fnmatchcase(p, g) for p in parts):
                    return True
                continue

            try:
                compiled.append(glob_to_regex(g))
            except Exception:
                # bad glob: ignore it
                continue

        for rx in compiled:
            if rx.search(rel_posix) or rx.search(synthetic_child):
                return True

        return False

    def _compile_matcher(self, pattern: str, is_regex: bool, case_sensitive: bool):
        if is_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                rx = re.compile(pattern, flags)
            except re.error as e:
                return None, f"Invalid regex: {e}"

            def match(s: str) -> bool:
                return rx.search(s) is not None

            return match, None

        # substring
        if not case_sensitive:
            pat = pattern.lower()

            def match(s: str) -> bool:
                return pat in s.lower()

            return match, None

        def match(s: str) -> bool:
            return pattern in s

        return match, None

    def run(
        self,
        mode: str = "names",
        pattern: str = "",
        start_path: str = ".",
        is_regex: bool = False,
        case_sensitive: bool = False,
        target: str = "any",
        file_globs: Optional[List[str]] = None,
        ignore_globs: Optional[List[str]] = None,
        max_results: int = 200,
        max_file_size_bytes: int = 1024 * 1024,
        follow_symlinks: bool = False,
        scope: Optional[str] = None,
        survive: Optional[bool] = None,
    ) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        # Normalize / defaults (JSON schema defaults are not reliably applied by callers)
        start_path = (start_path or ".").strip() or "."
        pattern = pattern or ""
        file_globs = file_globs or []

        DEFAULT_IGNORE_GLOBS = [
            ".aria",
            ".git",
            "node_modules",
            "__pycache__",
            ".venv",
        ]
        # Null => tool defaults. [] => search everything.
        ignore_globs = DEFAULT_IGNORE_GLOBS if ignore_globs is None else ignore_globs

        if not pattern:
            return {"status": "error", "message": "pattern must be a non-empty string"}

        if mode not in ("names", "content"):
            return {"status": "error", "message": "mode must be 'names' or 'content'"}

        if target not in ("any", "file", "dir"):
            return {"status": "error", "message": "target must be 'any', 'file', or 'dir'"}

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)

        if not _is_safe_path(root, start_path):
            return {"status": "error", "message": "start_path outside scope"}

        full_start = os.path.abspath(os.path.join(root, start_path))
        is_dir_start = os.path.isdir(full_start)
        is_file_start = os.path.isfile(full_start)
        if not is_dir_start and not is_file_start:
            if not os.path.exists(full_start):
                return {"status": "error", "message": "start_path not found"}
            return {"status": "error", "message": "start_path must be a directory or file"}

        matcher, err = self._compile_matcher(pattern=pattern, is_regex=is_regex, case_sensitive=case_sensitive)
        if err:
            return {"status": "error", "message": err}

        results: List[Dict[str, Any]] = []
        truncated = False

        def rel_from_root(abs_path: str) -> str:
            return os.path.relpath(abs_path, root)

        if is_file_start:
            # Single-file start_path is allowed (common papercut).
            if mode == "names":
                results2: List[Dict[str, Any]] = []
                if target in ("any", "file") and matcher(os.path.basename(full_start)):
                    results2.append({"path": rel_from_root(full_start), "kind": "file"})

                out = {
                    "status": "success",
                    "mode": mode,
                    "start_path": start_path,
                    "pattern": pattern,
                    "is_regex": is_regex,
                    "case_sensitive": case_sensitive,
                    "results": results2,
                    "count": len(results2),
                    "truncated": False,
                }
                if survive is False:
                    out["__wrap_meta__"] = {"survive": False}
                return out

            # mode == "content"
            basename_posix = Path(os.path.basename(full_start)).as_posix()
            rel_to_root_posix = Path(os.path.relpath(full_start, root)).as_posix()
            if file_globs and not (
                self._path_matches_any_glob(basename_posix, file_globs)
                or self._path_matches_any_glob(rel_to_root_posix, file_globs)
            ):
                return {"status": "error", "message": "start_path file does not match file_globs"}

            try:
                st = os.stat(full_start)
                if st.st_size > max_file_size_bytes:
                    return {"status": "error", "message": "File exceeds max_file_size_bytes"}

                with open(full_start, "rb") as bf:
                    head = bf.read(2048)
                    if b"\x00" in head:
                        return {"status": "error", "message": "Binary files are not supported"}

                match_lines = 0
                match_line_numbers: List[int] = []
                matches: List[Dict[str, Any]] = []
                max_match_lines = 1000
                max_match_snippets = 200

                with open(full_start, "r", encoding="utf-8", errors="replace") as tf:
                    for ln_no, line in enumerate(tf, 1):
                        if matcher(line):
                            match_lines += 1
                            match_line_numbers.append(int(ln_no))
                            if len(matches) < max_match_snippets:
                                matches.append({"line": int(ln_no), "content": line.rstrip()})
                            if match_lines >= max_match_lines:
                                break

                results2: List[Dict[str, Any]] = []
                if match_lines > 0:
                    results2 = [
                        {
                            "path": rel_from_root(full_start),
                            "kind": "file",
                            "match_count": match_lines,
                            "match_line_numbers": match_line_numbers,
                            "matches": matches,
                        }
                    ]

                out = {
                    "status": "success",
                    "mode": mode,
                    "start_path": start_path,
                    "pattern": pattern,
                    "is_regex": is_regex,
                    "case_sensitive": case_sensitive,
                    "results": results2,
                    "count": len(results2),
                    "truncated": False,
                }
                if survive is False:
                    out["__wrap_meta__"] = {"survive": False}
                return out

            except Exception as e:
                return {"status": "error", "message": f"Failed to search file: {e}"}

        def rel_from_start_posix(abs_path: str) -> str:
            rel = os.path.relpath(abs_path, full_start)
            return Path(rel).as_posix()

        for dirpath, dirnames, filenames in os.walk(full_start, topdown=True, followlinks=follow_symlinks):
            # Prune ignored directories early
            kept_dirs: List[str] = []
            for d in dirnames:
                child_abs = os.path.join(dirpath, d)
                child_rel_posix = rel_from_start_posix(child_abs)
                if self._path_matches_any_glob(child_rel_posix, ignore_globs):
                    continue
                kept_dirs.append(d)
            dirnames[:] = kept_dirs

            if mode == "names":
                # directories
                if target in ("any", "dir"):
                    for d in dirnames:
                        if matcher(d):
                            abs_p = os.path.join(dirpath, d)
                            rel_p = rel_from_root(abs_p)
                            results.append({"path": rel_p, "kind": "dir"})
                            if len(results) >= max_results:
                                truncated = True
                                break
                    if truncated:
                        break

                # files
                if target in ("any", "file"):
                    for f in filenames:
                        if matcher(f):
                            abs_p = os.path.join(dirpath, f)
                            rel_p = rel_from_root(abs_p)
                            results.append({"path": rel_p, "kind": "file"})
                            if len(results) >= max_results:
                                truncated = True
                                break
                    if truncated:
                        break

            else:  # mode == "content"
                for f in filenames:
                    abs_p = os.path.join(dirpath, f)
                    rel_posix = rel_from_start_posix(abs_p)

                    if self._path_matches_any_glob(rel_posix, ignore_globs):
                        continue

                    if file_globs and (not self._path_matches_any_glob(rel_posix, file_globs)):
                        continue

                    try:
                        st = os.stat(abs_p)
                        if st.st_size > max_file_size_bytes:
                            continue

                        # quick binary sniff
                        with open(abs_p, "rb") as bf:
                            head = bf.read(2048)
                            if b"\x00" in head:
                                continue

                        match_lines = 0
                        match_line_numbers = []
                        with open(abs_p, "r", encoding="utf-8", errors="replace") as tf:
                            for ln_no, line in enumerate(tf, 1):
                                if matcher(line):
                                    match_lines += 1
                                    match_line_numbers.append(int(ln_no))
                                    if match_lines >= 1000:
                                        break

                        if match_lines > 0:
                            results.append(
                                {
                                    "path": rel_from_root(abs_p),
                                    "kind": "file",
                                    "match_count": match_lines,
                                    "match_line_numbers": match_line_numbers,
                                }
                            )
                            if len(results) >= max_results:
                                truncated = True
                                break

                    except Exception:
                        # Ignore unreadable files
                        continue

                if truncated:
                    break

        out = {
            "status": "success",
            "mode": mode,
            "start_path": start_path,
            "pattern": pattern,
            "is_regex": is_regex,
            "case_sensitive": case_sensitive,
            "results": results,
            "count": len(results),
            "truncated": truncated,
        }
        if survive is False:
            out["__wrap_meta__"] = {"survive": False}
        return out

class CopyPathsTool:
    """Tool to copy files or folders."""
    
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "copy_paths",
            "description": "Copy files or directories.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "destination": {"type": "string"},
                            },
                            "required": ["source", "destination"],
                            "additionalProperties": False,
                        },
                        "description": "List of copy operations.",
                    },
                    "scope": _scope_schema_prop(),
                },
                "required": ["operations", "scope"],
                "additionalProperties": False,
            },
        }
    
    def run(self, operations: List[Dict[str, str]], scope: Optional[str] = None) -> List[Dict[str, Any]]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return [{"source": (op or {}).get("source"), "status": "error", "message": str(err)} for op in (operations or [])]
        root = str(root)

        results: List[Optional[Dict[str, Any]]] = [None] * len(operations)
        safe_ops: List[Tuple[int, str, str]] = []  # (index, src, dst)

        for i, op in enumerate(operations or []):
            if not isinstance(op, dict):
                results[i] = {"status": "error", "message": "Invalid operation"}
                continue
            src, dst = op.get("source"), op.get("destination")
            if not isinstance(src, str) or not isinstance(dst, str) or not src or not dst:
                results[i] = {"source": src, "status": "error", "message": "source and destination are required"}
                continue
            if not _is_safe_path(root, src) or not _is_safe_path(root, dst):
                results[i] = {"source": src, "destination": dst, "status": "error", "message": "Outside scope"}
                continue
            safe_ops.append((i, src, dst))

        txn_id: Optional[str] = None
        changes: List[Dict[str, Any]] = []
        manifest: Optional[Dict[str, Any]] = None

        # Snapshot destination pre-state first (so copy is undoable).
        if self.revision_store and safe_ops:
            try:
                txn_id = self.revision_store.begin_transaction(
                    "copy_paths",
                    {"operations": [{"source": s, "destination": d} for _, s, d in safe_ops], "scope": (scope or "project")},
                )
                for _, _, dst in safe_ops:
                    snap = self.revision_store.snapshot_path(root, dst)
                    changes.append({"op": "copy", "before": snap})

                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "copy_paths",
                    "args": {"operations": [{"source": s, "destination": d} for _, s, d in safe_ops], "scope": (scope or "project")},
                    "status": "prepared",
                    "changes": changes,
                }
                # Non-negotiable: if this fails, we do NOT copy.
                self.revision_store.commit_transaction(txn_id, manifest)

            except FsRevisionError as e:
                for i, src, dst in safe_ops:
                    results[i] = {
                        "source": src,
                        "destination": dst,
                        "status": "error",
                        "message": f"Revision snapshot failed; copy aborted: {str(e)}",
                        "transaction_id": txn_id,
                    }

                out_list = [
                    r or {"source": (operations[idx] or {}).get("source"), "status": "error", "message": "Unknown"}
                    for idx, r in enumerate(results)
                ]
                if self.revision_store and txn_id:
                    try:
                        prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                        if isinstance(prev, dict) and prev.get("status") == "success":
                            out_list.append({"__wrap_meta__": {"diff_preview": prev}})
                    except Exception:
                        pass
                return out_list

        # Execute copies.
        for i, src, dst in safe_ops:
            src_path = os.path.join(root, src)
            dst_path = os.path.join(root, dst)
            try:
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path)
                else:
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(src_path, dst_path)
                results[i] = {"source": src, "destination": dst, "status": "success", "transaction_id": txn_id}
            except Exception as e:
                results[i] = {"source": src, "destination": dst, "status": "error", "message": str(e), "transaction_id": txn_id}

        # Mark applied.
        if self.revision_store and txn_id and manifest:
            try:
                # Capture post-state snapshots for diff receipts (best-effort).
                try:
                    for idx, (_, _, dst) in enumerate(safe_ops):
                        if idx < len(changes):
                            try:
                                changes[idx]["after"] = self.revision_store.snapshot_path(root, dst)
                            except Exception:
                                pass
                except Exception:
                    pass

                manifest["status"] = "applied"
                self.revision_store.commit_transaction(txn_id, manifest)
            except Exception:
                pass

        out_list = [
            r or {"source": (operations[idx] or {}).get("source"), "status": "error", "message": "Unknown"}
            for idx, r in enumerate(results)
        ]
        if self.revision_store and txn_id:
            try:
                prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                if isinstance(prev, dict) and prev.get("status") == "success":
                    out_list.append({"__wrap_meta__": {"diff_preview": prev}})
            except Exception:
                pass
        return out_list


class RenamePathTool:
    """Tool to rename a file or folder."""
    
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "rename_path",
            "description": "Rename a file or directory.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": "Current path (relative to the selected scope root)."},
                    "new_path": {"type": "string", "description": "New path (relative to the selected scope root)."},
                    "scope": _scope_schema_prop(),
                },
                "required": ["old_path", "new_path", "scope"],
                "additionalProperties": False,
            },
        }
    
    def run(self, old_path: str, new_path: str, scope: Optional[str] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err_scope = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err_scope:
            return {"status": "error", "message": str(err_scope)}
        root = str(root)
        if not _is_safe_path(root, old_path) or not _is_safe_path(root, new_path):
            return {"status": "error", "message": "Path outside scope"}
        
        src = os.path.join(root, old_path)
        dst = os.path.join(root, new_path)
        
        txn_id: Optional[str] = None
        if not os.path.exists(src):
            return {"status": "error", "message": "Path not found"}

        before_src: Optional[Dict[str, Any]] = None
        before_dst: Optional[Dict[str, Any]] = None
        manifest: Optional[Dict[str, Any]] = None

        out: Dict[str, Any] = {"status": "error", "message": "Unknown", "transaction_id": None}
        ok = False
        err: Optional[str] = None

        try:
            if self.revision_store:
                txn_id = self.revision_store.begin_transaction(
                    "rename_path",
                    {"old_path": old_path, "new_path": new_path, "scope": (scope or "project")},
                )
                # Snapshot destination first, then source.
                before_dst = self.revision_store.snapshot_path(root, new_path)
                before_src = self.revision_store.snapshot_path(root, old_path)
                if before_src is None or before_dst is None:
                    raise FsRevisionError("Missing pre-snapshot; refusing to execute")

                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "rename_path",
                    "args": {"old_path": old_path, "new_path": new_path, "scope": (scope or "project")},
                    "status": "prepared",
                    "changes": [
                        {"op": "rename_dst", "before": before_dst},
                        {"op": "rename_src", "before": before_src},
                    ],
                }
                # Non-negotiable: if this fails, we do NOT rename.
                self.revision_store.commit_transaction(txn_id, manifest)

            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
            ok = True
            out = {
                "status": "success",
                "message": f"Renamed {old_path} to {new_path}",
                "transaction_id": txn_id,
            }

        except FsRevisionError as e:
            err = f"Revision snapshot failed; rename aborted: {str(e)}"
            out = {"status": "error", "message": err, "transaction_id": txn_id}
        except Exception as e:
            err = str(e)
            out = {"status": "error", "message": err, "transaction_id": txn_id}
        finally:
            # Capture post-state snapshots for diff receipts (best-effort).
            if self.revision_store and txn_id and manifest:
                try:
                    try:
                        manifest["changes"][0]["after"] = self.revision_store.snapshot_path(root, new_path)
                    except Exception:
                        pass
                    try:
                        manifest["changes"][1]["after"] = self.revision_store.snapshot_path(root, old_path)
                    except Exception:
                        pass

                    manifest["status"] = "applied" if ok else "error"
                    if err:
                        manifest["error"] = str(err)
                    self.revision_store.commit_transaction(txn_id, manifest)
                except Exception:
                    pass

            # Diff preview for UI (best-effort).
            if self.revision_store and txn_id:
                try:
                    prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                    if isinstance(prev, dict) and prev.get("status") == "success":
                        out["__wrap_meta__"] = {"diff_preview": prev}
                except Exception:
                    pass

        return out


class MovePathsTool:
    """Tool to move files or folders."""
    
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "move_paths",
            "description": "Move files or directories.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "destination": {"type": "string"},
                            },
                            "required": ["source", "destination"],
                            "additionalProperties": False,
                        },
                        "description": "List of move operations.",
                    },
                    "scope": _scope_schema_prop(),
                },
                "required": ["operations", "scope"],
                "additionalProperties": False,
            },
        }
    
    def run(self, operations: List[Dict[str, str]], scope: Optional[str] = None) -> List[Dict[str, Any]]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return [{"source": (op or {}).get("source"), "status": "error", "message": str(err)} for op in (operations or [])]
        root = str(root)

        results: List[Optional[Dict[str, Any]]] = [None] * len(operations)
        safe_ops: List[Tuple[int, str, str]] = []  # (index, src, dst)

        for i, op in enumerate(operations):
            src, dst = op["source"], op["destination"]
            if not _is_safe_path(root, src) or not _is_safe_path(root, dst):
                results[i] = {"source": src, "status": "error", "message": "Outside scope"}
                continue

            # Friendly error: missing source.
            try:
                if not os.path.exists(os.path.join(root, src)):
                    results[i] = {"source": src, "destination": dst, "status": "error", "message": "Not found"}
                    continue
            except Exception:
                pass

            safe_ops.append((i, src, dst))

        txn_id: Optional[str] = None
        changes: List[Dict[str, Any]] = []

        # Snapshot FIRST (non-negotiable). If this fails, nothing moves.
        manifest: Optional[Dict[str, Any]] = None
        if self.revision_store and safe_ops:
            try:
                txn_id = self.revision_store.begin_transaction(
                    "move_paths",
                    {"operations": [{"source": s, "destination": d} for _, s, d in safe_ops], "scope": (scope or "project")},
                )
                for _, src, dst in safe_ops:
                    before_dst = self.revision_store.snapshot_path(root, dst)
                    before_src = self.revision_store.snapshot_path(root, src)
                    changes.append({"op": "move_dst", "before": before_dst})
                    changes.append({"op": "move_src", "before": before_src})

                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "move_paths",
                    "args": {"operations": [{"source": s, "destination": d} for _, s, d in safe_ops], "scope": (scope or "project")},
                    "status": "prepared",
                    "changes": changes,
                }
                # Non-negotiable: if this fails, we do NOT move.
                self.revision_store.commit_transaction(txn_id, manifest)

            except FsRevisionError as e:
                for i, src, _ in safe_ops:
                    results[i] = {"source": src, "status": "error", "message": f"Revision snapshot failed; move aborted: {str(e)}"}
                return [r or {"source": operations[idx].get("source"), "status": "error", "message": "Unknown"} for idx, r in enumerate(results)]

        # Execute moves.
        for i, src, dst in safe_ops:
            src_path = os.path.join(root, src)
            dst_path = os.path.join(root, dst)
            try:
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.move(src_path, dst_path)
                results[i] = {"source": src, "destination": dst, "status": "success", "transaction_id": txn_id}
            except Exception as e:
                results[i] = {"source": src, "destination": dst, "status": "error", "message": str(e), "transaction_id": txn_id}

        # Mark applied.
        if self.revision_store and txn_id and manifest:
            try:
                # Capture post-state snapshots for diff receipts (best-effort).
                try:
                    for op_idx, (_, src, dst) in enumerate(safe_ops):
                        dst_change_idx = (2 * op_idx)
                        src_change_idx = (2 * op_idx) + 1
                        if dst_change_idx < len(changes):
                            try:
                                changes[dst_change_idx]["after"] = self.revision_store.snapshot_path(root, dst)
                            except Exception:
                                pass
                        if src_change_idx < len(changes):
                            try:
                                changes[src_change_idx]["after"] = self.revision_store.snapshot_path(root, src)
                            except Exception:
                                pass
                except Exception:
                    pass

                manifest["status"] = "applied"
                self.revision_store.commit_transaction(txn_id, manifest)
            except Exception:
                pass
        return [r or {"source": operations[idx].get("source"), "status": "error", "message": "Unknown"} for idx, r in enumerate(results)]


class PathStatTool:
    """Tool to get file/folder statistics."""

    _MAX_BYTES_FOR_LINE_COUNT = 50_000_000  # 50MB safety cap

    def __init__(self):
        self.schema = {
            "type": "function",
            "name": "path_stat",
            "description": (
                "Get information about a file or directory. "
                "For files, also returns image dimensions when the file is a decodable image, "
                "and returns a text line_count for non-binary files (best-effort; may be skipped for very large files)."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "Path to check (relative to the selected scope root)."},
                    "scope": _scope_schema_prop(),
                    "survive": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                    },
                },
                "required": ["relative_path", "scope", "survive"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str, scope: Optional[str] = None, survive: Optional[bool] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)
        if not _is_safe_path(root, relative_path):
            return {"status": "error", "message": "Path outside scope"}
        
        full_path = os.path.join(root, relative_path)
        
        if not os.path.exists(full_path):
            return {"status": "error", "message": "Path not found"}
        
        try:
            stat = os.stat(full_path)

            is_file = bool(os.path.isfile(full_path))
            is_dir = bool(os.path.isdir(full_path))

            out: Dict[str, Any] = {
                "status": "success",
                "path": relative_path,
                "exists": True,
                "is_file": is_file,
                "is_dir": is_dir,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                # Optional enrichments (files only)
                "image": None,
                "text": None,
            }

            if is_file:
                # Image dimensions (best-effort)
                try:
                    with Image.open(full_path) as img:
                        out["image"] = {
                            "width": int(getattr(img, "width", 0) or 0),
                            "height": int(getattr(img, "height", 0) or 0),
                            "format": str(getattr(img, "format", "") or "") or None,
                        }
                except UnidentifiedImageError:
                    pass
                except Exception:
                    pass

                # Text line count (best-effort; non-binary only)
                try:
                    if int(stat.st_size) > int(self._MAX_BYTES_FOR_LINE_COUNT):
                        out["text"] = {
                            "line_count": None,
                            "line_count_truncated": True,
                            "max_bytes": int(self._MAX_BYTES_FOR_LINE_COUNT),
                        }
                    else:
                        with open(full_path, "rb") as bf:
                            head = bf.read(4096)
                            # Binary sniff: if NUL bytes exist, don't pretend we can count lines.
                            if b"\x00" not in head:
                                n = int(head.count(b"\n"))
                                last = head[-1:] if head else b""
                                while True:
                                    chunk = bf.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    n += int(chunk.count(b"\n"))
                                    last = chunk[-1:]
                                if int(stat.st_size) > 0 and last != b"\n":
                                    n += 1
                                out["text"] = {"line_count": int(n)}
                except Exception:
                    pass

            if survive is False:
                out["__wrap_meta__"] = {"survive": False}
            return out
        except Exception as e:
            return {"status": "error", "message": str(e)}




class ApplyPatchTool:
    """Apply a unified-diff patch to one or more files.

    Safety (v1):
    - scope is required (project|sandbox)
    - paths must stay within scope root
    - strict apply: if any hunk context mismatches, NOTHING is written (fail closed)
    - text-only (utf-8); binary files rejected
    - best-effort rollback on unexpected I/O failures during apply

    Notes:
    - The patch content parameter is named `content` so the UI redaction logic doesn't
      dump it into the timeline.
    - Writes are wrapped in FsRevisionStore transaction so undo + diff badges work.

    Practical guidance:
    - This is not a fuzzy editor: hunks are matched strictly.
    - Generate patches against the CURRENT file contents. If it fails, re-read and
      regenerate/rebase the diff.
    - Unified diff hunk headers (the @@ -a,b +c,d @@ line numbers) matter; don't
      freestyle them.
    """

    _MAX_PATCH_CHARS = 1_000_000
    _MAX_FILES = 50
    _MAX_HUNKS = 500

    _HUNK_RX = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "apply_patch",
            "description": (
                "Apply a unified-diff (git-style) patch within the selected scope. "
                "Strict (not fuzzy): hunks must match the current file text exactly; if it fails, re-read and regenerate the diff. "
                "Fail-closed + rollback: if any hunk can't be applied exactly, the tool aborts and rolls back any partial writes (best-effort). "
                "Text-only (UTF-8); binary files rejected. "
                "Paths must be relative to the scope root (no absolute paths or '..')."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": _scope_schema_prop(),
                    "content": {
                        "type": "string",
                        "description": "Unified diff patch text.",
                    },
                },
                "required": ["scope", "content"],
                "additionalProperties": False,
            },
        }

    def _clean_header_path(self, s: str) -> str:
        p = (s or "").strip()
        if "\t" in p:
            p = p.split("\t", 1)[0].strip()
        if (p.startswith('"') and p.endswith('"')) or (p.startswith("'") and p.endswith("'")):
            p = p[1:-1].strip()
        p = p.replace("\\", "/")
        if p == "/dev/null" or p == "dev/null":
            return "/dev/null"
        if p.startswith("a/") or p.startswith("b/"):
            p = p[2:]
        # Never allow absolute paths.
        p = p.lstrip("/")
        return p

    def _validate_rel_path(self, root: str, rel_path: str) -> Optional[str]:
        if not isinstance(rel_path, str) or not rel_path:
            return "Missing file path in patch"
        if rel_path == "/dev/null":
            return None
        if os.path.isabs(rel_path):
            return "Absolute paths are not allowed"
        # Block traversal.
        parts = [x for x in rel_path.replace("\\", "/").split("/") if x and x != "."]
        if any(x == ".." for x in parts):
            return "Path traversal ('..') is not allowed"
        if not _is_safe_path(str(root), rel_path):
            return "Path outside scope"
        return None

    def _read_text_file(self, full_path: str) -> Tuple[str, str, bool]:
        """Return (normalized_text, newline_style, had_trailing_newline)."""
        raw = Path(full_path).read_bytes()
        # Binary-ish guard.
        if b"\x00" in raw[:8192]:
            raise ValueError("Binary file; refusing to patch")
        newline_style = "\r\n" if b"\r\n" in raw else "\n"
        had_trailing_newline = raw.endswith(b"\n")
        # Normalize to \n for matching/applying.
        txt = raw.decode("utf-8")
        txt = txt.replace("\r\n", "\n").replace("\r", "\n")
        return txt, newline_style, had_trailing_newline

    def _split_lines(self, txt: str) -> List[str]:
        if txt is None:
            return []
        # We work on lines without line endings.
        return str(txt).split("\n") if txt != "" else [""]

    def _parse_unified_diff(self, patch_text: str) -> List[Dict[str, Any]]:
        lines = str(patch_text or "").splitlines(keepends=False)
        files: List[Dict[str, Any]] = []
        cur: Optional[Dict[str, Any]] = None
        hunks_total = 0

        def _start_new():
            nonlocal cur
            if cur is not None:
                files.append(cur)
            cur = {"old": None, "new": None, "hunks": [], "git_old": None, "git_new": None}

        for i, ln in enumerate(lines):
            if ln.startswith("diff --git "):
                _start_new()
                # diff --git a/x b/y
                try:
                    rest = ln[len("diff --git "):].strip()
                    a, b = rest.split(" ", 1)
                    if cur is not None:
                        cur["git_old"] = self._clean_header_path(a)
                        cur["git_new"] = self._clean_header_path(b)
                except Exception:
                    pass
                continue

            if ln.startswith("--- "):
                if cur is None:
                    _start_new()
                if cur is not None:
                    cur["old"] = self._clean_header_path(ln[4:])
                continue

            if ln.startswith("+++ "):
                if cur is None:
                    _start_new()
                if cur is not None:
                    cur["new"] = self._clean_header_path(ln[4:])
                continue

            if ln.startswith("@@ "):
                if cur is None:
                    raise ValueError("Patch has hunk without file header")
                m = self._HUNK_RX.match(ln)
                if not m:
                    raise ValueError(f"Invalid hunk header: {ln}")
                o_start = int(m.group(1))
                o_cnt = int(m.group(2) or 1)
                n_start = int(m.group(3))
                n_cnt = int(m.group(4) or 1)
                h = {"old_start": o_start, "old_count": o_cnt, "new_start": n_start, "new_count": n_cnt, "lines": []}
                cur["hunks"].append(h)
                hunks_total += 1
                if hunks_total > self._MAX_HUNKS:
                    raise ValueError(f"Too many hunks (max {self._MAX_HUNKS})")
                continue

            if cur is not None and cur.get("hunks"):
                # Hunk body lines belong to the last hunk until the next header.
                # (We only get here for non-header lines.)
                last = cur["hunks"][-1]
                if not isinstance(last, dict):
                    continue
                if ln.startswith("\\"):
                    # Common marker emitted by git.
                    # Example: "\\ No newline at end of file"
                    if "No newline at end of file" in ln:
                        # Best-effort tracking: marker applies to the immediately
                        # preceding +/- line.
                        try:
                            prev = last.get("lines")[-1] if (isinstance(last.get("lines"), list) and last.get("lines")) else None
                            prev_tag = prev[0] if (isinstance(prev, tuple) and len(prev) >= 1) else None
                            if prev_tag == "-":
                                last["old_no_trailing_newline"] = True
                            elif prev_tag == "+":
                                last["new_no_trailing_newline"] = True
                        except Exception:
                            pass
                    # Marker line is not part of the hunk diff content.
                    continue
                if not ln:
                    raise ValueError("Invalid patch line (missing prefix)")
                tag = ln[0]
                if tag not in (" ", "+", "-"):
                    # Ignore unrelated lines (like index, ---/+++ timestamps) outside hunks.
                    continue
                last["lines"].append((tag, ln[1:]))

        if cur is not None:
            files.append(cur)

        # Drop empty stubs
        files = [f for f in files if isinstance(f, dict) and (f.get("old") or f.get("new") or f.get("git_old") or f.get("git_new"))]
        if len(files) > self._MAX_FILES:
            raise ValueError(f"Too many files in patch (max {self._MAX_FILES})")
        return files

    def _apply_hunks_strict(self, orig_lines: List[str], hunks: List[Dict[str, Any]]) -> List[str]:
        lines = list(orig_lines or [])
        delta = 0

        for h_idx, h in enumerate(hunks or []):
            if not isinstance(h, dict):
                continue
            o_start = int(h.get("old_start") or 1)
            o_cnt = int(h.get("old_count") or 0)
            # Unified diff can use old_start=0 for empty files / insert-at-start hunks.
            base_idx = 0 if o_start <= 0 else (o_start - 1)
            start_idx = base_idx + delta
            if start_idx < 0 or start_idx > len(lines):
                raise ValueError(f"Hunk {h_idx + 1}: start out of range")

            pos = start_idx
            out_chunk: List[str] = []
            consumed = 0
            produced = 0

            for tag, txt in (h.get("lines") or []):
                if tag == " ":
                    if pos >= len(lines) or lines[pos] != txt:
                        got = lines[pos] if (0 <= pos < len(lines)) else "<EOF>"
                        raise ValueError(f"Context mismatch at hunk {h_idx + 1}: expected '{txt}' got '{got}'")
                    out_chunk.append(txt)
                    pos += 1
                    consumed += 1
                    produced += 1
                elif tag == "-":
                    if pos >= len(lines) or lines[pos] != txt:
                        got = lines[pos] if (0 <= pos < len(lines)) else "<EOF>"
                        raise ValueError(f"Delete mismatch at hunk {h_idx + 1}: expected '{txt}' got '{got}'")
                    pos += 1
                    consumed += 1
                elif tag == "+":
                    out_chunk.append(txt)
                    produced += 1

            if consumed != o_cnt:
                # Strictness: header must match actual removed/context lines.
                raise ValueError(f"Hunk {h_idx + 1}: header old_count={o_cnt} but consumed={consumed}")

            lines = lines[:start_idx] + out_chunk + lines[pos:]
            delta += (produced - consumed)

        return lines

    def run(self, content: str, scope: Optional[str] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        if not isinstance(content, str) or not content.strip():
            return {"status": "error", "message": "content is required"}
        if len(content) > self._MAX_PATCH_CHARS:
            return {"status": "error", "message": f"Patch too large (max {self._MAX_PATCH_CHARS} chars)"}

        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)

        if not self.revision_store:
            return {"status": "error", "message": "Fs revision store not available"}

        try:
            files = self._parse_unified_diff(content)
        except Exception as e:
            return {"status": "error", "message": f"Invalid patch: {e}"}

        if not files:
            return {"status": "error", "message": "Patch contains no files"}

        # Strictness: require at least one hunk (prevents confusing no-op "success").
        try:
            for f in files:
                hunks = f.get("hunks") if isinstance(f.get("hunks"), list) else []
                if not hunks:
                    p_old = f.get("old") or f.get("git_old")
                    p_new = f.get("new") or f.get("git_new")
                    p = self._clean_header_path(str(p_new or p_old or "")) if (p_new or p_old) else "(unknown)"
                    return {"status": "error", "message": f"Patch contains no hunks for {p}"}
        except Exception:
            return {"status": "error", "message": "Patch contains no hunks"}

        # Build plan (fail closed).
        plan: List[Dict[str, Any]] = []
        touched = set()
        created = 0
        deleted = 0

        try:
            for f in files:
                old_p = f.get("old") or f.get("git_old")
                new_p = f.get("new") or f.get("git_new")
                old_p = self._clean_header_path(str(old_p or "")) if old_p is not None else None
                new_p = self._clean_header_path(str(new_p or "")) if new_p is not None else None

                is_create = (old_p == "/dev/null")
                is_delete = (new_p == "/dev/null")

                rel = new_p if not is_delete else old_p
                rel = str(rel or "").strip()

                ve = self._validate_rel_path(root, rel)
                if ve:
                    raise ValueError(f"{rel}: {ve}")

                if rel in touched:
                    raise ValueError(f"Duplicate file in patch: {rel}")
                touched.add(rel)

                full = os.path.join(root, rel)

                hunks = f.get("hunks") if isinstance(f.get("hunks"), list) else []

                if is_delete:
                    if not os.path.exists(full):
                        raise ValueError(f"Delete target not found: {rel}")
                    if os.path.isdir(full):
                        raise ValueError(f"Delete target is a directory (unsupported): {rel}")

                    # Validate hunks match current content (prevents /dev/null from
                    # becoming a blind 'delete file' permission slip).
                    try:
                        orig_text, _newline_style, _trailing_nl = self._read_text_file(full)
                        orig_lines = orig_text.splitlines(keepends=False)
                        new_lines = self._apply_hunks_strict(orig_lines, hunks)
                        # For delete patches, we only support patches that remove the
                        # entire file content.
                        if any((isinstance(x, str) and x != "") for x in (new_lines or [])):
                            raise ValueError("Delete patch does not remove entire file; use delete_paths instead")
                    except Exception as e:
                        raise ValueError(f"Delete patch mismatch for {rel}: {e}")

                    plan.append({"op": "delete", "rel": rel, "full": full, "newline": "\n", "trailing_nl": True, "new_text": None, "hunks": hunks})
                    deleted += 1
                    continue

                # Read original if exists.
                if os.path.exists(full) and not os.path.isfile(full):
                    raise ValueError(f"Patch target is not a file: {rel}")

                if (not os.path.exists(full)) and (not is_create):
                    raise ValueError(f"Patch target not found: {rel}")

                if is_create and os.path.exists(full):
                    raise ValueError(f"Create target already exists: {rel}")

                if os.path.exists(full):
                    orig_text, newline_style, trailing_nl = self._read_text_file(full)
                    orig_lines = orig_text.splitlines(keepends=False)
                else:
                    newline_style = "\n"
                    trailing_nl = True
                    orig_lines = []

                # If the patch explicitly says the NEW file has no trailing newline,
                # honor it (best-effort).
                try:
                    if any(bool(h.get("new_no_trailing_newline")) for h in hunks if isinstance(h, dict)):
                        trailing_nl = False
                except Exception:
                    pass

                new_lines = self._apply_hunks_strict(orig_lines, hunks)
                new_text = "\n".join(new_lines)
                if trailing_nl and not new_text.endswith("\n"):
                    new_text += "\n"
                if newline_style == "\r\n":
                    new_text = new_text.replace("\n", "\r\n")

                plan.append({"op": ("create" if is_create else "patch"), "rel": rel, "full": full, "newline": newline_style, "trailing_nl": trailing_nl, "new_text": new_text, "hunks": hunks})
                if is_create:
                    created += 1

        except Exception as e:
            return {"status": "error", "message": str(e)}

        # Transaction (prepared -> apply -> applied)
        txn_id = None
        manifest = None
        try:
            txn_id = self.revision_store.begin_transaction(
                "apply_patch",
                {"scope": scope, "files": int(len(plan)), "content_len": int(len(content))},
            )

            manifest = {
                "id": txn_id,
                "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "tool": "apply_patch",
                "args": {"scope": scope, "files": int(len(plan)), "content_len": int(len(content))},
                "status": "prepared",
                "changes": [],
            }

            for it in plan:
                rel = it["rel"]
                before = self.revision_store.snapshot_path(root, rel)
                manifest["changes"].append({"op": it["op"], "before": before})

            # Non-negotiable: if this fails, we do NOT apply.
            self.revision_store.commit_transaction(txn_id, manifest)

            # Apply
            undo_txn_id: Optional[str] = None
            rolled_back = False
            try:
                for it in plan:
                    op = it["op"]
                    rel = it["rel"]
                    full = it["full"]
                    if op == "delete":
                        os.unlink(full)
                    else:
                        os.makedirs(os.path.dirname(full), exist_ok=True)
                        data = (it.get("new_text") or "").encode("utf-8")
                        Path(full).write_bytes(data)
            except Exception as e:
                # Best-effort rollback so the tool behaves atomically.
                if self.revision_store and txn_id:
                    try:
                        undo_txn_id = self.revision_store.undo_transaction(root, str(txn_id))
                        rolled_back = True
                    except Exception:
                        rolled_back = False

                # Update original manifest to reflect current post-rollback state.
                try:
                    if isinstance(manifest, dict):
                        manifest["status"] = "failed_rolled_back" if rolled_back else "failed"
                        manifest["error"] = str(e)
                        if undo_txn_id:
                            manifest["undo_transaction_id"] = str(undo_txn_id)
                        for ch, it2 in zip(manifest.get("changes") or [], plan):
                            try:
                                rel2 = it2["rel"]
                                ch["after"] = self.revision_store.snapshot_path(root, rel2)
                            except Exception:
                                pass
                        try:
                            self.revision_store.commit_transaction(txn_id, manifest)
                        except Exception:
                            pass
                except Exception:
                    pass

                msg = f"Patch apply failed: {e}"
                if rolled_back:
                    msg += " (rolled back)"
                else:
                    msg += " (rollback failed)"

                out_err: Dict[str, Any] = {"status": "error", "message": msg, "transaction_id": txn_id}
                if undo_txn_id:
                    out_err["undo_transaction_id"] = str(undo_txn_id)
                return out_err

            # After snapshots
            for ch, it in zip(manifest.get("changes") or [], plan):
                try:
                    rel = it["rel"]
                    after = self.revision_store.snapshot_path(root, rel)
                    ch["after"] = after
                except Exception:
                    pass

            manifest["status"] = "applied"
            try:
                self.revision_store.commit_transaction(txn_id, manifest)
            except Exception:
                pass

            out = {
                "status": "success",
                "message": "Patch applied",
                "transaction_id": txn_id,
                "files_touched": int(len(plan)),
                "files_created": int(created),
                "files_deleted": int(deleted),
            }

            # Diff preview for UI (best-effort).
            try:
                prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                if isinstance(prev, dict) and prev.get("status") == "success":
                    out["__wrap_meta__"] = {"diff_preview": prev}
            except Exception:
                pass

            return out

        except Exception as e:
            return {"status": "error", "message": str(e)}

class ApplyAnchorPatchTool:
    """Apply an anchor-based patch (agent-friendly; no line numbers).
    Safety: each op replaces an EXACT `before` block with an `after` block.
    The `before` block must match exactly once (otherwise fail).
    Optional `anchor` narrows search to an anchor-window but does not bypass exact matching.
    Content is JSON (v1): {"version":1,"files":[{"path":"...","ops":[{"op":"replace","anchor":"...","anchor_window_lines":200,"before":"...","after":"..."}]}]}.
    """
    _MAX_PATCH_CHARS = 1_000_000
    _MAX_FILES = 50
    _MAX_OPS = 500
    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "apply_anchor_patch",
            "description": (
                "Apply an anchor-based patch (no line numbers). "
                "Strict (not fuzzy): replaces an exact `before` text block with an `after` block; "
                "the `before` block must match exactly once (otherwise the tool fails). "
                "Optional `anchor` narrows search but does not bypass exact matching. "
                "Fail-closed + rollback: abort and roll back partial writes (best-effort)."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": _scope_schema_prop(),
                    "content": {"type": "string", "description": "JSON anchor-patch payload."},
                },
                "required": ["scope", "content"],
                "additionalProperties": False,
            },
        }
    def _validate_rel_path(self, root: str, rel_path: str) -> Optional[str]:
        if not isinstance(rel_path, str) or not rel_path:
            return "Missing file path"
        if os.path.isabs(rel_path):
            return "Absolute paths are not allowed"
        parts = [x for x in rel_path.replace("\\", "/").split("/") if x and x != "."]
        if any(x == ".." for x in parts):
            return "Path traversal ('..') is not allowed"
        if not _is_safe_path(str(root), rel_path):
            return "Path outside scope"
        return None
    def _norm(self, s: Any) -> str:
        try:
            return str(s).replace("\r\n", "\n").replace("\r", "\n")
        except Exception:
            return ""
    def _read_text_file(self, full_path: str) -> Tuple[str, str, bool]:
        raw = Path(full_path).read_bytes()
        if b"\x00" in raw[:8192]:
            raise ValueError("Binary file; refusing to patch")
        newline_style = "\r\n" if b"\r\n" in raw else "\n"
        had_trailing_newline = raw.endswith(b"\n")
        txt = raw.decode("utf-8")
        txt = txt.replace("\r\n", "\n").replace("\r", "\n")
        return txt, newline_style, had_trailing_newline
    def _find_unique(self, haystack: str, needle: str, *, start: int = 0, end: Optional[int] = None) -> Tuple[Optional[int], str]:
        if not isinstance(haystack, str):
            haystack = str(haystack)
        if not isinstance(needle, str) or needle == "":
            return None, "Empty before block"
        e = len(haystack) if end is None else int(end)
        a = haystack.find(needle, int(start), e)
        if a < 0:
            return None, "Before block not found"
        b = haystack.find(needle, a + 1, e)
        if b >= 0:
            return None, "Before block is ambiguous (matches multiple times)"
        return int(a), ""
    def run(self, content: str, scope: Optional[str] = None) -> Dict[str, Any]:
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        if not isinstance(content, str) or not content.strip():
            return {"status": "error", "message": "content is required"}
        if len(content) > self._MAX_PATCH_CHARS:
            return {"status": "error", "message": f"Patch too large (max {self._MAX_PATCH_CHARS} chars)"}
        root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
        if err:
            return {"status": "error", "message": str(err)}
        root = str(root)
        if not self.revision_store:
            return {"status": "error", "message": "Fs revision store not available"}
        try:
            payload = json.loads(content)
        except Exception as e:
            return {"status": "error", "message": f"Invalid JSON: {e}"}
        if not isinstance(payload, dict):
            return {"status": "error", "message": "Invalid patch: expected a JSON object"}
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            return {"status": "error", "message": "Invalid patch: missing files[]"}
        if len(files) > self._MAX_FILES:
            return {"status": "error", "message": f"Too many files (max {self._MAX_FILES})"}
        plan: List[Dict[str, Any]] = []
        touched = set()
        ops_done = 0
        try:
            for f in files:
                if not isinstance(f, dict):
                    raise ValueError("Invalid file entry")
                rel = f.get("path")
                if not isinstance(rel, str) or not rel.strip():
                    raise ValueError("File entry missing path")
                rel = rel.strip().replace("\\", "/")
                ve = self._validate_rel_path(root, rel)
                if ve:
                    raise ValueError(f"{rel}: {ve}")
                if rel in touched:
                    raise ValueError(f"Duplicate file: {rel}")
                touched.add(rel)
                full = os.path.join(root, rel)
                if not os.path.exists(full) or not os.path.isfile(full):
                    raise ValueError(f"Patch target not found (file): {rel}")
                ops = f.get("ops")
                if not isinstance(ops, list) or not ops:
                    raise ValueError(f"{rel}: missing ops[]")
                if ops_done + len(ops) > self._MAX_OPS:
                    raise ValueError(f"Too many ops (max {self._MAX_OPS})")
                orig_text, newline_style, trailing_nl = self._read_text_file(full)
                working = str(orig_text)
                if isinstance(f.get("trailing_newline"), bool):
                    trailing_nl = bool(f.get("trailing_newline"))
                for op in ops:
                    if not isinstance(op, dict):
                        raise ValueError(f"{rel}: invalid op")
                    if str(op.get("op") or "").strip().lower() != "replace":
                        raise ValueError(f"{rel}: unsupported op (only 'replace' is supported)")
                    anchor = op.get("anchor")
                    before = self._norm(op.get("before"))
                    after = self._norm(op.get("after"))
                    if len(before.strip()) < 8:
                        raise ValueError(f"{rel}: before block too small; include more context")
                    if isinstance(anchor, str) and anchor.strip():
                        anchor_line = anchor.replace("\r", "").replace("\n", "")
                        if anchor_line and anchor_line not in before:
                            raise ValueError(f"{rel}: anchor must be included in before block")
                        window_lines = 200
                        try:
                            window_lines = int(op.get("anchor_window_lines") or 200)
                        except Exception:
                            window_lines = 200
                        window_lines = max(5, min(2000, window_lines))
                        lines_plain = working.splitlines(keepends=False)
                        anchor_hits = [i for i, ln in enumerate(lines_plain) if ln == anchor_line]
                        if len(anchor_hits) != 1:
                            raise ValueError(f"{rel}: anchor must match exactly one line (found {len(anchor_hits)})")
                        a_idx = int(anchor_hits[0])
                        lines_nl = working.splitlines(keepends=True)
                        start_off = 0
                        for i in range(a_idx):
                            start_off += len(lines_nl[i])
                        end_idx = min(len(lines_nl), a_idx + window_lines)
                        end_off = start_off
                        for i in range(a_idx, end_idx):
                            end_off += len(lines_nl[i])
                        pos, perr = self._find_unique(working, before, start=start_off, end=end_off)
                        if pos is None:
                            raise ValueError(f"{rel}: {perr} (within anchor window)")
                        working = working[:pos] + after + working[pos + len(before) :]
                    else:
                        pos, perr = self._find_unique(working, before)
                        if pos is None:
                            raise ValueError(f"{rel}: {perr}")
                        working = working[:pos] + after + working[pos + len(before) :]
                    ops_done += 1
                if trailing_nl and not working.endswith("\n"):
                    working += "\n"
                if (not trailing_nl) and working.endswith("\n"):
                    working = working[:-1]
                new_text = working
                if newline_style == "\r\n":
                    new_text = new_text.replace("\n", "\r\n")
                plan.append({"op": "patch", "rel": rel, "full": full, "new_text": new_text})
        except Exception as e:
            return {"status": "error", "message": str(e)}
        txn_id = None
        manifest = None
        try:
            txn_id = self.revision_store.begin_transaction("apply_anchor_patch", {"scope": scope, "files": int(len(plan)), "ops": int(ops_done), "content_len": int(len(content))})
            manifest = {"id": txn_id, "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z", "tool": "apply_anchor_patch", "args": {"scope": scope, "files": int(len(plan)), "ops": int(ops_done), "content_len": int(len(content))}, "status": "prepared", "changes": []}
            for it in plan:
                manifest["changes"].append({"op": it["op"], "before": self.revision_store.snapshot_path(root, it["rel"])})
            self.revision_store.commit_transaction(txn_id, manifest)
            undo_txn_id: Optional[str] = None
            rolled_back = False
            try:
                for it in plan:
                    os.makedirs(os.path.dirname(it["full"]), exist_ok=True)
                    Path(it["full"]).write_bytes((it.get("new_text") or "").encode("utf-8"))
            except Exception as e:
                if self.revision_store and txn_id:
                    try:
                        undo_txn_id = self.revision_store.undo_transaction(root, str(txn_id))
                        rolled_back = True
                    except Exception:
                        rolled_back = False
                try:
                    if isinstance(manifest, dict):
                        manifest["status"] = "failed_rolled_back" if rolled_back else "failed"
                        manifest["error"] = str(e)
                        if undo_txn_id:
                            manifest["undo_transaction_id"] = str(undo_txn_id)
                        for ch, it2 in zip(manifest.get("changes") or [], plan):
                            try:
                                ch["after"] = self.revision_store.snapshot_path(root, it2["rel"])
                            except Exception:
                                pass
                        try:
                            self.revision_store.commit_transaction(txn_id, manifest)
                        except Exception:
                            pass
                except Exception:
                    pass
                msg = f"Anchor patch apply failed: {e}"
                msg += " (rolled back)" if rolled_back else " (rollback failed)"
                out_err: Dict[str, Any] = {"status": "error", "message": msg, "transaction_id": txn_id}
                if undo_txn_id:
                    out_err["undo_transaction_id"] = str(undo_txn_id)
                return out_err
            for ch, it in zip(manifest.get("changes") or [], plan):
                try:
                    ch["after"] = self.revision_store.snapshot_path(root, it["rel"])
                except Exception:
                    pass
            manifest["status"] = "applied"
            try:
                self.revision_store.commit_transaction(txn_id, manifest)
            except Exception:
                pass
            out = {"status": "success", "message": "Anchor patch applied", "transaction_id": txn_id, "files_touched": int(len(plan)), "ops_applied": int(ops_done)}
            try:
                prev = compute_transaction_diff_preview(self.revision_store, str(txn_id))
                if isinstance(prev, dict) and prev.get("status") == "success":
                    out["__wrap_meta__"] = {"diff_preview": prev}
            except Exception:
                pass
            return out
        except Exception as e:
            return {"status": "error", "message": str(e)}

class FsListTransactionsTool:
    """Tool to list recent filesystem transactions for the CURRENT SESSION.

    Important: this is session-scoped via TransactionsManager (ledger), not the global
    FsRevisionStore index. This prevents giant cross-session dumps that can blow context.
    """

    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "fs_list_transactions",
            "description": (
                "List filesystem revision transactions linked to the CURRENT session (session-scoped). "
                "This tool uses the TransactionsManager ledger (not the global FsRevisionStore index), "
                "so it will not dump cross-session history. "
                "Returns a minimal list of {txn_id, status, tool, scope}."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of transactions to return (clamped to 1..200).",
                    },
                    "survive": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                    },
                },
                "required": ["limit", "survive"],
                "additionalProperties": False,
            },
        }

    def run(self, limit: int = 20, survive: Optional[bool] = None) -> Dict[str, Any]:
        try:
            # Defensive limit clamp: protect the model context window.
            try:
                lim = int(limit)
            except Exception:
                lim = 20
            if lim <= 0:
                lim = 20
            lim = max(1, min(lim, 200))

            # Session scope comes from the ambient RunContext.
            ctx = get_run_context()
            sid = (ctx.session_id or ctx.parent_session_id or "").strip() if ctx else ""
            if not sid:
                return {
                    "status": "error",
                    "message": "No session context available; refusing to list global transactions.",
                }

            if not self.revision_store:
                return {"status": "error", "message": "Fs revision store not available"}

            tm = TransactionsManager()
            recs = tm.list_transactions_for_session(session_id=sid, limit=lim, include_undone=True)

            # Enrich lightly with manifest tool/scope (still bounded by lim).
            out = []
            for r in recs:
                if not isinstance(r, dict):
                    continue
                tid = r.get("txn_id")
                tid = str(tid) if isinstance(tid, str) and tid else None
                tool = None
                scope = None
                try:
                    if tid:
                        manifest = self.revision_store.get_transaction(tid)
                        if isinstance(manifest, dict):
                            tool = manifest.get("tool") if isinstance(manifest.get("tool"), str) else None
                            args = manifest.get("args") if isinstance(manifest.get("args"), dict) else None
                            sc = args.get("scope") if isinstance(args, dict) else None
                            scope = str(sc).strip().lower() if isinstance(sc, str) else None
                except Exception:
                    tool = None
                    scope = None

                out.append({
                    "txn_id": tid,
                    "status": r.get("status"),
                    "tool": tool,
                    "scope": scope,
                })

            result = {
                "status": "success",
                "session_id": sid,
                "limit": lim,
                "transactions": out,
                "count": int(len(out)),
            }
            if survive is False:
                result["__wrap_meta__"] = {"survive": False}
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}


class FsUndoTransactionTool:
    """Tool to undo a filesystem transaction by id."""

    def __init__(self):
        self.revision_store = Runtime.get_fs_revision_store()
        self.schema = {
            "type": "function",
            "name": "fs_undo_transaction",
            "description": "Undo a filesystem transaction within the selected scope (revert recorded filesystem state to its pre-transaction snapshot).",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {
                        "type": "string",
                        "description": "Transaction id to undo.",
                    },
                    "scope": _scope_schema_prop(),
                },
                "required": ["transaction_id", "scope"],
                "additionalProperties": False,
            },
        }

    def run(self, transaction_id: str, scope: Optional[str] = None) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        try:
            root, err = _resolve_scope_root(Runtime.get_paths().get_project_root(), scope)
            if err:
                return {"status": "error", "message": str(err)}
            if not self.revision_store:
                return {"status": "error", "message": "Fs revision store not available"}

            # Defensive: refuse restoring into the wrong scope root.
            # Otherwise a fat-fingered scope can "recreate" project files inside sandbox (or vice versa).
            try:
                manifest = self.revision_store.get_transaction(str(transaction_id))
                if isinstance(manifest, dict):
                    args = manifest.get("args")
                    if isinstance(args, dict):
                        recorded_scope = args.get("scope")
                        if isinstance(recorded_scope, str) and recorded_scope.strip():
                            rs = recorded_scope.strip().lower()
                            if rs in ("project", "sandbox") and rs != scope:
                                return {
                                    "status": "error",
                                    "message": (
                                        f"Scope mismatch: transaction was recorded under scope='{rs}' "
                                        f"but undo requested scope='{scope}'. Refusing to restore into wrong root."
                                    ),
                                    "expected_scope": rs,
                                }
            except Exception:
                # Back-compat / best-effort only.
                pass

            undo_txn_id = self.revision_store.undo_transaction(str(root), transaction_id)
            return {
                "status": "success",
                "undone_transaction_id": transaction_id,
                "undo_transaction_id": undo_txn_id,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
