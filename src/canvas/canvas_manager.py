from __future__ import annotations

import base64
import json
import math
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageChops

from ..storage.sandbox_storage import get_sandbox_root
from .brushes import (
    STROKE_TOOL_ENGINES,
    AlphaEraserEngine,
    StrokeToolType,
    ToolSettings,
    ToolState,
    parse_stroke_tool_type,
)


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _clamp_int(v: Any, lo: int, hi: int) -> int:
    try:
        i = int(v)
    except Exception:
        i = lo
    return max(lo, min(hi, i))


def _clamp_float(v: Any, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except Exception:
        x = lo
    return max(lo, min(hi, x))


def _rgba_tuple(rgba: Any) -> Tuple[int, int, int, int]:
    if isinstance(rgba, (list, tuple)) and len(rgba) == 4:
        r, g, b, a = rgba
        return (
            _clamp_int(r, 0, 255),
            _clamp_int(g, 0, 255),
            _clamp_int(b, 0, 255),
            _clamp_int(a, 0, 255),
        )
    # default black
    return (0, 0, 0, 255)


def _densify_points(points: List[Tuple[float, float]], step: float) -> List[Tuple[float, float]]:
    if not points:
        return []
    if len(points) == 1:
        return points
    out: List[Tuple[float, float]] = [points[0]]
    step = float(step) if step and step > 0 else 1.0

    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx = x2 - x1
        dy = y2 - y1
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= step:
            out.append((x2, y2))
            continue
        n = max(1, int(dist / step))
        for i in range(1, n + 1):
            t = i / n
            out.append((x1 + dx * t, y1 + dy * t))
    return out


class CanvasManager:
    """Manage persistent canvas projects in the app-data Sandbox.

    This is the backend for Canvas Studio.
    """

    def __init__(self, *, default_injected_max_side: int = 1024):
        self.default_injected_max_side = int(default_injected_max_side or 1024)

    # ----------------------------
    # Paths
    # ----------------------------

    def _root(self) -> Path:
        return get_sandbox_root(ensure_exists=True) / "canvases"

    def _index_path(self) -> Path:
        return self._root() / "index.json"

    def _current_path(self) -> Path:
        return self._root() / "current.json"

    def _canvas_dir(self, canvas_id: str) -> Path:
        return self._root() / str(canvas_id)

    def _canvas_json_path(self, canvas_id: str) -> Path:
        return self._canvas_dir(canvas_id) / "canvas.json"

    def _actions_path(self, canvas_id: str) -> Path:
        return self._canvas_dir(canvas_id) / "actions.jsonl"

    def _snapshots_dir(self, canvas_id: str) -> Path:
        return self._canvas_dir(canvas_id) / "history" / "snapshots"

    def _snapshot_path(self, canvas_id: str, rev: int) -> Path:
        return self._snapshots_dir(canvas_id) / f"{int(rev):06d}.png"

    # Layer snapshots (V2)
    def _layers_root_dir(self, canvas_id: str) -> Path:
        return self._canvas_dir(canvas_id) / "history" / "layers"

    def _layer_snapshots_dir(self, canvas_id: str, layer_id: str) -> Path:
        return self._layers_root_dir(canvas_id) / str(layer_id) / "snapshots"

    def _layer_snapshot_path(self, canvas_id: str, layer_id: str, rev: int) -> Path:
        return self._layer_snapshots_dir(canvas_id, layer_id) / f"{int(rev):06d}.png"

    # Layer stack state snapshots (per rev)
    def _layers_state_dir(self, canvas_id: str) -> Path:
        return self._canvas_dir(canvas_id) / "history" / "layers_state"

    def _layers_state_path(self, canvas_id: str, rev: int) -> Path:
        return self._layers_state_dir(canvas_id) / f"{int(rev):06d}.json"

    # ----------------------------
    # Index + current
    # ----------------------------

    def list_canvases(self) -> List[Dict[str, Any]]:
        idx = _read_json(self._index_path(), {"schema_version": 1, "canvases": []})
        canvases = idx.get("canvases") if isinstance(idx, dict) else None
        if not isinstance(canvases, list):
            return []

        # Keep newest first.
        def key(it: Dict[str, Any]) -> str:
            try:
                return str(it.get("updated_at") or "")
            except Exception:
                return ""

        out = [c for c in canvases if isinstance(c, dict)]
        out.sort(key=key, reverse=True)
        return out

    def get_current_canvas_id(self) -> Optional[str]:
        cur = _read_json(self._current_path(), {})
        if isinstance(cur, dict):
            cid = cur.get("canvas_id")
            if isinstance(cid, str) and cid.strip():
                return cid.strip()
        return None

    def set_current_canvas_id(self, canvas_id: Optional[str]) -> None:
        if canvas_id is None:
            _write_json_atomic(self._current_path(), {"schema_version": 1, "canvas_id": None, "updated_at": _utc_now_z()})
            return
        _write_json_atomic(self._current_path(), {"schema_version": 1, "canvas_id": str(canvas_id), "updated_at": _utc_now_z()})

    # ----------------------------
    # Canvas lifecycle
    # ----------------------------

    def create_canvas(
        self,
        *,
        width: int,
        height: int,
        background_rgba: Tuple[int, int, int, int] = (255, 255, 255, 255),
        name: Optional[str] = None,
        set_current: bool = True,
        actor: str = "user",
        mode: str = "normal",
        cell_px: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a new canvas.

        Modes:
        - normal: current Paint-like raster canvas.
        - pixel_art: logical grid canvas (width/height are *cells*).
          `cell_px` is the intended display/export scale (UI overlay; export scales up using NEAREST).
        """
        w = _clamp_int(width, 1, 16384)
        h = _clamp_int(height, 1, 16384)
        bg = _rgba_tuple(background_rgba)

        m = str(mode or "normal").strip().lower() or "normal"
        if m not in ("normal", "pixel_art"):
            m = "normal"

        # Pixel-art cell size (display/export scale). Keep conservative caps.
        cp = 10 if cell_px is None else _clamp_int(cell_px, 1, 256)

        canvas_id = f"c_{uuid.uuid4().hex[:12]}"
        created_at = _utc_now_z()

        # Tool defaults
        if m == "pixel_art":
            # radius=1 means "1 cell" in our pixel-art semantics.
            round_settings = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=(0, 0, 0, 255), radius=1, opacity=1.0)
            eraser_settings = ToolSettings(tool_type=StrokeToolType.ERASER, rgba=round_settings.rgba, radius=1, opacity=1.0)
        else:
            round_settings = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=(0, 0, 0, 255), radius=12, opacity=1.0)
            eraser_settings = ToolSettings(tool_type=StrokeToolType.ERASER, rgba=round_settings.rgba, radius=24, opacity=1.0)

        tool_state = ToolState(
            current_tool=StrokeToolType.ROUND,
            settings_by_tool={
                StrokeToolType.ROUND: round_settings,
                StrokeToolType.ERASER: eraser_settings,
            },
        )

        meta: Dict[str, Any] = {
            "schema_version": 1,
            "canvas_id": canvas_id,
            "name": (name or "Untitled"),
            "created_at": created_at,
            "updated_at": created_at,
            "mode": m,
            "width": int(w),
            "height": int(h),
            "background": {"r": bg[0], "g": bg[1], "b": bg[2], "a": bg[3]},
            "history": {
                "min_rev": 0,
                "cursor_rev": 0,
                "max_rev": 0,
            },
        }

        if m == "pixel_art":
            meta["pixel_art"] = {
                "schema_version": 1,
                "cell_px": int(cp),
            }

        # Layers (Phase 2): enabled for all canvases. Every canvas starts with a non-deletable
        # Background layer (layer 0). If background alpha is 0, the background layer is transparent.
        bg_layer_id = f"l_{uuid.uuid4().hex[:12]}"
        meta["layers_enabled"] = True
        meta["layers"] = {
            "schema_version": 1,
            "active_layer_id": bg_layer_id,
            "layers": [
                {
                    "layer_id": bg_layer_id,
                    "name": "Background",
                    "description": None,
                    "visible": True,
                    "opacity": 1.0,
                    "role": "background",
                }
            ],
        }

        tool_state.apply_to_meta(meta)

        cdir = self._canvas_dir(canvas_id)
        cdir.mkdir(parents=True, exist_ok=True)

        # Initial snapshots (rev 0)
        bg_layer = Image.new("RGBA", (w, h), bg)
        self._layer_snapshots_dir(canvas_id, bg_layer_id).mkdir(parents=True, exist_ok=True)
        bg_layer.save(self._layer_snapshot_path(canvas_id, bg_layer_id, 0), format="PNG")

        composite = self._composite_layers_for_rev(canvas_id=canvas_id, meta=meta, rev=0)
        self._snapshots_dir(canvas_id).mkdir(parents=True, exist_ok=True)
        composite.save(self._snapshot_path(canvas_id, 0), format="PNG")

        _write_json_atomic(self._canvas_json_path(canvas_id), meta)
        self._write_layers_state_snapshot(canvas_id=canvas_id, rev=0, meta=meta)

        # Action log
        _append_jsonl(
            self._actions_path(canvas_id),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": created_at,
                "actor": str(actor or "user"),
                "type": "canvas.create",
                "canvas_id": canvas_id,
                "payload": {
                    "width": w,
                    "height": h,
                    "background_rgba": list(bg),
                    "name": meta["name"],
                    "mode": m,
                    "cell_px": (int(cp) if m == "pixel_art" else None),
                },
            },
        )

        # Update index
        self._upsert_index_entry(
            {
                "canvas_id": canvas_id,
                "name": meta["name"],
                "created_at": created_at,
                "updated_at": created_at,
                "width": w,
                "height": h,
                "mode": m,
            }
        )

        if set_current:
            self.set_current_canvas_id(canvas_id)

        return meta

    def delete_canvas(self, *, canvas_id: str, actor: str = "user") -> None:
        cid = str(canvas_id or "").strip()
        if not cid:
            return

        # Remove folder
        cdir = self._canvas_dir(cid)
        if cdir.exists() and cdir.is_dir():
            for _ in range(2):
                try:
                    import shutil

                    shutil.rmtree(cdir)
                    break
                except Exception:
                    time.sleep(0.05)

        # Update index
        self._remove_index_entry(cid)

        # Clear current if needed
        if self.get_current_canvas_id() == cid:
            self.set_current_canvas_id(None)

    # ----------------------------
    # Loading / metadata
    # ----------------------------

    def load_canvas_meta(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        cid = str(canvas_id or "").strip()
        if not cid:
            return None
        path = self._canvas_json_path(cid)
        meta = _read_json(path, None)
        return meta if isinstance(meta, dict) else None

    def resolve_canvas_id(self, canvas_id: Optional[str]) -> Optional[str]:
        if isinstance(canvas_id, str) and canvas_id.strip():
            return canvas_id.strip()
        return self.get_current_canvas_id()

    def _background_rgba_from_meta(self, meta: Dict[str, Any]) -> Tuple[int, int, int, int]:
        bg_obj = meta.get("background") if isinstance(meta.get("background"), dict) else {}
        return (
            _clamp_int(bg_obj.get("r", 255), 0, 255),
            _clamp_int(bg_obj.get("g", 255), 0, 255),
            _clamp_int(bg_obj.get("b", 255), 0, 255),
            _clamp_int(bg_obj.get("a", 255), 0, 255),
        )

    def _layers_enabled(self, meta: Dict[str, Any]) -> bool:
        return bool(meta.get("layers_enabled"))

    def _layers_state(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        out = meta.get("layers") if isinstance(meta.get("layers"), dict) else {}
        return out if isinstance(out, dict) else {}

    def _layers_list(self, meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        st = self._layers_state(meta)
        layers = st.get("layers") if isinstance(st.get("layers"), list) else []
        return [x for x in layers if isinstance(x, dict)]

    def _active_layer_id(self, meta: Dict[str, Any]) -> Optional[str]:
        st = self._layers_state(meta)
        lid = st.get("active_layer_id")
        if isinstance(lid, str) and lid.strip():
            return lid.strip()
        # Fallback: first layer.
        layers = self._layers_list(meta)
        if layers:
            lid2 = layers[0].get("layer_id")
            if isinstance(lid2, str) and lid2.strip():
                return lid2.strip()
        return None

    def _find_layer(self, meta: Dict[str, Any], layer_id: str) -> Optional[Dict[str, Any]]:
        lid = str(layer_id or "").strip()
        if not lid:
            return None
        for layer in self._layers_list(meta):
            if str(layer.get("layer_id") or "").strip() == lid:
                return layer
        return None

    def _active_layer(self, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        lid = self._active_layer_id(meta)
        if not lid:
            return None
        return self._find_layer(meta, lid)

    def _layer_role(self, layer: Optional[Dict[str, Any]]) -> str:
        if not isinstance(layer, dict):
            return "layer"
        r = str(layer.get("role") or "").strip().lower()
        return r if r else "layer"

    def _background_layer_id(self, meta: Dict[str, Any]) -> Optional[str]:
        # Prefer explicit role.
        for layer in self._layers_list(meta):
            if self._layer_role(layer) == "background":
                lid = layer.get("layer_id")
                if isinstance(lid, str) and lid.strip():
                    return lid.strip()
        # Fallback: first layer.
        layers = self._layers_list(meta)
        if layers:
            lid2 = layers[0].get("layer_id")
            if isinstance(lid2, str) and lid2.strip():
                return lid2.strip()
        return None

    def _is_background_layer_id(self, meta: Dict[str, Any], layer_id: str) -> bool:
        try:
            return str(self._background_layer_id(meta) or "") == str(layer_id or "")
        except Exception:
            return False

    def _write_layers_state_snapshot(self, *, canvas_id: str, rev: int, meta: Dict[str, Any]) -> None:
        if not self._layers_enabled(meta):
            return
        data = {
            "schema_version": 1,
            "rev": int(rev),
            "layers": self._layers_state(meta),
        }
        _write_json_atomic(self._layers_state_path(canvas_id, int(rev)), data)

    def _read_layers_state_snapshot(self, *, canvas_id: str, rev: int) -> Optional[Dict[str, Any]]:
        p = self._layers_state_path(canvas_id, int(rev))
        obj = _read_json(p, None)
        if not isinstance(obj, dict):
            return None
        layers = obj.get("layers")
        return layers if isinstance(layers, dict) else None

    def _ensure_layers_state_snapshots(self, *, canvas_id: str, meta: Dict[str, Any]) -> None:
        """Ensure `layers_state/<rev>.json` exists for all existing revs.

        This is a stability shim for older canvases that had layers-enabled before we started
        versioning the layer stack. In those canvases, the stack is effectively constant.
        """
        if not self._layers_enabled(meta):
            return
        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        min_rev = int(hist.get("min_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", 0) or 0)
        # Fast check: if cursor snapshot exists, assume the set is populated.
        cur = int(hist.get("cursor_rev", max_rev) or max_rev)
        if self._layers_state_path(canvas_id, cur).exists():
            return
        for r in range(int(min_rev), int(max_rev) + 1):
            try:
                self._write_layers_state_snapshot(canvas_id=canvas_id, rev=int(r), meta=meta)
            except Exception:
                pass

    def _layer_snapshot_exists(self, canvas_id: str, layer_id: str, rev: int) -> bool:
        try:
            return self._layer_snapshot_path(canvas_id, layer_id, rev).exists()
        except Exception:
            return False

    def _load_layer_image(self, *, canvas_id: str, layer_id: str, rev: int, size: Tuple[int, int]) -> Image.Image:
        p = self._layer_snapshot_path(canvas_id, layer_id, rev)
        if p.exists():
            try:
                return Image.open(p).convert("RGBA")
            except Exception:
                pass
        # Missing layer snapshot: treat as empty.
        return Image.new("RGBA", (int(size[0]), int(size[1])), (0, 0, 0, 0))

    def _save_layer_image(self, *, canvas_id: str, layer_id: str, rev: int, img: Image.Image) -> None:
        self._layer_snapshots_dir(canvas_id, layer_id).mkdir(parents=True, exist_ok=True)
        img.save(self._layer_snapshot_path(canvas_id, layer_id, rev), format="PNG")

    def _composite_layers_for_rev(self, *, canvas_id: str, meta: Dict[str, Any], rev: int) -> Image.Image:
        bg = self._background_rgba_from_meta(meta)
        w = int(meta.get("width") or 0)
        h = int(meta.get("height") or 0)

        # Layered canvases composite from transparent and rely on the background layer
        # (if present) to provide an opaque base.
        if self._layers_enabled(meta):
            base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        else:
            base = Image.new("RGBA", (w, h), bg)

        out = base
        for layer in self._layers_list(meta):
            if not bool(layer.get("visible", True)):
                continue
            opacity = layer.get("opacity", 1.0)
            try:
                op = max(0.0, min(1.0, float(opacity)))
            except Exception:
                op = 1.0
            if op <= 0.0:
                continue
            lid = layer.get("layer_id")
            if not (isinstance(lid, str) and lid.strip()):
                continue
            img = self._load_layer_image(canvas_id=canvas_id, layer_id=lid.strip(), rev=int(rev), size=(w, h))
            if op < 0.999:
                try:
                    a = img.getchannel("A")
                    img.putalpha(a.point(lambda p: int(p * op)))
                except Exception:
                    pass
            out = Image.alpha_composite(out, img)

        return out

    def rename_canvas(self, *, canvas_id: str, name: str, actor: str = "user") -> Dict[str, Any]:
        cid = str(canvas_id or "").strip()
        nm = (name or "").strip()
        if not cid:
            raise RuntimeError("canvas_id is required")
        if not nm:
            raise RuntimeError("name is required")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        now = _utc_now_z()
        meta["name"] = nm
        meta["updated_at"] = now
        _write_json_atomic(self._canvas_json_path(cid), meta)

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "canvas.rename",
                "canvas_id": cid,
                "payload": {"name": nm},
            },
        )

        self._update_index_entry(cid, {"name": nm, "updated_at": now})
        return meta

    def duplicate_canvas(
        self,
        *,
        source_canvas_id: str,
        name: Optional[str] = None,
        set_current: bool = True,
        actor: str = "user",
    ) -> Dict[str, Any]:
        src_id = str(source_canvas_id or "").strip()
        if not src_id:
            raise RuntimeError("source_canvas_id is required")

        src_meta = self.load_canvas_meta(src_id)
        if not src_meta:
            raise RuntimeError("Source canvas not found")

        # Copy current head snapshot bytes.
        _cid, _m, png_bytes = self.get_current_image_png_bytes(canvas_id=src_id)

        created_at = _utc_now_z()

        w = int(src_meta.get("width") or 0)
        h = int(src_meta.get("height") or 0)
        if w <= 0 or h <= 0:
            raise RuntimeError("Source canvas has invalid dimensions")

        bg = self._background_rgba_from_meta(src_meta)

        src_name = str(src_meta.get("name") or "Untitled")
        nm = (name or "").strip() or (src_name + " Copy")

        canvas_id = f"c_{uuid.uuid4().hex[:12]}"

        tool_state = ToolState.from_meta(src_meta)

        meta: Dict[str, Any] = {
            "schema_version": 1,
            "canvas_id": canvas_id,
            "name": nm,
            "created_at": created_at,
            "updated_at": created_at,
            "width": int(w),
            "height": int(h),
            "background": {"r": bg[0], "g": bg[1], "b": bg[2], "a": bg[3]},
            "history": {
                "min_rev": 0,
                "cursor_rev": 0,
                "max_rev": 0,
            },
            "source_canvas_id": src_id,
            "mode": str(src_meta.get("mode") or "normal"),
        }

        # Preserve pixel-art metadata (scale intent).
        try:
            if str(meta.get("mode") or "").strip().lower() == "pixel_art":
                pa = src_meta.get("pixel_art")
                if isinstance(pa, dict):
                    meta["pixel_art"] = dict(pa)
        except Exception:
            pass

        # Layered duplication (preserve layers only if the source is layered).
        if self._layers_enabled(src_meta):
            src_layers = self._layers_list(src_meta)
            src_layers_state = self._layers_state(src_meta)
            src_cursor = int((src_meta.get("history") or {}).get("cursor_rev", 0) or 0) if isinstance(src_meta.get("history"), dict) else 0

            id_map: Dict[str, str] = {}
            new_layers: List[Dict[str, Any]] = []

            for i, layer in enumerate(src_layers):
                src_lid = layer.get("layer_id")
                if not (isinstance(src_lid, str) and src_lid.strip()):
                    continue
                new_lid = f"l_{uuid.uuid4().hex[:12]}"
                id_map[src_lid.strip()] = new_lid
                new_layers.append(
                    {
                        "layer_id": new_lid,
                        "name": str(layer.get("name") or f"Layer {len(new_layers) + 1}"),
                        "description": (str(layer.get("description")) if isinstance(layer.get("description"), str) else None),
                        "visible": bool(layer.get("visible", True)),
                        "opacity": float(layer.get("opacity", 1.0) or 1.0),
                        "role": (str(layer.get("role") or "layer") or "layer"),
                    }
                )

            # If source layers were malformed, fall back to a single layer.
            if not new_layers:
                new_lid = f"l_{uuid.uuid4().hex[:12]}"
                new_layers = [{"layer_id": new_lid, "name": "Background", "description": None, "visible": True, "opacity": 1.0, "role": "background"}]

            src_active = src_layers_state.get("active_layer_id")
            src_active_id = str(src_active).strip() if isinstance(src_active, str) else None
            new_active_id = id_map.get(src_active_id or "") or str(new_layers[0].get("layer_id"))

            meta["layers_enabled"] = True
            meta["layers"] = {
                "schema_version": 1,
                "active_layer_id": new_active_id,
                "layers": new_layers,
            }

        tool_state.apply_to_meta(meta)

        cdir = self._canvas_dir(canvas_id)
        cdir.mkdir(parents=True, exist_ok=True)

        if self._layers_enabled(meta):
            # Copy per-layer current snapshots into rev 0.
            src_hist = src_meta.get("history") if isinstance(src_meta.get("history"), dict) else {}
            src_cursor = int(src_hist.get("cursor_rev", 0) or 0)

            for layer in self._layers_list(meta):
                lid = layer.get("layer_id")
                if not (isinstance(lid, str) and lid.strip()):
                    continue
                # Try to find the corresponding source layer by name/order.
                # (We kept order; map by index if possible.)
                # Best-effort: use source layer at same index.
                try:
                    idx = self._layers_list(meta).index(layer)
                except Exception:
                    idx = 0
                src_layers = self._layers_list(src_meta)
                src_lid = None
                if 0 <= idx < len(src_layers):
                    src_lid = src_layers[idx].get("layer_id")
                if not (isinstance(src_lid, str) and src_lid.strip()):
                    src_lid = self._active_layer_id(src_meta)

                if isinstance(src_lid, str) and src_lid.strip() and self._layer_snapshot_path(src_id, src_lid.strip(), src_cursor).exists():
                    data = self._layer_snapshot_path(src_id, src_lid.strip(), src_cursor).read_bytes()
                    self._layer_snapshots_dir(canvas_id, lid.strip()).mkdir(parents=True, exist_ok=True)
                    self._layer_snapshot_path(canvas_id, lid.strip(), 0).write_bytes(data)
                else:
                    # Fallback: blank layer
                    blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    self._save_layer_image(canvas_id=canvas_id, layer_id=lid.strip(), rev=0, img=blank)

            composite = self._composite_layers_for_rev(canvas_id=canvas_id, meta=meta, rev=0)
            self._snapshots_dir(canvas_id).mkdir(parents=True, exist_ok=True)
            composite.save(self._snapshot_path(canvas_id, 0), format="PNG")

        else:
            # Legacy: copy the flattened composite snapshot.
            self._snapshots_dir(canvas_id).mkdir(parents=True, exist_ok=True)
            self._snapshot_path(canvas_id, 0).write_bytes(png_bytes)

        _write_json_atomic(self._canvas_json_path(canvas_id), meta)

        _append_jsonl(
            self._actions_path(canvas_id),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": created_at,
                "actor": str(actor or "user"),
                "type": "canvas.duplicate",
                "canvas_id": canvas_id,
                "payload": {
                    "source_canvas_id": src_id,
                    "name": nm,
                    "mode": str(meta.get("mode") or "normal"),
                    "cell_px": (int(self._pixel_cell_px(meta)) if self._is_pixel_art(meta) else None),
                },
            },
        )

        self._upsert_index_entry(
            {
                "canvas_id": canvas_id,
                "name": nm,
                "created_at": created_at,
                "updated_at": created_at,
                "width": w,
                "height": h,
                "mode": str(meta.get("mode") or "normal"),
            }
        )

        if set_current:
            self.set_current_canvas_id(canvas_id)

        return meta

    # ----------------------------
    # Tool / drawing
    # ----------------------------

    def _is_pixel_art(self, meta: Any) -> bool:
        try:
            return bool(isinstance(meta, dict) and str(meta.get("mode") or "").strip().lower() == "pixel_art")
        except Exception:
            return False

    def _pixel_cell_px(self, meta: Any) -> int:
        """Return pixel-art display/export cell size.

        This does not affect the logical snapshot resolution (which remains width×height).
        """
        try:
            if not isinstance(meta, dict):
                return 1
            pa = meta.get("pixel_art")
            if isinstance(pa, dict):
                v = pa.get("cell_px")
                if v is not None:
                    return _clamp_int(v, 1, 256)
        except Exception:
            pass
        return 1

    def _pixel_effective_half_size(self, radius: int) -> int:
        """Map UI/tool radius to a pixel-art square stamp half-size.

        In pixel_art mode:
        - radius=1 => 1 cell
        - radius=2 => 3×3 cells
        - radius=3 => 5×5 cells
        """
        try:
            r = int(radius)
        except Exception:
            r = 1
        return max(0, int(r) - 1)

    def _pixel_effective_rgba(self, settings: ToolSettings) -> Tuple[int, int, int, int]:
        try:
            r, g, b, a0 = [int(x) for x in (settings.rgba or (0, 0, 0, 255))]
        except Exception:
            r, g, b, a0 = (0, 0, 0, 255)
        try:
            op = max(0.0, min(1.0, float(settings.opacity)))
        except Exception:
            op = 1.0
        a = max(0, min(255, int(round(float(a0) * op))))
        return (int(r), int(g), int(b), int(a))

    def _pixel_xy(self, x: float, y: float, *, w: int, h: int) -> Tuple[int, int]:
        """Convert float coords to pixel-art cell coords (clamped)."""
        try:
            ix = int(math.floor(float(x)))
            iy = int(math.floor(float(y)))
        except Exception:
            ix, iy = (0, 0)
        ix = max(0, min(int(w) - 1, int(ix)))
        iy = max(0, min(int(h) - 1, int(iy)))
        return int(ix), int(iy)

    def _bresenham(self, x0: int, y0: int, x1: int, y1: int):
        """Yield integer points on a line (Bresenham)."""
        x0 = int(x0)
        y0 = int(y0)
        x1 = int(x1)
        y1 = int(y1)
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            yield int(x), int(y)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def _pixel_iter_path_cells(self, pts: List[Tuple[float, float]], *, w: int, h: int):
        if not pts:
            return
        x0, y0 = self._pixel_xy(pts[0][0], pts[0][1], w=w, h=h)
        yield int(x0), int(y0)
        for (ax, ay) in pts[1:]:
            x1, y1 = self._pixel_xy(ax, ay, w=w, h=h)
            for cx, cy in self._bresenham(x0, y0, x1, y1):
                yield int(cx), int(cy)
            x0, y0 = x1, y1

    def _pixel_blend_over(self, dst: Tuple[int, int, int, int], src: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        """Alpha composite src over dst (both straight RGBA, 0..255)."""
        try:
            sr, sg, sb, sa = [int(x) for x in src]
            dr, dg, db, da = [int(x) for x in dst]
        except Exception:
            return src

        sa = max(0, min(255, sa))
        da = max(0, min(255, da))

        if sa <= 0:
            return (dr, dg, db, da)
        if sa >= 255 or da <= 0:
            return (sr, sg, sb, sa)

        inv = 255 - sa
        out_a = sa + (da * inv + 127) // 255
        if out_a <= 0:
            return (0, 0, 0, 0)

        # premultiplied math
        out_r_p = sr * sa + (dr * da * inv + 127) // 255
        out_g_p = sg * sa + (dg * da * inv + 127) // 255
        out_b_p = sb * sa + (db * da * inv + 127) // 255

        out_r = int(out_r_p // out_a)
        out_g = int(out_g_p // out_a)
        out_b = int(out_b_p // out_a)

        return (
            max(0, min(255, out_r)),
            max(0, min(255, out_g)),
            max(0, min(255, out_b)),
            max(0, min(255, int(out_a))),
        )

    def _pixel_stamp_square(
        self,
        px,
        *,
        w: int,
        h: int,
        cx: int,
        cy: int,
        half: int,
        rgba: Tuple[int, int, int, int],
        blend: bool,
    ) -> int:
        """Stamp a filled square of side (2*half+1) in cell coords. Returns pixels touched.

        If blend=True, does SourceOver alpha blending; if blend=False, overwrites.
        """
        x0 = max(0, int(cx) - int(half))
        x1 = min(int(w) - 1, int(cx) + int(half))
        y0 = max(0, int(cy) - int(half))
        y1 = min(int(h) - 1, int(cy) + int(half))
        touched = 0
        for yy in range(int(y0), int(y1) + 1):
            for xx in range(int(x0), int(x1) + 1):
                try:
                    if bool(blend):
                        px[xx, yy] = self._pixel_blend_over(px[xx, yy], rgba)
                    else:
                        px[xx, yy] = rgba
                except Exception:
                    pass
                touched += 1
        return int(touched)

    def _pixel_apply_polyline(
        self,
        img: Image.Image,
        *,
        pts: List[Tuple[float, float]],
        half: int,
        rgba: Tuple[int, int, int, int],
        blend: bool,
    ) -> int:
        """Apply a pixel-art polyline by stamping a square brush along the path.

        Important: when blend=True, avoid repeated blends from overlapping stamps (which creates
        unintended darkening/"blur" artifacts). We do this by computing the union of covered cells
        and blending each cell at most once.
        """
        out = img
        px = out.load()
        w, h = out.size

        hh = max(0, int(half))
        cells = set()
        for cx, cy in self._pixel_iter_path_cells(pts, w=int(w), h=int(h)):
            x0 = max(0, int(cx) - hh)
            x1 = min(int(w) - 1, int(cx) + hh)
            y0 = max(0, int(cy) - hh)
            y1 = min(int(h) - 1, int(cy) + hh)
            for yy in range(int(y0), int(y1) + 1):
                for xx in range(int(x0), int(x1) + 1):
                    cells.add((int(xx), int(yy)))

        touched = 0
        for (xx, yy) in cells:
            try:
                if bool(blend):
                    px[xx, yy] = self._pixel_blend_over(px[xx, yy], rgba)
                else:
                    px[xx, yy] = rgba
                touched += 1
            except Exception:
                pass

        return int(touched)

    def set_brush(
        self,
        *,
        canvas_id: Optional[str],
        rgba: Tuple[int, int, int, int],
        radius: int,
        opacity: float,
        actor: str,
        brush_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set the current stroke tool settings.

        Despite the name, this covers both the regular round brush and the eraser.
        """
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        tool_state = ToolState.from_meta(meta)

        # Select target tool.
        if brush_type is not None:
            target = parse_stroke_tool_type(brush_type)
        else:
            target = tool_state.current_tool

        if target not in STROKE_TOOL_ENGINES:
            raise RuntimeError(f"Unsupported tool type: {target.value}")

        s_prev = tool_state.settings_by_tool.get(target) or ToolSettings(tool_type=target)
        s_new = ToolSettings(
            tool_type=target,
            rgba=_rgba_tuple(rgba),
            radius=_clamp_int(radius, 1, 4096),
            opacity=_clamp_float(opacity, 0.0, 1.0),
        )

        # Preserve rgba for eraser if caller passed nonsense; but we keep it anyway.
        if target == StrokeToolType.ERASER:
            # Keep the stored rgba either from caller or previous; it is not used by the engine today.
            if not isinstance(rgba, (list, tuple)) and s_prev is not None:
                s_new.rgba = s_prev.rgba

        tool_state.settings_by_tool[target] = s_new
        tool_state.current_tool = target

        now = _utc_now_z()
        meta["updated_at"] = now
        tool_state.apply_to_meta(meta)
        _write_json_atomic(self._canvas_json_path(cid), meta)
        try:
            if bool(layers_enabled):
                self._write_layers_state_snapshot(canvas_id=cid, rev=int(new_rev), meta=meta)
        except Exception:
            pass

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "tool.set",
                "canvas_id": cid,
                "payload": {
                    "tool": target.value,
                    "settings": s_new.to_dict(),
                },
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta


    def update_tool_settings(
        self,
        *,
        canvas_id: Optional[str],
        tool_type: str,
        rgba: Optional[Tuple[int, int, int, int]] = None,
        radius: Optional[int] = None,
        opacity: Optional[float] = None,
        actor: str = "user",
        set_current_tool: bool = False,
    ) -> Dict[str, Any]:
        """Patch settings for a known stroke tool.

        This is intentionally small and strict (currently: round, eraser).
        If set_current_tool=False, the tool selection is unchanged (useful for eyedropper
        that wants to update the brush color without yanking you off the eraser).
        """
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        tt = parse_stroke_tool_type(tool_type)
        if tt not in STROKE_TOOL_ENGINES:
            raise RuntimeError(f"Unsupported tool type: {tt.value}")

        tool_state = ToolState.from_meta(meta)
        cur = tool_state.settings_by_tool.get(tt) or ToolSettings(tool_type=tt)

        if rgba is not None:
            cur.rgba = _rgba_tuple(rgba)
        if radius is not None:
            cur.radius = _clamp_int(radius, 1, 4096)
        if opacity is not None:
            cur.opacity = _clamp_float(opacity, 0.0, 1.0)

        tool_state.settings_by_tool[tt] = cur
        if set_current_tool:
            tool_state.current_tool = tt

        now = _utc_now_z()
        meta["updated_at"] = now
        tool_state.apply_to_meta(meta)
        _write_json_atomic(self._canvas_json_path(cid), meta)

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "tool.settings.patch",
                "canvas_id": cid,
                "payload": {
                    "tool": tt.value,
                    "set_current_tool": bool(set_current_tool),
                    "settings": cur.to_dict(),
                },
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def sample_color(
        self,
        *,
        canvas_id: Optional[str],
        x: float,
        y: float,
        expected_cursor_rev: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sample a pixel RGBA from the current snapshot."""
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        snap = self._snapshot_path(cid, cursor)
        if not snap.exists():
            raise RuntimeError("Missing snapshot")

        img = Image.open(snap).convert("RGBA")
        w, h = img.size

        try:
            ix = int(round(float(x)))
            iy = int(round(float(y)))
        except Exception:
            raise RuntimeError("Invalid coordinates")

        ix = max(0, min(int(w) - 1, ix))
        iy = max(0, min(int(h) - 1, iy))

        r, g, b, a = img.getpixel((ix, iy))

        return {
            "status": "success",
            "canvas_id": cid,
            "cursor_rev": int(cursor),
            "x": int(ix),
            "y": int(iy),
            "rgba": [int(r), int(g), int(b), int(a)],
        }
    def _build_stroke_mask(self, *, size: Tuple[int, int], points: List[Tuple[float, float]], radius: int) -> Image.Image:
        w, h = int(size[0]), int(size[1])
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)

        r = max(1, int(radius))
        width = max(1, int(r * 2))

        # Densify so we don't get dotted lines when points are sparse.
        step = max(0.75, float(r) * 0.33)
        dense = _densify_points(points, step=step)

        # Round brush strategy:
        # - stamp circles along the path (guarantees no gaps)
        # - also draw a thick line as a fast base fill
        if len(dense) == 1:
            x, y = dense[0]
            md.ellipse([x - r, y - r, x + r, y + r], fill=255)
        else:
            try:
                md.line(dense, fill=255, width=width)
            except Exception:
                pass
            for x, y in dense:
                md.ellipse([x - r, y - r, x + r, y + r], fill=255)

        return mask

    def _build_line_mask(self, *, size: Tuple[int, int], x1: float, y1: float, x2: float, y2: float, radius: int) -> Image.Image:
        """Build a mask for a *straight* segment with flat ends.

        This is used by the Line tool to avoid rounded end caps.
        """
        w, h = int(size[0]), int(size[1])
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)

        r = max(1, int(radius))
        hw = float(r)

        dx = float(x2 - x1)
        dy = float(y2 - y1)
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= 1e-6:
            return mask

        ux = dx / dist
        uy = dy / dist
        px = -uy
        py = ux

        # Flat (butt) caps: rectangle ends exactly at (x1,y1) and (x2,y2).
        p1 = (x1 + px * hw, y1 + py * hw)
        p2 = (x1 - px * hw, y1 - py * hw)
        p3 = (x2 - px * hw, y2 - py * hw)
        p4 = (x2 + px * hw, y2 + py * hw)

        try:
            md.polygon([p1, p2, p3, p4], fill=255)
        except Exception:
            pass

        return mask

    def _build_line_mask_pixel(self, *, size: Tuple[int, int], x1: float, y1: float, x2: float, y2: float, thickness: int) -> Image.Image:
        """Pixel-art line mask.

        Pixel-art radius semantics are in "cells" (radius=1 => 1 cell). The classic line mask
        expects a geometric half-width; here we derive it from a target thickness in cells.
        """
        w, h = int(size[0]), int(size[1])
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)

        t = max(1, int(thickness))
        hw = float(t) / 2.0

        dx = float(x2 - x1)
        dy = float(y2 - y1)
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= 1e-6:
            return mask

        ux = dx / dist
        uy = dy / dist
        px = -uy
        py = ux

        p1 = (x1 + px * hw, y1 + py * hw)
        p2 = (x1 - px * hw, y1 - py * hw)
        p3 = (x2 - px * hw, y2 - py * hw)
        p4 = (x2 + px * hw, y2 + py * hw)

        try:
            md.polygon([p1, p2, p3, p4], fill=255)
        except Exception:
            pass

        return mask

    def _sorted_box(self, x1: float, y1: float, x2: float, y2: float) -> Tuple[float, float, float, float]:
        try:
            ax, ay = float(x1), float(y1)
            bx, by = float(x2), float(y2)
        except Exception:
            ax, ay, bx, by = (0.0, 0.0, 0.0, 0.0)
        x0 = float(min(ax, bx))
        y0 = float(min(ay, by))
        x1b = float(max(ax, bx))
        y1b = float(max(ay, by))
        return x0, y0, x1b, y1b

    def _build_rect_mask(self, *, size: Tuple[int, int], x1: float, y1: float, x2: float, y2: float, radius: int, filled: bool) -> Image.Image:
        w, h = int(size[0]), int(size[1])
        x0, y0, x3, y3 = self._sorted_box(x1, y1, x2, y2)

        r = max(1, int(radius))

        outer = Image.new("L", (w, h), 0)
        od = ImageDraw.Draw(outer)

        if bool(filled):
            # Filled shapes ignore brush size (no stroke).
            try:
                od.rectangle([x0, y0, x3, y3], fill=255)
            except Exception:
                pass
            return outer

        # Outline: match UI preview semantics (stroke centered on the edge): expand by r.
        xo0 = float(x0 - r)
        yo0 = float(y0 - r)
        xo1 = float(x3 + r)
        yo1 = float(y3 + r)

        try:
            od.rectangle([xo0, yo0, xo1, yo1], fill=255)
        except Exception:
            pass

        inner = Image.new("L", (w, h), 0)
        idr = ImageDraw.Draw(inner)
        xi0 = float(x0 + r)
        yi0 = float(y0 + r)
        xi1 = float(x3 - r)
        yi1 = float(y3 - r)
        if xi1 <= xi0 or yi1 <= yi0:
            return outer

        try:
            idr.rectangle([xi0, yi0, xi1, yi1], fill=255)
        except Exception:
            return outer

        try:
            return ImageChops.subtract(outer, inner)
        except Exception:
            return outer

    def _build_ellipse_mask(self, *, size: Tuple[int, int], x1: float, y1: float, x2: float, y2: float, radius: int, filled: bool) -> Image.Image:
        w, h = int(size[0]), int(size[1])
        x0, y0, x3, y3 = self._sorted_box(x1, y1, x2, y2)

        r = max(1, int(radius))

        outer = Image.new("L", (w, h), 0)
        od = ImageDraw.Draw(outer)

        if bool(filled):
            # Filled shapes ignore brush size (no stroke).
            try:
                od.ellipse([x0, y0, x3, y3], fill=255)
            except Exception:
                pass
            return outer

        # Outline: match UI preview semantics (stroke centered on the edge): expand by r.
        xo0 = float(x0 - r)
        yo0 = float(y0 - r)
        xo1 = float(x3 + r)
        yo1 = float(y3 + r)

        try:
            od.ellipse([xo0, yo0, xo1, yo1], fill=255)
        except Exception:
            pass

        inner = Image.new("L", (w, h), 0)
        idr = ImageDraw.Draw(inner)
        xi0 = float(x0 + r)
        yi0 = float(y0 + r)
        xi1 = float(x3 - r)
        yi1 = float(y3 - r)
        if xi1 <= xi0 or yi1 <= yi0:
            return outer

        try:
            idr.ellipse([xi0, yi0, xi1, yi1], fill=255)
        except Exception:
            return outer

        try:
            return ImageChops.subtract(outer, inner)
        except Exception:
            return outer

    def draw_stroke(
        self,
        *,
        canvas_id: Optional[str],
        points: List[Tuple[float, float]],
        actor: str,
        expected_cursor_rev: Optional[int] = None,
    ) -> Dict[str, Any]:
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)
        min_rev = int(hist.get("min_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        # Normalize points
        pts: List[Tuple[float, float]] = []
        for p in points or []:
            try:
                x, y = float(p[0]), float(p[1])
                pts.append((x, y))
            except Exception:
                continue
        if not pts:
            return meta

        tool_state = ToolState.from_meta(meta)
        tool = tool_state.current_tool
        settings = tool_state.settings_by_tool.get(tool) or ToolSettings(tool_type=tool)

        pixel_touched: Optional[int] = None

        if tool not in STROKE_TOOL_ENGINES:
            raise RuntimeError(f"Unsupported tool type: {tool.value}")

        layers_enabled = self._layers_enabled(meta)

        # If we're not at the tip, invalidate redo history.
        if cursor < max_rev:
            for r in range(cursor + 1, max_rev + 1):
                try:
                    self._snapshot_path(cid, r).unlink(missing_ok=True)
                except Exception:
                    pass

                if layers_enabled:
                    for layer in self._layers_list(meta):
                        lid = layer.get("layer_id")
                        if isinstance(lid, str) and lid.strip():
                            try:
                                self._layer_snapshot_path(cid, lid.strip(), r).unlink(missing_ok=True)
                            except Exception:
                                pass
            max_rev = cursor

        bg = self._background_rgba_from_meta(meta)
        new_rev = int(cursor + 1)

        if layers_enabled:
            w = int(meta.get("width") or 0)
            h = int(meta.get("height") or 0)

            active_lid = self._active_layer_id(meta)
            if not active_lid:
                raise RuntimeError("Layered canvas is missing active_layer_id")

            # Apply stroke to the active layer.
            layer_base = self._load_layer_image(canvas_id=cid, layer_id=active_lid, rev=int(cursor), size=(w, h))

            if self._is_pixel_art(meta):
                half = self._pixel_effective_half_size(int(settings.radius))
                is_bg_layer = self._is_background_layer_id(meta, active_lid)

                if tool == StrokeToolType.ERASER:
                    # Background layer: erase back to background color when opaque; otherwise alpha-erase.
                    if bool(is_bg_layer) and int(bg[3]) >= 255:
                        rgba2 = bg
                    else:
                        rgba2 = (0, 0, 0, 0)
                else:
                    rgba2 = self._pixel_effective_rgba(settings)

                pixel_touched = self._pixel_apply_polyline(layer_base, pts=pts, half=int(half), rgba=rgba2, blend=(tool != StrokeToolType.ERASER))
                layer_out = layer_base

            else:
                mask = self._build_stroke_mask(size=layer_base.size, points=pts, radius=int(settings.radius))

                if tool == StrokeToolType.ERASER:
                    is_bg_layer = self._is_background_layer_id(meta, active_lid)
                    if bool(is_bg_layer) and int(bg[3]) >= 255:
                        # Erase to opaque background color.
                        s2 = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=bg, radius=int(settings.radius), opacity=1.0)
                        engine2 = STROKE_TOOL_ENGINES[StrokeToolType.ROUND]
                        layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=s2, background_rgba=bg)
                    else:
                        engine2 = AlphaEraserEngine()
                        layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=settings, background_rgba=bg)
                else:
                    engine2 = STROKE_TOOL_ENGINES[tool]
                    layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=settings, background_rgba=bg)
            self._save_layer_image(canvas_id=cid, layer_id=active_lid, rev=int(new_rev), img=layer_out)

            # Carry forward non-active layers to new_rev.
            for layer in self._layers_list(meta):
                lid = layer.get("layer_id")
                if not (isinstance(lid, str) and lid.strip()):
                    continue
                lid_s = lid.strip()
                if lid_s == active_lid:
                    continue
                src_p = self._layer_snapshot_path(cid, lid_s, int(cursor))
                dst_p = self._layer_snapshot_path(cid, lid_s, int(new_rev))
                if src_p.exists():
                    try:
                        self._layer_snapshots_dir(cid, lid_s).mkdir(parents=True, exist_ok=True)
                        dst_p.write_bytes(src_p.read_bytes())
                    except Exception:
                        blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                        self._save_layer_image(canvas_id=cid, layer_id=lid_s, rev=int(new_rev), img=blank)
                else:
                    blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    self._save_layer_image(canvas_id=cid, layer_id=lid_s, rev=int(new_rev), img=blank)

            # Write composite snapshot for compatibility.
            composite = self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=int(new_rev))
            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            composite.save(self._snapshot_path(cid, new_rev), format="PNG")

        else:
            # Legacy flattened model
            snap_path = self._snapshot_path(cid, cursor)
            if not snap_path.exists():
                raise RuntimeError("Missing snapshot for current cursor")

            base = Image.open(snap_path).convert("RGBA")

            if self._is_pixel_art(meta):
                half = self._pixel_effective_half_size(int(settings.radius))
                alpha_erase = bool(int(bg[3]) < 255)

                if tool == StrokeToolType.ERASER:
                    rgba2 = (0, 0, 0, 0) if alpha_erase else bg
                else:
                    rgba2 = self._pixel_effective_rgba(settings)

                pixel_touched = self._pixel_apply_polyline(base, pts=pts, half=int(half), rgba=rgba2, blend=(tool != StrokeToolType.ERASER))
                out = base
            else:
                mask = self._build_stroke_mask(size=base.size, points=pts, radius=int(settings.radius))

                # Legacy transparent canvases (bg alpha < 255) need true alpha erase even without layers.
                if tool == StrokeToolType.ERASER and int(bg[3]) < 255:
                    engine = AlphaEraserEngine()
                else:
                    engine = STROKE_TOOL_ENGINES[tool]

                out = engine.apply(base=base, mask_l=mask, settings=settings, background_rgba=bg)

            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            out.save(self._snapshot_path(cid, new_rev), format="PNG")

        # Update meta
        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {
            "min_rev": int(min_rev),
            "cursor_rev": int(new_rev),
            "max_rev": int(new_rev),
        }
        # Keep tool_state + current_brush coherent.
        tool_state.apply_to_meta(meta)
        _write_json_atomic(self._canvas_json_path(cid), meta)

        # Log action
        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "stroke.draw",
                "canvas_id": cid,
                "payload": {
                    "tool": tool.value,
                    "settings": settings.to_dict(),
                    "points": [{"x": float(x), "y": float(y)} for (x, y) in pts],
                    "from_rev": int(cursor),
                    "to_rev": int(new_rev),
                    "mode": str(meta.get("mode") or "normal"),
                    "pixels_touched": (int(pixel_touched) if pixel_touched is not None else None),
                },
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def draw_line(
        self,
        *,
        canvas_id: Optional[str],
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        actor: str,
        expected_cursor_rev: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Draw a perfectly straight segment with flat ends (Line tool)."""
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)
        min_rev = int(hist.get("min_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        # Normalize endpoints
        try:
            ax, ay = float(x1), float(y1)
            bx, by = float(x2), float(y2)
        except Exception:
            raise RuntimeError("Invalid coordinates")

        tool_state = ToolState.from_meta(meta)
        tool = tool_state.current_tool
        settings = tool_state.settings_by_tool.get(tool) or ToolSettings(tool_type=tool)

        pixel_touched: Optional[int] = None

        if tool not in STROKE_TOOL_ENGINES:
            raise RuntimeError(f"Unsupported tool type: {tool.value}")

        layers_enabled = self._layers_enabled(meta)

        # If we're not at the tip, invalidate redo history.
        if cursor < max_rev:
            for r in range(cursor + 1, max_rev + 1):
                try:
                    self._snapshot_path(cid, r).unlink(missing_ok=True)
                except Exception:
                    pass

                if layers_enabled:
                    for layer in self._layers_list(meta):
                        lid = layer.get("layer_id")
                        if isinstance(lid, str) and lid.strip():
                            try:
                                self._layer_snapshot_path(cid, lid.strip(), r).unlink(missing_ok=True)
                            except Exception:
                                pass
            max_rev = cursor

        bg = self._background_rgba_from_meta(meta)
        new_rev = int(cursor + 1)

        if layers_enabled:
            w = int(meta.get("width") or 0)
            h = int(meta.get("height") or 0)

            active_lid = self._active_layer_id(meta)
            if not active_lid:
                raise RuntimeError("Layered canvas is missing active_layer_id")

            layer_base = self._load_layer_image(canvas_id=cid, layer_id=active_lid, rev=int(cursor), size=(w, h))

            if self._is_pixel_art(meta):
                # Pixel-art line: use a single crisp mask (avoid overlap artifacts).
                thickness = max(1, int(int(settings.radius) * 2 - 1))
                mask = self._build_line_mask_pixel(size=layer_base.size, x1=ax, y1=ay, x2=bx, y2=by, thickness=int(thickness))

                is_bg_layer = self._is_background_layer_id(meta, active_lid)

                if tool == StrokeToolType.ERASER:
                    if bool(is_bg_layer) and int(bg[3]) >= 255:
                        s2 = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=bg, radius=1, opacity=1.0)
                        engine2 = STROKE_TOOL_ENGINES[StrokeToolType.ROUND]
                        layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=s2, background_rgba=bg)
                    else:
                        engine2 = AlphaEraserEngine()
                        layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=settings, background_rgba=bg)
                else:
                    rgba2 = self._pixel_effective_rgba(settings)
                    s2 = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=rgba2, radius=1, opacity=1.0)
                    engine2 = STROKE_TOOL_ENGINES[StrokeToolType.ROUND]
                    layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=s2, background_rgba=bg)

                pixel_touched = None

            else:
                mask = self._build_line_mask(size=layer_base.size, x1=ax, y1=ay, x2=bx, y2=by, radius=int(settings.radius))

                if tool == StrokeToolType.ERASER:
                    is_bg_layer = self._is_background_layer_id(meta, active_lid)
                    if bool(is_bg_layer) and int(bg[3]) >= 255:
                        s2 = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=bg, radius=int(settings.radius), opacity=1.0)
                        engine2 = STROKE_TOOL_ENGINES[StrokeToolType.ROUND]
                        layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=s2, background_rgba=bg)
                    else:
                        engine2 = AlphaEraserEngine()
                        layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=settings, background_rgba=bg)
                else:
                    engine2 = STROKE_TOOL_ENGINES[tool]
                    layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=settings, background_rgba=bg)
            self._save_layer_image(canvas_id=cid, layer_id=active_lid, rev=int(new_rev), img=layer_out)

            # Carry forward non-active layers to new_rev.
            for layer in self._layers_list(meta):
                lid = layer.get("layer_id")
                if not (isinstance(lid, str) and lid.strip()):
                    continue
                lid_s = lid.strip()
                if lid_s == active_lid:
                    continue
                src_p = self._layer_snapshot_path(cid, lid_s, int(cursor))
                dst_p = self._layer_snapshot_path(cid, lid_s, int(new_rev))
                if src_p.exists():
                    try:
                        self._layer_snapshots_dir(cid, lid_s).mkdir(parents=True, exist_ok=True)
                        dst_p.write_bytes(src_p.read_bytes())
                    except Exception:
                        blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                        self._save_layer_image(canvas_id=cid, layer_id=lid_s, rev=int(new_rev), img=blank)
                else:
                    blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    self._save_layer_image(canvas_id=cid, layer_id=lid_s, rev=int(new_rev), img=blank)

            composite = self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=int(new_rev))
            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            composite.save(self._snapshot_path(cid, new_rev), format="PNG")

        else:
            snap_path = self._snapshot_path(cid, cursor)
            if not snap_path.exists():
                raise RuntimeError("Missing snapshot for current cursor")

            base = Image.open(snap_path).convert("RGBA")

            if self._is_pixel_art(meta):
                thickness = max(1, int(int(settings.radius) * 2 - 1))
                mask = self._build_line_mask_pixel(size=base.size, x1=ax, y1=ay, x2=bx, y2=by, thickness=int(thickness))

                alpha_erase = bool(int(bg[3]) < 255)

                if tool == StrokeToolType.ERASER:
                    if bool(alpha_erase):
                        engine = AlphaEraserEngine()
                        out = engine.apply(base=base, mask_l=mask, settings=settings, background_rgba=bg)
                    else:
                        s2 = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=bg, radius=1, opacity=1.0)
                        engine = STROKE_TOOL_ENGINES[StrokeToolType.ROUND]
                        out = engine.apply(base=base, mask_l=mask, settings=s2, background_rgba=bg)
                else:
                    rgba2 = self._pixel_effective_rgba(settings)
                    s2 = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=rgba2, radius=1, opacity=1.0)
                    engine = STROKE_TOOL_ENGINES[StrokeToolType.ROUND]
                    out = engine.apply(base=base, mask_l=mask, settings=s2, background_rgba=bg)

                pixel_touched = None
            else:
                mask = self._build_line_mask(size=base.size, x1=ax, y1=ay, x2=bx, y2=by, radius=int(settings.radius))

                if tool == StrokeToolType.ERASER and int(bg[3]) < 255:
                    engine = AlphaEraserEngine()
                else:
                    engine = STROKE_TOOL_ENGINES[tool]

                out = engine.apply(base=base, mask_l=mask, settings=settings, background_rgba=bg)

            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            out.save(self._snapshot_path(cid, new_rev), format="PNG")

        # Update meta
        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_rev), "max_rev": int(new_rev)}
        tool_state.apply_to_meta(meta)
        _write_json_atomic(self._canvas_json_path(cid), meta)
        try:
            if bool(layers_enabled):
                self._write_layers_state_snapshot(canvas_id=cid, rev=int(new_rev), meta=meta)
        except Exception:
            pass

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "line.draw",
                "canvas_id": cid,
                "payload": {
                    "x1": float(ax),
                    "y1": float(ay),
                    "x2": float(bx),
                    "y2": float(by),
                    "tool": tool.value,
                    "settings": settings.to_dict(),
                    "from_rev": int(cursor),
                    "to_rev": int(new_rev),
                    "mode": str(meta.get("mode") or "normal"),
                    "pixels_touched": (int(pixel_touched) if pixel_touched is not None else None),
                },
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def draw_shape(
        self,
        *,
        canvas_id: Optional[str],
        shape: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        filled: bool,
        actor: str,
        expected_cursor_rev: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Draw a shape (line/rect/ellipse) using the current tool settings.

        - Uses the active layer when layers are enabled.
        - Works in normal + pixel_art.
        - Outline thickness is driven by current brush radius.
        """
        s = str(shape or "").strip().lower()
        if s in ("rectangle", "rect"):
            s = "rect"
        elif s in ("ellipse", "circle"):
            s = "ellipse"
        elif s in ("line",):
            s = "line"
        else:
            raise RuntimeError(f"Unsupported shape: {shape}")

        if s == "line":
            return self.draw_line(
                canvas_id=canvas_id,
                x1=float(x1),
                y1=float(y1),
                x2=float(x2),
                y2=float(y2),
                actor=str(actor or "user"),
                expected_cursor_rev=expected_cursor_rev,
            )

        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)
        min_rev = int(hist.get("min_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        # Normalize endpoints
        try:
            ax, ay = float(x1), float(y1)
            bx, by = float(x2), float(y2)
        except Exception:
            raise RuntimeError("Invalid coordinates")

        tool_state = ToolState.from_meta(meta)
        tool = tool_state.current_tool
        settings = tool_state.settings_by_tool.get(tool) or ToolSettings(tool_type=tool)

        pixel_touched: Optional[int] = None

        if tool not in STROKE_TOOL_ENGINES:
            raise RuntimeError(f"Unsupported tool type: {tool.value}")

        layers_enabled = self._layers_enabled(meta)

        # If we're not at the tip, invalidate redo history.
        if cursor < max_rev:
            for r in range(cursor + 1, max_rev + 1):
                try:
                    self._snapshot_path(cid, r).unlink(missing_ok=True)
                except Exception:
                    pass

                if layers_enabled:
                    for layer in self._layers_list(meta):
                        lid = layer.get("layer_id")
                        if isinstance(lid, str) and lid.strip():
                            try:
                                self._layer_snapshot_path(cid, lid.strip(), r).unlink(missing_ok=True)
                            except Exception:
                                pass
            max_rev = cursor

        bg = self._background_rgba_from_meta(meta)
        new_rev = int(cursor + 1)

        def _pixel_apply_rect(img: Image.Image, *, x0: int, y0: int, x3: int, y3: int, rgba2: Tuple[int, int, int, int], half: int, filled2: bool, blend: bool) -> int:
            px = img.load()
            w2, h2 = img.size
            touched = 0
            t = max(1, int(half) * 2 + 1)
            for yy in range(int(y0), int(y3) + 1):
                for xx in range(int(x0), int(x3) + 1):
                    if not bool(filled2):
                        if (int(xx) - int(x0)) >= t and (int(x3) - int(xx)) >= t and (int(yy) - int(y0)) >= t and (int(y3) - int(yy)) >= t:
                            continue
                    try:
                        if 0 <= xx < int(w2) and 0 <= yy < int(h2):
                            if bool(blend):
                                px[int(xx), int(yy)] = self._pixel_blend_over(px[int(xx), int(yy)], rgba2)
                            else:
                                px[int(xx), int(yy)] = rgba2
                            touched += 1
                    except Exception:
                        pass
            return int(touched)

        def _pixel_apply_ellipse(img: Image.Image, *, x0: int, y0: int, x3: int, y3: int, rgba2: Tuple[int, int, int, int], half: int, filled2: bool, blend: bool) -> int:
            px = img.load()
            w2, h2 = img.size
            touched = 0

            # Center/radii in cell space.
            cx = (float(x0) + float(x3) + 1.0) / 2.0
            cy = (float(y0) + float(y3) + 1.0) / 2.0
            rx = max(1e-6, (float(x3) - float(x0) + 1.0) / 2.0)
            ry = max(1e-6, (float(y3) - float(y0) + 1.0) / 2.0)

            t = max(1.0, float(int(half) * 2 + 1))
            rx2 = float(rx - t)
            ry2 = float(ry - t)

            for yy in range(int(y0), int(y3) + 1):
                for xx in range(int(x0), int(x3) + 1):
                    x = float(xx) + 0.5
                    y = float(yy) + 0.5
                    dx = (x - cx) / rx
                    dy = (y - cy) / ry
                    if (dx * dx + dy * dy) > 1.0:
                        continue

                    if not bool(filled2) and rx2 > 1e-6 and ry2 > 1e-6:
                        dx2 = (x - cx) / rx2
                        dy2 = (y - cy) / ry2
                        if (dx2 * dx2 + dy2 * dy2) <= 1.0:
                            continue

                    try:
                        if 0 <= xx < int(w2) and 0 <= yy < int(h2):
                            if bool(blend):
                                px[int(xx), int(yy)] = self._pixel_blend_over(px[int(xx), int(yy)], rgba2)
                            else:
                                px[int(xx), int(yy)] = rgba2
                            touched += 1
                    except Exception:
                        pass

            return int(touched)

        if layers_enabled:
            w = int(meta.get("width") or 0)
            h = int(meta.get("height") or 0)

            active_lid = self._active_layer_id(meta)
            if not active_lid:
                raise RuntimeError("Layered canvas is missing active_layer_id")

            layer_base = self._load_layer_image(canvas_id=cid, layer_id=active_lid, rev=int(cursor), size=(w, h))

            if self._is_pixel_art(meta):
                half = self._pixel_effective_half_size(int(settings.radius))
                is_bg_layer = self._is_background_layer_id(meta, active_lid)

                if tool == StrokeToolType.ERASER:
                    if bool(is_bg_layer) and int(bg[3]) >= 255:
                        rgba2 = bg
                    else:
                        rgba2 = (0, 0, 0, 0)
                else:
                    rgba2 = self._pixel_effective_rgba(settings)

                ix0, iy0 = self._pixel_xy(min(ax, bx), min(ay, by), w=int(w), h=int(h))
                ix1, iy1 = self._pixel_xy(max(ax, bx), max(ay, by), w=int(w), h=int(h))

                if s == "rect":
                    pixel_touched = _pixel_apply_rect(layer_base, x0=int(ix0), y0=int(iy0), x3=int(ix1), y3=int(iy1), rgba2=rgba2, half=int(half), filled2=bool(filled), blend=(tool != StrokeToolType.ERASER))
                else:
                    pixel_touched = _pixel_apply_ellipse(layer_base, x0=int(ix0), y0=int(iy0), x3=int(ix1), y3=int(iy1), rgba2=rgba2, half=int(half), filled2=bool(filled), blend=(tool != StrokeToolType.ERASER))

                layer_out = layer_base

            else:
                if s == "rect":
                    mask = self._build_rect_mask(size=layer_base.size, x1=ax, y1=ay, x2=bx, y2=by, radius=int(settings.radius), filled=bool(filled))
                else:
                    mask = self._build_ellipse_mask(size=layer_base.size, x1=ax, y1=ay, x2=bx, y2=by, radius=int(settings.radius), filled=bool(filled))

                if tool == StrokeToolType.ERASER:
                    is_bg_layer = self._is_background_layer_id(meta, active_lid)
                    if bool(is_bg_layer) and int(bg[3]) >= 255:
                        s2 = ToolSettings(tool_type=StrokeToolType.ROUND, rgba=bg, radius=int(settings.radius), opacity=1.0)
                        engine2 = STROKE_TOOL_ENGINES[StrokeToolType.ROUND]
                        layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=s2, background_rgba=bg)
                    else:
                        engine2 = AlphaEraserEngine()
                        layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=settings, background_rgba=bg)
                else:
                    engine2 = STROKE_TOOL_ENGINES[tool]
                    layer_out = engine2.apply(base=layer_base, mask_l=mask, settings=settings, background_rgba=bg)

            self._save_layer_image(canvas_id=cid, layer_id=active_lid, rev=int(new_rev), img=layer_out)

            # Carry forward non-active layers to new_rev.
            for layer in self._layers_list(meta):
                lid = layer.get("layer_id")
                if not (isinstance(lid, str) and lid.strip()):
                    continue
                lid_s = lid.strip()
                if lid_s == active_lid:
                    continue
                src_p = self._layer_snapshot_path(cid, lid_s, int(cursor))
                dst_p = self._layer_snapshot_path(cid, lid_s, int(new_rev))
                if src_p.exists():
                    try:
                        self._layer_snapshots_dir(cid, lid_s).mkdir(parents=True, exist_ok=True)
                        dst_p.write_bytes(src_p.read_bytes())
                    except Exception:
                        blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                        self._save_layer_image(canvas_id=cid, layer_id=lid_s, rev=int(new_rev), img=blank)
                else:
                    blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    self._save_layer_image(canvas_id=cid, layer_id=lid_s, rev=int(new_rev), img=blank)

            composite = self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=int(new_rev))
            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            composite.save(self._snapshot_path(cid, new_rev), format="PNG")

        else:
            snap_path = self._snapshot_path(cid, cursor)
            if not snap_path.exists():
                raise RuntimeError("Missing snapshot for current cursor")

            base = Image.open(snap_path).convert("RGBA")

            if self._is_pixel_art(meta):
                half = self._pixel_effective_half_size(int(settings.radius))
                alpha_erase = bool(int(bg[3]) < 255)

                if tool == StrokeToolType.ERASER:
                    rgba2 = (0, 0, 0, 0) if alpha_erase else bg
                else:
                    rgba2 = self._pixel_effective_rgba(settings)

                ix0, iy0 = self._pixel_xy(min(ax, bx), min(ay, by), w=int(base.size[0]), h=int(base.size[1]))
                ix1, iy1 = self._pixel_xy(max(ax, bx), max(ay, by), w=int(base.size[0]), h=int(base.size[1]))

                if s == "rect":
                    pixel_touched = _pixel_apply_rect(base, x0=int(ix0), y0=int(iy0), x3=int(ix1), y3=int(iy1), rgba2=rgba2, half=int(half), filled2=bool(filled), blend=(tool != StrokeToolType.ERASER))
                else:
                    pixel_touched = _pixel_apply_ellipse(base, x0=int(ix0), y0=int(iy0), x3=int(ix1), y3=int(iy1), rgba2=rgba2, half=int(half), filled2=bool(filled), blend=(tool != StrokeToolType.ERASER))

                out = base
            else:
                if s == "rect":
                    mask = self._build_rect_mask(size=base.size, x1=ax, y1=ay, x2=bx, y2=by, radius=int(settings.radius), filled=bool(filled))
                else:
                    mask = self._build_ellipse_mask(size=base.size, x1=ax, y1=ay, x2=bx, y2=by, radius=int(settings.radius), filled=bool(filled))

                if tool == StrokeToolType.ERASER and int(bg[3]) < 255:
                    engine = AlphaEraserEngine()
                else:
                    engine = STROKE_TOOL_ENGINES[tool]

                out = engine.apply(base=base, mask_l=mask, settings=settings, background_rgba=bg)

            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            out.save(self._snapshot_path(cid, new_rev), format="PNG")

        # Update meta
        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_rev), "max_rev": int(new_rev)}
        tool_state.apply_to_meta(meta)
        _write_json_atomic(self._canvas_json_path(cid), meta)
        try:
            if bool(layers_enabled):
                self._write_layers_state_snapshot(canvas_id=cid, rev=int(new_rev), meta=meta)
        except Exception:
            pass

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "shape.draw",
                "canvas_id": cid,
                "payload": {
                    "shape": str(s),
                    "filled": bool(filled),
                    "x1": float(ax),
                    "y1": float(ay),
                    "x2": float(bx),
                    "y2": float(by),
                    "tool": tool.value,
                    "settings": settings.to_dict(),
                    "from_rev": int(cursor),
                    "to_rev": int(new_rev),
                    "mode": str(meta.get("mode") or "normal"),
                    "pixels_touched": (int(pixel_touched) if pixel_touched is not None else None),
                },
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def import_image_apply(
        self,
        *,
        canvas_id: Optional[str],
        layer_id: Optional[str],
        image_bytes: bytes,
        dest_rect: Dict[str, Any],
        crop_rect: Optional[Dict[str, Any]],
        rotation_deg: float,
        opacity: float,
        actor: str,
        expected_cursor_rev: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Import an image onto a layer with crop/transform/opacity.

        UI holds a temporary object; Apply commits a new rev (undoable).

        Coordinate contract:
        - dest_rect: canvas coords {x,y,w,h} (floats ok)  top-left position and size
        - crop_rect: source image pixel coords {l,t,r,b} (ints; optional)
        - rotation_deg: around dest_rect center
        - opacity: 0..1 multiplies alpha

        Pixel-art mode: uses NEAREST for resize/rotate (crisp, no AA).
        """
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)
        min_rev = int(hist.get("min_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        # Parse dest rect
        try:
            dx = float(dest_rect.get("x"))
            dy = float(dest_rect.get("y"))
            dw = float(dest_rect.get("w"))
            dh = float(dest_rect.get("h"))
        except Exception:
            raise RuntimeError("Invalid dest_rect")
        if dw <= 0 or dh <= 0:
            raise RuntimeError("dest_rect w/h must be > 0")

        try:
            rot = float(rotation_deg or 0.0)
        except Exception:
            rot = 0.0

        try:
            op = max(0.0, min(1.0, float(opacity)))
        except Exception:
            op = 1.0

        # Decode image
        try:
            from io import BytesIO

            src = Image.open(BytesIO(image_bytes))
            if str(getattr(src, "format", "") or "").strip().upper() == "GIF":
                raise RuntimeError("GIF import is not supported")
            src_rgba = src.convert("RGBA")
        except Exception as e:
            raise RuntimeError(f"Invalid image: {e}")

        sw, sh = src_rgba.size

        # Crop
        if isinstance(crop_rect, dict):
            try:
                l = int(crop_rect.get("l"))
                t = int(crop_rect.get("t"))
                r = int(crop_rect.get("r"))
                b = int(crop_rect.get("b"))
            except Exception:
                l, t, r, b = 0, 0, int(sw), int(sh)
        else:
            l, t, r, b = 0, 0, int(sw), int(sh)

        l = max(0, min(int(sw), int(l)))
        r = max(0, min(int(sw), int(r)))
        t = max(0, min(int(sh), int(t)))
        b = max(0, min(int(sh), int(b)))
        if r <= l or b <= t:
            raise RuntimeError("crop_rect is empty")

        img = src_rgba.crop((int(l), int(t), int(r), int(b)))

        # Opacity
        if op < 0.999:
            try:
                a = img.getchannel("A")
                img.putalpha(a.point(lambda p: int(p * op)))
            except Exception:
                pass

        # Resize + rotate
        is_pixel = self._is_pixel_art(meta)
        resample = Image.Resampling.NEAREST if bool(is_pixel) else Image.Resampling.LANCZOS

        try:
            out_w = max(1, int(round(dw)))
            out_h = max(1, int(round(dh)))
        except Exception:
            out_w, out_h = int(img.size[0]), int(img.size[1])

        img = img.resize((int(out_w), int(out_h)), resample=resample)

        # Rotate around center (PIL rotates around image center by default).
        if abs(float(rot)) > 1e-6:
            r_resample = Image.Resampling.NEAREST if bool(is_pixel) else Image.Resampling.BICUBIC
            # NOTE: rotation_deg comes from Canvas Studio (QPainter.rotate).
            # PIL Image.rotate() uses the opposite visual direction in our screen coords,
            # so we negate to ensure the committed pixels match the UI preview.
            img = img.rotate(-float(rot), resample=r_resample, expand=True)

        # Apply to canvas
        layers_enabled = self._layers_enabled(meta)

        # If we're not at the tip, invalidate redo history.
        if cursor < max_rev:
            for rr in range(cursor + 1, max_rev + 1):
                try:
                    self._snapshot_path(cid, rr).unlink(missing_ok=True)
                except Exception:
                    pass
                if layers_enabled:
                    try:
                        self._layers_state_path(cid, rr).unlink(missing_ok=True)
                    except Exception:
                        pass
                    for layer in self._layers_list(meta):
                        lid = layer.get("layer_id")
                        if isinstance(lid, str) and lid.strip():
                            try:
                                self._layer_snapshot_path(cid, lid.strip(), rr).unlink(missing_ok=True)
                            except Exception:
                                pass
            max_rev = cursor

        new_rev = int(cursor + 1)

        target_lid: Optional[str] = None

        if layers_enabled:
            w = int(meta.get("width") or 0)
            h = int(meta.get("height") or 0)

            target_lid = str(layer_id or "").strip() if isinstance(layer_id, str) else ""
            if target_lid:
                if not self._find_layer(meta, target_lid):
                    raise RuntimeError("layer_id not found")
            else:
                target_lid = self._active_layer_id(meta) or ""
            if not target_lid:
                raise RuntimeError("Layered canvas is missing active_layer_id")

            layer_base = self._load_layer_image(canvas_id=cid, layer_id=target_lid, rev=int(cursor), size=(w, h))

            # Compose onto a canvas-sized temp, then alpha composite.
            temp = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
            cx = float(dx) + float(dw) / 2.0
            cy = float(dy) + float(dh) / 2.0
            px = int(round(cx - (img.size[0] / 2.0)))
            py = int(round(cy - (img.size[1] / 2.0)))
            try:
                temp.paste(img, (int(px), int(py)), img)
            except Exception:
                temp.paste(img, (int(px), int(py)))

            layer_out = Image.alpha_composite(layer_base, temp)
            self._save_layer_image(canvas_id=cid, layer_id=target_lid, rev=int(new_rev), img=layer_out)

            # Carry forward other layers
            for lyr in self._layers_list(meta):
                lid = str(lyr.get("layer_id") or "").strip()
                if not lid or lid == target_lid:
                    continue
                self._copy_layer_forward(canvas_id=cid, layer_id=lid, src_rev=int(cursor), dst_rev=int(new_rev), size=(w, h))

            composite = self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=int(new_rev))
            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            composite.save(self._snapshot_path(cid, new_rev), format="PNG")

        else:
            # Legacy flattened model
            snap_path = self._snapshot_path(cid, cursor)
            if not snap_path.exists():
                raise RuntimeError("Missing snapshot for current cursor")
            base = Image.open(snap_path).convert("RGBA")
            w, h = base.size

            temp = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
            cx = float(dx) + float(dw) / 2.0
            cy = float(dy) + float(dh) / 2.0
            px = int(round(cx - (img.size[0] / 2.0)))
            py = int(round(cy - (img.size[1] / 2.0)))
            try:
                temp.paste(img, (int(px), int(py)), img)
            except Exception:
                temp.paste(img, (int(px), int(py)))

            out = Image.alpha_composite(base, temp)
            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            out.save(self._snapshot_path(cid, new_rev), format="PNG")

        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_rev), "max_rev": int(new_rev)}
        _write_json_atomic(self._canvas_json_path(cid), meta)
        try:
            if bool(layers_enabled):
                self._write_layers_state_snapshot(canvas_id=cid, rev=int(new_rev), meta=meta)
        except Exception:
            pass

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "image.import",
                "canvas_id": cid,
                "payload": {
                    "layer_id": (str(target_lid) if isinstance(target_lid, str) and target_lid.strip() else None),
                    "dest_rect": {"x": float(dx), "y": float(dy), "w": float(dw), "h": float(dh)},
                    "crop_rect": ({"l": int(l), "t": int(t), "r": int(r), "b": int(b)} if crop_rect is not None else None),
                    "rotation_deg": float(rot),
                    "opacity": float(op),
                    "from_rev": int(cursor),
                    "to_rev": int(new_rev),
                },
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def fill_bucket(
        self,
        *,
        canvas_id: Optional[str],
        x: float,
        y: float,
        alpha_threshold: Optional[int],
        actor: str,
        expected_cursor_rev: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Flood fill (bucket).

        Mode is auto:
        - If the start pixel is transparent (alpha <= alpha_threshold): fill the *transparent region*.
        - Otherwise: fill the *same-color connected region* (exact match, using the start pixel RGBA).

        Uses the current round brush rgba + opacity for the fill color.
        """
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)
        min_rev = int(hist.get("min_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        # If we're not at the tip, invalidate redo history.
        layers_enabled = self._layers_enabled(meta)
        if cursor < max_rev:
            for r in range(cursor + 1, max_rev + 1):
                try:
                    self._snapshot_path(cid, r).unlink(missing_ok=True)
                except Exception:
                    pass
                if layers_enabled:
                    for layer in self._layers_list(meta):
                        lid = layer.get("layer_id")
                        if isinstance(lid, str) and lid.strip():
                            try:
                                self._layer_snapshot_path(cid, lid.strip(), r).unlink(missing_ok=True)
                            except Exception:
                                pass
            max_rev = cursor

        tool_state = ToolState.from_meta(meta)
        round_settings = tool_state.settings_by_tool.get(StrokeToolType.ROUND) or ToolSettings(tool_type=StrokeToolType.ROUND)

        try:
            fr, fg, fb, fa0 = [int(v) for v in round_settings.rgba]
        except Exception:
            fr, fg, fb, fa0 = (0, 0, 0, 255)
        try:
            op = max(0.0, min(1.0, float(round_settings.opacity)))
        except Exception:
            op = 1.0
        fa = max(0, min(255, int(round(float(fa0) * op))))
        fill_rgba = (int(fr), int(fg), int(fb), int(fa))

        # If fill alpha is zero, this is effectively a no-op; refuse to create history noise.
        if int(fa) <= 0:
            raise RuntimeError("Fill color is fully transparent (alpha=0)")

        th = 20 if alpha_threshold is None else int(alpha_threshold)
        th = max(0, min(255, int(th)))

        new_rev = int(cursor + 1)
        bg = self._background_rgba_from_meta(meta)

        def _apply_fill(img: Image.Image, sx: int, sy: int) -> tuple[Image.Image, int]:
            w, h = img.size
            if w <= 0 or h <= 0:
                return img, 0

            # Safety cap: avoid runaway memory usage on huge canvases.
            max_pixels = 25_000_000
            if int(w) * int(h) > int(max_pixels):
                raise RuntimeError(f"Canvas too large for bucket fill ({w}x{h}); max {max_pixels} pixels")

            sx = max(0, min(int(w) - 1, int(sx)))
            sy = max(0, min(int(h) - 1, int(sy)))

            rgba_bytes = img.tobytes()  # RGBA packed
            alpha_bytes = memoryview(rgba_bytes)[3::4]

            start_i = int(sy) * int(w) + int(sx)
            if start_i < 0 or start_i >= len(alpha_bytes):
                return img, 0

            start_a = int(alpha_bytes[start_i])

            # Auto mode:
            # - start pixel transparent -> fill transparent region (alpha<=th)
            # - start pixel non-transparent -> fill same-color connected region (exact RGBA match)
            color_mode = bool(start_a > th)
            start_rgba = None
            if color_mode:
                off = int(start_i) * 4
                start_rgba = rgba_bytes[off : off + 4]

            out = img.copy()
            px = out.load()
            visited = bytearray(int(w) * int(h))

            q = deque([start_i])
            visited[start_i] = 1
            filled = 0

            def _match(i: int) -> bool:
                if not color_mode:
                    return int(alpha_bytes[i]) <= th
                off2 = int(i) * 4
                return rgba_bytes[off2 : off2 + 4] == start_rgba

            while q:
                idx = q.popleft()
                x0 = idx % int(w)
                y0 = idx // int(w)

                try:
                    px[x0, y0] = fill_rgba
                except Exception:
                    pass
                filled += 1

                # 4-neighbors
                if x0 > 0:
                    ni = idx - 1
                    if not visited[ni] and _match(ni):
                        visited[ni] = 1
                        q.append(ni)
                if x0 + 1 < int(w):
                    ni = idx + 1
                    if not visited[ni] and _match(ni):
                        visited[ni] = 1
                        q.append(ni)
                if y0 > 0:
                    ni = idx - int(w)
                    if not visited[ni] and _match(ni):
                        visited[ni] = 1
                        q.append(ni)
                if y0 + 1 < int(h):
                    ni = idx + int(w)
                    if not visited[ni] and _match(ni):
                        visited[ni] = 1
                        q.append(ni)

            return out, int(filled)

        # Convert coords
        try:
            sx = int(round(float(x)))
            sy = int(round(float(y)))
        except Exception:
            raise RuntimeError("Invalid coordinates")

        if layers_enabled:
            w = int(meta.get("width") or 0)
            h = int(meta.get("height") or 0)
            active_lid = self._active_layer_id(meta)
            if not active_lid:
                raise RuntimeError("Layered canvas is missing active_layer_id")

            layer_base = self._load_layer_image(canvas_id=cid, layer_id=active_lid, rev=int(cursor), size=(w, h))
            layer_out, filled = _apply_fill(layer_base, sx=sx, sy=sy)
            if int(filled) <= 0:
                return meta
            self._save_layer_image(canvas_id=cid, layer_id=active_lid, rev=int(new_rev), img=layer_out)

            # Carry forward other layers.
            for layer in self._layers_list(meta):
                lid = layer.get("layer_id")
                if not (isinstance(lid, str) and lid.strip()):
                    continue
                lid_s = lid.strip()
                if lid_s == active_lid:
                    continue
                src_p = self._layer_snapshot_path(cid, lid_s, int(cursor))
                dst_p = self._layer_snapshot_path(cid, lid_s, int(new_rev))
                if src_p.exists():
                    try:
                        self._layer_snapshots_dir(cid, lid_s).mkdir(parents=True, exist_ok=True)
                        dst_p.write_bytes(src_p.read_bytes())
                    except Exception:
                        blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                        self._save_layer_image(canvas_id=cid, layer_id=lid_s, rev=int(new_rev), img=blank)
                else:
                    blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    self._save_layer_image(canvas_id=cid, layer_id=lid_s, rev=int(new_rev), img=blank)

            composite = self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=int(new_rev))
            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            composite.save(self._snapshot_path(cid, new_rev), format="PNG")

        else:
            snap_path = self._snapshot_path(cid, cursor)
            if not snap_path.exists():
                raise RuntimeError("Missing snapshot for current cursor")
            base = Image.open(snap_path).convert("RGBA")
            out, filled = _apply_fill(base, sx=sx, sy=sy)
            if int(filled) <= 0:
                return meta
            self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
            out.save(self._snapshot_path(cid, new_rev), format="PNG")

        # Update meta
        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_rev), "max_rev": int(new_rev)}
        tool_state.apply_to_meta(meta)
        _write_json_atomic(self._canvas_json_path(cid), meta)
        try:
            if bool(layers_enabled):
                self._write_layers_state_snapshot(canvas_id=cid, rev=int(new_rev), meta=meta)
        except Exception:
            pass

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "fill.bucket",
                "canvas_id": cid,
                "payload": {
                    "x": int(sx),
                    "y": int(sy),
                    "alpha_threshold": int(th),
                    "fill_rgba": [int(fr), int(fg), int(fb), int(fa)],
                    "from_rev": int(cursor),
                    "to_rev": int(new_rev),
                },
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def undo(self, *, canvas_id: Optional[str], steps: int = 1, actor: str = "user") -> Dict[str, Any]:
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")
        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        min_rev = int(hist.get("min_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)

        try:
            if self._layers_enabled(meta):
                self._ensure_layers_state_snapshots(canvas_id=cid, meta=meta)
        except Exception:
            pass

        s = max(1, int(steps or 1))
        new_cursor = max(min_rev, cursor - s)
        if new_cursor == cursor:
            return meta

        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_cursor), "max_rev": int(max_rev)}
        try:
            if self._layers_enabled(meta):
                st = self._read_layers_state_snapshot(canvas_id=cid, rev=int(new_cursor))
                if isinstance(st, dict) and st:
                    meta["layers"] = st
        except Exception:
            pass
        _write_json_atomic(self._canvas_json_path(cid), meta)

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "history.undo",
                "canvas_id": cid,
                "payload": {"from_rev": int(cursor), "to_rev": int(new_cursor), "steps": int(s)},
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def redo(self, *, canvas_id: Optional[str], steps: int = 1, actor: str = "user") -> Dict[str, Any]:
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")
        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        min_rev = int(hist.get("min_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)

        try:
            if self._layers_enabled(meta):
                self._ensure_layers_state_snapshots(canvas_id=cid, meta=meta)
        except Exception:
            pass

        s = max(1, int(steps or 1))
        new_cursor = min(max_rev, cursor + s)
        if new_cursor == cursor:
            return meta

        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_cursor), "max_rev": int(max_rev)}
        try:
            if self._layers_enabled(meta):
                st = self._read_layers_state_snapshot(canvas_id=cid, rev=int(new_cursor))
                if isinstance(st, dict) and st:
                    meta["layers"] = st
        except Exception:
            pass
        _write_json_atomic(self._canvas_json_path(cid), meta)

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "history.redo",
                "canvas_id": cid,
                "payload": {"from_rev": int(cursor), "to_rev": int(new_cursor), "steps": int(s)},
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    # ----------------------------
    # Layers (Phase 2)
    # ----------------------------

    def _enable_layers_from_flattened_history(self, *, canvas_id: str, meta: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """One-way migration: convert a legacy flattened canvas into a layered canvas.

        We create a single Background layer and copy each existing composite snapshot into
        that layer for every rev, so undo/redo remains coherent.
        """
        if self._layers_enabled(meta):
            return meta

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        min_rev = int(hist.get("min_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", 0) or 0)

        bg = self._background_rgba_from_meta(meta)
        w = int(meta.get("width") or 0)
        h = int(meta.get("height") or 0)

        bg_layer_id = f"l_{uuid.uuid4().hex[:12]}"
        meta["layers_enabled"] = True
        meta["layers"] = {
            "schema_version": 1,
            "active_layer_id": bg_layer_id,
            "layers": [
                {
                    "layer_id": bg_layer_id,
                    "name": "Background",
                    "description": None,
                    "visible": True,
                    "opacity": 1.0,
                    "role": "background",
                }
            ],
        }

        # Copy existing snapshots into the background layer.
        for r in range(int(min_rev), int(max_rev) + 1):
            snap = self._snapshot_path(canvas_id, int(r))
            if snap.exists():
                try:
                    data = snap.read_bytes()
                    self._layer_snapshots_dir(canvas_id, bg_layer_id).mkdir(parents=True, exist_ok=True)
                    self._layer_snapshot_path(canvas_id, bg_layer_id, int(r)).write_bytes(data)
                except Exception:
                    img = Image.new("RGBA", (w, h), bg)
                    self._save_layer_image(canvas_id=canvas_id, layer_id=bg_layer_id, rev=int(r), img=img)
            else:
                img = Image.new("RGBA", (w, h), bg)
                self._save_layer_image(canvas_id=canvas_id, layer_id=bg_layer_id, rev=int(r), img=img)

            try:
                self._write_layers_state_snapshot(canvas_id=canvas_id, rev=int(r), meta=meta)
            except Exception:
                pass

        now = _utc_now_z()
        meta["updated_at"] = now
        _write_json_atomic(self._canvas_json_path(canvas_id), meta)
        self._touch_index_updated_at(canvas_id, now)

        _append_jsonl(
            self._actions_path(canvas_id),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "layers.enable",
                "canvas_id": canvas_id,
                "payload": {"min_rev": int(min_rev), "max_rev": int(max_rev)},
            },
        )

        return meta

    def _invalidate_redo_history_for_layers(self, *, canvas_id: str, meta: Dict[str, Any], cursor: int, max_rev: int) -> int:
        if int(cursor) >= int(max_rev):
            return int(max_rev)
        for r in range(int(cursor) + 1, int(max_rev) + 1):
            try:
                self._snapshot_path(canvas_id, r).unlink(missing_ok=True)
            except Exception:
                pass
            try:
                self._layers_state_path(canvas_id, r).unlink(missing_ok=True)
            except Exception:
                pass
            for layer in self._layers_list(meta):
                lid = layer.get("layer_id")
                if isinstance(lid, str) and lid.strip():
                    try:
                        self._layer_snapshot_path(canvas_id, lid.strip(), r).unlink(missing_ok=True)
                    except Exception:
                        pass
        return int(cursor)

    def _copy_layer_forward(self, *, canvas_id: str, layer_id: str, src_rev: int, dst_rev: int, size: Tuple[int, int]) -> None:
        src_p = self._layer_snapshot_path(canvas_id, layer_id, int(src_rev))
        dst_p = self._layer_snapshot_path(canvas_id, layer_id, int(dst_rev))
        if src_p.exists():
            self._layer_snapshots_dir(canvas_id, layer_id).mkdir(parents=True, exist_ok=True)
            dst_p.write_bytes(src_p.read_bytes())
        else:
            blank = Image.new("RGBA", (int(size[0]), int(size[1])), (0, 0, 0, 0))
            self._save_layer_image(canvas_id=canvas_id, layer_id=layer_id, rev=int(dst_rev), img=blank)

    def layer_create(
        self,
        *,
        canvas_id: Optional[str],
        name: Optional[str],
        description: Optional[str],
        set_active: bool,
        source_layer_id: Optional[str],
        expected_cursor_rev: Optional[int],
        actor: str,
    ) -> Dict[str, Any]:
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")

        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        if not self._layers_enabled(meta):
            meta = self._enable_layers_from_flattened_history(canvas_id=cid, meta=meta, actor=actor)

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)
        min_rev = int(hist.get("min_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        if cursor < max_rev:
            max_rev = self._invalidate_redo_history_for_layers(canvas_id=cid, meta=meta, cursor=cursor, max_rev=max_rev)

        w = int(meta.get("width") or 0)
        h = int(meta.get("height") or 0)

        lid_new = f"l_{uuid.uuid4().hex[:12]}"
        nm = (str(name).strip() if isinstance(name, str) else "") or f"Layer {len(self._layers_list(meta))}"
        desc = (str(description) if isinstance(description, str) else None)

        # Insert above active layer.
        layers = list(self._layers_list(meta))
        active_lid = self._active_layer_id(meta) or (layers[-1].get("layer_id") if layers else None)
        insert_at = len(layers)
        if active_lid:
            for i, lyr in enumerate(layers):
                if str(lyr.get("layer_id") or "").strip() == str(active_lid):
                    insert_at = i + 1
                    break

        new_layer = {
            "layer_id": lid_new,
            "name": nm,
            "description": desc,
            "visible": True,
            "opacity": 1.0,
            "role": "layer",
        }
        layers.insert(int(insert_at), new_layer)

        st = self._layers_state(meta)
        st["layers"] = layers
        if bool(set_active):
            st["active_layer_id"] = lid_new
        meta["layers"] = st

        new_rev = int(cursor + 1)

        # Copy-forward existing layer snapshots.
        for lyr in layers:
            lid = str(lyr.get("layer_id") or "").strip()
            if not lid:
                continue
            if lid == lid_new:
                continue
            self._copy_layer_forward(canvas_id=cid, layer_id=lid, src_rev=cursor, dst_rev=new_rev, size=(w, h))

        # New layer pixels.
        if isinstance(source_layer_id, str) and source_layer_id.strip():
            src_lid = source_layer_id.strip()
            self._copy_layer_forward(canvas_id=cid, layer_id=src_lid, src_rev=cursor, dst_rev=new_rev, size=(w, h))
            # Overwrite into new id.
            try:
                data = self._layer_snapshot_path(cid, src_lid, new_rev).read_bytes()
                self._layer_snapshots_dir(cid, lid_new).mkdir(parents=True, exist_ok=True)
                self._layer_snapshot_path(cid, lid_new, new_rev).write_bytes(data)
            except Exception:
                blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                self._save_layer_image(canvas_id=cid, layer_id=lid_new, rev=new_rev, img=blank)
        else:
            blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            self._save_layer_image(canvas_id=cid, layer_id=lid_new, rev=new_rev, img=blank)

        # Composite snapshot
        composite = self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=new_rev)
        self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
        composite.save(self._snapshot_path(cid, new_rev), format="PNG")

        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_rev), "max_rev": int(new_rev)}
        _write_json_atomic(self._canvas_json_path(cid), meta)
        self._write_layers_state_snapshot(canvas_id=cid, rev=new_rev, meta=meta)

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "layer.create",
                "canvas_id": cid,
                "payload": {"layer_id": lid_new, "name": nm, "from_rev": int(cursor), "to_rev": int(new_rev)},
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def layer_delete(
        self,
        *,
        canvas_id: Optional[str],
        layer_id: str,
        expected_cursor_rev: Optional[int],
        actor: str,
    ) -> Dict[str, Any]:
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")
        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")
        if not self._layers_enabled(meta):
            raise RuntimeError("Layers are not enabled")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)
        min_rev = int(hist.get("min_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        lid_del = str(layer_id or "").strip()
        if not lid_del:
            raise RuntimeError("layer_id is required")
        if self._is_background_layer_id(meta, lid_del):
            raise RuntimeError("Cannot delete the Background layer")

        if cursor < max_rev:
            max_rev = self._invalidate_redo_history_for_layers(canvas_id=cid, meta=meta, cursor=cursor, max_rev=max_rev)

        layers = [l for l in self._layers_list(meta) if str(l.get("layer_id") or "").strip() != lid_del]
        if not layers:
            raise RuntimeError("Refusing to delete the last layer")

        st = self._layers_state(meta)
        st["layers"] = layers

        # Fix active layer.
        active = st.get("active_layer_id")
        if str(active or "").strip() == lid_del:
            st["active_layer_id"] = str(layers[-1].get("layer_id"))
        meta["layers"] = st

        w = int(meta.get("width") or 0)
        h = int(meta.get("height") or 0)
        new_rev = int(cursor + 1)

        for lyr in layers:
            lid = str(lyr.get("layer_id") or "").strip()
            if not lid:
                continue
            self._copy_layer_forward(canvas_id=cid, layer_id=lid, src_rev=cursor, dst_rev=new_rev, size=(w, h))

        composite = self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=new_rev)
        self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
        composite.save(self._snapshot_path(cid, new_rev), format="PNG")

        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_rev), "max_rev": int(new_rev)}
        _write_json_atomic(self._canvas_json_path(cid), meta)
        self._write_layers_state_snapshot(canvas_id=cid, rev=new_rev, meta=meta)

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "layer.delete",
                "canvas_id": cid,
                "payload": {"layer_id": lid_del, "from_rev": int(cursor), "to_rev": int(new_rev)},
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    def layer_update(
        self,
        *,
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
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")
        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")
        if not self._layers_enabled(meta):
            raise RuntimeError("Layers are not enabled")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        max_rev = int(hist.get("max_rev", cursor) or cursor)
        min_rev = int(hist.get("min_rev", 0) or 0)

        if expected_cursor_rev is not None and int(expected_cursor_rev) != int(cursor):
            raise RuntimeError(f"Canvas history cursor mismatch (expected {expected_cursor_rev}, actual {cursor})")

        if cursor < max_rev:
            max_rev = self._invalidate_redo_history_for_layers(canvas_id=cid, meta=meta, cursor=cursor, max_rev=max_rev)

        lid = str(layer_id or "").strip()
        if not lid:
            raise RuntimeError("layer_id is required")

        layers = list(self._layers_list(meta))
        lyr = None
        for it in layers:
            if str(it.get("layer_id") or "").strip() == lid:
                lyr = it
                break
        if lyr is None:
            raise RuntimeError("Layer not found")

        is_bg = self._is_background_layer_id(meta, lid)

        if isinstance(name, str):
            lyr["name"] = str(name)

        # Description tri-state:
        # - description=str -> set
        # - clear_description=True -> clear
        # - otherwise: no change
        if isinstance(description, str):
            lyr["description"] = str(description)
        elif bool(clear_description):
            lyr["description"] = None

        if visible is not None and not bool(is_bg):
            lyr["visible"] = bool(visible)
        if opacity is not None:
            try:
                lyr["opacity"] = max(0.0, min(1.0, float(opacity)))
            except Exception:
                pass

        st = self._layers_state(meta)
        if set_active is True:
            st["active_layer_id"] = lid

        # Reorder (index is bottom->top). Background is pinned at 0.
        if move_to_index is not None:
            try:
                idx = int(move_to_index)
            except Exception:
                idx = None
            if idx is not None:
                if bool(is_bg):
                    raise RuntimeError("Cannot reorder the Background layer")
                layers2 = [x for x in layers if str(x.get("layer_id") or "").strip() != lid]
                bg_id = self._background_layer_id(meta)
                if bg_id and layers2 and str(layers2[0].get("layer_id") or "") != str(bg_id):
                    # Ensure background stays at index 0.
                    for i2, it2 in enumerate(layers2):
                        if str(it2.get("layer_id") or "") == str(bg_id):
                            bg_layer = layers2.pop(i2)
                            layers2.insert(0, bg_layer)
                            break
                idx2 = max(1, min(len(layers2), int(idx)))
                layers2.insert(idx2, lyr)
                layers = layers2

        st["layers"] = layers
        meta["layers"] = st

        w = int(meta.get("width") or 0)
        h = int(meta.get("height") or 0)
        new_rev = int(cursor + 1)

        for it in layers:
            it_lid = str(it.get("layer_id") or "").strip()
            if not it_lid:
                continue
            self._copy_layer_forward(canvas_id=cid, layer_id=it_lid, src_rev=cursor, dst_rev=new_rev, size=(w, h))

        composite = self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=new_rev)
        self._snapshots_dir(cid).mkdir(parents=True, exist_ok=True)
        composite.save(self._snapshot_path(cid, new_rev), format="PNG")

        now = _utc_now_z()
        meta["updated_at"] = now
        meta["history"] = {"min_rev": int(min_rev), "cursor_rev": int(new_rev), "max_rev": int(new_rev)}
        _write_json_atomic(self._canvas_json_path(cid), meta)
        self._write_layers_state_snapshot(canvas_id=cid, rev=new_rev, meta=meta)

        _append_jsonl(
            self._actions_path(cid),
            {
                "schema_version": 1,
                "action_id": str(uuid.uuid4()),
                "ts": now,
                "actor": str(actor or "user"),
                "type": "layer.update",
                "canvas_id": cid,
                "payload": {"layer_id": lid, "from_rev": int(cursor), "to_rev": int(new_rev)},
            },
        )

        self._touch_index_updated_at(cid, now)
        return meta

    # ----------------------------
    # Images
    # ----------------------------

    def get_current_image_png_bytes(self, *, canvas_id: Optional[str]) -> Tuple[str, Dict[str, Any], bytes]:
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")
        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)
        snap = self._snapshot_path(cid, cursor)
        if not snap.exists():
            raise RuntimeError("Missing snapshot")
        data = snap.read_bytes()
        return cid, meta, data

    def get_layer_image_png_bytes(self, *, canvas_id: Optional[str], layer_id: str) -> Tuple[str, Dict[str, Any], bytes]:
        """Return PNG bytes for a single layer at the current cursor_rev.

        Intended for agent vision/debugging. This returns the raw layer pixels (with alpha),
        and does not apply layer visibility/opacity.
        """
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")
        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        if not self._layers_enabled(meta):
            raise RuntimeError("Canvas does not have layers enabled")

        lid = str(layer_id or "").strip()
        if not lid:
            raise RuntimeError("layer_id is required")

        # Validate existence.
        found = False
        for lyr in self._layers_list(meta):
            try:
                if str(lyr.get("layer_id") or "") == lid:
                    found = True
                    break
            except Exception:
                pass
        if not found:
            raise RuntimeError("Layer not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)

        w = int(meta.get("width") or 0)
        h = int(meta.get("height") or 0)

        try:
            from io import BytesIO

            img = self._load_layer_image(canvas_id=cid, layer_id=lid, rev=int(cursor), size=(w, h))
            buf = BytesIO()
            img.save(buf, format="PNG")
            return cid, meta, buf.getvalue()
        except Exception as e:
            raise RuntimeError(f"Failed to render layer image: {e}")

    def get_export_image_png_bytes(self, *, canvas_id: Optional[str]) -> Tuple[str, Dict[str, Any], bytes]:
        """Return PNG bytes intended for export.

        For pixel_art canvases, this scales the logical grid up by `cell_px` using NEAREST.
        For normal canvases, this returns the raw snapshot.
        """
        cid, meta, png_bytes = self.get_current_image_png_bytes(canvas_id=canvas_id)

        if not self._is_pixel_art(meta):
            return cid, meta, png_bytes

        cell_px = int(self._pixel_cell_px(meta))
        if cell_px <= 1:
            return cid, meta, png_bytes

        try:
            from io import BytesIO

            img = Image.open(BytesIO(png_bytes)).convert("RGBA")
            w, h = img.size

            out_w = int(w) * int(cell_px)
            out_h = int(h) * int(cell_px)
            max_pixels = 25_000_000
            if int(out_w) * int(out_h) > int(max_pixels):
                raise RuntimeError(f"Export image too large ({out_w}x{out_h}); max {max_pixels} pixels")

            img2 = img.resize((int(out_w), int(out_h)), resample=Image.Resampling.NEAREST)
            out_buf = BytesIO()
            img2.save(out_buf, format="PNG")
            return cid, meta, out_buf.getvalue()
        except Exception as e:
            raise RuntimeError(f"Failed to render export image: {e}")

    def get_export_gif_bytes(
        self,
        *,
        canvas_id: Optional[str],
        frame_duration_ms: int = 120,
        loop_forever: bool = True,
    ) -> Tuple[str, Dict[str, Any], bytes]:
        """Return animated GIF bytes intended for export.

        Phase 1 semantics: a cumulative build-up animation across the visible layer stack.
        Frame1: bottom-most visible layer(s) (usually Background)
        FrameN: full composite.

        Pixel-art canvases are scaled by `cell_px` using NEAREST.
        """
        cid = self.resolve_canvas_id(canvas_id)
        if not cid:
            raise RuntimeError("No current canvas")
        meta = self.load_canvas_meta(cid)
        if not meta:
            raise RuntimeError("Canvas not found")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cursor = int(hist.get("cursor_rev", 0) or 0)

        try:
            frame_ms = int(frame_duration_ms)
        except Exception:
            frame_ms = 120
        frame_ms = max(10, min(10000, int(frame_ms)))

        # Build frames.
        frames: List[Image.Image] = []

        if not self._layers_enabled(meta):
            # Legacy/flattened fallback: single-frame GIF.
            snap = self._snapshot_path(cid, int(cursor))
            if not snap.exists():
                raise RuntimeError("Missing snapshot")
            frames = [Image.open(snap).convert("RGBA")]
        else:
            layers = self._layers_list(meta)

            # Only animate layers that actually contribute.
            contributing: List[Dict[str, Any]] = []
            for lyr in layers:
                if not isinstance(lyr, dict):
                    continue
                if not bool(lyr.get("visible", True)):
                    continue
                try:
                    op = float(lyr.get("opacity", 1.0))
                except Exception:
                    op = 1.0
                if op <= 0.0:
                    continue
                lid = str(lyr.get("layer_id") or "").strip()
                if not lid:
                    continue
                contributing.append(lyr)

            # Always produce at least one frame.
            if not contributing:
                frames = [self._composite_layers_for_rev(canvas_id=cid, meta=meta, rev=int(cursor))]
            else:
                base_state = self._layers_state(meta)
                for i in range(1, len(contributing) + 1):
                    st = dict(base_state)
                    st["layers"] = contributing[:i]
                    m2 = dict(meta)
                    m2["layers"] = st
                    frames.append(self._composite_layers_for_rev(canvas_id=cid, meta=m2, rev=int(cursor)))

        # Pixel-art export scaling (crisp).
        if self._is_pixel_art(meta):
            cell_px = int(self._pixel_cell_px(meta))
            if cell_px > 1:
                scaled: List[Image.Image] = []
                for im in frames:
                    w, h = im.size
                    out_w = int(w) * int(cell_px)
                    out_h = int(h) * int(cell_px)
                    max_pixels = 25_000_000
                    if int(out_w) * int(out_h) > int(max_pixels):
                        raise RuntimeError(f"Export GIF frame too large ({out_w}x{out_h}); max {max_pixels} pixels")
                    scaled.append(im.resize((int(out_w), int(out_h)), resample=Image.Resampling.NEAREST))
                frames = scaled

        # Encode.
        try:
            from io import BytesIO

            buf = BytesIO()
            save_kwargs: Dict[str, Any] = {
                "format": "GIF",
                "save_all": True,
                "append_images": frames[1:],
                "duration": int(frame_ms),
                "optimize": False,
            }
            if bool(loop_forever):
                save_kwargs["loop"] = 0
            frames[0].save(buf, **save_kwargs)
            return cid, meta, buf.getvalue()
        except Exception as e:
            raise RuntimeError(f"Failed to encode GIF: {e}")

    def _render_injected_image_message_from_png_bytes(
        self,
        *,
        canvas_id: str,
        meta: Dict[str, Any],
        png_bytes: bytes,
        max_side: Optional[int],
        caption: Optional[str],
    ) -> Dict[str, Any]:
        # Downscale if needed.
        max_side_i = int(max_side) if isinstance(max_side, int) and max_side > 0 else int(self.default_injected_max_side)

        try:
            from io import BytesIO

            img = Image.open(BytesIO(png_bytes)).convert("RGBA")
            w, h = img.size

            # Pixel-art inject visibility: if the logical grid is tiny, upscale it first (crisp).
            # We clamp the upscale so the injected image still respects max_side.
            if self._is_pixel_art(meta):
                try:
                    cell_px = int(self._pixel_cell_px(meta))
                except Exception:
                    cell_px = 1
                if int(cell_px) > 1 and int(max_side_i) > 0:
                    max_scale = max(1, int(max_side_i) // max(1, int(max(w, h))))
                    scale = max(1, min(int(cell_px), int(max_scale)))
                    if scale > 1:
                        img = img.resize((int(w) * int(scale), int(h) * int(scale)), resample=Image.Resampling.NEAREST)
                        w, h = img.size

            # Downscale if needed.
            if max_side_i and max(w, h) > max_side_i:
                if w >= h:
                    nw = max_side_i
                    nh = max(1, int(round(h * (max_side_i / w))))
                else:
                    nh = max_side_i
                    nw = max(1, int(round(w * (max_side_i / h))))
                resample = Image.Resampling.NEAREST if self._is_pixel_art(meta) else Image.Resampling.LANCZOS
                img = img.resize((nw, nh), resample=resample)

            out_buf = BytesIO()
            img.save(out_buf, format="PNG")
            b64 = base64.b64encode(out_buf.getvalue()).decode("utf-8")
        except Exception:
            b64 = base64.b64encode(png_bytes).decode("utf-8")

        try:
            cursor = int((meta.get("history") or {}).get("cursor_rev") or 0)
        except Exception:
            cursor = 0

        cap = caption or f"Canvas {canvas_id} (cursor_rev={cursor})"

        return {
            "role": "user",
            "content": [
                {"type": "input_text", "text": cap},
                {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
            ],
        }

    def render_injected_image_message(
        self,
        *,
        canvas_id: Optional[str],
        max_side: Optional[int],
        caption: Optional[str],
    ) -> Dict[str, Any]:
        cid, meta, png_bytes = self.get_current_image_png_bytes(canvas_id=canvas_id)
        return self._render_injected_image_message_from_png_bytes(
            canvas_id=str(cid),
            meta=meta,
            png_bytes=png_bytes,
            max_side=max_side,
            caption=caption,
        )

    # ----------------------------
    # Index helpers
    # ----------------------------

    def _upsert_index_entry(self, entry: Dict[str, Any]) -> None:
        idx_path = self._index_path()
        idx = _read_json(idx_path, {"schema_version": 1, "canvases": []})
        if not isinstance(idx, dict):
            idx = {"schema_version": 1, "canvases": []}

        canvases = idx.get("canvases")
        if not isinstance(canvases, list):
            canvases = []

        cid = str(entry.get("canvas_id") or "")
        out: List[Dict[str, Any]] = []
        replaced = False
        for it in canvases:
            if isinstance(it, dict) and str(it.get("canvas_id") or "") == cid:
                out.append(dict(entry))
                replaced = True
            elif isinstance(it, dict):
                out.append(it)
        if not replaced:
            out.append(dict(entry))

        idx["canvases"] = out
        _write_json_atomic(idx_path, idx)

    def _remove_index_entry(self, canvas_id: str) -> None:
        idx_path = self._index_path()
        idx = _read_json(idx_path, {"schema_version": 1, "canvases": []})
        if not isinstance(idx, dict):
            return
        canvases = idx.get("canvases")
        if not isinstance(canvases, list):
            return
        cid = str(canvas_id or "")
        idx["canvases"] = [it for it in canvases if not (isinstance(it, dict) and str(it.get("canvas_id") or "") == cid)]
        _write_json_atomic(idx_path, idx)

    def _update_index_entry(self, canvas_id: str, patch: Dict[str, Any]) -> None:
        """Update selected fields for a canvas in index.json."""
        idx_path = self._index_path()
        idx = _read_json(idx_path, {"schema_version": 1, "canvases": []})
        if not isinstance(idx, dict):
            return
        canvases = idx.get("canvases")
        if not isinstance(canvases, list):
            return

        cid = str(canvas_id or "")
        out = []
        for it in canvases:
            if not isinstance(it, dict):
                continue
            if str(it.get("canvas_id") or "") == cid:
                it2 = dict(it)
                for k, v in (patch or {}).items():
                    it2[str(k)] = v
                out.append(it2)
            else:
                out.append(it)

        idx["canvases"] = out
        _write_json_atomic(idx_path, idx)

    def _touch_index_updated_at(self, canvas_id: str, updated_at: str) -> None:
        idx_path = self._index_path()
        idx = _read_json(idx_path, {"schema_version": 1, "canvases": []})
        if not isinstance(idx, dict):
            return
        canvases = idx.get("canvases")
        if not isinstance(canvases, list):
            return
        cid = str(canvas_id or "")
        out = []
        for it in canvases:
            if not isinstance(it, dict):
                continue
            if str(it.get("canvas_id") or "") == cid:
                it2 = dict(it)
                it2["updated_at"] = str(updated_at)
                out.append(it2)
            else:
                out.append(it)
        idx["canvases"] = out
        _write_json_atomic(idx_path, idx)
