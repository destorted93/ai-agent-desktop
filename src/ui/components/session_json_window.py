"""Session JSON viewer window.

Read-only, performance-oriented.

- Uses the in-process EventBus (Runtime singleton) to fetch session entries.
- Shows a small always-visible "Session meta" panel so we can inspect what's stored
  in the encrypted sessions index (sessions/index.enc) without opening files.
- Disables mutation UI (Edit / Load / Clear) on purpose.
- Uses a cheap JSON highlighter (keys + punctuation only) for speed.

Extension (2026-03): adds a drop-down selector to view either:
- the main session wrapped entries
- linked persistent sub-agent stores referenced by subhistory wrapper meta
"""

from __future__ import annotations

import json
import time
import uuid
import re
from typing import Any, Dict, List, Optional

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QSizePolicy,
    QComboBox,
    QPushButton,
)
from PyQt6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat, QColor
from PyQt6.QtCore import Qt

from ...appcore.runtime_context import Runtime
from .json_viewer_dialog import JsonViewerDialog


class _MinimalJsonHighlighter(QSyntaxHighlighter):
    """Fast JSON highlighter: keys + punctuation only (performance-oriented)."""

    def __init__(self, document):
        super().__init__(document)

        self._key_format = QTextCharFormat()
        self._key_format.setForeground(QColor("#9cdcfe"))

        self._punct_format = QTextCharFormat()
        self._punct_format.setForeground(QColor("#d4d4d4"))

        self._re_key = re.compile(r'"([^"\\]|\\.)*"(?=\s*:)')
        self._re_punct = re.compile(r'[\{\}\[\]:,]')

    def highlightBlock(self, text: str):
        for m in self._re_punct.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._punct_format)
        for m in self._re_key.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._key_format)


