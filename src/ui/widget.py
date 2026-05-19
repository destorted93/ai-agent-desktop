import os
import sounddevice as sd
import wave
import io
import time
import threading
import json
import traceback
import uuid
from ..appcore.runtime_context import Runtime
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QDialog,
    QLabel,
    QPlainTextEdit,
)
from PyQt6.QtGui import QAction, QIcon, QPixmap, QPainter, QFont, QColor, QPen, QGuiApplication, QCursor
from PyQt6.QtCore import Qt, QEvent, pyqtSignal, pyqtSlot, QTimer, QPoint

from .components import SettingsWindow
from .components import MemoriesWindow
from .components import DocumentsWindow
from .components import InnerVoiceWindow

from .components import SessionJsonWindow
from .components import ChatWindow


class CircleEmojiButton(QPushButton):
    """A crisp circular emoji button drawn with QPainter.

    We use this instead of QSS borders to avoid the dotted/dithered ring artifacts
    that can appear on translucent windows (especially with fractional DPI scaling).
    """

    def __init__(self, emoji: str, parent=None):
        super().__init__(emoji, parent)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")

    def enterEvent(self, event):
        self._hover = True
        try:
            self.update()
        except Exception:
            pass
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        try:
            self.update()
        except Exception:
            pass
        return super().leaveEvent(event)

    def paintEvent(self, event):
        # Custom paint: background + border + emoji text.
        p = None
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            r = self.rect()

            # Colors tuned to match the app vibe.
            bg = QColor(70, 70, 70, 220) if self._hover else QColor(50, 50, 50, 200)
            border = QColor(255, 255, 255, 90) if self._hover else QColor(255, 255, 255, 70)

            pen = QPen(border)
            pen.setWidth(2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(bg)

            inset = pen.widthF() / 2.0
            rr = r.adjusted(int(inset), int(inset), -int(inset), -int(inset))
            p.drawEllipse(rr)

            # Inference animation: subtle rotating ring (keep the fox visible).
            try:
                par = self.parent()
                running = bool(getattr(par, "_inference_running", False)) if par is not None else False
                step = int(getattr(par, "_inference_anim_step", 0)) if par is not None else 0
                if running:
                    ring_pen = QPen(QColor(214, 179, 106, 210))  # warm gold
                    ring_pen.setWidth(3)
                    ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(ring_pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)

                    ring_rect = rr.adjusted(3, 3, -3, -3)
                    start = int((-step * 45) * 16)
                    span = int(90 * 16)
                    p.drawArc(ring_rect, start, span)
                    p.drawArc(ring_rect, start + int(180 * 16), span)
            except Exception:
                pass

            # Emoji
            font_px = max(10, int(min(r.width(), r.height()) * 0.50))
            p.setFont(QFont("Segoe UI Emoji", font_px))
            p.setPen(QColor(255, 255, 255))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())

        finally:
            try:
                if p is not None:
                    p.end()
            except Exception:
                pass

class DockHandleButton(QPushButton):
    """A slim vertical 'dock handle' shown when the widget is snapped to a screen edge."""

    def __init__(self, emoji: str = "🦊", parent=None):
        super().__init__("", parent)
        self._hover = False
        self._edge = "left"  # left|right
        self._emoji = emoji or "🦊"

        # Avoid the confusing up/down resize cursor.
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")

    def set_edge(self, edge: str) -> None:
        edge = (edge or "").strip().lower()
        if edge in ("left", "right"):
            self._edge = edge
            try:
                self.update()
            except Exception:
                pass

    def set_emoji(self, emoji: str) -> None:
        try:
            self._emoji = emoji or self._emoji
            self.update()
        except Exception:
            pass

    def enterEvent(self, event):
        self._hover = True
        try:
            self.update()
        except Exception:
            pass
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        try:
            self.update()
        except Exception:
            pass
        return super().leaveEvent(event)

    def paintEvent(self, event):
        p = None
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            r = self.rect()

            bg = QColor(40, 40, 40, 180) if not self._hover else QColor(55, 55, 55, 210)
            rim_soft = QColor(255, 255, 255, 50)
            rim_hard = QColor(255, 255, 255, 95)

            # Body
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            radius = max(6, int(min(r.width(), r.height()) * 0.45))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius, radius)

            # Rim (pulses gently during inference)
            try:
                par = self.parent()
                running = bool(getattr(par, "_inference_running", False)) if par is not None else False
                step = int(getattr(par, "_inference_anim_step", 0)) if par is not None else 0
            except Exception:
                running = False
                step = 0

            if running:
                alphas = [55, 70, 90, 110, 90, 70, 55, 45]
                a = alphas[int(step) % len(alphas)]
                rim = QColor(214, 179, 106, min(255, int(a * 1.2)))  # warm gold pulse (+20%)
            else:
                rim = rim_hard if self._hover else rim_soft

            pen = QPen(rim)
            pen.setWidth(2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius, radius)

            # Brand: fox emoji + subtle grip dots
            try:
                font_px = max(10, int(min(r.width(), r.height()) * 0.60))
                p.setFont(QFont("Segoe UI Emoji", font_px))
                p.setPen(QColor(255, 255, 255, 210 if self._hover else 170))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._emoji)
            except Exception:
                pass

            p.setPen(QColor(255, 255, 255, 130 if self._hover else 90))
            dot_r = max(1, int(r.width() * 0.09))
            cx = r.center().x()
            cy = r.center().y()
            dy = max(6, int(r.height() * 0.16))
            for yy in (cy - dy, cy, cy + dy):
                p.drawEllipse(QPoint(cx, int(yy)), dot_r, dot_r)

        finally:
            try:
                if p is not None:
                    p.end()
            except Exception:
                pass


