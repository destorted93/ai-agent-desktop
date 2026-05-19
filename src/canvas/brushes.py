from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from PIL import Image, ImageChops


class StrokeToolType(str, Enum):
    """Stroke-producing tools.

    NOTE: This is intentionally small and strict. Add new values only when the
    corresponding rendering semantics are implemented.
    """

    ROUND = "round"
    ERASER = "eraser"


def parse_stroke_tool_type(value: Any) -> StrokeToolType:
    s = str(value or "").strip().lower()
    try:
        return StrokeToolType(s)
    except Exception:
        raise ValueError(f"Unknown tool type: {value!r}")


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
    return (0, 0, 0, 255)


@dataclass
class ToolSettings:
    """Settings for a stroke-producing tool."""

    tool_type: StrokeToolType
    rgba: Tuple[int, int, int, int] = (0, 0, 0, 255)
    radius: int = 12
    opacity: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "type": self.tool_type.value,
            "radius": int(self.radius),
            "rgba": [int(x) for x in self.rgba],
            "opacity": float(self.opacity),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], *, default_type: StrokeToolType) -> "ToolSettings":
        t = default_type
        try:
            if isinstance(d.get("type"), str) and str(d.get("type")).strip():
                t = parse_stroke_tool_type(d.get("type"))
        except Exception:
            t = default_type

        rgba = _rgba_tuple(d.get("rgba"))
        radius = _clamp_int(d.get("radius", 12), 1, 4096)
        opacity = _clamp_float(d.get("opacity", 1.0), 0.0, 1.0)
        return cls(tool_type=t, rgba=rgba, radius=radius, opacity=opacity)


@dataclass
class ToolState:
    """Persisted tool selection + per-tool settings for a canvas."""

    current_tool: StrokeToolType
    settings_by_tool: Dict[StrokeToolType, ToolSettings]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "current_tool": self.current_tool.value,
            "tool_settings": {k.value: v.to_dict() for (k, v) in self.settings_by_tool.items()},
        }

    @classmethod
    def from_meta(cls, meta: Mapping[str, Any]) -> "ToolState":
        # Preferred: tool_state
        ts = meta.get("tool_state") if isinstance(meta.get("tool_state"), dict) else None
        if isinstance(ts, dict):
            cur_raw = ts.get("current_tool")
            cur = parse_stroke_tool_type(cur_raw)

            tb = ts.get("tool_settings") if isinstance(ts.get("tool_settings"), dict) else {}
            settings_by_tool: Dict[StrokeToolType, ToolSettings] = {}

            # Strictly accept only known tools.
            for tool in (StrokeToolType.ROUND, StrokeToolType.ERASER):
                d = tb.get(tool.value)
                if isinstance(d, dict):
                    settings_by_tool[tool] = ToolSettings.from_dict(d, default_type=tool)

            # Backfill missing from legacy current_brush.
            if StrokeToolType.ROUND not in settings_by_tool:
                cb = meta.get("current_brush") if isinstance(meta.get("current_brush"), dict) else {}
                settings_by_tool[StrokeToolType.ROUND] = ToolSettings.from_dict(cb, default_type=StrokeToolType.ROUND)

            if StrokeToolType.ERASER not in settings_by_tool:
                # Default eraser: copy radius/opacity from round so it feels sane.
                b = settings_by_tool.get(StrokeToolType.ROUND) or ToolSettings(tool_type=StrokeToolType.ROUND)
                settings_by_tool[StrokeToolType.ERASER] = ToolSettings(
                    tool_type=StrokeToolType.ERASER,
                    rgba=b.rgba,
                    radius=b.radius,
                    opacity=b.opacity,
                )

            return cls(current_tool=cur, settings_by_tool=settings_by_tool)

        # Legacy fallback: derive from current_brush
        cb = meta.get("current_brush") if isinstance(meta.get("current_brush"), dict) else {}
        round_settings = ToolSettings.from_dict(cb, default_type=StrokeToolType.ROUND)
        eraser_settings = ToolSettings(tool_type=StrokeToolType.ERASER, rgba=round_settings.rgba, radius=round_settings.radius, opacity=round_settings.opacity)
        return cls(
            current_tool=StrokeToolType.ROUND,
            settings_by_tool={
                StrokeToolType.ROUND: round_settings,
                StrokeToolType.ERASER: eraser_settings,
            },
        )

    def apply_to_meta(self, meta: Dict[str, Any]) -> None:
        meta["tool_state"] = self.to_dict()

        # Keep legacy `current_brush` field aligned with the *current tool's* settings,
        # so existing UI/tooling that reads it still gets a coherent view.
        try:
            meta["current_brush"] = self.settings_by_tool[self.current_tool].to_dict()
        except Exception:
            meta["current_brush"] = ToolSettings(tool_type=StrokeToolType.ROUND).to_dict()


