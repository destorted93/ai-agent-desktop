"""Memories JSON viewer window with edit capability.

This window is UI-only: it talks to the app via the in-process EventBus
(Runtime singleton) and does not hold an Application reference.

Note: JsonViewerDialog expects sync return values from save_to_source()/clear_all_data().
We implement a small synchronous bus-request helper that *actively pumps the bus*
while waiting, so we don't depend on the app's QTimer pump.

2026-03: Memories are now per-agent stores (aria/ariane/other persistent agents).
This window supports selecting which memory store to view/edit.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from PyQt6.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel, QComboBox, QPushButton
from PyQt6.QtCore import Qt

from ...appcore.runtime_context import Runtime
from .json_viewer_dialog import JsonViewerDialog


class MemoriesWindow(JsonViewerDialog):
    """Window to display and edit memories JSON."""

    window_title = "Memories"
    settings_key = "memories_window"
    default_filename_prefix = "memories"

    def __init__(self, parent=None):
        super().__init__(parent, editable=True)

        self._stores: List[Dict[str, Any]] = []
        self._populating = False
        self._selected_agent_id: Optional[str] = None

        # UI: store selector panel (inserted above the JSON text editor)
        self._store_panel = QWidget(self)
        hl = QHBoxLayout(self._store_panel)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)

        lab = QLabel("Store:")
        lab.setStyleSheet("color: #b5b5b5;")
        hl.addWidget(lab)

        self._store_combo = QComboBox()
        self._store_combo.setFixedHeight(26)
        self._store_combo.setToolTip("Choose which agent's memory store to view/edit")
        hl.addWidget(self._store_combo, 0)

        self._store_refresh_btn = QPushButton("↻ Refresh")
        self._store_refresh_btn.setFixedHeight(26)
        self._store_refresh_btn.setToolTip("Reload store list")
        try:
            # Use the same styling helper as the base dialog.
            self._style_button(self._store_refresh_btn)
        except Exception:
            pass
        hl.addWidget(self._store_refresh_btn, 0)

        hl.addStretch(1)

        # Insert panel above the main JSON text editor.
        try:
            lay = self.layout()
            if lay is not None:
                idx = lay.indexOf(self.text)
                if idx >= 0:
                    lay.insertWidget(idx, self._store_panel)
                else:
                    lay.addWidget(self._store_panel)
        except Exception:
            pass

        # Clarify semantics: this clears the selected store.
        try:
            self.clear_all_btn.setToolTip("Clear all memories in the selected store")
        except Exception:
            pass

        try:
            self._store_combo.currentIndexChanged.connect(self._on_store_changed)
            self._store_refresh_btn.clicked.connect(self._refresh_store_list)
        except Exception:
            pass

        # Initial load
        self._refresh_store_list(prefer_agent_id=None)

    def _bus_request(self, cmd_topic: str, payload: Dict[str, Any], timeout_ms: int = 5000) -> Dict[str, Any]:
        bus = Runtime.get_event_bus()
        reply_topic = f"memories.ui.reply.json_window.{uuid.uuid4()}"

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
        # Actively pump: deliver cmd event to app handlers + then deliver reply.
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

    def _refresh_store_list(self, prefer_agent_id: Optional[str] = None) -> None:
        res = self._bus_request("memories.cmd.list_stores", {})
        if res.get("status") != "success":
            self.set_json_text(f"// Error loading stores: {res.get('message', 'Unknown error')}")
            return

        stores = res.get("stores")
        stores = stores if isinstance(stores, list) else []

        # Normalize
        cleaned: List[Dict[str, Any]] = []
        for s in stores:
            if not isinstance(s, dict):
                continue
            aid = s.get("agent_id")
            if not isinstance(aid, str) or not aid.strip():
                continue
            cleaned.append(s)

        self._stores = cleaned

        # Populate combo
        self._populating = True
        try:
            self._store_combo.clear()
            for s in cleaned:
                aid = str(s.get("agent_id"))
                fn = s.get("file_name")
                label = f"{aid}" if not fn else f"{aid}  ({fn})"
                self._store_combo.addItem(label, userData=aid)

            # Choose selection
            target = prefer_agent_id or self._selected_agent_id
            if not target:
                # Prefer aria if present, otherwise first store.
                target = "aria" if any(self._store_combo.itemData(i) == "aria" for i in range(self._store_combo.count())) else None

            idx = -1
            if target:
                for i in range(self._store_combo.count()):
                    if self._store_combo.itemData(i) == target:
                        idx = i
                        break

            if idx < 0 and self._store_combo.count() > 0:
                idx = 0

            if idx >= 0:
                self._store_combo.setCurrentIndex(idx)
        finally:
            self._populating = False

        # Trigger load for selected store
        self.refresh_content()

    def _on_store_changed(self, _idx: int) -> None:
        if self._populating:
            return
        aid = self._store_combo.currentData()
        aid = str(aid).strip() if isinstance(aid, str) else ""
        self._selected_agent_id = aid or None
        self.refresh_content()

    def _apply_store_ui(self, agent_id: str) -> None:
        aid = str(agent_id or "").strip() or "unknown"
        self.setWindowTitle(f"Memories — {aid}")
        try:
            self.default_filename_prefix = f"memories_{aid}"
        except Exception:
            pass

    def save_to_source(self, data) -> dict:
        """Save memories data back to storage (selected store)."""
        if not isinstance(data, list):
            return {"status": "error", "message": "memories must be a list"}
        aid = self._selected_agent_id
        if not isinstance(aid, str) or not aid.strip():
            return {"status": "error", "message": "No store selected"}
        return self._bus_request(
            "memories.cmd.set_store",
            {"agent_id": str(aid), "memories": data},
        )

    def clear_all_data(self) -> dict:
        """Clear all memories from storage (selected store)."""
        aid = self._selected_agent_id
        if not isinstance(aid, str) or not aid.strip():
            return {"status": "error", "message": "No store selected"}
        return self._bus_request(
            "memories.cmd.clear_store",
            {"agent_id": str(aid)},
        )

    def refresh_content(self):
        """Refresh memories from storage (selected store)."""
        aid = self._selected_agent_id
        if not isinstance(aid, str) or not aid.strip():
            # If we don't know yet, try to pick something.
            if self._store_combo.count() > 0:
                try:
                    self._selected_agent_id = str(self._store_combo.currentData())
                    aid = self._selected_agent_id
                except Exception:
                    aid = None

        if not isinstance(aid, str) or not aid.strip():
            self.set_json_text("// No memory store selected")
            return

        self._apply_store_ui(aid)

        result = self._bus_request(
            "memories.cmd.get_store",
            {"agent_id": str(aid)},
        )

        if result.get("status") != "success":
            self.set_json_text(f"// Error loading memories: {result.get('message', 'Unknown error')}")
            return

        memories = result.get("memories", [])
        if not isinstance(memories, list):
            memories = []
        self.set_json_text(json.dumps(memories, indent=2, ensure_ascii=False))