class FloatingWidget(QWidget):
    """Main floating widget - the entry point for the AI assistant."""
    
    session_loaded = pyqtSignal(list)  # Now carries wrapped history with IDs
    session_json_loaded = pyqtSignal(str)
    agent_event_received = pyqtSignal(dict)
    transcription_received = pyqtSignal(str)
    message_deleted = pyqtSignal(dict)  # Result of delete operation
    edit_send_message = pyqtSignal(str, object, object)  # Send new message after edit (text, images_b64, files)

    # --- screen stability helpers (multi-monitor hotplug safety) ---

    def _move_to_default_position(self) -> None:
        """Place the widget somewhere sane on the primary screen (bottom-right)."""
        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            ag = screen.availableGeometry()
            x = ag.x() + ag.width() - self.width() - 20
            y = ag.y() + ag.height() - self.height() - 40
            self.move(x, y)
        except Exception:
            pass

    def _set_docked_visual(self, docked: bool, edge: str = None) -> None:
        """Switch UI between floating circle and docked slim handle."""
        try:
            if docked:
                if edge in ("left", "right"):
                    try:
                        self.dock_btn.set_edge(edge)
                        self.dock_btn.set_emoji(self.icon_emoji)
                    except Exception:
                        pass

                try:
                    self.main_btn.hide()
                except Exception:
                    pass
                try:
                    self.dock_btn.show()
                except Exception:
                    pass

                # Make it feel like it belongs to the edge: no margins.
                try:
                    if getattr(self, "_layout", None):
                        self._layout.setContentsMargins(0, 0, 0, 0)
                except Exception:
                    pass

                # Resize to the handle.
                try:
                    self.setFixedSize(self.dock_btn.width(), self.dock_btn.height())
                except Exception:
                    pass

                # Keep drag grab continuous across state switches.
                try:
                    if bool(getattr(self, "_dragging", False)) and self._drag_offset is not None:
                        self._drag_offset = QCursor.pos() - self.frameGeometry().topLeft()
                except Exception:
                    pass

            else:
                try:
                    self.dock_btn.hide()
                except Exception:
                    pass
                try:
                    self.main_btn.show()
                except Exception:
                    pass

                try:
                    if getattr(self, "_layout", None):
                        self._layout.setContentsMargins(5, 5, 5, 5)
                except Exception:
                    pass

                # Back to the circle size (56 + margins = 66)
                try:
                    self.setFixedSize(66, 66)
                except Exception:
                    pass

            # Keep drag grab continuous across state switches.
            try:
                if bool(getattr(self, "_dragging", False)) and self._drag_offset is not None:
                    self._drag_offset = QCursor.pos() - self.frameGeometry().topLeft()
            except Exception:
                pass

        except Exception:
            pass

    # --- inference UI helpers ---

    def _set_inference_running(self, running: bool) -> None:
        running = bool(running)
        if bool(getattr(self, "_inference_running", False)) == running:
            return
        self._inference_running = running

        try:
            if running:
                self._inference_anim_step = 0
                if getattr(self, "_inference_anim_timer", None):
                    self._inference_anim_timer.start()
            else:
                if getattr(self, "_inference_anim_timer", None):
                    self._inference_anim_timer.stop()
        except Exception:
            pass

        try:
            self.main_btn.update()
        except Exception:
            pass
        try:
            self.dock_btn.update()
        except Exception:
            pass

    def _on_inference_anim_tick(self) -> None:
        try:
            if not bool(getattr(self, "_inference_running", False)):
                return
            self._inference_anim_step = (int(getattr(self, "_inference_anim_step", 0)) + 1) % 8
        except Exception:
            return

        try:
            if getattr(self, "main_btn", None):
                self.main_btn.update()
        except Exception:
            pass
        try:
            if getattr(self, "dock_btn", None):
                self.dock_btn.update()
        except Exception:
            pass

    # --- docking helpers (phase 2: sticky edge) ---

    def _load_dock_settings(self) -> None:
        try:
            from PyQt6.QtCore import QSettings

            settings = QSettings("ai-agent", "widget")
            edge = str(settings.value("floating_dock_edge", "") or "").strip().lower()
            if edge not in ("left", "right"):
                edge = None

            self._dock_edge = edge
            self._dock_screen_name = str(settings.value("floating_dock_screen", "") or "").strip() or None

            try:
                self._dock_y_frac = float(settings.value("floating_dock_y_frac", 0.75) or 0.75)
            except Exception:
                self._dock_y_frac = 0.75
            try:
                self._dock_y_frac = max(0.0, min(1.0, float(self._dock_y_frac)))
            except Exception:
                self._dock_y_frac = 0.75
        except Exception:
            self._dock_edge = None
            self._dock_screen_name = None
            self._dock_y_frac = 0.75

    def _save_dock_settings(self) -> None:
        try:
            from PyQt6.QtCore import QSettings

            settings = QSettings("ai-agent", "widget")
            settings.setValue("floating_dock_edge", self._dock_edge or "")
            settings.setValue("floating_dock_screen", self._dock_screen_name or "")
            settings.setValue("floating_dock_y_frac", float(getattr(self, "_dock_y_frac", 0.75) or 0.75))
        except Exception:
            pass

    def _get_screen_by_name(self, name: str):
        try:
            if not isinstance(name, str) or not name.strip():
                return None
            for s in (QGuiApplication.screens() or []):
                try:
                    if hasattr(s, "name") and s.name() == name:
                        return s
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def _dock_move(self, *, screen, edge: str, y_px: int) -> None:
        """Move to left/right edge of `screen`, clamping vertical position."""
        try:
            ag = screen.availableGeometry()
            fg = self.frameGeometry()
            w, h = fg.width(), fg.height()

            x = ag.x() if edge == "left" else (ag.x() + ag.width() - w)

            min_y = ag.y()
            max_y = ag.y() + max(0, ag.height() - h)
            y = max(min_y, min(int(y_px), int(max_y)))

            self.move(int(x), int(y))

            # Update persistent dock anchors
            den = max(1, int(ag.height() - h))
            self._dock_y_frac = float(y - ag.y()) / float(den)
            try:
                self._dock_screen_name = screen.name() if hasattr(screen, "name") else None
            except Exception:
                self._dock_screen_name = None
            self._dock_edge = edge
        except Exception:
            pass

    def _cancel_dock_candidate(self) -> None:
        try:
            self._dock_candidate_gen = int(getattr(self, "_dock_candidate_gen", 0) or 0) + 1
            self._dock_candidate_edge = None
            self._dock_candidate_screen_name = None
            self._dock_candidate_anchor_pos = None
        except Exception:
            pass

    def _start_dock_candidate(self, *, edge: str, screen_name: str, anchor_pos=None) -> None:
        try:
            self._dock_candidate_gen = int(getattr(self, "_dock_candidate_gen", 0) or 0) + 1
            gen = int(self._dock_candidate_gen)
            self._dock_candidate_edge = edge
            self._dock_candidate_screen_name = screen_name
            self._dock_candidate_anchor_pos = anchor_pos
            delay = int(getattr(self, "_dock_delay_ms", 1000) or 1000)

            QTimer.singleShot(delay, lambda gen=gen: self._dock_candidate_commit(gen))
        except Exception:
            pass

    def _dock_candidate_commit(self, gen: int) -> None:
        try:
            if int(gen) != int(getattr(self, "_dock_candidate_gen", -1) or -1):
                return
            if not bool(getattr(self, "_dragging", False)):
                return
            if self._dock_edge in ("left", "right"):
                return

            edge = getattr(self, "_dock_candidate_edge", None)
            scr_name = getattr(self, "_dock_candidate_screen_name", None)
            if edge not in ("left", "right") or not scr_name:
                return

            pos = QCursor.pos()
            screen = QGuiApplication.screenAt(pos)
            if screen is None:
                return
            try:
                if hasattr(screen, "name") and screen.name() != scr_name:
                    return
            except Exception:
                return

            ag = screen.availableGeometry()
            mx = int(pos.x())
            left_edge = ag.x()
            right_edge = ag.x() + ag.width() - 1
            snap_px = int(getattr(self, "_dock_snap_px", 18) or 18)

            if edge == "left" and abs(mx - left_edge) > snap_px:
                return
            if edge == "right" and abs(mx - right_edge) > snap_px:
                return

            # Commit docking.
            self._dock_edge = edge
            self._dock_screen_name = scr_name
            self._set_docked_visual(True, edge=edge)
            self._dock_move(screen=screen, edge=edge, y_px=int(self.frameGeometry().y()))

        except Exception:
            return

    def _move_with_dock(self, proposed_top_left, mouse_global_pos=None) -> None:
        """Move while supporting snap-to-edge docking.

        Docking is delayed (hold near edge) to make cross-monitor moves painless.
        """
        try:
            if mouse_global_pos is None:
                self.move(proposed_top_left)
                return

            screen = QGuiApplication.screenAt(mouse_global_pos) or QApplication.primaryScreen()
            if screen is None:
                self.move(proposed_top_left)
                return

            ag = screen.availableGeometry()
            snap_px = int(getattr(self, "_dock_snap_px", 18) or 18)
            undock_px = int(getattr(self, "_dock_undock_px", 32) or 32)

            left_edge = ag.x()
            right_edge = ag.x() + ag.width() - 1
            mx = int(mouse_global_pos.x())

            was_docked = self._dock_edge in ("left", "right")

            # --- undock (immediate) ---
            if self._dock_edge == "left":
                if mx > left_edge + undock_px:
                    self._dock_edge = None
                    self._dock_screen_name = None
                    self._cancel_dock_candidate()
            elif self._dock_edge == "right":
                if mx < right_edge - undock_px:
                    self._dock_edge = None
                    self._dock_screen_name = None
                    self._cancel_dock_candidate()

            # --- dock (delayed) ---
            if self._dock_edge is None:
                near_left = abs(mx - left_edge) <= snap_px
                near_right = abs(mx - right_edge) <= snap_px

                want_edge = "left" if near_left else ("right" if near_right else None)
                scr_name = None
                try:
                    scr_name = screen.name() if hasattr(screen, "name") else None
                except Exception:
                    scr_name = None

                if want_edge and scr_name:
                    # Only dock if the cursor "dwells" near the edge for a moment.
                    # If the user keeps moving (e.g., trying to cross to another screen),
                    # restart the timer so it doesn't snap mid-flight.
                    if self._dock_candidate_edge != want_edge or self._dock_candidate_screen_name != scr_name:
                        self._start_dock_candidate(edge=want_edge, screen_name=scr_name, anchor_pos=mouse_global_pos)
                    else:
                        try:
                            ap = getattr(self, "_dock_candidate_anchor_pos", None)
                            if ap is not None and (mouse_global_pos - ap).manhattanLength() > 10:
                                self._start_dock_candidate(edge=want_edge, screen_name=scr_name, anchor_pos=mouse_global_pos)
                        except Exception:
                            pass
                else:
                    self._cancel_dock_candidate()

            is_docked = self._dock_edge in ("left", "right")
            if is_docked != was_docked:
                self._set_docked_visual(is_docked, edge=self._dock_edge)

            if self._dock_edge in ("left", "right"):
                self._dock_move(screen=screen, edge=self._dock_edge, y_px=int(proposed_top_left.y()))
            else:
                self.move(proposed_top_left)

        except Exception:
            try:
                self.move(proposed_top_left)
            except Exception:
                pass

    def _apply_docked_position(self) -> bool:
        """If docked, compute position from (edge, screen_name, y_frac). Returns True if applied."""
        try:
            edge = getattr(self, "_dock_edge", None)
            if edge not in ("left", "right"):
                # Ensure visuals match
                self._set_docked_visual(False)
                return False

            screen = self._get_screen_by_name(getattr(self, "_dock_screen_name", None) or "")
            if screen is None:
                # Security rule: if the screen we were docked to is gone, revert to
                # default floating on primary.
                self._dock_edge = None
                self._dock_screen_name = None
                self._save_dock_settings()
                self._set_docked_visual(False)
                self._move_to_default_position()
                return True

            self._set_docked_visual(True, edge=edge)

            ag = screen.availableGeometry()
            fg = self.frameGeometry()
            w, h = fg.width(), fg.height()

            den = max(1, int(ag.height() - h))
            frac = float(getattr(self, "_dock_y_frac", 0.75) or 0.75)
            frac = max(0.0, min(1.0, frac))

            y = ag.y() + int(round(frac * den))
            self._dock_move(screen=screen, edge=edge, y_px=int(y))
            return True
        except Exception:
            return False

    def _current_screen_topology_sig(self):
        try:
            parts = []
            for s in (QGuiApplication.screens() or []):
                try:
                    g = s.geometry()
                    ag = s.availableGeometry()
                    parts.append(
                        (
                            str(getattr(s, "name", lambda: "")()) if hasattr(s, "name") else "",
                            g.x(),
                            g.y(),
                            g.width(),
                            g.height(),
                            ag.x(),
                            ag.y(),
                            ag.width(),
                            ag.height(),
                        )
                    )
                except Exception:
                    continue
            parts.sort()
            return tuple(parts)
        except Exception:
            return None

    def _on_screen_watch_tick(self) -> None:
        sig = self._current_screen_topology_sig()
        if sig != getattr(self, "_screen_topology_sig", None):
            self._screen_topology_sig = sig
            self.schedule_ensure_visible()

    def schedule_ensure_visible(self) -> None:
        """Debounced "keep me on-screen" call."""
        if getattr(self, "_rehoming_pending", False):
            return
        self._rehoming_pending = True
        QTimer.singleShot(50, self._ensure_visible_now)

    def _ensure_visible_now(self) -> None:
        self._rehoming_pending = False

        try:
            if self.isMinimized():
                self.showNormal()
        except Exception:
            pass

        try:
            screens = QGuiApplication.screens() or []
            if not screens:
                return

            # If docked, dock is the source of truth (and will also naturally handle
            # "screen disappeared" by falling back to primary).
            if self._apply_docked_position():
                try:
                    self.show()
                    self.raise_()
                except Exception:
                    pass
                return

            fg = self.frameGeometry()
            best_screen = None
            best_area = 0
            for s in screens:
                try:
                    ag = s.availableGeometry()
                    inter = fg.intersected(ag)
                    area = max(0, inter.width()) * max(0, inter.height())
                    if area > best_area:
                        best_area = area
                        best_screen = s
                except Exception:
                    continue

            if best_screen is None or best_area <= 0:
                self._move_to_default_position()
            else:
                ag = best_screen.availableGeometry()
                w, h = fg.width(), fg.height()

                min_x = ag.left()
                min_y = ag.top()
                max_x = ag.left() + max(0, ag.width() - w)
                max_y = ag.top() + max(0, ag.height() - h)

                x = max(min_x, min(fg.x(), max_x))
                y = max(min_y, min(fg.y(), max_y))

                if (x, y) != (fg.x(), fg.y()):
                    self.move(x, y)

            # Windows hotplug can mess with z-order for Tool windows.
            try:
                self.show()
                self.raise_()
            except Exception:
                pass

        except Exception:
            # Last resort: don't crash, just re-home.
            self._move_to_default_position()
            try:
                self.show()
                self.raise_()
            except Exception:
                pass

    def showEvent(self, event):
        super().showEvent(event)
        # windowHandle() can be None before first show.
        if not getattr(self, "_wh_connected", False):
            try:
                wh = self.windowHandle()
                if wh is not None:
                    wh.screenChanged.connect(lambda *_: self.schedule_ensure_visible())
                    self._wh_connected = True
            except Exception:
                pass
        self.schedule_ensure_visible()

    def event(self, event):
        try:
            if event.type() == QEvent.Type.ScreenChangeInternal:
                self.schedule_ensure_visible()
        except Exception:
            pass
        return super().event(event)


    def __init__(self, app=None, icon_emoji="🦊"):
        super().__init__()
        
        # Store reference to the app (optional; UI should use the event bus for app I/O)
        self.app = app
        self.icon_emoji = icon_emoji

        # Event bus (UI talks to app through this, not via direct method calls)
        self._bus = Runtime.get_event_bus()
        self._agent_stream_unsub = None
        self._current_run_id = None
        # Multi-session state (no defaults: UI always uses a real UUID session_id)
        self.active_session_id = None
        self.sessions_meta = []

        
        # Create app icon from emoji (GUI responsibility)
        self.app_icon = self._create_icon_from_emoji(icon_emoji)
        
        # Set icon globally for all windows
        qt_app = QApplication.instance()
        if qt_app:
            qt_app.setWindowIcon(self.app_icon)

        # Recording state
        self.is_recording = False
        self.frames = []
        self.samplerate = 44100

        # Keep the session dropdown in sync with app-side changes (e.g. tools updating title/description).
        # (Best-effort; if this fails we still refresh on run end.)
        self._session_bus_unsubs = []
        try:
            self._session_bus_unsubs.append(self._bus.subscribe("session.list.changed", lambda ev: self.refresh_sessions()))
        except Exception:
            pass
        self.channels = 1
        self.filename = "recording.wav"
        self.selected_language = "en"

        # Group Session: ask_human popup state (UI stays responsive; loop pauses in the tool).
        self._ask_human_dialog = None
        self._ask_human_reply_topic = None
        self._ask_human_timeout_timer = None
        try:
            self._session_bus_unsubs.append(self._bus.subscribe("human.cmd.ask", self._on_human_cmd_ask))
        except Exception:
            pass

        # Long press state
        self.press_start_time = None
        self.long_press_threshold = 1000
        self.long_press_timer = QTimer()
        self.long_press_timer.setSingleShot(True)
        self.long_press_timer.timeout.connect(self.on_long_press)
        self.ready_to_record = False

        # Animation state
        self.recording_animation_timer = QTimer()
        self.recording_animation_timer.timeout.connect(self.animate_recording)
        self.animation_step = 0

        # Chat window (no parent - standalone top-level windows for taskbar visibility)
        self.chat_window = ChatWindow(parent=None, widget=self)
        self.chat_window.hide()
        if self.app_icon:
            self.chat_window.setWindowIcon(self.app_icon)
        
        self.session_json_window = SessionJsonWindow(parent=None)
        if self.app_icon:
            self.session_json_window.setWindowIcon(self.app_icon)
        # Session (JSON) is read-only (debug viewer). No data_loaded/data_cleared wiring.
        # TODO: If we re-enable editing/import later, re-add those signals carefully.
        
        self.memories_window = MemoriesWindow(parent=None)
        if self.app_icon:
            self.memories_window.setWindowIcon(self.app_icon)
        
        self.documents_window = DocumentsWindow(parent=None)
        if self.app_icon:
            self.documents_window.setWindowIcon(self.app_icon)
        
        self.inner_voice_window = InnerVoiceWindow(parent=None)
        self.inner_voice_window.hide()
        if self.app_icon:
            self.inner_voice_window.setWindowIcon(self.app_icon)
        
        self.settings_window = None

        # Agent inference tracking
        self.stop_requested = False
        self.agent_thread = None

        # Connect signals
        self.session_loaded.connect(self.display_session)
        self.session_json_loaded.connect(self._display_session_json)
        self.agent_event_received.connect(self.handle_agent_event)
        self.transcription_received.connect(self.chat_window.send_message)
        self.message_deleted.connect(self._on_message_deleted)
        self.edit_send_message.connect(self._on_edit_send_message)
        
        # Connect ChatWindow signals for message operations
        self.chat_window.delete_message_requested.connect(self.handle_delete_message)
        self.chat_window.edit_message_requested.connect(self.handle_edit_message)

        # Transparent, always-on-top window
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(5, 5, 5, 5)

        self.main_btn = CircleEmojiButton(self.icon_emoji)
        self.main_btn.setFixedSize(56, 56)
        self.main_btn.installEventFilter(self)
        self._layout.addWidget(self.main_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Docked handle (hidden unless snapped)
        self.dock_btn = DockHandleButton(emoji=self.icon_emoji, parent=self)
        self.dock_btn.setFixedSize(20, 92)
        self.dock_btn.installEventFilter(self)
        self.dock_btn.hide()
        self._layout.addWidget(self.dock_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Dragging state
        self.drag_position = None
        self._drag_offset = None
        self._dragging = False
        self._press_global_pos = None

        # Docking state (phase 2: sticky edge)
        self._dock_edge = None  # "left" | "right" | None
        self._dock_screen_name = None
        self._dock_y_frac = 0.75
        self._dock_snap_px = 18
        self._dock_undock_px = 32
        self._dock_delay_ms = 500
        self._dock_candidate_gen = 0
        self._dock_candidate_edge = None
        self._dock_candidate_screen_name = None
        self._dock_candidate_anchor_pos = None
        self._mouse_grabbed = False
        self._load_dock_settings()

        # Inference animation (reuses ChatWindow sending/stop pipeline; no new bus signals)
        self._inference_running = False
        self._inference_anim_step = 0
        self._inference_anim_timer = QTimer(self)
        self._inference_anim_timer.setInterval(110)
        self._inference_anim_timer.timeout.connect(self._on_inference_anim_tick)

        # Position: always start on the primary screen by default.
        # If docked state is present, ensure_visible() will apply it.
        self.adjustSize()
        self.setFixedSize(66, 66)
        self._set_docked_visual(False)
        self._move_to_default_position()

        # Keep the widget from getting stranded off-screen on monitor hotplug / resolution changes.
        self._rehoming_pending = False
        self._wh_connected = False
        self._screen_topology_sig = None
        self._screen_watch_timer = QTimer(self)
        self._screen_watch_timer.setInterval(500)
        self._screen_watch_timer.timeout.connect(self._on_screen_watch_tick)
        self._screen_watch_timer.start()
        try:
            app = QApplication.instance()
            if app is not None:
                app.screenAdded.connect(lambda *_: self.schedule_ensure_visible())
                app.screenRemoved.connect(lambda *_: self.schedule_ensure_visible())
        except Exception:
            pass
    
    def _create_icon_from_emoji(self, emoji: str):
        """Create QIcon from emoji string (GUI layer responsibility)."""
        size = 256
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Background circle (looks better on Windows taskbar)
        painter.setBrush(QColor(50, 50, 50, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size, size)
        
        # Draw emoji
        font = QFont("Segoe UI Emoji", int(size * 0.6))
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
        painter.end()
        
        return QIcon(pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        # If we grabbed the mouse for a drag (so switching visuals doesn't drop the drag),
        # keep moving from here.
        try:
            if getattr(self, "_mouse_grabbed", False) and getattr(self, "_dragging", False) and self._drag_offset is not None:
                current = event.globalPosition().toPoint()
                self._move_with_dock(current - self._drag_offset, current)
                event.accept()
                return
        except Exception:
            pass

        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_position:
            current = event.globalPosition().toPoint()
            proposed = current - self.drag_position
            self._move_with_dock(proposed, current)
            event.accept()
            return

        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # If we grabbed the mouse for a drag, release it and finalize the drag here
        # (because the release event won't go through the child button's eventFilter).
        if getattr(self, "_mouse_grabbed", False):
            try:
                self.releaseMouse()
            except Exception:
                pass
            self._mouse_grabbed = False

            # Reset drag state
            self.long_press_timer.stop()
            self._press_global_pos = None
            self._drag_offset = None
            self._dragging = False
            self.press_start_time = None
            self._cancel_dock_candidate()

            try:
                self._save_dock_settings()
            except Exception:
                pass
            try:
                self.schedule_ensure_visible()
            except Exception:
                pass

            self.drag_position = None
            event.accept()
            return

        self.drag_position = None
        # Persist dock state (if any) and keep it clamped on-screen.
        try:
            self._save_dock_settings()
        except Exception:
            pass
        try:
            self.schedule_ensure_visible()
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj == self.main_btn or obj == getattr(self, "dock_btn", None):
            is_chat_sending = self.chat_window and self.chat_window.is_sending
            
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._press_global_pos = event.globalPosition().toPoint()
                self._drag_offset = self._press_global_pos - self.frameGeometry().topLeft()
                self._dragging = False
                self.press_start_time = time.time()
                if not self.is_recording and not is_chat_sending:
                    self.long_press_timer.start(self.long_press_threshold)
                return False
            
            elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                if not is_chat_sending:
                    self.show_menu()
                return True
            
            elif event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
                if self._press_global_pos is not None:
                    current = event.globalPosition().toPoint()
                    if not self._dragging:
                        if (current - self._press_global_pos).manhattanLength() >= QApplication.startDragDistance():
                            self._dragging = True
                            self.long_press_timer.stop()
                            # Grab the mouse so switching visuals (dock/undock) doesn't drop the drag.
                            try:
                                self.grabMouse()
                                self._mouse_grabbed = True
                            except Exception:
                                self._mouse_grabbed = False

                            if self.ready_to_record:
                                self.ready_to_record = False
                                self.main_btn.setText(self.icon_emoji)
                    if self._dragging and self._drag_offset is not None:
                        self._move_with_dock(current - self._drag_offset, current)
                        return True
                return False
            
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.long_press_timer.stop()
                was_dragging = self._dragging
                self._press_global_pos = None
                self._drag_offset = None
                self._dragging = False
                
                if not was_dragging:
                    if self.is_recording:
                        self.stop_recording()
                    elif self.ready_to_record:
                        self.ready_to_record = False
                        self.start_recording()
                    else:
                        if self.press_start_time and (time.time() - self.press_start_time) < (self.long_press_threshold / 1000.0):
                            self.toggle_chat_window()
                
                # If we just dragged, persist dock state (if any) and clamp visible.
                if was_dragging:
                    try:
                        self._cancel_dock_candidate()
                    except Exception:
                        pass
                    try:
                        self._save_dock_settings()
                    except Exception:
                        pass
                    try:
                        self.schedule_ensure_visible()
                    except Exception:
                        pass

                self.press_start_time = None
                return True if was_dragging else False

        return super().eventFilter(obj, event)

    def on_long_press(self):
        if not self.is_recording and not self._dragging:
            self.ready_to_record = True
            self.main_btn.setText("🎙️")
    
    def animate_recording(self):
        self.animation_step = (self.animation_step + 1) % 8
        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]
        self.main_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(80, 80, 80, 200);
                color: #ff4444;
                border-radius: 28px;
                font-size: 28px;
                border: 2px solid rgba(255, 80, 80, 0.6);
            }
        """)
        self.main_btn.setText(spinner_chars[self.animation_step])
    
    def show_menu(self):
        menu = QMenu(self)
        langs = [("en", "English"), ("ro", "Romanian"), ("ru", "Russian"), ("de", "German"), ("fr", "French"), ("es", "Spanish")]
        lang_menu = QMenu("Language", self)
        self._lang_actions = {}
        for code, label in langs:
            act = QAction(f"{label} ({code})", self)
            act.setCheckable(True)
            act.setChecked(code == self.selected_language)
            act.triggered.connect(lambda checked, c=code: self._set_language(c))
            lang_menu.addAction(act)
            self._lang_actions[code] = act
        menu.addMenu(lang_menu)
        
        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self.open_settings)
        menu.addAction(settings_action)

        menu.addSeparator()
        inner_voice_action = QAction("Inner Voice Chat…", self)
        inner_voice_action.triggered.connect(self.open_inner_voice)
        menu.addAction(inner_voice_action)

        open_memories_action = QAction("Memories", self)
        open_memories_action.triggered.connect(self.open_memories)
        menu.addAction(open_memories_action)
        
        open_documents_action = QAction("Documents", self)
        open_documents_action.triggered.connect(self.open_documents)
        menu.addAction(open_documents_action)

        menu.addSeparator()
        restart_action = QAction("Restart App", self)
        restart_action.triggered.connect(self.restart_app)
        menu.addAction(restart_action)

        menu.addSeparator()
        close_action = QAction("Close", self)
        close_action.triggered.connect(self.quit_app)
        menu.addAction(close_action)

        anchor = self.main_btn if self.main_btn.isVisible() else self.dock_btn
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def restart_app(self):
        """Request a full app restart (via the event bus).

        UI must not call app methods directly.
        The app decides the restart mechanism (currently: exit with a special code + run.bat relaunches).
        """
        try:
            self._bus.publish("app.cmd.restart", {"reason": "ui.menu"})
        except Exception:
            try:
                QMessageBox.information(self, "Restart Failed", "Could not publish restart request.")
            except Exception:
                pass

    def open_settings(self):
        """Open settings window and load current settings (via event bus)."""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(parent=None)
            if self.app_icon:
                self.settings_window.setWindowIcon(self.app_icon)
            self.settings_window.settings_save_requested.connect(self._on_settings_save_requested)
            self.settings_window.confluence_upsert_requested.connect(self._on_confluence_upsert_requested)
            self.settings_window.confluence_delete_requested.connect(self._on_confluence_delete_requested)

        # Load current settings from app (bus request)
        reply_topic = f"settings.ui.reply.get_current.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to load settings") if isinstance(payload, dict) else "Failed to load settings"
                try:
                    self.settings_window.show_save_error(msg)
                except Exception:
                    QMessageBox.warning(self, "Settings", msg)
                return

            s = payload.get("settings", {}) or {}
            self.settings_window.load_settings(
                base_url=s.get("base_url", ""),
                api_token=s.get("api_token", ""),
                api_mode=s.get("api_mode", "responses"),
                confluence_tokens=s.get("confluence_tokens", []),
            )

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish("settings.cmd.get_current", {"reply_topic": reply_topic})

        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _on_settings_save_requested(self, settings):
        """Handle settings save request from settings window (via event bus)."""
        if not self.settings_window:
            return

        reply_topic = f"settings.ui.reply.save.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if isinstance(payload, dict) and payload.get("status") == "success":
                self.settings_window.show_save_success()
            else:
                msg = payload.get("message", "Failed to save settings") if isinstance(payload, dict) else "Failed to save settings"
                self.settings_window.show_save_error(msg)

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish("settings.cmd.save", {"reply_topic": reply_topic, "settings": settings})

    def _on_confluence_upsert_requested(self, payload: dict):
        if not self.settings_window:
            return
        if not isinstance(payload, dict):
            self.settings_window.show_save_error("Invalid confluence payload")
            return

        base_url = str(payload.get("base_url") or "").strip()
        token = str(payload.get("token") or "").strip()

        reply_topic = f"settings.ui.reply.confluence.upsert.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload2 = getattr(ev, "payload", {}) or {}
            if not isinstance(payload2, dict) or payload2.get("status") != "success":
                msg = payload2.get("message", "Failed to save Confluence token") if isinstance(payload2, dict) else "Failed to save Confluence token"
                self.settings_window.show_save_error(msg)
                # Reload from app (truth).
                try:
                    self.open_settings()
                except Exception:
                    pass
                return

            s = payload2.get("settings", {}) or {}
            try:
                self.settings_window.load_settings(
                    base_url=s.get("base_url", ""),
                    api_token=s.get("api_token", ""),
                    api_mode=s.get("api_mode", "responses"),
                    confluence_tokens=s.get("confluence_tokens", []),
                )
            except Exception:
                pass

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "settings.cmd.confluence.upsert",
            {"reply_topic": reply_topic, "base_url": base_url, "token": token},
        )

    def _on_confluence_delete_requested(self, base_url: str):
        if not self.settings_window:
            return

        b = str(base_url or "").strip()
        if not b:
            return

        reply_topic = f"settings.ui.reply.confluence.delete.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload2 = getattr(ev, "payload", {}) or {}
            if not isinstance(payload2, dict) or payload2.get("status") != "success":
                msg = payload2.get("message", "Failed to delete Confluence token") if isinstance(payload2, dict) else "Failed to delete Confluence token"
                self.settings_window.show_save_error(msg)
                try:
                    self.open_settings()
                except Exception:
                    pass
                return

            s = payload2.get("settings", {}) or {}
            try:
                self.settings_window.load_settings(
                    base_url=s.get("base_url", ""),
                    api_token=s.get("api_token", ""),
                    api_mode=s.get("api_mode", "responses"),
                    confluence_tokens=s.get("confluence_tokens", []),
                )
            except Exception:
                pass

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "settings.cmd.confluence.delete",
            {"reply_topic": reply_topic, "base_url": b},
        )

    def _set_language(self, code: str):
        allowed = {"en", "ro", "ru", "de", "fr", "es"}
        if code not in allowed:
            code = "en"
        self.selected_language = code
        if hasattr(self, "_lang_actions"):
            for c, act in self._lang_actions.items():
                act.setChecked(c == code)

    def toggle_chat_window(self):
        if self.chat_window is None:
            self.chat_window = ChatWindow(parent=None, widget=self)
            if self.app_icon:
                self.chat_window.setWindowIcon(self.app_icon)
        
        if self.chat_window.isVisible():
            self.chat_window.hide()
        else:
            self.position_chat_window()
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
            self.refresh_sessions(on_done=lambda ok: self.fetch_and_display_session() if ok else None)
    
    def position_chat_window(self):
        if not self.chat_window:
            return
        widget_rect = self.frameGeometry()
        chat_width = self.chat_window.width()
        chat_height = self.chat_window.height()
        scr = QGuiApplication.screenAt(widget_rect.center()) or QApplication.primaryScreen()
        screen = scr.availableGeometry() if scr is not None else QApplication.primaryScreen().availableGeometry()
        chat_x = widget_rect.x() + (widget_rect.width() - chat_width) // 2
        chat_y = widget_rect.y() - chat_height - 10
        if chat_x < screen.x():
            chat_x = screen.x() + 10
        elif chat_x + chat_width > screen.x() + screen.width():
            chat_x = screen.x() + screen.width() - chat_width - 10
        if chat_y < screen.y():
            chat_y = screen.y() + 10
        self.chat_window.move(chat_x, chat_y)
    
    # -----------------------------------------------------------------
    # Sessions (multi-session)
    # -----------------------------------------------------------------

    def refresh_sessions(self, on_done=None):
        """Fetch session list + active id from app and update UI."""
        reply_topic = f"session.ui.reply.list.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to fetch sessions") if isinstance(payload, dict) else "Failed to fetch sessions"
                print(f"[UI] Failed to fetch sessions: {msg}")
                if callable(on_done):
                    on_done(False)
                return

            self.active_session_id = payload.get("active_session_id")
            self.sessions_meta = payload.get("sessions", []) if isinstance(payload.get("sessions"), list) else []

            # Cache active session type for run-time rendering decisions (group sessions).
            self.active_session_type = "single"
            try:
                for s in (self.sessions_meta or []):
                    if isinstance(s, dict) and s.get("session_id") == self.active_session_id:
                        st = s.get("type")
                        st = str(st).strip().lower() if isinstance(st, str) else "single"
                        self.active_session_type = st if st in ("single", "group") else "single"
                        break
            except Exception:
                self.active_session_type = "single"

            # Push into chat window dropdown.
            try:
                if self.chat_window and hasattr(self.chat_window, "set_session_list"):
                    self.chat_window.set_session_list(self.sessions_meta, self.active_session_id)
            except Exception:
                pass

            # Keep JSON window in sync.
            try:
                if self.session_json_window is not None:
                    self.session_json_window.current_session_id = self.active_session_id
            except Exception:
                pass

            if callable(on_done):
                on_done(True)

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish("session.cmd.list", {"reply_topic": reply_topic})

    def request_new_session(self):
        if not self.chat_window:
            return
        if getattr(self.chat_window, "is_sending", False):
            try:
                self.chat_window._show_toast("Currently running")
            except Exception:
                pass
            return

        reply_topic = f"session.ui.reply.create_new.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to create new session") if isinstance(payload, dict) else "Failed to create new session"
                try:
                    self.chat_window._show_toast(msg)
                except Exception:
                    pass
                return

            # Refresh list + load.
            def _after(ok: bool):
                if not ok:
                    return
                self.fetch_and_display_session()
                # If the JSON window is open, refresh it too (new session should show immediately).
                if self.session_json_window and self.session_json_window.isVisible():
                    self._fetch_session_json_async()

            self.refresh_sessions(on_done=_after)

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish("session.cmd.create_new", {"reply_topic": reply_topic})


    def request_new_group_session(self):
        """Create a new group session (Phase 1: default participants Aria + Ariane)."""
        if not self.chat_window:
            return
        if getattr(self.chat_window, "is_sending", False):
            try:
                self.chat_window._show_toast("Currently running")
            except Exception:
                pass
            return

        reply_topic = f"session.ui.reply.create_new_group.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to create group session") if isinstance(payload, dict) else "Failed to create group session"
                try:
                    self.chat_window._show_toast(msg)
                except Exception:
                    pass
                return

            def _after(ok: bool):
                if not ok:
                    return
                self.fetch_and_display_session()
                if self.session_json_window and self.session_json_window.isVisible():
                    self._fetch_session_json_async()

            self.refresh_sessions(on_done=_after)

        participants = [
            {"agent_id": "aria", "display_name": "Aria"},
            {"agent_id": "ariane", "display_name": "Ariane"},
        ]

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.create_new",
            {
                "reply_topic": reply_topic,
                "session_type": "group",
                "participants": participants,
            },
        )


    def open_group_participants_picker(self, session_id: str) -> None:
        """Open a simple participants picker for a group session."""
        if not isinstance(session_id, str) or not session_id.strip():
            return
        if not self.chat_window:
            return
        if getattr(self.chat_window, "is_sending", False):
            try:
                self.chat_window._show_toast("Currently running")
            except Exception:
                pass
            return

        # Fetch available agents from ConfigManager (via bus), then open a simple dialog.
        reply_topic = f"agents.ui.reply.list_for_picker.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to list agents") if isinstance(payload, dict) else "Failed to list agents"
                try:
                    self.chat_window._show_toast(msg)
                except Exception:
                    pass
                return

            agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []

            try:
                from PyQt6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QListWidgetItem, QDialogButtonBox, QLabel
                from PyQt6.QtCore import Qt

                dlg = QDialog(self.chat_window)
                dlg.setWindowTitle("Group participants")
                lay = QVBoxLayout(dlg)
                lay.addWidget(QLabel("Select participants (group session):"))

                lst = QListWidget()
                lay.addWidget(lst, 1)

                # Current selection from sessions meta (best-effort)
                current_ids = set()
                try:
                    for s in (self.sessions_meta or []):
                        if isinstance(s, dict) and s.get("session_id") == session_id:
                            for p in (s.get("participants") or []):
                                if isinstance(p, dict) and isinstance(p.get("agent_id"), str):
                                    current_ids.add(p.get("agent_id"))
                            break
                except Exception:
                    current_ids = set()

                # Populate
                for a in agents:
                    if not isinstance(a, dict):
                        continue
                    aid = a.get("id")
                    if not isinstance(aid, str) or not aid.strip():
                        continue
                    dn = a.get("display_name") if isinstance(a.get("display_name"), str) else aid
                    role = a.get("role") if isinstance(a.get("role"), str) else ""
                    label = f"{dn}  ({aid})" if dn != aid else str(aid)
                    if role:
                        label = label + f"  [{role}]"

                    it = QListWidgetItem(label)
                    it.setData(Qt.ItemDataRole.UserRole, aid)
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(Qt.CheckState.Checked if aid in current_ids else Qt.CheckState.Unchecked)
                    lst.addItem(it)

                bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
                lay.addWidget(bb)

                def _accept():
                    selected = []
                    for i in range(lst.count()):
                        it = lst.item(i)
                        if it.checkState() != Qt.CheckState.Checked:
                            continue
                        aid = it.data(Qt.ItemDataRole.UserRole)
                        if isinstance(aid, str) and aid.strip():
                            # display_name will be derived server-side from agent spec; keep it simple.
                            selected.append({"agent_id": aid.strip()})

                    if not selected:
                        try:
                            self.chat_window._show_toast("Pick at least one participant")
                        except Exception:
                            pass
                        return

                    self.request_set_group_participants(session_id=session_id, participants=selected)
                    dlg.accept()

                bb.accepted.connect(_accept)
                bb.rejected.connect(dlg.reject)

                dlg.exec()
            except Exception as e:
                try:
                    self.chat_window._show_toast(str(e))
                except Exception:
                    pass

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish("agents.cmd.list", {"reply_topic": reply_topic})


    def request_set_group_participants(self, *, session_id: str, participants: list) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            return
        if not isinstance(participants, list):
            return

        reply_topic = f"session.ui.reply.group.participants.set.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to update participants") if isinstance(payload, dict) else "Failed to update participants"
                try:
                    if self.chat_window:
                        self.chat_window._show_toast(msg)
                except Exception:
                    pass
                return

            # Refresh sessions meta so dropdown + owner labels stay accurate.
            self.refresh_sessions()

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.group.participants.set",
            {"reply_topic": reply_topic, "session_id": str(session_id).strip(), "participants": participants},
        )

    def request_set_active_session(self, session_id: str):
        if not session_id:
            return
        if not self.chat_window:
            return
        if getattr(self.chat_window, "is_sending", False):
            try:
                self.chat_window._show_toast("Currently running")
            except Exception:
                pass
            return

        reply_topic = f"session.ui.reply.active.set.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Cannot switch sessions") if isinstance(payload, dict) else "Cannot switch sessions"
                try:
                    self.chat_window._show_toast(msg)
                    # Re-sync dropdown to actual active.
                    self.refresh_sessions()
                except Exception:
                    pass
                return

            self.active_session_id = payload.get("active_session_id") or session_id

            # Update the JSON window's scope immediately.
            try:
                if self.session_json_window is not None:
                    self.session_json_window.current_session_id = self.active_session_id
            except Exception:
                pass

            # Refresh list metadata (active highlight, timestamps)
            self.refresh_sessions()

            # Reload chat + JSON (if visible)
            self.fetch_and_display_session()
            if self.session_json_window and self.session_json_window.isVisible():
                self._fetch_session_json_async()

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.active.set",
            {"session_id": session_id, "reply_topic": reply_topic},
        )

    def fetch_and_display_session(self):
        """Request wrapped session entries (with IDs) from app (via event bus)."""
        if not self.active_session_id:
            self.refresh_sessions(on_done=lambda ok: self.fetch_and_display_session() if ok else None)
            return

        reply_topic = f"session.ui.reply.entries.get_wrapped.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to fetch session") if isinstance(payload, dict) else "Failed to fetch session"
                print(f"Failed to fetch session: {msg}")
                return

            wrapped_entries = payload.get("entries", [])
            if not isinstance(wrapped_entries, list):
                wrapped_entries = []
            self.session_loaded.emit(wrapped_entries)

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.entries.get_wrapped",
            {"session_id": self.active_session_id, "reply_topic": reply_topic},
        )
    
    @pyqtSlot(list)
    def display_session(self, wrapped_entries):
        """Display wrapped session entries with entry IDs."""
        if not self.chat_window:
            return
        print("Loading session...")
        self.chat_window.clear_chat()
        tool_calls_by_id = {}

        # Group-session display name map (owner_id -> display)
        owner_display = {}
        try:
            if getattr(self, "active_session_type", "single") == "group":
                cur_meta = None
                for s in (self.sessions_meta or []):
                    if isinstance(s, dict) and s.get("session_id") == self.active_session_id:
                        cur_meta = s
                        break
                if isinstance(cur_meta, dict):
                    for p in (cur_meta.get("participants") or []):
                        if not isinstance(p, dict):
                            continue
                        aid = p.get("agent_id") or p.get("id")
                        if not isinstance(aid, str) or not aid.strip():
                            continue
                        oid = f"agent:{aid.strip()}"
                        dn = p.get("display_name") or p.get("display") or aid
                        dn = str(dn).strip() if isinstance(dn, str) else str(aid)
                        owner_display[oid] = dn
        except Exception:
            owner_display = {}
        

        # Group-session reload markers: insert round + participant headers based on persisted wrapper meta.
        cur_group_round = None
        cur_group_owner = None
        cur_group_owner_had_text = False
        for wrapped_entry in wrapped_entries:
            entry_id = wrapped_entry.get("id")
            entry = wrapped_entry.get("content", {})
            role = entry.get("role", "")
            content = entry.get("content", [])

            # Group sessions: render round + participant headers (reload path)
            try:
                if getattr(self, "active_session_type", "single") == "group":
                    oid = wrapped_entry.get("owner_id")
                    oid = str(oid) if isinstance(oid, str) else ""
                    gr = wrapped_entry.get("group_round")
                    gr_i = int(gr) if gr is not None else None

                    if oid.startswith("agent:") and isinstance(gr_i, int) and gr_i >= 0:
                        if cur_group_round != gr_i:
                            cur_group_round = gr_i
                            cur_group_owner = None
                            if hasattr(self.chat_window, "add_group_round_marker"):
                                self.chat_window.add_group_round_marker(gr_i)

                        if cur_group_owner != oid:
                            # If the previous participant section had no assistant text (tools-only), add a spacer
                            # so the boundary is visible on reload too.
                            try:
                                if cur_group_owner is not None and (not bool(cur_group_owner_had_text)):
                                    if hasattr(self.chat_window, "add_group_section_spacer"):
                                        self.chat_window.add_group_section_spacer(14)
                            except Exception:
                                pass

                            cur_group_owner = oid
                            cur_group_owner_had_text = False
                            nm = owner_display.get(oid) or oid
                            if hasattr(self.chat_window, "add_group_participant_marker"):
                                self.chat_window.add_group_participant_marker(str(nm), owner_id=str(oid), round_idx=gr_i)
            except Exception:
                pass
            
            if role == "user":
                # Render the whole user entry as ONE message bubble (don’t split per content item).
                # This keeps the live UI consistent with what the agent receives (attachments + text).
                # If this is a tool-injected user-role message (e.g. canvas_get injected image),
                # render it as an injected/AI widget (not as a user bubble).
                if bool(wrapped_entry.get("injected")):
                    injected_texts = []
                    injected_images = []
                    for item in content:
                        t = item.get("type")
                        if t == "input_text":
                            txt = (item.get("text", "") or "").strip()
                            if txt:
                                injected_texts.append(txt)
                        elif t == "input_image":
                            image_url = item.get("image_url", "")
                            if image_url:
                                injected_images.append(image_url)

                    timestamp = wrapped_entry.get("ts")
                    self.chat_window.add_injected_message(
                        text="\n\n".join(injected_texts).strip(),
                        images=injected_images,
                        origin_call_id=wrapped_entry.get("origin_tool_call_id"),
                        origin_tool_name=wrapped_entry.get("origin_tool_name"),
                        entry_id=entry_id,
                        timestamp=timestamp,
                    )
                    continue

                attachments_texts = []
                message_texts = []
                images = []

                for item in content:
                    t = item.get("type")
                    if t == "input_text":
                        text = item.get("text", "") or ""
                        if "User's input:" in text:
                            text = text.split("User's input:", 1)[1].strip()
                        text = text.strip()
                        if not text:
                            continue

                        # Legacy: older sessions may have an "Attached files:" block in content.
                        if text.startswith("Attached files:") or "\nAttached files:" in text:
                            attachments_texts.append(text)
                        else:
                            message_texts.append(text)

                    elif t == "input_image":
                        # Legacy: older sessions may have images embedded in content.
                        image_url = item.get("image_url", "")
                        if image_url:
                            images.append(image_url)

                # New: prefer wrapper-stored images (clean model).
                try:
                    if not images:
                        ims = wrapped_entry.get("image_attachments") if isinstance(wrapped_entry.get("image_attachments"), list) else []
                        for im in ims:
                            if not isinstance(im, dict):
                                continue
                            b64 = im.get("b64")
                            mime = im.get("mime") or "image/png"
                            if isinstance(b64, str) and b64:
                                images.append(f"data:{mime};base64,{b64}")
                except Exception:
                    pass

                # Prefer structured wrapper attachments (new model). Fallback: parse legacy "Attached files:" text.
                attachments = wrapped_entry.get("attachments") if isinstance(wrapped_entry.get("attachments"), list) else None

                if attachments is None and attachments_texts:
                    parsed = []
                    try:
                        for block in attachments_texts:
                            for ln in str(block or "").splitlines():
                                s = ln.strip()
                                if s.startswith("--- File:"):
                                    p = s.split(":", 1)[1].strip()
                                    if p:
                                        parsed.append({"kind": "file", "path": p})
                                elif s.startswith("--- Directory:"):
                                    p = s.split(":", 1)[1].strip()
                                    if p:
                                        parsed.append({"kind": "dir", "path": p})
                    except Exception:
                        parsed = []
                    attachments = parsed if parsed else None

                # Display only the user's text; attachments render as chips.
                display_text = "\n\n".join(message_texts).strip()
                edit_text = "\n\n".join(message_texts).strip()

                timestamp = wrapped_entry.get("ts")
                self.chat_window.add_user_message(
                    display_text,
                    entry_id=entry_id,
                    timestamp=timestamp,
                    edit_text=edit_text,
                    images=images,
                    attachments=attachments,
                )
            
            elif role == "assistant":
                # Merge all output_text parts into a single assistant bubble (no accidental truncation/splitting).
                out_parts = []
                for item in content:
                    if item.get("type") == "output_text":
                        t = item.get("text", "")
                        if isinstance(t, dict):
                            t = t.get("value", "")
                        if t is None:
                            t = ""
                        out_parts.append(str(t))

                if out_parts:
                    full_text = "".join(out_parts)

                    # Mark that this participant actually produced visible assistant text
                    # (used for spacing between participants).
                    try:
                        if getattr(self, "active_session_type", "single") == "group":
                            oid2 = wrapped_entry.get("owner_id")
                            oid2 = str(oid2) if isinstance(oid2, str) else ""
                            if oid2.startswith("agent:") and full_text.strip():
                                cur_group_owner_had_text = True
                    except Exception:
                        pass

                    self.chat_window.start_ai_response()
                    self.chat_window.append_to_ai_response(full_text)
                    self.chat_window.finish_ai_response()
            
            # Check wrapped_entry type for non-message entries
            elif wrapped_entry.get("kind") == "system_notice" or (isinstance(entry, dict) and entry.get("type") == "system_notice"):
                try:
                    if hasattr(self.chat_window, "add_system_notice"):
                        self.chat_window.add_system_notice(wrapped_entry)
                except Exception:
                    pass

            elif wrapped_entry.get("kind") == "reasoning":
                summary = entry.get("summary", "")
                if summary:
                    if isinstance(summary, list):
                        summary_text = "\n\n".join(str(s.get("text", s)) for s in summary)
                    else:
                        summary_text = str(summary.get("text", summary))
                    if summary_text.strip():
                        self.chat_window.add_reasoning_summary_block(
                            summary_text=summary_text,
                            header="Thinking…",
                        )
            

            elif wrapped_entry.get("kind") == "run_summary":
                # Render a compact run receipt block (files changed, totals, quick diff access).
                try:
                    if hasattr(self.chat_window, "add_run_receipt_block"):
                        self.chat_window.add_run_receipt_block(entry)
                except Exception:
                    pass
            elif wrapped_entry.get("kind") == "function_call":
                func_name = entry.get("name", "")
                func_args = entry.get("arguments", "")
                call_id = entry.get("call_id")
                if call_id:
                    tool_calls_by_id[call_id] = {"name": func_name, "arguments": func_args}

                # Group sessions: tag tool blocks with owner label on reload.
                title = f"Tool Call: {func_name}"
                try:
                    if getattr(self, "active_session_type", "single") == "group":
                        oid = wrapped_entry.get("owner_id")
                        oid = str(oid) if isinstance(oid, str) else ""
                        label = owner_display.get(oid) if oid else None
                        if not label and oid:
                            label = oid
                        if label:
                            title = f"[{label}] Tool Call: {func_name}"
                except Exception:
                    pass

                self.chat_window.add_tool_call_block(
                    title=title,
                    args_text=func_args,
                    call_id=call_id,
                    tool_name=func_name,
                )

            elif wrapped_entry.get("kind") == "function_call_output":
                call_id = entry.get("call_id")
                output_text = entry.get("output")
                meta = tool_calls_by_id.get(call_id or "", {})

                # If this tool output has wrapper-only meta (e.g., consult_ariane subhistory), attach it to the UI.
                try:
                    extra = {}
                    if isinstance(call_id, str) and call_id:
                        if isinstance(wrapped_entry.get("subhistory"), dict):
                            extra["subhistory"] = wrapped_entry.get("subhistory")
                        if isinstance(wrapped_entry.get("transaction_ids"), list):
                            extra["transaction_ids"] = wrapped_entry.get("transaction_ids")
                        if isinstance(wrapped_entry.get("diff_preview"), dict):
                            extra["diff_preview"] = wrapped_entry.get("diff_preview")
                        if extra and hasattr(self.chat_window, "update_wrap_meta_by_call_id"):
                            self.chat_window.update_wrap_meta_by_call_id({call_id: extra})
                except Exception:
                    pass
                # Group sessions: tag tool blocks with owner label on reload.
                title = "Tool Output"
                try:
                    if getattr(self, "active_session_type", "single") == "group":
                        oid = wrapped_entry.get("owner_id")
                        oid = str(oid) if isinstance(oid, str) else ""
                        label = owner_display.get(oid) if oid else None
                        if not label and oid:
                            label = oid
                        if label:
                            title = f"[{label}] Tool Output"
                except Exception:
                    pass

                self.chat_window.add_tool_output_block(
                    title=title,
                    output_text=output_text,
                    call_id=call_id,
                    args_text=meta.get("arguments"),
                )
        
        if self.chat_window:
            QTimer.singleShot(100, self.chat_window.scroll_to_bottom)
    
    def open_memories(self):
        """Open the memories window and load current memories."""
        if self.memories_window:
            self.memories_window.show()
            self.memories_window.raise_()
            self.memories_window.activateWindow()
            self.memories_window.refresh_content()
    
    def open_documents(self):
        """Open the documents/collections window."""
        if self.documents_window:
            self.documents_window.show()
            self.documents_window.raise_()
            self.documents_window.activateWindow()
            # Refresh collections list when opened
            QTimer.singleShot(0, self.documents_window.refresh_collections)
    
    def open_inner_voice(self):
        """Open inner voice chat window."""
        if self.inner_voice_window and not self.inner_voice_window.isVisible():
            self.inner_voice_window.show()
            self.inner_voice_window.raise_()
            self.inner_voice_window.activateWindow()
        elif self.inner_voice_window:
            self.inner_voice_window.raise_()
            self.inner_voice_window.activateWindow()

    def _fetch_session_json_async(self):
        """Request session JSON from app (via event bus).

        Note: we use WRAPPED entries here (with IDs), since that is what the UI expects.
        """
        reply_topic = f"session.ui.reply.entries.json.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to fetch session") if isinstance(payload, dict) else "Failed to fetch session"
                self.session_json_loaded.emit(f"// Error loading session: {msg}")
                return

            entries = payload.get("entries", [])
            if not isinstance(entries, list):
                entries = []
            json_text = json.dumps(entries, indent=2, ensure_ascii=False)
            self.session_json_loaded.emit(json_text)

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.entries.get_wrapped",
            {"session_id": self.active_session_id, "reply_topic": reply_topic},
        )

    @pyqtSlot(str)
    def _display_session_json(self, json_text: str):
        if self.session_json_window:
            self.session_json_window.set_json_text(json_text)
    
    def _on_session_cleared(self):
        """Handle session cleared signal from JSON window."""
        # Refresh the chat window display
        if self.chat_window:
            self.chat_window.clear_chat()
    
    def delete_current_session(self):
        """Request app to permanently delete the current session (via event bus)."""
        if not self.active_session_id:
            return

        reply_topic = f"session.ui.reply.delete.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if isinstance(payload, dict) and payload.get("status") == "success":
                self.active_session_id = payload.get("active_session_id") or self.active_session_id

                def _after(ok: bool):
                    if not ok:
                        return
                    self.fetch_and_display_session()
                    if self.session_json_window and self.session_json_window.isVisible():
                        self._fetch_session_json_async()

                self.refresh_sessions(on_done=_after)

                try:
                    warns = payload.get("cleanup_warnings")
                    if isinstance(warns, list) and warns:
                        self.chat_window._show_toast("Session deleted (cleanup warning)", 1800)
                except Exception:
                    pass
            else:
                msg = payload.get("message", "Failed to delete session") if isinstance(payload, dict) else "Failed to delete session"
                print(f"Failed to delete session: {msg}")
                try:
                    self.chat_window._show_toast(str(msg))
                except Exception:
                    pass

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.delete",
            {"session_id": self.active_session_id, "reply_topic": reply_topic},
        )

    def clear_session_all(self):
        """Backward-compat alias. Old callers now perform a real session delete."""
        self.delete_current_session()
    
    def stop_agent_inference(self):
        """Request app to stop agent (via event bus)."""
        self.stop_requested = True

        # If an ask_human popup is open, close it and best-effort cancel the pending request.
        try:
            if self._ask_human_dialog is not None and self._ask_human_reply_topic:
                try:
                    self._bus.publish(self._ask_human_reply_topic, {"status": "cancelled", "reason": "stopped"})
                except Exception:
                    pass
                try:
                    self._ask_human_dialog.close()
                except Exception:
                    pass
                self._ask_human_dialog = None
                self._ask_human_reply_topic = None
                try:
                    if self._ask_human_timeout_timer is not None:
                        self._ask_human_timeout_timer.stop()
                except Exception:
                    pass
                self._ask_human_timeout_timer = None
        except Exception:
            pass

        self._bus.publish(
            "agent.cmd.stop",
            {"run_id": self._current_run_id, "session_id": self.active_session_id},
        )
        print("Stop inference requested")

    def _on_human_cmd_ask(self, ev):
        """Handle ask_human requests (UI popup)."""
        payload = getattr(ev, "payload", {}) or {}
        if not isinstance(payload, dict):
            return

        reply_topic = payload.get("reply_topic")
        q = payload.get("question")
        timeout_seconds = payload.get("timeout_seconds")

        reply_topic = str(reply_topic).strip() if isinstance(reply_topic, str) else ""
        q = str(q).strip() if isinstance(q, str) else ""

        if not reply_topic or not q:
            return

        # Single outstanding dialog at a time (fail closed).
        if self._ask_human_dialog is not None:
            try:
                self._bus.publish(reply_topic, {"status": "error", "message": "Another ask_human prompt is already open"})
            except Exception:
                pass
            return

        self._ask_human_reply_topic = reply_topic

        dlg = QDialog(self.chat_window if self.chat_window is not None else self)
        dlg.setWindowTitle("Question")
        try:
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        except Exception:
            pass

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)

        lab = QLabel(q)
        lab.setWordWrap(True)
        layout.addWidget(lab)

        edit = QPlainTextEdit()
        edit.setPlaceholderText("Type your reply...")
        try:
            edit.setMinimumHeight(80)
        except Exception:
            pass
        layout.addWidget(edit)

        btn_row = QHBoxLayout()
        btn_send = QPushButton("Send")
        btn_cancel = QPushButton("Cancel")
        btn_row.addWidget(btn_send)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self._ask_human_dialog = dlg

        def _cleanup():
            try:
                if self._ask_human_timeout_timer is not None:
                    self._ask_human_timeout_timer.stop()
            except Exception:
                pass
            self._ask_human_timeout_timer = None
            self._ask_human_dialog = None
            self._ask_human_reply_topic = None

        def _send_cancel(reason: str = "cancelled"):
            try:
                self._bus.publish(reply_topic, {"status": "cancelled", "reason": str(reason)})
            except Exception:
                pass
            try:
                dlg.close()
            except Exception:
                pass
            _cleanup()

        def _send_ok():
            msg = (edit.toPlainText() or "").strip()
            try:
                self._bus.publish(reply_topic, {"status": "success", "message": msg})
            except Exception:
                pass
            try:
                dlg.close()
            except Exception:
                pass
            _cleanup()

        btn_send.clicked.connect(_send_ok)
        btn_cancel.clicked.connect(lambda: _send_cancel("cancelled"))

        # Close (X) — treat as cancel.
        try:
            dlg.rejected.connect(lambda: _send_cancel("closed"))
        except Exception:
            pass

        # Auto-cancel on timeout (UI-level).
        try:
            t = int(timeout_seconds) if timeout_seconds is not None else 0
        except Exception:
            t = 0
        if t and t > 0:
            try:
                self._ask_human_timeout_timer = QTimer()
                self._ask_human_timeout_timer.setSingleShot(True)
                self._ask_human_timeout_timer.timeout.connect(lambda: _send_cancel("timeout"))
                self._ask_human_timeout_timer.start(int(t) * 1000)
            except Exception:
                self._ask_human_timeout_timer = None

        try:
            dlg.show()
        except Exception:
            pass

    def handle_delete_message(self, entry_id: str, undo_file_edits: bool = False):
        """Handle request to delete a message and all subsequent messages (via event bus)."""
        reply_topic = f"session.ui.reply.entries.delete_from_id.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict):
                payload = {"status": "error", "message": "Unexpected reply payload"}
            self.message_deleted.emit(payload)

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.entries.delete_from_id",
            {
                "session_id": self.active_session_id,
                "entry_id": entry_id,
                "undo_file_edits": bool(undo_file_edits),
                "origin_action": "delete_message",
                "reply_topic": reply_topic,
            },
        )
    
    @pyqtSlot(dict)
    def _on_message_deleted(self, result: dict):
        """Handle message deletion result - refresh UI."""
        if result.get("status") == "success":
            print(f"[UI] Message deleted successfully. Refreshing chat...")
            # Refresh the chat display
            self.fetch_and_display_session()
            # Also refresh JSON window if visible
            if self.session_json_window and self.session_json_window.isVisible():
                self._fetch_session_json_async()
            self.refresh_sessions()
        else:
            error_msg = result.get("message", "Unknown error")
            print(f"[UI] Message deletion failed: {error_msg}")
            QMessageBox.warning(self, "Delete Failed", f"Failed to delete message: {error_msg}")
    
    def handle_edit_message(self, entry_id: str, new_text: str, images_b64=None, undo_file_edits: bool = False, files=None):
        """Handle request to edit a message.

        We implement this as:
        1) delete-from-id (optionally undo file edits in the deleted tail)
        2) after UI refresh, send the new message

        All via event bus (no direct app calls).
        """
        reply_topic = f"session.ui.reply.entries.delete_from_id.edit.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            result = getattr(ev, "payload", {}) or {}
            if not isinstance(result, dict):
                result = {"status": "error", "message": "Unexpected reply payload"}

            if result.get("status") == "success":
                print("[UI] Messages deleted for edit. Sending new message...")
                self.message_deleted.emit(result)
                QTimer.singleShot(200, lambda: self.edit_send_message.emit(new_text, images_b64 or [], files or []))
            else:
                print(f"[UI] Failed to delete messages for edit: {result.get('message')}")
                self.message_deleted.emit(result)

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.entries.delete_from_id",
            {
                "session_id": self.active_session_id,
                "entry_id": entry_id,
                "undo_file_edits": bool(undo_file_edits),
                "origin_action": "edit_message",
                "reply_topic": reply_topic,
            },
        )
    
    @pyqtSlot(str, object, object)
    def _on_edit_send_message(self, new_text: str, images_b64, files):
        """Handle sending new message after edit (called on main thread via signal)."""
        if self.chat_window:
            self.send_to_agent(new_text, files_list=(files or []), screenshots_data=(images_b64 or []))
    
    def send_to_agent(self, text, files_list=None, screenshots_data=None):
        """Send message to the app's agent runner (via event bus streaming)."""
        if not self.chat_window:
            return

        if not self.active_session_id:
            try:
                self.chat_window._show_toast("No active session")
            except Exception:
                pass
            # Best-effort: refresh sessions in the background.
            self.refresh_sessions()
            return

        # Display: render attachments as chips (not as an "Attached files:" text block).
        file_paths = []
        attachments = []
        for it in (files_list or []):
            p = None
            kind = None
            if isinstance(it, str):
                p = it
            elif isinstance(it, dict):
                p = it.get("path")
                kind = it.get("kind")

            if not isinstance(p, str) or not p:
                continue

            file_paths.append(p)

            k = str(kind).strip().lower() if isinstance(kind, str) else ""
            if k not in ("file", "dir"):
                try:
                    k = "dir" if os.path.isdir(p) else "file"
                except Exception:
                    k = "file"

            attachments.append({"kind": k, "path": p})

        # Group-session streaming marker state (so we can render consistent round/participant headers).
        try:
            self._live_group_round = None
            self._live_group_owner_id = None
            self._live_group_prev_had_text = False
            self._live_group_has_prev_participant = False
        except Exception:
            pass

        display_text = (str(text).strip() if text and str(text).strip() else "")
        self.chat_window.add_user_message(
            display_text,
            edit_text=(text or ""),
            images=screenshots_data,
            attachments=attachments,
        )
        self.chat_window.start_sending_state()
        self._set_inference_running(True)
        self.chat_window.reset_stream_state()
        self.stop_requested = False

        # If we still have a previous stream subscription, kill it.
        try:
            if self._agent_stream_unsub:
                self._agent_stream_unsub()
        except Exception:
            pass
        self._agent_stream_unsub = None

        run_id = str(uuid.uuid4())
        self._current_run_id = run_id
        stream_topic = f"agent.ui.stream.run.{run_id}"

        print(f"[UI] Requesting agent run via bus: run_id={run_id}")

        def _cleanup_stream():
            try:
                if self._agent_stream_unsub:
                    self._agent_stream_unsub()
            except Exception:
                pass
            self._agent_stream_unsub = None

        def _on_stream(ev):
            payload = getattr(ev, "payload", None)
            if not isinstance(payload, dict):
                return

            # Forward to the existing UI event pipeline.
            self.agent_event_received.emit(payload)

            if payload.get("type") == "stream.finished":
                self.stop_requested = False
                _cleanup_stream()

        self._agent_stream_unsub = self._bus.subscribe(stream_topic, _on_stream)

        self._bus.publish(
            "agent.cmd.run",
            {
                "session_id": self.active_session_id,
                "message": text,
                "files": file_paths,
                "images": screenshots_data,
                "run_id": run_id,
                "stream_topic": stream_topic,
            },
        )
    
    @pyqtSlot(dict)
    def handle_agent_event(self, event):
        if not self.chat_window:
            return
        
        try:
            event_type = event.get("type", "")

            # First sign-of-life: hide the pre-first-signal indicator as soon as *any* event arrives.
            try:
                if hasattr(self.chat_window, "mark_first_agent_signal"):
                    self.chat_window.mark_first_agent_signal()
            except Exception:
                pass
            agent_name = event.get("agent_name", "Agent")
            content = event.get("content", {})

            # Sub-agent events stream into the same topic but must be rendered as a subtree.
            if event.get("source") == "subagent":
                try:
                    if hasattr(self.chat_window, "handle_subagent_event"):
                        self.chat_window.handle_subagent_event(event)
                        return
                except Exception:
                    pass
            
            # print(f"[DEBUG] handle_agent_event: {event_type}")

            # Group-session participant boundary marker: render consistent round + participant headers.
            if event_type == "group.participant.started":
                if getattr(self, "active_session_type", "single") != "group":
                    return
                try:
                    self.chat_window.finish_reasoning()
                except Exception:
                    pass
                try:
                    self.chat_window.finish_ai_response()
                except Exception:
                    pass

                nm = str(agent_name or "Participant").strip() or "Participant"
                try:
                    rd = content.get("round") if isinstance(content, dict) else None
                    rd_i = int(rd) if rd is not None else 0
                except Exception:
                    rd_i = 0
                try:
                    oid = content.get("owner_id") if isinstance(content, dict) else None
                    oid = str(oid) if isinstance(oid, str) and oid else f"agent:{nm.lower()}"
                except Exception:
                    oid = f"agent:{nm.lower()}"

                # Round marker (only when round changes)
                try:
                    if getattr(self, "_live_group_round", None) != rd_i:
                        if hasattr(self.chat_window, "add_group_round_marker"):
                            self.chat_window.add_group_round_marker(rd_i)
                        self._live_group_round = rd_i
                        self._live_group_owner_id = None
                except Exception:
                    pass

                # If the previous participant never produced assistant text (tools-only), insert a spacer
                # so it's visually obvious that one participant ended and another begins.
                try:
                    if bool(getattr(self, "_live_group_has_prev_participant", False)) and (not bool(getattr(self, "_live_group_prev_had_text", False))):
                        if hasattr(self.chat_window, "add_group_section_spacer"):
                            self.chat_window.add_group_section_spacer(14)
                except Exception:
                    pass

                # New participant starts now.
                try:
                    self._live_group_has_prev_participant = True
                    self._live_group_prev_had_text = False
                except Exception:
                    pass

                # Participant marker
                try:
                    if hasattr(self.chat_window, "add_group_participant_marker"):
                        self.chat_window.add_group_participant_marker(nm, owner_id=str(oid), round_idx=rd_i)
                    self._live_group_owner_id = str(oid)
                except Exception:
                    pass

                # Do NOT start an assistant output widget yet.
                # We'll start it lazily on the first output_text delta. This avoids empty-space gaps
                # when a participant only calls tools (no text).
                return
            

            # Group-session participant end marker: render a run receipt for what they changed this turn.
            if event_type == "group.participant.ended":
                if getattr(self, "active_session_type", "single") != "group":
                    return
                try:
                    self.chat_window.finish_reasoning()
                except Exception:
                    pass
                try:
                    self.chat_window.finish_ai_response()
                except Exception:
                    pass

                try:
                    rs = content.get("run_summary_item") if isinstance(content, dict) else None
                    if isinstance(rs, dict) and rs.get("type") == "run_summary":
                        if hasattr(self.chat_window, "add_run_receipt_block"):
                            self.chat_window.add_run_receipt_block(rs)
                        # Treat as "had output" so the next participant boundary doesn't add a tools-only spacer.
                        try:
                            self._live_group_prev_had_text = True
                        except Exception:
                            pass
                except Exception:
                    pass
                return
            if event_type == "response.reasoning_summary_part.added":
                # Show reasoning as a collapsed one-liner, expandable on click.
                self.chat_window.start_reasoning_block(title="Thinking…")
            elif event_type == "response.reasoning_summary_text.delta":
                self.chat_window.append_to_reasoning(content.get("delta", ""))
            elif event_type == "response.output_text.delta":
                try:
                    if getattr(self, "active_session_type", "single") == "group":
                        self._live_group_prev_had_text = True
                except Exception:
                    pass
                self.chat_window.append_to_ai_response(content.get("delta", ""))
            elif event_type == "response.output_text.done":
                self.chat_window.append_to_ai_response("\n\n")
            elif event_type == "response.output_item.done":
                item = content.get("item", {})
                if isinstance(item, dict) and item.get("type") == "function_call":
                    func_name = item.get("name", "")
                    func_args = item.get("arguments", "")
                    call_id = item.get("call_id")

                    # Close any in-progress assistant output, then add a collapsed tool-call block.
                    self.chat_window.finish_ai_response()

                    self.chat_window.add_tool_call_block(
                        title=(f"[{agent_name}] Tool Call: {func_name}" if getattr(self, "active_session_type", "single") == "group" else f"Tool Call: {func_name}"),
                        args_text=func_args,
                        call_id=call_id,
                        tool_name=func_name,
                    )
                elif isinstance(item, dict) and item.get("type") == "function_call_output":
                    call_id = item.get("call_id")
                    output_text = item.get("output")

                    self.chat_window.add_tool_output_block(
                        title=(f"[{agent_name}] Tool Output" if getattr(self, "active_session_type", "single") == "group" else "Tool Output"),
                        output_text=output_text,
                        call_id=call_id,
                    )
            elif event_type == "response.tool_output":
                # Synthetic event emitted by Agent after executing a tool.
                call_id = content.get("call_id")
                tool_name = content.get("name") or ""
                args = content.get("arguments") or ""
                out = content.get("output")

                # Live wrapper meta (diff previews, subhistory links, etc.).
                try:
                    wm = content.get("wrap_meta") if isinstance(content, dict) else None
                    if call_id and isinstance(wm, dict) and hasattr(self.chat_window, "update_wrap_meta_by_call_id"):
                        self.chat_window.update_wrap_meta_by_call_id({str(call_id): wm})
                except Exception:
                    pass

                # If we never saw/handled the tool-call event, create a call block now
                # so output doesn't look like an orphan ("async").
                try:
                    if call_id and hasattr(self.chat_window, "_tool_calls_by_id") and call_id not in self.chat_window._tool_calls_by_id:
                        self.chat_window.finish_ai_response()
                        self.chat_window.add_tool_call_block(
                            title=(f"[{agent_name}] Tool Call: {tool_name}" if getattr(self, "active_session_type", "single") == "group" else f"Tool Call: {tool_name}"),
                            args_text=args,
                            call_id=call_id,
                            tool_name=tool_name,
                        )
                except Exception:
                    pass

                self.chat_window.add_tool_output_block(
                    title=(f"[{agent_name}] Tool Output" if getattr(self, "active_session_type", "single") == "group" else "Tool Output"),
                    output_text=out,
                    call_id=call_id,
                    args_text=args,
                )
            elif event_type == "response.image_generation_call.generating":
                self.chat_window.append_to_ai_response(f"\n[{agent_name}] [Image Generation]...\n", '34')
            elif event_type == "response.image_generation_call.completed":
                self.chat_window.append_to_ai_response(f"[{agent_name}] [Image Generation] Completed\n\n", '34')
            elif event_type == "response.injected_message":
                # Synthetic event emitted by Agent when a tool injects a user-role message
                # (e.g. input_image from canvas_get). Render as an injected/AI widget, not as a user bubble.
                try:
                    if hasattr(self.chat_window, "handle_injected_message_event"):
                        self.chat_window.handle_injected_message_event(content)
                        return
                except Exception:
                    pass
            elif event_type == "response.agent.done":
                # print token usage if available, for debugging, beatutifully formatted
                token_usage_history = event.get("token_usage_history", {})
                print(f"[{agent_name}] Token Usage Summary:\n{json.dumps(token_usage_history, indent=2)}")
                # App handles saving session entries and images - UI just updates display
                if content.get("stopped"):
                    # Stop is now represented by the persisted run receipt (STOPPED pill)
                    # and preserved tool/output history. Don't inject an extra "[Stopped]" bubble.
                    try:
                        self.chat_window.finish_reasoning()
                    except Exception:
                        pass
                    try:
                        self.chat_window.finish_ai_response()
                    except Exception:
                        pass

                # Attach wrapper-only tool metadata (subhistory links, transaction receipts) to UI widgets.
                try:
                    wrap_meta = content.get("wrap_meta_by_call_id") if isinstance(content, dict) else None
                    if wrap_meta and hasattr(self.chat_window, "update_wrap_meta_by_call_id"):
                        self.chat_window.update_wrap_meta_by_call_id(wrap_meta)
                except Exception:
                    pass
                
                # Update the user message widget with the saved entry ID
                user_entry_id = content.get("user_entry_id")
                if user_entry_id:
                    self.chat_window.update_last_user_message_id(user_entry_id)
                    print(f"[UI] Updated user message with entry_id: {user_entry_id}")
                
                # Refresh history JSON window if visible
                if self.session_json_window and self.session_json_window.isVisible():
                    self._fetch_session_json_async()
                
                # Refresh memories window if visible (agent may have created/updated memories)
                if self.memories_window and self.memories_window.isVisible():
                    self.memories_window.refresh_content()

                # Update session list metadata (items_count/updated_at)
                self.refresh_sessions()

                # Render run receipt immediately (no reload required).
                try:
                    rs = content.get("run_summary_item") if isinstance(content, dict) else None
                    if isinstance(rs, dict) and rs.get("type") == "run_summary":
                        if hasattr(self.chat_window, "add_run_receipt_block"):
                            self.chat_window.add_run_receipt_block(rs)
                except Exception:
                    pass
            elif event_type == "stream.finished":
                self.chat_window.finish_reasoning()
                self.chat_window.finish_ai_response()
                self.chat_window.stop_sending_state()
                self._set_inference_running(False)
            elif event_type == "response.error":
                self.chat_window.finish_reasoning()
                error_msg = content
                self.chat_window.append_to_ai_response(f"\n[{agent_name}] [Error] {error_msg}\n\n", '31')
                self.chat_window.finish_ai_response()
                self.chat_window.stop_sending_state()
                self._set_inference_running(False)
        except Exception as e:
            print(f"Error in handle_agent_event: {e}")
            traceback.print_exc()

    def quit_app(self):
        reply = QMessageBox.question(self, 'Close Application',
            'Are you sure you want to close the application?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                if hasattr(self, "stream") and self.stream is not None:
                    try:
                        self.stream.stop()
                    except Exception:
                        pass
            finally:
                # Close all windows (no parent relationship, so must close explicitly)
                if self.chat_window:
                    self.chat_window.close()
                if self.session_json_window:
                    self.session_json_window.close()
                if self.memories_window:
                    self.memories_window.close()
                if self.documents_window:
                    self.documents_window.close()
                if self.inner_voice_window:
                    self.inner_voice_window.close()
                if self.settings_window:
                    self.settings_window.close()
                app = QApplication.instance()
                if app is not None:
                    app.quit()

    def closeEvent(self, event):
        # Intentionally do not persist FloatingWidget position.
        try:
            self._save_dock_settings()
        except Exception:
            pass

        try:
            if hasattr(self, "stream") and self.stream is not None:
                try:
                    self.stream.stop()
                except Exception:
                    pass
            # Close all windows (no parent relationship, so must close explicitly)
            if self.chat_window:
                self.chat_window.close()
            if self.session_json_window:
                self.session_json_window.close()
            if self.memories_window:
                self.memories_window.close()
            if self.documents_window:
                self.documents_window.close()
            if self.inner_voice_window:
                self.inner_voice_window.close()
            if self.settings_window:
                self.settings_window.close()
            event.accept()
        except Exception as e:
            print(f"Error during closeEvent: {e}")
            event.accept()

    def start_recording(self):
        self.is_recording = True
        self.frames = []

        def callback(indata, frames, time, status):
            if self.is_recording:
                self.frames.append(indata.copy().tobytes())

        if hasattr(self, "stream") and self.stream is not None:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        self.stream = sd.InputStream(samplerate=self.samplerate, channels=self.channels, dtype="int16", blocksize=512, latency="low", callback=callback)
        self.stream.start()
        self.animation_step = 0
        self.main_btn.setText("⠋")
        self.recording_animation_timer.start(100)

    def stop_recording(self):
        self.is_recording = False
        self.recording_animation_timer.stop()
        self.main_btn.setText(self.icon_emoji)
        self.main_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(50, 50, 50, 200);
                color: white;
                border-radius: 28px;
                font-size: 28px;
                border: 1px solid rgba(255, 255, 255, 0.25);
            }
            QPushButton:hover {
                background-color: rgba(70, 70, 70, 220);
                border: 1px solid rgba(255, 255, 255, 0.35);
            }
        """)
        
        t0 = time.perf_counter()
        if hasattr(self, "stream") and self.stream is not None:
            try:
                self.stream.abort()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        t1 = time.perf_counter()

        def _transcribe():
            try:
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(2)
                    wf.setframerate(self.samplerate)
                    wf.writeframes(b"".join(self.frames))
                buf.seek(0)
                t2 = time.perf_counter()

                audio_bytes = buf.read()

                reply_topic = f"transcribe.ui.reply.run.{uuid.uuid4()}"
                unsub = None

                def _on_reply(ev):
                    nonlocal unsub
                    if unsub:
                        try:
                            unsub()
                        except Exception:
                            pass
                        unsub = None

                    payload = getattr(ev, "payload", {}) or {}
                    t3 = time.perf_counter()
                    print(
                        "Transcribe reply:",
                        payload,
                        " timings(s): abort+close=",
                        round(t1 - t0, 3),
                        " build_wav=",
                        round(t2 - t1, 3),
                        " transcribe_wait=",
                        round(t3 - t2, 3),
                    )

                    if isinstance(payload, dict) and payload.get("status") == "success":
                        text = payload.get("text")
                        if text:
                            self.transcription_received.emit(str(text))
                    else:
                        msg = payload.get("message", "Transcription failed") if isinstance(payload, dict) else "Transcription failed"
                        print("Transcription failed:", msg)

                unsub = self._bus.subscribe(reply_topic, _on_reply)
                self._bus.publish(
                    "transcribe.cmd.run",
                    {"reply_topic": reply_topic, "language": self.selected_language, "audio_data": audio_bytes},
                )
            except Exception as e:
                print("Transcription failed:", e)
                traceback.print_exc()

        threading.Thread(target=_transcribe, daemon=True).start()

    def request_set_session_telemetry(self, *, enabled: bool) -> None:
        """Toggle per-session injected telemetry (stored in session meta)."""
        if not self.chat_window:
            return
        if getattr(self.chat_window, "is_sending", False):
            try:
                self.chat_window._show_toast("Currently running")
            except Exception:
                pass
            return

        sid = getattr(self, "active_session_id", None)
        sid = str(sid).strip() if isinstance(sid, str) else ""
        if not sid:
            return

        reply_topic = f"session.ui.reply.telemetry.set.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to update telemetry") if isinstance(payload, dict) else "Failed to update telemetry"
                try:
                    self.chat_window._show_toast(msg)
                except Exception:
                    pass
                # Re-sync sessions list (truth)
                try:
                    self.refresh_sessions()
                except Exception:
                    pass
                return

            # Refresh session list so the dropdown's cached meta stays true.
            try:
                self.refresh_sessions()
            except Exception:
                pass

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        self._bus.publish(
            "session.cmd.telemetry.set",
            {"reply_topic": reply_topic, "session_id": sid, "enabled": bool(enabled)},
        )