class SessionJsonWindow(JsonViewerDialog):
    """Window to display raw session JSON."""

    window_title = "Session (JSON)"
    settings_key = "session_json_window"
    default_filename_prefix = "session"

    def __init__(self, parent=None, app=None):
        # `app` is ignored (kept for backwards compatibility with older UI wiring).
        super().__init__(parent, editable=False)

        self.current_session_id: str | None = None

        # Hide mutating actions (read-only viewer).
        try:
            self.load_btn.hide()
        except Exception:
            pass
        try:
            self.clear_all_btn.hide()
        except Exception:
            pass

        # Replace the expensive JSON highlighter with a cheap one (keys + punctuation).
        try:
            if getattr(self, "_highlighter", None) is not None:
                self._highlighter.setDocument(None)
        except Exception:
            pass
        self._highlighter = _MinimalJsonHighlighter(self.text.document())

        # JSON viewer performance tweaks
        try:
            # Wrapping is expensive on multi-megabyte text.
            self.text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        except Exception:
            pass

        # Small always-visible session meta panel (read-only)
        self._meta_panel = QWidget(self)
        meta_layout = QVBoxLayout(self._meta_panel)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(6)

        # Don't let this panel expand and eat the whole dialog.
        sp_panel = self._meta_panel.sizePolicy()
        sp_panel.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        sp_panel.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self._meta_panel.setSizePolicy(sp_panel)

        # Source selector: main session vs linked persistent sub-agent stores.
        self._populating_sources = False
        self._sources: List[Dict[str, Any]] = []

        self._source_combo = QComboBox()
        self._source_combo.setFixedHeight(26)
        self._source_combo.setToolTip("Choose which log to view")
        self._source_combo.setStyleSheet(
            """
            QComboBox {
                background-color: #23272e;
                color: #d4d4d4;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 10pt;
                min-width: 240px;
            }
            QComboBox QAbstractItemView {
                background-color: #23272e;
                color: #d4d4d4;
                selection-background-color: rgba(255,255,255,0.12);
                selection-color: white;
            }
            """
        )

        self._source_refresh_btn = QPushButton("Refresh")
        self._source_refresh_btn.setFixedHeight(26)
        self._source_refresh_btn.setToolTip("Re-fetch and re-render the selected log")
        self._source_refresh_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px;
                padding: 2px 10px;
                font-size: 10pt;
            }
            QPushButton:hover { background-color: #4a4a4a; }
            """
        )

        source_row = QWidget(self._meta_panel)
        source_row_lay = QHBoxLayout(source_row)
        source_row_lay.setContentsMargins(0, 0, 0, 0)
        source_row_lay.setSpacing(8)
        lbl = QLabel("View:")
        lbl.setStyleSheet("QLabel { color: #b5b5b5; font-size: 9pt; font-weight: bold; }")
        source_row_lay.addWidget(lbl)
        source_row_lay.addWidget(self._source_combo, 1)
        source_row_lay.addWidget(self._source_refresh_btn)
        meta_layout.addWidget(source_row)

        meta_title = QLabel("Session meta (from sessions/index.enc)")
        meta_title.setStyleSheet("QLabel { color: #b5b5b5; font-size: 9pt; font-weight: bold; }")

        self._meta_text = QTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setAcceptRichText(False)
        self._meta_text.setFont(QFont("Consolas", 9))
        self._meta_text.setFixedHeight(120)
        sp = self._meta_text.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        sp.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self._meta_text.setSizePolicy(sp)
        self._meta_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._meta_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._meta_text.setStyleSheet(
            """
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px;
                padding: 8px;
            }
            """
        )

        meta_layout.addWidget(meta_title)
        meta_layout.addWidget(self._meta_text)

        # Insert meta panel above the main JSON text editor.
        try:
            lay = self.layout()
            if lay is not None:
                idx = lay.indexOf(self.text)
                if idx >= 0:
                    lay.insertWidget(idx, self._meta_panel)
                else:
                    lay.addWidget(self._meta_panel)
        except Exception:
            pass

        # Wire source controls
        try:
            self._source_combo.currentIndexChanged.connect(self._on_source_changed)
            self._source_refresh_btn.clicked.connect(self.refresh_content)
        except Exception:
            pass

        self._update_meta_panel()

    def set_app(self, app):
        return

    # -----------------------------------------------------------------
    # Bus helpers
    # -----------------------------------------------------------------

    def _bus_request(self, cmd_topic: str, payload: Dict[str, Any], timeout_ms: int = 5000) -> Dict[str, Any]:
        bus = Runtime.get_event_bus()
        reply_topic = f"session.ui.reply.json_window.{uuid.uuid4()}"

        result: Dict[str, Any] = {}
        unsub = None

        def _on_reply(ev):
            nonlocal result, unsub
            try:
                if unsub:
                    unsub()
            except Exception:
                pass
            unsub = None

            payload_obj = getattr(ev, "payload", {}) or {}
            if isinstance(payload_obj, dict):
                result = payload_obj
            else:
                result = {"status": "error", "message": "Unexpected reply payload"}

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish(cmd_topic, {**payload, "reply_topic": reply_topic})

        deadline = time.time() + (timeout_ms / 1000.0)
        while not result and time.time() < deadline:
            try:
                bus.pump(max_events=50)
            except Exception:
                pass
            try:
                QApplication.processEvents()
            except Exception:
                pass
            time.sleep(0.01)

        if not result:
            try:
                if unsub:
                    unsub()
            except Exception:
                pass
            return {"status": "error", "message": "Timeout waiting for reply"}

        return result

    # -----------------------------------------------------------------
    # Source selection (main session vs linked persistent agent stores)
    # -----------------------------------------------------------------

    def _get_selected_source_key(self) -> str:
        try:
            key = self._source_combo.currentData()
            return str(key) if isinstance(key, str) and key else "main"
        except Exception:
            return "main"

    def _build_sources_from_main_entries(self, main_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []

        sources.append(
            {
                "key": "main",
                "label": "Main session",
                "tooltip": "The shared timeline stored at sessions/<session_id>.enc",
            }
        )

        # Note: we do NOT list Ariane's inner-voice store by default.
        # It will appear if (and only if) it was referenced via subhistory in this session.
        # This avoids confusing empty entries in group sessions.

        seen = {s["key"] for s in sources if isinstance(s.get("key"), str)}

        for we in (main_entries or []):
            if not isinstance(we, dict):
                continue
            sh = we.get("subhistory")
            if not isinstance(sh, dict):
                continue
            mode = str(sh.get("mode") or "").strip().lower()
            if mode != "persistent":
                continue

            store_id = sh.get("store_id")
            if not isinstance(store_id, str) or not store_id.strip():
                continue
            store_id = store_id.strip()
            if store_id in seen:
                continue

            name = sh.get("subagent_name")
            name = str(name).strip() if isinstance(name, str) and name.strip() else store_id

            sources.append(
                {
                    "key": store_id,
                    "label": f"{name} (persistent)",
                    "tooltip": store_id,
                }
            )
            seen.add(store_id)

        return sources

    def _set_sources(self, sources: List[Dict[str, Any]], *, preserve_key: Optional[str] = None) -> None:
        preserve_key = preserve_key or "main"

        self._populating_sources = True
        try:
            self._source_combo.blockSignals(True)
            self._source_combo.clear()

            self._sources = sources if isinstance(sources, list) else []

            for s in (self._sources or []):
                if not isinstance(s, dict):
                    continue
                key = s.get("key")
                label = s.get("label")
                if not isinstance(key, str) or not key:
                    continue
                if not isinstance(label, str) or not label.strip():
                    label = key

                self._source_combo.addItem(label, key)
                i = self._source_combo.count() - 1
                tip = s.get("tooltip")
                if isinstance(tip, str) and tip.strip():
                    try:
                        self._source_combo.setItemData(i, tip.strip(), Qt.ItemDataRole.ToolTipRole)
                    except Exception:
                        pass

            idx = self._source_combo.findData(preserve_key)
            if idx < 0:
                idx = self._source_combo.findData("main")
            if idx >= 0:
                self._source_combo.setCurrentIndex(idx)
        finally:
            try:
                self._source_combo.blockSignals(False)
            except Exception:
                pass
            self._populating_sources = False

    def _on_source_changed(self, _idx: int) -> None:
        if bool(getattr(self, "_populating_sources", False)):
            return
        self.refresh_content()

    # -----------------------------------------------------------------
    # Meta panel
    # -----------------------------------------------------------------

    def _update_meta_panel(self) -> None:
        try:
            if not self.current_session_id:
                self._meta_text.setPlainText("// No active session")
                return

            res = self._bus_request("session.cmd.list", {}, timeout_ms=3000)
            if res.get("status") != "success":
                self._meta_text.setPlainText(
                    f"// Error loading session meta: {res.get('message', 'unknown error')}"
                )
                return

            sessions = res.get("sessions", [])
            if not isinstance(sessions, list):
                sessions = []

            meta = None
            for s in sessions:
                if isinstance(s, dict) and s.get("session_id") == self.current_session_id:
                    meta = s
                    break

            if not meta:
                self._meta_text.setPlainText(f"// Session not found in index: {self.current_session_id}")
                return

            self._meta_text.setPlainText(json.dumps(meta, ensure_ascii=False, indent=1))

        except Exception:
            try:
                self._meta_text.setPlainText("// Error loading session meta")
            except Exception:
                pass

    # Faster bulk set + keep meta in sync.
    def set_json_text(self, text: str):
        text = text or ""

        # For very large payloads, disable highlighting entirely.
        try:
            if len(text) > 200_000:
                if getattr(self, "_highlighter", None) is not None:
                    self._highlighter.setDocument(None)
                    self._highlighter = None
            else:
                if getattr(self, "_highlighter", None) is None:
                    self._highlighter = _MinimalJsonHighlighter(self.text.document())
        except Exception:
            pass

        try:
            self.text.setUpdatesEnabled(False)
        except Exception:
            pass

        # Avoid JsonViewerDialog.scroll_to_bottom() (it forces extra layout work).
        try:
            self.text.setPlainText(text)
        except Exception:
            # Fallback
            super().set_json_text(text)

        # Keep view at top for huge logs.
        try:
            sb = self.text.verticalScrollBar()
            if sb is not None:
                sb.setValue(0)
        except Exception:
            pass

        try:
            self.text.setUpdatesEnabled(True)
        except Exception:
            pass

        self._update_meta_panel()

    # JsonViewerDialog requires these abstract methods.
    def save_to_source(self, data) -> dict:
        return {"status": "error", "message": "Session (JSON) is read-only"}

    def clear_all_data(self) -> dict:
        return {"status": "error", "message": "Session (JSON) is read-only"}

    def refresh_content(self):
        if not self.current_session_id:
            self.set_json_text("// No active session")
            return

        # Always fetch main session first (so we can discover linked persistent stores).
        main_res = self._bus_request(
            "session.cmd.entries.get_wrapped",
            {"session_id": self.current_session_id},
        )

        if main_res.get("status") != "success":
            self.set_json_text(f"// Error loading session: {main_res.get('message', 'Unknown error')}")
            return

        main_entries = main_res.get("entries", [])
        if not isinstance(main_entries, list):
            main_entries = []

        # Refresh selector options.
        prev_key = self._get_selected_source_key()
        sources = self._build_sources_from_main_entries(main_entries)

        # If this is a group session, also offer per-participant filtered views.
        try:
            meta_res = self._bus_request("session.cmd.list", {}, timeout_ms=3000)
            sessions = meta_res.get("sessions", []) if isinstance(meta_res, dict) else []
            sessions = sessions if isinstance(sessions, list) else []
            meta = None
            for s in sessions:
                if isinstance(s, dict) and s.get("session_id") == self.current_session_id:
                    meta = s
                    break

            if isinstance(meta, dict) and str(meta.get("type") or "").strip().lower() == "group":
                parts = meta.get("participants") if isinstance(meta.get("participants"), list) else []

                seen_keys = {str(x.get('key')) for x in sources if isinstance(x, dict) and isinstance(x.get('key'), str)}

                # Ask backend for participant store ids (no path building in UI).
                try:
                    res2 = self._bus_request(
                        "session.cmd.group.participant_stores.list",
                        {"session_id": str(self.current_session_id)},
                    )
                    if isinstance(res2, dict) and res2.get("status") == "success":
                        srcs = res2.get("sources") if isinstance(res2.get("sources"), list) else []
                        for s in srcs:
                            if not isinstance(s, dict):
                                continue
                            key = s.get("key")
                            if not isinstance(key, str) or not key.strip():
                                continue
                            if key in seen_keys:
                                continue
                            sources.append({"key": key, "label": s.get("label") or key, "tooltip": s.get("tooltip") or key})
                            seen_keys.add(key)
                except Exception:
                    pass
        except Exception:
            pass

        self._set_sources(sources, preserve_key=prev_key)

        key = self._get_selected_source_key()

        # Load selected source.
        if key == "main":
            entries = main_entries
            self.set_json_text(json.dumps(entries, indent=1, ensure_ascii=False))
            return

        sub_res = self._bus_request(
            "subagent.cmd.session.entries.get",
            {"store_id": key},
            timeout_ms=5000,
        )

        if sub_res.get("status") != "success":
            self.set_json_text(f"// Error loading store '{key}': {sub_res.get('message', 'Unknown error')}")
            return

        entries = sub_res.get("entries", [])
        if not isinstance(entries, list):
            entries = []

        self.set_json_text(json.dumps(entries, indent=1, ensure_ascii=False))