class StrokeToolEngine:
    """Strategy interface for applying a stroke mask to a base image."""

    def apply(
        self,
        *,
        base: Image.Image,
        mask_l: Image.Image,
        settings: ToolSettings,
        background_rgba: Tuple[int, int, int, int],
    ) -> Image.Image:
        raise NotImplementedError


class RoundBrushEngine(StrokeToolEngine):
    def apply(self, *, base: Image.Image, mask_l: Image.Image, settings: ToolSettings, background_rgba: Tuple[int, int, int, int]) -> Image.Image:
        rgba = settings.rgba

        # Apply brush alpha/opacity once.
        try:
            factor = float(settings.opacity) * (float(rgba[3]) / 255.0)
            factor = max(0.0, min(1.0, factor))
        except Exception:
            factor = 1.0

        alpha = mask_l if factor >= 0.999 else mask_l.point(lambda p: int(p * factor))

        overlay = Image.new("RGBA", base.size, (int(rgba[0]), int(rgba[1]), int(rgba[2]), 0))
        overlay.putalpha(alpha)
        return Image.alpha_composite(base, overlay)


class EraserEngine(StrokeToolEngine):
    """Eraser for the legacy flattened storage model.

    The historical semantics are: paint with the canvas background color.

    For layered/transparent canvases, prefer AlphaEraserEngine (true alpha erase).
    """

    def apply(self, *, base: Image.Image, mask_l: Image.Image, settings: ToolSettings, background_rgba: Tuple[int, int, int, int]) -> Image.Image:
        bg = background_rgba

        try:
            factor = float(settings.opacity) * (float(bg[3]) / 255.0)
            factor = max(0.0, min(1.0, factor))
        except Exception:
            factor = 1.0

        alpha = mask_l if factor >= 0.999 else mask_l.point(lambda p: int(p * factor))

        overlay = Image.new("RGBA", base.size, (int(bg[0]), int(bg[1]), int(bg[2]), 0))
        overlay.putalpha(alpha)
        return Image.alpha_composite(base, overlay)


class AlphaEraserEngine(StrokeToolEngine):
    """True alpha erase.

    Subtracts from the alpha channel under the stroke mask, preserving RGB.

    Used for:
    - transparent-background canvases
    - layered canvases (erase reveals lower layers)
    """

    def apply(self, *, base: Image.Image, mask_l: Image.Image, settings: ToolSettings, background_rgba: Tuple[int, int, int, int]) -> Image.Image:
        try:
            factor = float(settings.opacity)
            factor = max(0.0, min(1.0, factor))
        except Exception:
            factor = 1.0

        scaled = mask_l if factor >= 0.999 else mask_l.point(lambda p: int(p * factor))

        out = base.copy()
        a = out.getchannel("A")
        out.putalpha(ImageChops.subtract(a, scaled))
        return out


STROKE_TOOL_ENGINES: Dict[StrokeToolType, StrokeToolEngine] = {
    StrokeToolType.ROUND: RoundBrushEngine(),
    StrokeToolType.ERASER: EraserEngine(),
}
