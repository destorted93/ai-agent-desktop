"""Canvas Studio tools.

These tools manipulate persistent canvas projects stored in the app-data Sandbox.

Design goals:
- Canvas is a *project* (metadata + action log + history), not just an image.
- Same language for UI + agents: operations are recorded as actions.
- Vision loop: canvas_get_image injects the current canvas (or a single layer) into the next model turn.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...canvas import CanvasManager
from ...appcore.runtime_context import Runtime
from ...storage.fs_revisions import FsRevisionError
from ...storage.fs_diff import compute_transaction_diff_preview


def _bus_publish(topic: str, payload: Dict[str, Any]) -> None:
    try:
        Runtime.get_event_bus().publish(str(topic), payload)
    except Exception:
        return


def _history_receipt(meta: Any) -> Dict[str, int]:
    try:
        hist = meta.get("history") if isinstance(meta, dict) else None
        hist = hist if isinstance(hist, dict) else {}
        return {
            "min_rev": int(hist.get("min_rev", 0) or 0),
            "cursor_rev": int(hist.get("cursor_rev", 0) or 0),
            "max_rev": int(hist.get("max_rev", 0) or 0),
        }
    except Exception:
        return {"min_rev": 0, "cursor_rev": 0, "max_rev": 0}


def _layers_receipt(meta: Any) -> Dict[str, Any]:
    """Minimal layers stack for tool outputs (avoid full canvas meta spam)."""
    out: Dict[str, Any] = {"active_layer_id": None, "layers": []}
    try:
        layers_obj = meta.get("layers") if isinstance(meta, dict) else None
        layers_obj = layers_obj if isinstance(layers_obj, dict) else {}
        out["active_layer_id"] = layers_obj.get("active_layer_id")

        layers_list = layers_obj.get("layers") if isinstance(layers_obj, dict) else None
        layers_list = layers_list if isinstance(layers_list, list) else []

        slim = []
        for it in layers_list:
            if not isinstance(it, dict):
                continue
            slim.append(
                {
                    "layer_id": it.get("layer_id"),
                    "name": it.get("name"),
                    "visible": bool(it.get("visible", True)),
                    "opacity": float(it.get("opacity", 1.0) if it.get("opacity") is not None else 1.0),
                    "role": it.get("role"),
                }
            )
        out["layers"] = slim
    except Exception:
        pass
    return out


def _tool_receipt(meta: Any) -> Dict[str, Any]:
    """Minimal current tool state for tool outputs."""
    out: Dict[str, Any] = {"current_tool": None, "current_brush": None}
    try:
        ts = meta.get("tool_state") if isinstance(meta, dict) else None
        ts = ts if isinstance(ts, dict) else {}
        out["current_tool"] = ts.get("current_tool")

        cb = meta.get("current_brush") if isinstance(meta, dict) else None
        cb = cb if isinstance(cb, dict) else None
        if cb is not None:
            out["current_brush"] = {
                "type": cb.get("type"),
                "radius": cb.get("radius"),
                "rgba": cb.get("rgba"),
                "opacity": cb.get("opacity"),
            }
    except Exception:
        pass
    return out


def _canvas_receipt(meta: Any, *, include_basics: bool = False) -> Dict[str, Any]:
    """Minimal canvas receipt safe for chaining ops (keeps context light)."""
    out: Dict[str, Any] = {
        "canvas_id": None,
        "updated_at": None,
        "mode": None,
        "history": _history_receipt(meta),
        "layers_enabled": False,
        "active_layer_id": None,
    }

    try:
        if not isinstance(meta, dict):
            return out

        out["canvas_id"] = meta.get("canvas_id")
        out["updated_at"] = meta.get("updated_at")
        out["mode"] = meta.get("mode")
        out["layers_enabled"] = bool(meta.get("layers_enabled"))

        layers_obj = meta.get("layers") if isinstance(meta.get("layers"), dict) else {}
        out["active_layer_id"] = layers_obj.get("active_layer_id")

        if bool(include_basics):
            out["name"] = meta.get("name")
            out["width"] = meta.get("width")
            out["height"] = meta.get("height")
            bg = meta.get("background") if isinstance(meta.get("background"), dict) else {}
            out["background_rgba"] = [bg.get("r"), bg.get("g"), bg.get("b"), bg.get("a")]
            pa = meta.get("pixel_art") if isinstance(meta.get("pixel_art"), dict) else None
            if isinstance(pa, dict):
                out["pixel_art"] = {"cell_px": pa.get("cell_px")}

    except Exception:
        pass

    return out


def _nullable_int_prop(desc: str, *, minimum: Optional[int] = None, maximum: Optional[int] = None) -> Dict[str, Any]:
    p: Dict[str, Any] = {"anyOf": [{"type": "integer"}, {"type": "null"}], "description": desc}
    if isinstance(minimum, int):
        p["minimum"] = minimum
    if isinstance(maximum, int):
        p["maximum"] = maximum
    return p


def _nullable_str_prop(desc: str) -> Dict[str, Any]:
    return {"anyOf": [{"type": "string"}, {"type": "null"}], "description": desc}


def _scope_schema_prop() -> Dict[str, Any]:
    return {
        "type": "string",
        "enum": ["project", "sandbox"],
        "description": "Filesystem scope (required). Must be 'project' or 'sandbox'. Do not omit; defaulting is not allowed.",
    }


def _resolve_scope_root(scope: str) -> Tuple[Optional[str], Optional[str]]:
    sc = (scope or "").strip().lower()
    if sc == "project":
        return str(Runtime.get_paths().get_project_root()), None
    if sc == "sandbox":
        try:
            return str(Runtime.get_paths().get_sandbox_root(ensure_exists=True)), None
        except Exception as e:
            return None, f"Sandbox unavailable: {e}"
    return None, "Invalid scope (expected 'project' or 'sandbox')"


def _is_safe_path(root: str, rel_path: str) -> bool:
    """Defensive path check: ensure target stays within root."""
    try:
        root_p = Path(root).resolve()
        try:
            target_p = (root_p / rel_path).resolve(strict=False)
        except TypeError:
            target_p = (root_p / rel_path).resolve()
        root_s = os.path.normcase(str(root_p))
        target_s = os.path.normcase(str(target_p))
        return os.path.commonpath([root_s, target_s]) == root_s
    except Exception:
        return False


class CanvasCreateTool:
    """Create a new canvas project."""

    schema = {
        "type": "function",
        "name": "canvas_create",
        "description": "Create a new persistent canvas project in the app Sandbox.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "width": {"type": "integer", "description": "Canvas width in pixels (normal) or cells (pixel_art).", "minimum": 1, "maximum": 16384},
                "height": {"type": "integer", "description": "Canvas height in pixels (normal) or cells (pixel_art).", "minimum": 1, "maximum": 16384},
                "background_rgba": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        {"type": "null"},
                    ],
                    "description": "Background color as [r,g,b,a]. Null defaults to white.",
                },
                "name": _nullable_str_prop("Optional canvas name. Null defaults to 'Untitled'."),
                "transparent_background": {"type": "boolean", "description": "If true, create a transparent background (PNG alpha). Use background_rgba=null to enable this flag."},
                "set_current": {"type": "boolean", "description": "If true, set this canvas as current."},
                "actor": {"type": "string", "description": "Who is performing the action (e.g. 'user', 'aria')."},
                "mode": {"type": "string", "enum": ["normal", "pixel_art"], "description": "Canvas mode."},
                "cell_px": {
                    "anyOf": [{"type": "integer", "minimum": 1, "maximum": 256}, {"type": "null"}],
                    "description": "Pixel-art cell size in pixels (only used when mode='pixel_art'). Null uses default.",
                },
            },
            "required": ["width", "height", "background_rgba", "name", "transparent_background", "set_current", "actor", "mode", "cell_px"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        width: int,
        height: int,
        background_rgba: Optional[List[int]] = None,
        name: Optional[str] = None,
        transparent_background: bool = False,
        set_current: bool = True,
        actor: str = "user",
        mode: str = "normal",
        cell_px: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            if isinstance(background_rgba, list):
                bg = tuple(background_rgba)
            else:
                bg = (255, 255, 255, 0) if bool(transparent_background) else (255, 255, 255, 255)
            meta = self._mgr.create_canvas(
                width=int(width),
                height=int(height),
                background_rgba=bg,  # type: ignore[arg-type]
                name=name,
                set_current=bool(set_current),
                actor=str(actor or "user"),
                mode=str(mode or "normal"),
                cell_px=(int(cell_px) if cell_px is not None else None),
            )
            _bus_publish("canvas.list.changed", {"action": "create", "canvas_id": meta.get("canvas_id"), "current_canvas_id": meta.get("canvas_id"), "source": "tool"})
            _bus_publish("canvas.changed", {"action": "create", "canvas_id": meta.get("canvas_id"), "current_canvas_id": meta.get("canvas_id"), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta, include_basics=True)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasListTool:
    """List canvas projects."""

    schema = {
        "type": "function",
        "name": "canvas_list",
        "description": "List existing canvas projects in the app Sandbox.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, **kwargs) -> Dict[str, Any]:
        try:
            canvases = self._mgr.list_canvases()
            cur = self._mgr.get_current_canvas_id()
            return {"status": "success", "current_canvas_id": cur, "count": len(canvases), "canvases": canvases}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasSetCurrentTool:
    """Set (or clear) the current canvas."""

    schema = {
        "type": "function",
        "name": "canvas_set_current",
        "description": "Set the current canvas (session-like).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id to set current. Null clears current."),
            },
            "required": ["canvas_id"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, canvas_id: Optional[str]) -> Dict[str, Any]:
        try:
            self._mgr.set_current_canvas_id(canvas_id)
            cur = self._mgr.get_current_canvas_id()
            _bus_publish("canvas.list.changed", {"action": "set_current", "canvas_id": canvas_id, "current_canvas_id": cur, "source": "tool"})
            _bus_publish("canvas.changed", {"action": "set_current", "canvas_id": canvas_id, "current_canvas_id": cur, "source": "tool"})
            return {"status": "success", "current_canvas_id": cur}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasGetTool:
    """Get canvas metadata (no image)."""

    schema = {
        "type": "function",
        "name": "canvas_get",
        "description": "Fetch current canvas metadata (no image). Use this to inspect history/layers/tool state. For vision/image injection, use canvas_get_image.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current canvas."),
            },
            "required": ["canvas_id"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, canvas_id: Optional[str]) -> Dict[str, Any]:
        try:
            cid = self._mgr.resolve_canvas_id(canvas_id)
            if not cid:
                raise RuntimeError("No current canvas")
            meta = self._mgr.load_canvas_meta(cid)
            if not meta:
                raise RuntimeError("Canvas not found")
            return {"status": "success", "canvas_id": cid, "canvas": meta}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasGetImageTool:
    """Inject a canvas image (or a single layer) into the next turn."""

    schema = {
        "type": "function",
        "name": "canvas_get_image",
        "description": "Inject the current canvas image into the next model turn as a user input_image. If layer_id is provided, inject that single layer image; if layer_id is null, inject the full composite.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "layer_id": _nullable_str_prop("Layer id. Null uses full composite; non-null injects that layer only."),
                "max_side": _nullable_int_prop("Max side in pixels for injected image. Null uses default.", minimum=64, maximum=4096),
                "caption": _nullable_str_prop("Optional caption text injected alongside the image."),
            },
            "required": ["canvas_id", "layer_id", "max_side", "caption"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, canvas_id: Optional[str], layer_id: Optional[str], max_side: Optional[int] = None, caption: Optional[str] = None) -> Dict[str, Any]:
        try:
            cid = self._mgr.resolve_canvas_id(canvas_id)
            if not cid:
                raise RuntimeError("No current canvas")
            meta = self._mgr.load_canvas_meta(cid)
            if not meta:
                raise RuntimeError("Canvas not found")

            hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
            cursor = int(hist.get("cursor_rev", 0) or 0)

            lid = str(layer_id or "").strip() or None

            if lid:
                _cid2, _m2, png_bytes = self._mgr.get_layer_image_png_bytes(canvas_id=cid, layer_id=lid)
                default_cap = f"Canvas {cid} layer {lid} (cursor_rev={cursor})"
            else:
                _cid2, _m2, png_bytes = self._mgr.get_current_image_png_bytes(canvas_id=cid)
                default_cap = f"Canvas {cid} (cursor_rev={cursor})"

            cap = (str(caption) if isinstance(caption, str) and caption.strip() else default_cap)

            inject_msg = self._mgr._render_injected_image_message_from_png_bytes(
                canvas_id=str(cid),
                meta=meta,
                png_bytes=png_bytes,
                max_side=max_side,
                caption=cap,
            )

            max_side_used = int(max_side) if isinstance(max_side, int) and max_side > 0 else int(self._mgr.default_injected_max_side)

            return {
                "status": "success",
                "canvas_id": cid,
                "cursor_rev": int(cursor),
                "layer_id": lid,
                "image": {"injected": True, "max_side": int(max_side_used)},
                "__inject_message__": inject_msg,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

class CanvasImportImageTool:
    """Import an image file onto a layer with crop/transform/opacity (undoable)."""

    schema = {
        "type": "function",
        "name": "canvas_import_image",
        "description": "Import an image onto the selected layer with crop/resize/rotate/opacity in one commit (undoable). Uses canvas-space dest_rect and source-image crop_rect. For pixel_art canvases, resize/rotate uses NEAREST (no blur).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "layer_id": _nullable_str_prop("Target layer id. Null uses active layer."),
                "scope": _scope_schema_prop(),
                "relative_path": {"type": "string", "description": "Image file path relative to scope root (must not be .gif)."},
                "image_b64": _nullable_str_prop("Optional base64 bytes for the image. If provided, relative_path is ignored."),
                "dest_rect": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "w": {"type": "number"},
                        "h": {"type": "number"},
                    },
                    "required": ["x", "y", "w", "h"],
                    "additionalProperties": False,
                    "description": "Canvas coords destination rect (top-left x/y + width/height).",
                },
                "crop_rect": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {
                                "l": {"type": "integer"},
                                "t": {"type": "integer"},
                                "r": {"type": "integer"},
                                "b": {"type": "integer"},
                            },
                            "required": ["l", "t", "r", "b"],
                            "additionalProperties": False,
                        },
                    ],
                    "description": "Optional crop rect in source image pixel coords (left/top/right/bottom).",
                },
                "rotation_deg": {"type": "number", "description": "Rotation in degrees around dest_rect center."},
                "opacity": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Opacity multiplier 0..1."},
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs."),
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": [
                "canvas_id",
                "layer_id",
                "scope",
                "relative_path",
                "image_b64",
                "dest_rect",
                "crop_rect",
                "rotation_deg",
                "opacity",
                "expected_cursor_rev",
                "actor",
            ],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        layer_id: Optional[str],
        scope: str,
        relative_path: str,
        image_b64: Optional[str],
        dest_rect: Dict[str, Any],
        crop_rect: Optional[Dict[str, Any]],
        rotation_deg: float,
        opacity: float,
        expected_cursor_rev: Optional[int],
        actor: str,
    ) -> Dict[str, Any]:
        # scope validation
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}

        img_bytes: Optional[bytes] = None

        if isinstance(image_b64, str) and image_b64.strip():
            import base64

            try:
                img_bytes = base64.b64decode(image_b64)
            except Exception:
                return {"status": "error", "message": "Invalid image_b64"}
        else:
            rel = str(relative_path or "").strip()
            if not rel:
                return {"status": "error", "message": "relative_path is required when image_b64 is null"}
            if rel.lower().endswith(".gif"):
                return {"status": "error", "message": "GIF import is not supported"}

            root, err = _resolve_scope_root(scope)
            if err:
                return {"status": "error", "message": str(err)}

            rel2 = os.path.normpath(rel).replace("\\", "/")
            if not _is_safe_path(str(root), rel2):
                return {"status": "error", "message": "Path outside scope"}

            full_path = os.path.join(str(root), rel2)
            try:
                img_bytes = Path(full_path).read_bytes()
            except Exception as e:
                return {"status": "error", "message": f"Could not read image: {e}"}

        if not isinstance(img_bytes, (bytes, bytearray)) or not img_bytes:
            return {"status": "error", "message": "Missing image bytes"}

        try:
            meta = self._mgr.import_image_apply(
                canvas_id=canvas_id,
                layer_id=layer_id,
                image_bytes=bytes(img_bytes),
                dest_rect=dest_rect,
                crop_rect=crop_rect,
                rotation_deg=float(rotation_deg or 0.0),
                opacity=float(opacity if opacity is not None else 1.0),
                actor=str(actor or "user"),
                expected_cursor_rev=expected_cursor_rev,
            )
            _bus_publish("canvas.changed", {"action": "image.import", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasExportPngTool:
    """Export the current canvas image as a PNG to the filesystem (project or sandbox)."""

    schema = {
        "type": "function",
        "name": "canvas_export_png",
        "description": "Export the current canvas image as a PNG file (writes to project or sandbox; transaction + diff supported; returns transaction_id + diff preview).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "scope": _scope_schema_prop(),
                "relative_path": {"type": "string", "description": "Directory path relative to the selected scope root. Use '.' for root."},
                "name": {"type": "string", "description": "Output filename ('.png' will be added if missing)."},
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "scope", "relative_path", "name", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)
        self._rev = Runtime.get_fs_revision_store()

    def run(self, canvas_id: Optional[str], scope: str, relative_path: str, name: str, actor: str) -> Dict[str, Any]:
        # --- scope validation (mandatory) ---
        if scope is None:
            return {"status": "error", "message": "scope is required (expected 'project' or 'sandbox')"}
        scope = str(scope).strip().lower()
        if scope not in ("project", "sandbox"):
            return {"status": "error", "message": "Invalid scope (expected 'project' or 'sandbox')"}
        # -----------------------------------

        rel_dir = str(relative_path or ".").strip() or "."
        nm = str(name or "").strip()
        if not nm:
            return {"status": "error", "message": "name is required"}
        if any(sep in nm for sep in ("/", "\\")):
            return {"status": "error", "message": "name must be a filename only (no path separators)"}
        if not nm.lower().endswith(".png"):
            nm = nm + ".png"

        root, err = _resolve_scope_root(scope)
        if err:
            return {"status": "error", "message": str(err)}

        out_rel = os.path.normpath(os.path.join(rel_dir, nm)).replace("\\", "/")
        if not _is_safe_path(str(root), out_rel):
            return {"status": "error", "message": "Path outside scope"}

        try:
            cid, _meta, png_bytes = self._mgr.get_export_image_png_bytes(canvas_id=canvas_id)
        except Exception as e:
            return {"status": "error", "message": str(e)}

        full_path = os.path.join(str(root), out_rel)

        txn_id: Optional[str] = None
        manifest: Optional[Dict[str, Any]] = None

        try:
            if self._rev:
                txn_id = self._rev.begin_transaction(
                    "canvas_export_png",
                    {"canvas_id": cid, "scope": scope, "relative_path": out_rel, "bytes": len(png_bytes)},
                )
                before = self._rev.snapshot_path(str(root), out_rel)
                if before is None:
                    raise FsRevisionError("Missing pre-snapshot; refusing to execute")
                manifest = {
                    "id": txn_id,
                    "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "tool": "canvas_export_png",
                    "args": {"canvas_id": cid, "scope": scope, "relative_path": out_rel, "bytes": len(png_bytes)},
                    "status": "prepared",
                    "changes": [
                        {"op": "write", "before": before},
                    ],
                }
                # Fail closed: if this fails, do NOT write.
                self._rev.commit_transaction(txn_id, manifest)

            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(png_bytes)

            if self._rev and txn_id and manifest:
                after = self._rev.snapshot_path(str(root), out_rel)
                manifest["status"] = "applied"
                if manifest.get("changes") and isinstance(manifest["changes"], list):
                    try:
                        manifest["changes"][0]["after"] = after
                    except Exception:
                        pass
                try:
                    self._rev.commit_transaction(txn_id, manifest)
                except Exception:
                    pass

            out: Dict[str, Any] = {
                "status": "success",
                "message": f"Exported PNG to {out_rel} ({scope})",
                "canvas_id": cid,
                "scope": scope,
                "relative_path": out_rel,
                "bytes": int(len(png_bytes)),
                "transaction_id": txn_id,
            }

            if self._rev and txn_id:
                try:
                    prev = compute_transaction_diff_preview(self._rev, str(txn_id))
                    if isinstance(prev, dict) and prev.get("status") == "success":
                        out["__wrap_meta__"] = {"diff_preview": prev}
                except Exception:
                    pass

            return out

        except FsRevisionError as e:
            return {"status": "error", "message": f"Revision snapshot failed; export aborted: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}




class CanvasSampleColorTool:
    """Sample a pixel RGBA from the current canvas snapshot (eyedropper)."""

    schema = {
        "type": "function",
        "name": "canvas_sample_color",
        "description": "Sample a pixel RGBA from the current canvas snapshot (eyedropper). Use this when you need to match an existing color in the painting. If apply_to_brush=true, the sampled color is applied to the 'round' brush settings (without necessarily switching away from eraser unless you later call canvas_brush_set with brush_type='round').",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "x": {"type": "number", "description": "Canvas-space x coordinate (0..width)."},
                "y": {"type": "number", "description": "Canvas-space y coordinate (0..height)."},
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs."),
                "apply_to_brush": {"type": "boolean", "description": "If true, apply the sampled color to the round brush settings."},
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "x", "y", "expected_cursor_rev", "apply_to_brush", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        x: float,
        y: float,
        expected_cursor_rev: Optional[int],
        apply_to_brush: bool,
        actor: str,
    ) -> Dict[str, Any]:
        try:
            result = self._mgr.sample_color(
                canvas_id=canvas_id,
                x=float(x),
                y=float(y),
                expected_cursor_rev=expected_cursor_rev,
            )
            if not isinstance(result, dict) or result.get("status") != "success":
                return result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"}

            if bool(apply_to_brush):
                rgba = result.get("rgba")
                if isinstance(rgba, list) and len(rgba) == 4:
                    # Patch round tool settings without forcing tool switch.
                    self._mgr.update_tool_settings(
                        canvas_id=result.get("canvas_id"),
                        tool_type="round",
                        rgba=tuple(rgba),  # type: ignore[arg-type]
                        actor=str(actor or "user"),
                        set_current_tool=False,
                    )
                    _bus_publish(
                        "canvas.changed",
                        {
                            "action": "sample_color_apply",
                            "canvas_id": result.get("canvas_id"),
                            "current_canvas_id": self._mgr.get_current_canvas_id(),
                            "source": "tool",
                        },
                    )

            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}
class CanvasBrushSetTool:
    """Set the current stroke tool (brush/eraser) and its settings on a canvas."""

    schema = {
        "type": "function",
        "name": "canvas_brush_set",
        "description": "Set the current stroke tool (brush or eraser) and its parameters for the current canvas. Use brush_type='round' for painting and brush_type='eraser' for local removal or refinement. If brush_type is null, keep the current tool. Note: rgba is required by the schema but is ignored for eraser.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "rgba": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "Brush color as [r,g,b,a].",
                },
                "radius": {"type": "integer", "minimum": 1, "maximum": 4096, "description": "Brush radius in pixels for normal mode, or in cells for pixel_art mode."},
                "opacity": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Opacity multiplier 0..1."},
                "brush_type": _nullable_str_prop("Current tool type. Null keeps the current tool. Known values: 'round', 'eraser'."),
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "rgba", "radius", "opacity", "brush_type", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        rgba: List[int],
        radius: int,
        opacity: float,
        brush_type: Optional[str],
        actor: str,
    ) -> Dict[str, Any]:
        try:
            meta = self._mgr.set_brush(
                canvas_id=canvas_id,
                rgba=tuple(rgba),  # type: ignore[arg-type]
                radius=int(radius),
                opacity=float(opacity),
                actor=str(actor or "user"),
                brush_type=(str(brush_type) if isinstance(brush_type, str) and brush_type.strip() else None),
            )
            _bus_publish("canvas.changed", {"action": "brush_set", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta), "tool": _tool_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasStrokeTool:
    """Draw a stroke on the canvas."""

    schema = {
        "type": "function",
        "name": "canvas_stroke",
        "description": "Draw a brush stroke defined by a list of points. For smooth curves, provide multiple points along the path (more points = smoother stroke).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                        },
                        "required": ["x", "y"],
                        "additionalProperties": False,
                    },
                    "description": "Stroke polyline points in canvas coordinates (0,0 top-left). Provide multiple points along the intended path for a smooth line; a single point makes a dot.",
                },
                "actor": {"type": "string", "description": "Actor label."},
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs.")
            },
            "required": ["canvas_id", "points", "actor", "expected_cursor_rev"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        points: List[Dict[str, Any]],
        actor: str,
        expected_cursor_rev: Optional[int],
    ) -> Dict[str, Any]:
        try:
            pts = []
            for p in points or []:
                if not isinstance(p, dict):
                    continue
                pts.append((float(p.get("x")), float(p.get("y"))))
            meta = self._mgr.draw_stroke(canvas_id=canvas_id, points=pts, actor=str(actor or "user"), expected_cursor_rev=expected_cursor_rev)
            _bus_publish("canvas.changed", {"action": "stroke", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasLineTool:
    """Draw a perfectly straight segment (click-drag line tool)."""

    schema = {
        "type": "function",
        "name": "canvas_line",
        "description": "Draw a perfectly straight line segment from (x1,y1) to (x2,y2) using the current tool settings (color/size/opacity). Line tool uses flat ends (not rounded caps) for crisp geometry.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "x1": {"type": "number", "description": "Start x in canvas coordinates."},
                "y1": {"type": "number", "description": "Start y in canvas coordinates."},
                "x2": {"type": "number", "description": "End x in canvas coordinates."},
                "y2": {"type": "number", "description": "End y in canvas coordinates."},
                "actor": {"type": "string", "description": "Actor label."},
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs."),
            },
            "required": ["canvas_id", "x1", "y1", "x2", "y2", "actor", "expected_cursor_rev"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        actor: str,
        expected_cursor_rev: Optional[int],
    ) -> Dict[str, Any]:
        try:
            meta = self._mgr.draw_line(
                canvas_id=canvas_id,
                x1=float(x1),
                y1=float(y1),
                x2=float(x2),
                y2=float(y2),
                actor=str(actor or "user"),
                expected_cursor_rev=expected_cursor_rev,
            )
            _bus_publish("canvas.changed", {"action": "line", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasShapeTool:
    """Draw a shape (line/rect/ellipse)."""

    schema = {
        "type": "function",
        "name": "canvas_shape",
        "description": "Draw a shape (line/rectangle/ellipse) from drag endpoints (x1,y1) to (x2,y2), using current tool settings (color/size/opacity). Works with layers and pixel_art. Use filled=true to fill rect/ellipse.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "shape": {"type": "string", "description": "Shape kind: 'line' | 'rect' | 'ellipse'."},
                "x1": {"type": "number", "description": "Start x in canvas coordinates."},
                "y1": {"type": "number", "description": "Start y in canvas coordinates."},
                "x2": {"type": "number", "description": "End x in canvas coordinates."},
                "y2": {"type": "number", "description": "End y in canvas coordinates."},
                "filled": {"type": "boolean", "description": "Whether to fill the shape (rect/ellipse only)."},
                "actor": {"type": "string", "description": "Actor label."},
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs."),
            },
            "required": ["canvas_id", "shape", "x1", "y1", "x2", "y2", "filled", "actor", "expected_cursor_rev"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        shape: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        filled: bool,
        actor: str,
        expected_cursor_rev: Optional[int],
    ) -> Dict[str, Any]:
        try:
            meta = self._mgr.draw_shape(
                canvas_id=canvas_id,
                shape=str(shape or ""),
                x1=float(x1),
                y1=float(y1),
                x2=float(x2),
                y2=float(y2),
                filled=bool(filled),
                actor=str(actor or "user"),
                expected_cursor_rev=expected_cursor_rev,
            )
            _bus_publish(
                "canvas.changed",
                {"action": "shape", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"},
            )
            return {"status": "success", "canvas": _canvas_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasFillTool:
    """Bucket fill (auto-mode: transparent-region OR same-color region)."""

    schema = {
        "type": "function",
        "name": "canvas_fill",
        "description": "Bucket fill (auto mode). If the start pixel is transparent (alpha <= alpha_threshold), fills the connected transparent region. Otherwise, fills the connected same-color region (exact RGBA match). Uses current brush color/opacity.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "x": {"type": "number", "description": "Canvas-space x coordinate (0..width)."},
                "y": {"type": "number", "description": "Canvas-space y coordinate (0..height)."},
                "alpha_threshold": _nullable_int_prop("Alpha threshold 0..255. Pixels with alpha <= threshold are considered fillable. Null uses default.", minimum=0, maximum=255),
                "actor": {"type": "string", "description": "Actor label."},
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs."),
            },
            "required": ["canvas_id", "x", "y", "alpha_threshold", "actor", "expected_cursor_rev"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        x: float,
        y: float,
        alpha_threshold: Optional[int],
        actor: str,
        expected_cursor_rev: Optional[int],
    ) -> Dict[str, Any]:
        try:
            meta = self._mgr.fill_bucket(
                canvas_id=canvas_id,
                x=float(x),
                y=float(y),
                alpha_threshold=alpha_threshold,
                actor=str(actor or "user"),
                expected_cursor_rev=expected_cursor_rev,
            )
            _bus_publish("canvas.changed", {"action": "fill", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasUndoTool:
    schema = {
        "type": "function",
        "name": "canvas_undo",
        "description": "Undo one or more recent committed operations on the canvas (like Ctrl+Z in the UI). Prefer this for fresh mistakes; use the eraser instead when later good work should remain or only a local area needs cleanup.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "steps": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "How many operations to undo (1 = typical; higher = multiple Ctrl+Z)."},
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "steps", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, canvas_id: Optional[str], steps: int, actor: str) -> Dict[str, Any]:
        try:
            meta = self._mgr.undo(canvas_id=canvas_id, steps=int(steps), actor=str(actor or "user"))
            _bus_publish("canvas.changed", {"action": "undo", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasRedoTool:
    schema = {
        "type": "function",
        "name": "canvas_redo",
        "description": "Redo one or more undone operations on the canvas (like Ctrl+Y in the UI). Use this after undo to restore strokes.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "steps": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "How many operations to redo (1 = typical; higher = multiple Ctrl+Y)."},
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "steps", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, canvas_id: Optional[str], steps: int, actor: str) -> Dict[str, Any]:
        try:
            meta = self._mgr.redo(canvas_id=canvas_id, steps=int(steps), actor=str(actor or "user"))
            _bus_publish("canvas.changed", {"action": "redo", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasDeleteTool:
    schema = {
        "type": "function",
        "name": "canvas_delete",
        "description": "Delete a canvas project from the app Sandbox.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": {"type": "string", "description": "Canvas id to delete."},
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, canvas_id: str, actor: str) -> Dict[str, Any]:
        try:
            self._mgr.delete_canvas(canvas_id=str(canvas_id), actor=str(actor or "user"))
            cur = self._mgr.get_current_canvas_id()
            _bus_publish("canvas.list.changed", {"action": "delete", "canvas_id": str(canvas_id), "current_canvas_id": cur, "source": "tool"})
            _bus_publish("canvas.changed", {"action": "delete", "canvas_id": str(canvas_id), "current_canvas_id": cur, "source": "tool"})
            return {"status": "success", "deleted_canvas_id": str(canvas_id), "current_canvas_id": cur}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasRenameTool:
    schema = {
        "type": "function",
        "name": "canvas_rename",
        "description": "Rename a canvas project.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": {"type": "string", "description": "Canvas id to rename."},
                "name": {"type": "string", "description": "New canvas name."},
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "name", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, canvas_id: str, name: str, actor: str) -> Dict[str, Any]:
        try:
            meta = self._mgr.rename_canvas(canvas_id=str(canvas_id), name=str(name), actor=str(actor or "user"))
            _bus_publish("canvas.list.changed", {"action": "rename", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            _bus_publish("canvas.changed", {"action": "rename", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta, include_basics=True)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasDuplicateTool:
    schema = {
        "type": "function",
        "name": "canvas_duplicate",
        "description": "Duplicate a canvas (copies the current state; does not clone full undo history).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "source_canvas_id": {"type": "string", "description": "Source canvas id to copy."},
                "name": _nullable_str_prop("Optional new canvas name. Null defaults to '<source> Copy'."),
                "set_current": {"type": "boolean", "description": "If true, set the duplicate as current."},
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["source_canvas_id", "name", "set_current", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(self, source_canvas_id: str, name: Optional[str], set_current: bool, actor: str) -> Dict[str, Any]:
        try:
            meta = self._mgr.duplicate_canvas(
                source_canvas_id=str(source_canvas_id),
                name=(str(name) if isinstance(name, str) else None),
                set_current=bool(set_current),
                actor=str(actor or "user"),
            )
            cur = self._mgr.get_current_canvas_id()
            _bus_publish("canvas.list.changed", {"action": "duplicate", "canvas_id": meta.get("canvas_id"), "current_canvas_id": cur, "source": "tool"})
            _bus_publish("canvas.changed", {"action": "duplicate", "canvas_id": meta.get("canvas_id"), "current_canvas_id": cur, "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta, include_basics=True), "current_canvas_id": cur}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ----------------------------
# Layers (Phase 2)
# ----------------------------


class CanvasLayerCreateTool:
    """Create a new layer (optionally duplicating an existing layer)."""

    schema = {
        "type": "function",
        "name": "canvas_layer_create",
        "description": "Create a new layer on the current canvas (optionally duplicated from an existing layer).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "name": _nullable_str_prop("Optional layer name."),
                "description": _nullable_str_prop("Optional layer description."),
                "set_active": {"type": "boolean", "description": "If true, make the new layer active."},
                "source_layer_id": _nullable_str_prop("If provided, duplicate pixels from this layer id (at the current cursor_rev)."),
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs."),
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "name", "description", "set_active", "source_layer_id", "expected_cursor_rev", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        name: Optional[str],
        description: Optional[str],
        set_active: bool,
        source_layer_id: Optional[str],
        expected_cursor_rev: Optional[int],
        actor: str,
    ) -> Dict[str, Any]:
        try:
            meta = self._mgr.layer_create(
                canvas_id=canvas_id,
                name=name,
                description=description,
                set_active=bool(set_active),
                source_layer_id=source_layer_id,
                expected_cursor_rev=expected_cursor_rev,
                actor=str(actor or "user"),
            )
            _bus_publish("canvas.changed", {"action": "layer.create", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta), "layers": _layers_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasLayerUpdateTool:
    """Update a layer (rename/description/visibility/opacity/reorder/activate)."""

    schema = {
        "type": "function",
        "name": "canvas_layer_update",
        "description": "Update a layer (rename/visibility/opacity/reorder/set active). Layer ops create a new history rev.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "layer_id": {"type": "string", "description": "Layer id to update."},
                "name": _nullable_str_prop("New name (null = no change)."),
                "description": _nullable_str_prop("New description (null = no change)."),
                "clear_description": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "description": "If true, clear the description (null = no change)."},
                "visible": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "description": "Set layer visibility (null = no change)."},
                "opacity": {"anyOf": [{"type": "number", "minimum": 0.0, "maximum": 1.0}, {"type": "null"}], "description": "Set layer opacity 0..1 (null = no change)."},
                "move_to_index": _nullable_int_prop("Move layer to this index (bottom->top). Null = no move.", minimum=0, maximum=16384),
                "set_active": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "description": "If true, make this layer active (null = no change)."},
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs."),
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": [
                "canvas_id",
                "layer_id",
                "name",
                "description",
                "clear_description",
                "visible",
                "opacity",
                "move_to_index",
                "set_active",
                "expected_cursor_rev",
                "actor",
            ],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        layer_id: str,
        name: Optional[str],
        description: Optional[str],
        clear_description: Optional[bool],
        visible: Optional[bool],
        opacity: Optional[float],
        move_to_index: Optional[int],
        set_active: Optional[bool],
        expected_cursor_rev: Optional[int],
        actor: str,
    ) -> Dict[str, Any]:
        try:
            meta = self._mgr.layer_update(
                canvas_id=canvas_id,
                layer_id=str(layer_id),
                name=name,
                description=description,
                clear_description=clear_description,
                visible=visible,
                opacity=opacity,
                move_to_index=move_to_index,
                set_active=set_active,
                expected_cursor_rev=expected_cursor_rev,
                actor=str(actor or "user"),
            )
            _bus_publish("canvas.changed", {"action": "layer.update", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta), "layers": _layers_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CanvasLayerDeleteTool:
    """Delete a layer (cannot delete Background)."""

    schema = {
        "type": "function",
        "name": "canvas_layer_delete",
        "description": "Delete a layer (cannot delete Background). Layer ops create a new history rev.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_id": _nullable_str_prop("Canvas id. Null uses current."),
                "layer_id": {"type": "string", "description": "Layer id to delete."},
                "expected_cursor_rev": _nullable_int_prop("If provided, fail if the current history cursor differs."),
                "actor": {"type": "string", "description": "Actor label."},
            },
            "required": ["canvas_id", "layer_id", "expected_cursor_rev", "actor"],
            "additionalProperties": False,
        },
    }

    def __init__(self, manager: Optional[CanvasManager] = None):
        self._mgr = manager or CanvasManager(default_injected_max_side=1024)

    def run(
        self,
        canvas_id: Optional[str],
        layer_id: str,
        expected_cursor_rev: Optional[int],
        actor: str,
    ) -> Dict[str, Any]:
        try:
            meta = self._mgr.layer_delete(
                canvas_id=canvas_id,
                layer_id=str(layer_id),
                expected_cursor_rev=expected_cursor_rev,
                actor=str(actor or "user"),
            )
            _bus_publish("canvas.changed", {"action": "layer.delete", "canvas_id": meta.get("canvas_id"), "current_canvas_id": self._mgr.get_current_canvas_id(), "source": "tool"})
            return {"status": "success", "canvas": _canvas_receipt(meta), "layers": _layers_receipt(meta)}
        except Exception as e:
            return {"status": "error", "message": str(e)}
