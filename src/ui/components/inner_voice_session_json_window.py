"""Inner voice session JSON viewer window.

UI-only: uses Runtime.get_event_bus() to talk to the app.

Note: JsonViewerDialog expects sync return values from save_to_source()/clear_all_data().
We implement a small synchronous bus-request helper that *actively pumps the bus*
while waiting, so we don't depend on the app's QTimer pump.
"""

import json
import time
import uuid
from typing import Any, Dict

from PyQt6.QtWidgets import QApplication

from ...appcore.runtime_context import Runtime
from .json_viewer_dialog import JsonViewerDialog


class InnerVoiceSessionJsonWindow(JsonViewerDialog):
    """Window to display raw inner-voice session JSON."""

    window_title = "Inner Voice Session (JSON)"
    settings_key = "inner_voice_session_json_window"
    default_filename_prefix = "inner_voice_session"

    def __init__(self, parent=None):
        super().__init__(parent, editable=True)

    def _bus_request(self, cmd_topic: str, payload: Dict[str, Any], timeout_ms: int = 5000) -> Dict[str, Any]:
        bus = Runtime.get_event_bus()
        reply_topic = f"inner_voice.ui.reply.session_json_window.{uuid.uuid4()}"

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

    def save_to_source(self, data) -> dict:
        if not isinstance(data, list):
            return {"status": "error", "message": "entries must be a list"}
        return self._bus_request(
            "inner_voice.cmd.session.entries.set",
            {"entries": data},
        )

    def clear_all_data(self) -> dict:
        return self._bus_request(
            "inner_voice.cmd.session.entries.clear",
            {},
        )

    def refresh_content(self):
        result = self._bus_request(
            "inner_voice.cmd.session.entries.get",
            {},
        )

        if result.get("status") != "success":
            self.set_json_text(f"// Error loading inner voice session: {result.get('message', 'Unknown error')}")
            return

        entries = result.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        self.set_json_text(json.dumps(entries, indent=2, ensure_ascii=False))
