"""Agents Studio window.

Goal:
- Let the user create/edit/delete agent definitions in app-data ConfigRoot (Config/agents/*.yaml)
- Hot-reload: changes are immediately available for run_subagent (app reloads ConfigManager)

This is intentionally a simple first pass:
- left: list of agents
- right: editable fields + prompt
- buttons: New / Save / Delete / Duplicate / Refresh

Ariane/Aria are protected from deletion at the app layer (editing is allowed).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QDoubleValidator, QIntValidator
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QCheckBox,
    QComboBox,
    QSlider,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
)

from ...appcore.runtime_context import Runtime
from ..screen_utils import validate_window_position


class AgentsStudioWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agents Studio")
        self.setModal(False)
        self.resize(1060, 720)

        # Restore last position if available
        try:
            from PyQt6.QtCore import QSettings

            settings = QSettings("ai-agent", "widget")
            saved_pos = settings.value("agents_studio_window_pos", None)
            validated_pos = validate_window_position(saved_pos, 1060, 720)
            if validated_pos is not None:
                x, y = validated_pos
                self.move(x, y)
        except Exception:
            pass

        # Model UI spec (defaults/options) + current agent role (for safe saving)
        self._model_ui_spec: Dict[str, Any] = {}
        self._current_role: str = "subagent"

        self._bus = Runtime.get_event_bus()
        self._agents_meta: List[Dict[str, Any]] = []
        self._current_agent_id: Optional[str] = None
        self._current_is_one_shot: bool = False
        self._creating_new = False

        # Best-effort: auto-refresh when the agent catalog changes
        try:
            self._agents_changed_unsub = self._bus.subscribe(
                "agents.list.changed",
                lambda ev: self.refresh_list(),
            )
        except Exception:
            self._agents_changed_unsub = None

        self.setStyleSheet(
            "QDialog { background-color: #1e1e1e; }"
            "QLabel { color: #d4d4d4; }"
            "QLineEdit, QTextEdit, QPlainTextEdit { background-color: #23272e; color: #d4d4d4; border: 1px solid #3d3d3d; border-radius: 6px; }"
            "QLineEdit { padding: 6px 8px; }"
            "QTextEdit, QPlainTextEdit { padding: 8px; }"
            "QListWidget { background-color: #161616; color: #d4d4d4; border: 1px solid #3d3d3d; border-radius: 6px; }"
            "QPushButton { background-color: #3d3d3d; color: #d4d4d4; border: none; border-radius: 6px; padding: 7px 12px; }"
            "QPushButton:hover { background-color: #4d4d4d; }"
            "QPushButton:disabled { background-color: #2a2a2a; color: #666666; }"
            "QComboBox { background-color: #23272e; color: #d4d4d4; border: 1px solid #3d3d3d; border-radius: 6px; padding: 4px 8px; }"
            "QSlider::groove:horizontal { height: 6px; background: #2f2f2f; border-radius: 3px; }"
            "QSlider::handle:horizontal { width: 14px; margin: -6px 0; border-radius: 7px; background: #d4d4d4; }"
            "QGroupBox { border: 1px solid #2f2f2f; border-radius: 8px; margin-top: 10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #b5b5b5; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QLabel("Agents Studio")
        header.setStyleSheet("font-weight: 800; font-size: 14px;")
        root.addWidget(header)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # Left panel: list
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_selected)
        left_l.addWidget(self.list, 1)

        left_btns = QWidget()
        lb = QHBoxLayout(left_btns)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(8)

        self.new_btn = QPushButton("New")
        self.new_btn.clicked.connect(self._new_agent)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_list)
        lb.addWidget(self.new_btn)
        lb.addWidget(self.refresh_btn)
        lb.addStretch(1)
        left_l.addWidget(left_btns)

        split.addWidget(left)

        # Right panel: editor
        right = QWidget()
        r = QVBoxLayout(right)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(10)

        # Basic fields
        form = QWidget()
        fl = QVBoxLayout(form)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(8)

        def _row(label: str, w: QWidget) -> QWidget:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(10)
            lab = QLabel(label)
            lab.setFixedWidth(110)
            lab.setStyleSheet("color: #b5b5b5;")
            hl.addWidget(lab)
            hl.addWidget(w, 1)
            return row

        self.id_in = QLineEdit()
        self.id_in.setPlaceholderText("e.g. gremlin")

        self.name_in = QLineEdit()
        self.name_in.setPlaceholderText("Display name")

        # Multi-line description (3–4 lines). We keep it plain-text.
        self.desc_in = QPlainTextEdit()
        self.desc_in.setPlaceholderText("Short description (optional)")

        try:
            self.desc_in.setMinimumHeight(64)
            self.desc_in.setMaximumHeight(96)
            self.desc_in.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        except Exception:
            pass
        self.desc_in.setStyleSheet(
            "QPlainTextEdit { padding: 8px; font-size: 10pt; font-family: 'Segoe UI', sans-serif; }"
        )


        fl.addWidget(_row("Agent ID", self.id_in))
        fl.addWidget(_row("Name", self.name_in))
        fl.addWidget(_row("Description", self.desc_in))

        # Tools (dynamic; derived from repo tool_group.yaml manifests)
        self._pending_tool_selection: Optional[Dict[str, List[str]]] = None

        tools_box = QGroupBox("Tools")
        self._tools_box = tools_box
        self._tools_layout = QVBoxLayout(tools_box)
        self._tools_layout.setContentsMargins(10, 14, 10, 10)
        self._tools_layout.setSpacing(6)

        self._tools_tree = QTreeWidget()
        try:
            self._tools_tree.setColumnCount(2)
            self._tools_tree.setHeaderHidden(True)

            # Don’t ellipsize tool names: size col0 to contents, let col1 stretch.
            hdr = self._tools_tree.header()
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        except Exception:
            pass
        self._tools_tree.setStyleSheet(
            "QTreeWidget { background-color: #161616; color: #d4d4d4; border: 1px solid #3d3d3d; border-radius: 6px; }"
            "QTreeWidget::item { padding: 4px 2px; }"
        )
        self._tools_layout.addWidget(self._tools_tree)
        self._tool_tree_updating = False
        try:
            self._tools_tree.itemChanged.connect(self._on_tool_tree_item_changed)
        except Exception:
            pass

        # Populated asynchronously.
        fl.addWidget(tools_box)

        # Model settings panel (right-side)
        self.model_box = QGroupBox("Model")
        mb = QVBoxLayout(self.model_box)
        mb.setContentsMargins(10, 14, 10, 10)
        mb.setSpacing(8)

        self.model_name_cb = QComboBox()
        self.model_name_cb.setEditable(False)

        self.reasoning_effort_cb = QComboBox()
        self.reasoning_effort_cb.setEditable(False)

        self.reasoning_summary_cb = QComboBox()
        self.reasoning_summary_cb.setEditable(False)

        self.text_verbosity_cb = QComboBox()
        self.text_verbosity_cb.setEditable(False)

        # Make dropdown widths sane (avoid the giant overlay list).
        try:
            self.model_name_cb.setMinimumContentsLength(14)
            self.model_name_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            view = self.model_name_cb.view()
            if view is not None:
                view.setMinimumWidth(180)

            for cb in (self.reasoning_effort_cb, self.reasoning_summary_cb, self.text_verbosity_cb):
                cb.setMinimumContentsLength(10)
                cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                v2 = cb.view()
                if v2 is not None:
                    v2.setMinimumWidth(180)
        except Exception:
            pass

        # Temperature (slider + editable value)
        self._temp_scale = 10  # will be updated from backend ui spec if provided
        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setMinimum(1)   # 0.1
        self.temp_slider.setMaximum(10)  # 1.0
        self.temp_slider.setSingleStep(1)

        self.temp_value = QLineEdit("1.0")
        self.temp_value.setFixedWidth(48)
        self.temp_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.temp_value.setStyleSheet("QLineEdit { padding: 4px 6px; }")
        try:
            self.temp_value.setValidator(QDoubleValidator(0.1, 1.0, 1))
        except Exception:
            pass

        # Max turns (slider + editable value)
        self.max_turns_slider = QSlider(Qt.Orientation.Horizontal)
        self.max_turns_slider.setMinimum(1)
        self.max_turns_slider.setMaximum(1024)
        self.max_turns_slider.setSingleStep(1)
        self.max_turns_slider.setPageStep(32)

        self.max_turns_value = QLineEdit("256")
        self.max_turns_value.setFixedWidth(56)
        self.max_turns_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.max_turns_value.setStyleSheet("QLineEdit { padding: 4px 6px; }")
        try:
            self.max_turns_value.setValidator(QIntValidator(1, 1024))
        except Exception:
            pass

        # Streaming is required for our UI streaming pipeline.
        self.stream_cb = QCheckBox("stream")
        self.stream_cb.setStyleSheet("QCheckBox { color: #d4d4d4; }")
        try:
            self.stream_cb.setChecked(True)
            self.stream_cb.setEnabled(False)
            self.stream_cb.setToolTip("Streaming is required for the current UI; this is always on.")
        except Exception:
            pass

        def _slider_row(sl: QSlider, val_widget: QWidget) -> QWidget:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            hl.addWidget(sl, 1)
            hl.addWidget(val_widget, 0)
            return row

        # Defaults button
        self.model_defaults_btn = QPushButton("Defaults")
        self.model_defaults_btn.setToolTip("Reset model settings to backend defaults")
        try:
            self.model_defaults_btn.clicked.connect(lambda: self._apply_model_defaults())
        except Exception:
            pass

        mb.addWidget(_row("model", self.model_name_cb))
        mb.addWidget(self.model_defaults_btn, alignment=Qt.AlignmentFlag.AlignRight)
        mb.addWidget(QLabel("temperature"))
        mb.addWidget(_slider_row(self.temp_slider, self.temp_value))
        mb.addWidget(QLabel("max_turns"))
        mb.addWidget(_slider_row(self.max_turns_slider, self.max_turns_value))
        mb.addWidget(_row("reasoning_effort", self.reasoning_effort_cb))
        mb.addWidget(_row("reasoning_summary", self.reasoning_summary_cb))
        mb.addWidget(_row("text_verbosity", self.text_verbosity_cb))
        mb.addWidget(self.stream_cb)

        # Live slider labels + editable value sync
        try:
            self.temp_slider.valueChanged.connect(lambda v: self.temp_value.setText(f"{float(v)/float(getattr(self, '_temp_scale', 10) or 10):.1f}"))
            self.max_turns_slider.valueChanged.connect(lambda v: self.max_turns_value.setText(str(int(v))))

            def _temp_commit():
                try:
                    txt = (self.temp_value.text() or "").strip()
                    if not txt:
                        return
                    t = float(txt)
                except Exception:
                    return
                # Clamp
                r = (self._model_ui_spec.get("ranges") if isinstance(self._model_ui_spec, dict) else {}) or {}
                tr = r.get("temperature") if isinstance(r.get("temperature"), dict) else {}
                mn = float(tr.get("min", 0.1) or 0.1)
                mx = float(tr.get("max", 1.0) or 1.0)
                step = float(tr.get("step", 0.1) or 0.1)
                t = max(mn, min(mx, t))
                scale = int(round(1.0 / step)) if step > 0 else 10
                scale = max(1, scale)
                self._temp_scale = scale
                self.temp_slider.setMinimum(int(round(mn * scale)))
                self.temp_slider.setMaximum(int(round(mx * scale)))
                self.temp_slider.setValue(int(round(t * scale)))

            def _turns_commit():
                try:
                    txt = (self.max_turns_value.text() or "").strip()
                    if not txt:
                        return
                    v = int(txt)
                except Exception:
                    return
                r = (self._model_ui_spec.get("ranges") if isinstance(self._model_ui_spec, dict) else {}) or {}
                rr = r.get("max_turns") if isinstance(r.get("max_turns"), dict) else {}
                mn = int(rr.get("min", 1) or 1)
                mx = int(rr.get("max", 1024) or 1024)
                v = max(mn, min(mx, v))
                self.max_turns_slider.setMinimum(mn)
                self.max_turns_slider.setMaximum(mx)
                self.max_turns_slider.setValue(v)

            self.temp_value.editingFinished.connect(_temp_commit)
            self.max_turns_value.editingFinished.connect(_turns_commit)
        except Exception:
            pass

        # Top row: basic form (left) + model panel (right)
        # Keep the model panel from eating the whole window.
        try:
            self.model_box.setFixedWidth(320)
        except Exception:
            pass

        top_row = QWidget()
        tr = QHBoxLayout(top_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(12)
        tr.addWidget(form, 1)
        tr.addWidget(self.model_box, 0)

        r.addWidget(top_row)

        # Prompt
        prompt_lab = QLabel("System prompt")
        prompt_lab.setStyleSheet("color: #b5b5b5;")
        r.addWidget(prompt_lab)

        # Use a plain-text editor so pasted rich-text/HTML doesn't render weird.
        # (This is exactly why you saw the "striped" formatting before saving.)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("Write the agent's system prompt here…")
        try:
            self.prompt_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        except Exception:
            pass
        self.prompt_edit.setStyleSheet(
            "QPlainTextEdit { padding: 10px; font-family: Consolas, 'Courier New', monospace; font-size: 10pt; }"
        )
        r.addWidget(self.prompt_edit, 1)

        # Action buttons
        actions = QWidget()
        ab = QHBoxLayout(actions)
        ab.setContentsMargins(0, 0, 0, 0)
        ab.setSpacing(8)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save)

        self.preview_btn = QPushButton("Preview prompt")
        self.preview_btn.clicked.connect(self._preview_prompt)

        self.dup_btn = QPushButton("Duplicate")
        self.dup_btn.clicked.connect(self._duplicate)

        self.del_btn = QPushButton("Delete")
        self.del_btn.setStyleSheet("QPushButton { background-color: rgba(255, 107, 107, 0.25); } QPushButton:hover { background-color: rgba(255, 107, 107, 0.35); }")
        self.del_btn.clicked.connect(self._delete)


        ab.addWidget(self.save_btn)
        ab.addWidget(self.preview_btn)
        ab.addWidget(self.dup_btn)
        ab.addWidget(self.del_btn)
        ab.addStretch(1)

        r.addWidget(actions)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)


        self._load_model_ui_spec()
        self._load_tool_groups()
        self.refresh_list()

    # -----------------
    # Bus helpers
    # -----------------

    def _request(self, topic: str, payload: Dict[str, Any], on_ok) -> None:

        reply_topic = f"agents.ui.reply.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None
            res = getattr(ev, "payload", {}) or {}
            if not isinstance(res, dict) or res.get("status") != "success":
                msg = res.get("message") if isinstance(res, dict) else None
                QMessageBox.warning(self, "Agents Studio", str(msg or "Request failed"))
                return
            try:
                on_ok(res)
            except Exception:
                pass

        unsub = self._bus.subscribe(reply_topic, _on_reply)
        p2 = dict(payload or {})
        p2["reply_topic"] = reply_topic
        self._bus.publish(topic, p2)


    # -----------------
    # Tool groups (dynamic)
    # -----------------

    def _show_toast(self, message: str, duration_ms: int = 1000):
        """Show a floating toast notification that fades out."""
        toast = QLabel(message, self)
        toast.setStyleSheet("""
            QLabel {
                background-color: rgba(50, 50, 50, 230);
                color: #4da6ff;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 10pt;
                font-weight: bold;
            }
        """)
        toast.adjustSize()
        
        # Position at bottom center of chat window
        x = (self.width() - toast.width()) // 2
        y = self.height() - toast.height() - 80
        toast.move(x, y)
        toast.show()
        
        # Fade out and delete after duration
        QTimer.singleShot(duration_ms, toast.deleteLater)

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        try:
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget() if item is not None else None
                if w is not None:
                    w.setParent(None)
        except Exception:
            pass

    def _on_tool_tree_item_changed(self, item: Any, column: int) -> None:
        # Make group checkbox toggle children, and keep parent state in sync.
        if bool(getattr(self, "_tool_tree_updating", False)):
            return
        tree = getattr(self, "_tools_tree", None)
        if tree is None or item is None:
            return

        try:
            self._tool_tree_updating = True

            # Parent toggled -> propagate to children.
            try:
                if hasattr(item, "childCount") and int(item.childCount() or 0) > 0:
                    st = item.checkState(0)
                    # Only propagate explicit user toggles (checked/unchecked).
                    if st in (Qt.CheckState.Checked, Qt.CheckState.Unchecked):
                        for j in range(item.childCount()):
                            ch = item.child(j)
                            if ch is not None:
                                ch.setCheckState(0, st)
                    return
            except Exception:
                pass

            # Child toggled -> recompute parent check state.
            try:
                parent = item.parent()
                if parent is None:
                    return
                total = int(parent.childCount() or 0)
                if total <= 0:
                    return
                checked = 0
                for j in range(total):
                    ch = parent.child(j)
                    if ch is not None and ch.checkState(0) == Qt.CheckState.Checked:
                        checked += 1

                if checked <= 0:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                elif checked >= total:
                    parent.setCheckState(0, Qt.CheckState.Checked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            except Exception:
                return
        finally:
            try:
                self._tool_tree_updating = False
            except Exception:
                pass

    def _apply_tool_selection_to_tree(self, selection: Dict[str, List[str]]) -> None:
        """Apply group->tools selection to the tri-state tree."""
        sel = selection if isinstance(selection, dict) else {}
        norm: Dict[str, set] = {}
        for gid, tools in sel.items():
            g = str(gid).strip().lower()
            if not g:
                continue
            ts = {str(x).strip() for x in (tools or []) if isinstance(x, str) and str(x).strip()}
            norm[g] = ts

        tree = getattr(self, "_tools_tree", None)
        if tree is None:
            return

        try:
            self._tool_tree_updating = True

            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                if parent is None:
                    continue
                gid = parent.data(0, Qt.ItemDataRole.UserRole)
                gid = str(gid or parent.text(0) or "").strip().lower()
                want = norm.get(gid, set())

                total = int(parent.childCount() or 0)
                checked = 0
                for j in range(total):
                    ch = parent.child(j)
                    if ch is None:
                        continue
                    tn = ch.data(0, Qt.ItemDataRole.UserRole)
                    tn = str(tn or ch.text(0) or "").strip()
                    st = Qt.CheckState.Checked if tn in want else Qt.CheckState.Unchecked
                    ch.setCheckState(0, st)
                    if st == Qt.CheckState.Checked:
                        checked += 1

                if checked <= 0:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                elif checked >= total and total > 0:
                    parent.setCheckState(0, Qt.CheckState.Checked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        except Exception:
            return
        finally:
            try:
                self._tool_tree_updating = False
            except Exception:
                pass

    def _load_tool_groups(self) -> None:
        def _ok(res: Dict[str, Any]) -> None:
            tgs = res.get("tool_groups") if isinstance(res.get("tool_groups"), list) else []

            tree = getattr(self, "_tools_tree", None)
            if tree is None:
                return

            try:
                tree.clear()
            except Exception:
                pass

            for tg in tgs:
                if not isinstance(tg, dict):
                    continue
                gid = str(tg.get("id") or "").strip().lower()
                if not gid:
                    continue

                disp = str(tg.get("display_name") or gid).strip() or gid
                tools = tg.get("tools") if isinstance(tg.get("tools"), list) else []

                def _trunc100(s: str) -> str:
                    s2 = " ".join(str(s or "").replace("\n", " ").split())
                    if len(s2) <= 100:
                        return s2
                    return s2[:97] + "..."

                tool_items: List[Dict[str, str]] = []
                for it in tools:
                    if isinstance(it, str) and it.strip():
                        tool_items.append({"name": it.strip(), "description": ""})
                    elif isinstance(it, dict):
                        nm = str(it.get("name") or "").strip()
                        if not nm:
                            continue
                        ds = str(it.get("description") or "")
                        tool_items.append({"name": nm, "description": ds})

                parent = QTreeWidgetItem([disp, ""]) 
                try:
                    parent.setToolTip(0, gid)
                except Exception:
                    pass
                parent.setData(0, Qt.ItemDataRole.UserRole, gid)
                try:
                    flags = parent.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                    tri = getattr(Qt.ItemFlag, "ItemIsAutoTristate", None) or getattr(Qt.ItemFlag, "ItemIsTristate", None)
                    if tri is not None:
                        flags |= tri
                    parent.setFlags(flags)
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                except Exception:
                    pass

                try:
                    tree.addTopLevelItem(parent)
                except Exception:
                    pass

                for itm in tool_items:
                    tn = str(itm.get("name") or "").strip()
                    if not tn:
                        continue
                    desc = str(itm.get("description") or "")
                    ch = QTreeWidgetItem([tn, _trunc100(desc)])
                    ch.setData(0, Qt.ItemDataRole.UserRole, tn)
                    try:
                        ch.setToolTip(0, tn)
                    except Exception:
                        pass
                    try:
                        if desc:
                            ch.setToolTip(0, desc)
                            ch.setToolTip(1, desc)
                    except Exception:
                        pass
                    try:
                        ch.setFlags(ch.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                        ch.setCheckState(0, Qt.CheckState.Unchecked)
                    except Exception:
                        pass
                    try:
                        parent.addChild(ch)
                    except Exception:
                        pass

                try:
                    parent.setExpanded(False)
                except Exception:
                    pass

            # Apply pending selection (if we loaded an agent before tool groups arrived).
            if isinstance(self._pending_tool_selection, dict):
                try:
                    self._apply_tool_selection_to_tree(self._pending_tool_selection)
                except Exception:
                    pass

        self._request("agents.cmd.tool_groups.list", {}, _ok)


    # -----------------
    # Model settings (defaults + allowed values come from backend)
    # -----------------

    def _load_model_ui_spec(self) -> None:
        def _ok(res: Dict[str, Any]) -> None:
            spec = res.get("spec") if isinstance(res.get("spec"), dict) else {}
            self._model_ui_spec = spec
            self._apply_model_ui_spec_options()
            self._apply_model_ui_spec_ranges()
            # If we're creating a new agent, apply defaults.
            if bool(getattr(self, "_creating_new", False)):
                self._apply_model_defaults()

        self._request("agents.cmd.model_ui_spec", {}, _ok)

    def _apply_model_ui_spec_options(self) -> None:
        opts = self._model_ui_spec.get("options") if isinstance(self._model_ui_spec, dict) else {}
        opts = opts if isinstance(opts, dict) else {}

        def _fill(cb: QComboBox, values: Any) -> None:
            vals = values if isinstance(values, list) else []
            try:
                cur = cb.currentText()
            except Exception:
                cur = ""
            try:
                cb.blockSignals(True)
                cb.clear()
                for v in vals:
                    if isinstance(v, str) and v:
                        cb.addItem(v)
                if cur:
                    idx = cb.findText(cur)
                    if idx >= 0:
                        cb.setCurrentIndex(idx)
            finally:
                try:
                    cb.blockSignals(False)
                except Exception:
                    pass

        _fill(self.model_name_cb, opts.get("model_name"))
        _fill(self.reasoning_effort_cb, opts.get("reasoning_effort"))
        _fill(self.reasoning_summary_cb, opts.get("reasoning_summary"))
        _fill(self.text_verbosity_cb, opts.get("text_verbosity"))

    def _apply_model_ui_spec_ranges(self) -> None:
        """Apply slider ranges + validators from backend-provided spec."""
        r = self._model_ui_spec.get("ranges") if isinstance(self._model_ui_spec, dict) else {}
        r = r if isinstance(r, dict) else {}

        # temperature
        tr = r.get("temperature") if isinstance(r.get("temperature"), dict) else {}
        try:
            mn = float(tr.get("min", 0.1) or 0.1)
            mx = float(tr.get("max", 1.0) or 1.0)
            step = float(tr.get("step", 0.1) or 0.1)
            mn = max(0.0, mn)
            mx = max(mn, mx)
            scale = int(round(1.0 / step)) if step > 0 else 10
            scale = max(1, scale)
            self._temp_scale = scale
            self.temp_slider.setMinimum(int(round(mn * scale)))
            self.temp_slider.setMaximum(int(round(mx * scale)))
            try:
                self.temp_value.setValidator(QDoubleValidator(mn, mx, 3))
            except Exception:
                pass
        except Exception:
            pass

        # max_turns
        mr = r.get("max_turns") if isinstance(r.get("max_turns"), dict) else {}
        try:
            mn2 = int(mr.get("min", 1) or 1)
            mx2 = int(mr.get("max", 1024) or 1024)
            mx2 = max(mn2, mx2)
            self.max_turns_slider.setMinimum(mn2)
            self.max_turns_slider.setMaximum(mx2)
            try:
                self.max_turns_value.setValidator(QIntValidator(mn2, mx2))
            except Exception:
                pass
        except Exception:
            pass

    def _apply_model_defaults(self) -> None:
        defaults = self._model_ui_spec.get("defaults") if isinstance(self._model_ui_spec, dict) else {}
        defaults = defaults if isinstance(defaults, dict) else {}
        if not defaults:
            return
        self._set_model_controls(defaults)

    def _set_model_controls(self, model: Any) -> None:
        m = model if isinstance(model, dict) else {}

        # name
        nm = m.get("name")
        if isinstance(nm, str) and nm:
            try:
                if self.model_name_cb.findText(nm) >= 0:
                    self.model_name_cb.setCurrentText(nm)
            except Exception:
                pass

        # ranges
        r = self._model_ui_spec.get("ranges") if isinstance(self._model_ui_spec, dict) else {}
        r = r if isinstance(r, dict) else {}
        tr = r.get("temperature") if isinstance(r.get("temperature"), dict) else {}
        mr = r.get("max_turns") if isinstance(r.get("max_turns"), dict) else {}

        # temperature
        try:
            t = float(m.get("temperature", 1.0))
        except Exception:
            t = 1.0
        try:
            mn = float(tr.get("min", 0.1) or 0.1)
            mx = float(tr.get("max", 1.0) or 1.0)
        except Exception:
            mn, mx = 0.1, 1.0
        t = max(mn, min(mx, t))
        scale = int(getattr(self, "_temp_scale", 10) or 10)
        scale = max(1, scale)
        try:
            self.temp_slider.setValue(int(round(t * scale)))
        except Exception:
            pass

        # max_turns
        try:
            mt = int(m.get("max_turns", 256))
        except Exception:
            mt = 256
        try:
            mn2 = int(mr.get("min", 1) or 1)
            mx2 = int(mr.get("max", 1024) or 1024)
        except Exception:
            mn2, mx2 = 1, 1024
        mt = max(mn2, min(mx2, mt))
        try:
            self.max_turns_slider.setValue(int(mt))
        except Exception:
            pass

        # reasoning effort/summary/verbosity
        reff = m.get("reasoning_effort")
        if isinstance(reff, str) and reff:
            try:
                if self.reasoning_effort_cb.findText(reff) >= 0:
                    self.reasoning_effort_cb.setCurrentText(reff)
            except Exception:
                pass

        rsum = m.get("reasoning_summary")
        if isinstance(rsum, str) and rsum:
            try:
                if self.reasoning_summary_cb.findText(rsum) >= 0:
                    self.reasoning_summary_cb.setCurrentText(rsum)
            except Exception:
                pass

        tv = m.get("text_verbosity")
        if isinstance(tv, str) and tv:
            try:
                if self.text_verbosity_cb.findText(tv) >= 0:
                    self.text_verbosity_cb.setCurrentText(tv)
            except Exception:
                pass

        # stream is always on (UI requirement)
        try:
            self.stream_cb.setChecked(True)
        except Exception:
            pass

    def _collect_model(self) -> Dict[str, Any]:
        try:
            name = str(self.model_name_cb.currentText() or "").strip()
        except Exception:
            name = ""

        scale = int(getattr(self, "_temp_scale", 10) or 10)
        scale = max(1, scale)
        try:
            temp = float(int(self.temp_slider.value()) / float(scale))
        except Exception:
            temp = 1.0

        try:
            mt = int(self.max_turns_slider.value())
        except Exception:
            mt = 256

        try:
            reff = str(self.reasoning_effort_cb.currentText() or "").strip()
        except Exception:
            reff = ""
        try:
            rsum = str(self.reasoning_summary_cb.currentText() or "").strip()
        except Exception:
            rsum = ""
        try:
            tv = str(self.text_verbosity_cb.currentText() or "").strip()
        except Exception:
            tv = ""

        # Clamp
        r = self._model_ui_spec.get("ranges") if isinstance(self._model_ui_spec, dict) else {}
        r = r if isinstance(r, dict) else {}
        tr = r.get("temperature") if isinstance(r.get("temperature"), dict) else {}
        mr = r.get("max_turns") if isinstance(r.get("max_turns"), dict) else {}
        try:
            mn = float(tr.get("min", 0.1) or 0.1)
            mx = float(tr.get("max", 1.0) or 1.0)
        except Exception:
            mn, mx = 0.1, 1.0
        temp = max(mn, min(mx, temp))

        try:
            mn2 = int(mr.get("min", 1) or 1)
            mx2 = int(mr.get("max", 1024) or 1024)
        except Exception:
            mn2, mx2 = 1, 1024
        mt = max(mn2, min(mx2, mt))

        out = {
            "name": name,
            "temperature": float(temp),
            "max_turns": int(mt),
            "reasoning_effort": reff,
            "reasoning_summary": rsum,
            "text_verbosity": tv,
            "stream": True,
        }
        return {k: v for k, v in out.items() if v is not None and (not (isinstance(v, str) and not v))}
    # -----------------
    # UI actions
    # -----------------

    def refresh_list(self) -> None:
        def _ok(res: Dict[str, Any]) -> None:
            agents = res.get("agents") if isinstance(res.get("agents"), list) else []
            self._agents_meta = agents

            self.list.blockSignals(True)
            try:
                self.list.clear()

                # One-shot subagent template entry
                try:
                    it0 = QListWidgetItem("One-shot template   (config/one_shot.yaml)")
                    it0.setData(Qt.ItemDataRole.UserRole, "__one_shot__")
                    it0.setToolTip("Template used for run_subagent(mode='run') when subagent_name is not a configured agent")
                    self.list.addItem(it0)
                except Exception:
                    pass

                for a in agents:
                    if not isinstance(a, dict):
                        continue
                    aid = str(a.get("id") or "")
                    nm = str(a.get("display_name") or aid)
                    role = str(a.get("role") or "subagent")
                    item = QListWidgetItem(f"{nm}   ({aid})")
                    item.setData(Qt.ItemDataRole.UserRole, aid)
                    if role in ("primary", "family"):
                        item.setToolTip("Primary/family (cannot delete)")
                    self.list.addItem(item)
            finally:
                self.list.blockSignals(False)

            # Preserve selection if possible
            if self._current_agent_id:
                for i in range(self.list.count()):
                    it = self.list.item(i)
                    if it and it.data(Qt.ItemDataRole.UserRole) == self._current_agent_id:
                        self.list.setCurrentItem(it)
                        break

        self._request("agents.cmd.list", {}, _ok)

    def _on_selected(self) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        aid = it.data(Qt.ItemDataRole.UserRole)
        aid = str(aid or "").strip()
        if not aid:
            return
        if aid == "__one_shot__":
            self._load_one_shot_template()
        else:
            self._load_agent(aid)

    def _new_agent(self) -> None:
        self._creating_new = True
        self._current_agent_id = None
        self._current_is_one_shot = False
        self._current_role = "subagent"
        self.id_in.setEnabled(True)
        self.id_in.setText("")
        self.name_in.setText("")
        try:
            self.desc_in.setPlainText("")
        except Exception:
            pass
        # tools
        self._pending_tool_selection = None
        try:
            tree = getattr(self, "_tools_tree", None)
            if tree is not None:
                for i in range(tree.topLevelItemCount()):
                    parent = tree.topLevelItem(i)
                    if parent is None:
                        continue
                    for j in range(parent.childCount()):
                        ch = parent.child(j)
                        if ch is not None:
                            ch.setCheckState(0, Qt.CheckState.Unchecked)
        except Exception:
            pass
        self.prompt_edit.setPlainText("")

        # Model defaults
        try:
            self._apply_model_defaults()
        except Exception:
            pass

        self._apply_protection(False)

    def _apply_protection(self, protected: bool) -> None:
        """Protected agents are editable, but cannot be deleted."""
        try:
            self.del_btn.setEnabled(not protected)
        except Exception:
            pass

        # Duplicate is fine (it creates a new one).
        try:
            self.dup_btn.setEnabled(self._current_agent_id is not None)
        except Exception:
            pass

    def _load_agent(self, agent_id: str) -> None:
        def _ok(res: Dict[str, Any]) -> None:
            spec = res.get("agent") if isinstance(res.get("agent"), dict) else {}
            self._creating_new = False
            self._current_agent_id = str(spec.get("id") or agent_id)
            self._current_is_one_shot = False

            self.id_in.setEnabled(False)
            self.id_in.setText(self._current_agent_id)
            self.name_in.setText(str(spec.get("display_name") or ""))
            try:
                self.desc_in.setPlainText(str(spec.get("description") or ""))
            except Exception:
                pass

            # tools selection (group -> [tool...])
            sel: Dict[str, List[str]] = {}
            tools = spec.get("tools") if isinstance(spec.get("tools"), dict) else {}
            gsel = tools.get("groups")
            if isinstance(gsel, dict):
                for gid, tl in gsel.items():
                    g = str(gid).strip().lower()
                    if not g:
                        continue
                    names = [str(x).strip() for x in (tl or []) if isinstance(x, str) and str(x).strip()]
                    if names:
                        sel[g] = names

            # Tool groups can load async; store pending selection and apply best-effort.
            self._pending_tool_selection = dict(sel)
            try:
                self._apply_tool_selection_to_tree(sel)
            except Exception:
                pass

            try:
                self.prompt_edit.setPlainText(str(spec.get("prompt") or ""))
            except Exception:
                pass


            try:
                self._apply_model_ui_spec_options()
            except Exception:
                pass
            # Role + model
            try:
                role = str(spec.get("role") or "subagent")
            except Exception:
                role = "subagent"
            self._current_role = role.strip() or "subagent"

            try:
                self._set_model_controls(spec.get("model"))
            except Exception:
                pass

            self._apply_protection(self._current_role in ("primary", "family"))
    
        self._request("agents.cmd.get", {"agent_id": str(agent_id)}, _ok)

    def _load_one_shot_template(self) -> None:
        def _ok(res: Dict[str, Any]) -> None:
            spec = res.get("agent") if isinstance(res.get("agent"), dict) else {}
            self._creating_new = False
            self._current_agent_id = "__one_shot__"
            self._current_is_one_shot = True
            self._current_role = "template"

            # Fixed id, but keep display_name editable.
            self.id_in.setEnabled(False)
            self.id_in.setText("one_shot")
            self.name_in.setText(str(spec.get("display_name") or "One-shot template"))
            try:
                self.desc_in.setPlainText(str(spec.get("description") or ""))
            except Exception:
                pass

            # tools selection (group -> [tool...])
            sel: Dict[str, List[str]] = {}
            tools = spec.get("tools") if isinstance(spec.get("tools"), dict) else {}
            gsel = tools.get("groups")
            if isinstance(gsel, dict):
                for gid, tl in gsel.items():
                    g = str(gid).strip().lower()
                    if not g:
                        continue
                    names = [str(x).strip() for x in (tl or []) if isinstance(x, str) and str(x).strip()]
                    if names:
                        sel[g] = names

            self._pending_tool_selection = dict(sel)
            try:
                self._apply_tool_selection_to_tree(sel)
            except Exception:
                pass

            try:
                self.prompt_edit.setPlainText(str(spec.get("prompt") or ""))
            except Exception:
                pass

            try:
                self._apply_model_ui_spec_options()
            except Exception:
                pass
            try:
                self._set_model_controls(spec.get("model"))
            except Exception:
                pass

            # Template cannot be deleted/duplicated.
            try:
                self.del_btn.setEnabled(False)
                self.dup_btn.setEnabled(False)
            except Exception:
                pass

        self._request("agents.cmd.one_shot.get", {}, _ok)

    def _collect_tool_selection(self) -> Dict[str, List[str]]:
        """Return group->selected tools for saving."""
        out: Dict[str, List[str]] = {}
        tree = getattr(self, "_tools_tree", None)
        if tree is None:
            return out

        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                if parent is None:
                    continue
                gid = parent.data(0, Qt.ItemDataRole.UserRole)
                gid = str(gid or parent.text(0) or "").strip().lower()
                if not gid:
                    continue

                selected: List[str] = []
                for j in range(parent.childCount()):
                    ch = parent.child(j)
                    if ch is None:
                        continue
                    if ch.checkState(0) != Qt.CheckState.Checked:
                        continue
                    tn = ch.data(0, Qt.ItemDataRole.UserRole)
                    tn = str(tn or ch.text(0) or "").strip()
                    if tn:
                        selected.append(tn)

                if selected:
                    out[gid] = selected
        except Exception:
            return out

        return out

    def _save(self) -> None:
        aid = (self.id_in.text() or "").strip()
        if not aid:
            QMessageBox.warning(self, "Agents Studio", "Agent ID is required")
            return
        name = (self.name_in.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Agents Studio", "Display name is required")
            return

        payload = {
            "agent": {
                "id": aid,
                "display_name": name,
                "description": (self.desc_in.toPlainText() or "").strip() or None,
                "role": str(getattr(self, "_current_role", "subagent") or "subagent"),
                "model": self._collect_model(),
                "tools": {"groups": self._collect_tool_selection()},
                "prompt": self.prompt_edit.toPlainText() or "",
            }
        }

        def _ok(res: Dict[str, Any]) -> None:
            saved = res.get("agent_id")
            if bool(getattr(self, "_current_is_one_shot", False)):
                self._current_agent_id = "__one_shot__"
            else:
                self._current_agent_id = str(saved or aid)
            self._creating_new = False
            self.refresh_list()

            self._show_toast("Template saved" if bool(getattr(self, "_current_is_one_shot", False)) else "Agent saved")

        topic = "agents.cmd.one_shot.save" if bool(getattr(self, "_current_is_one_shot", False)) else "agents.cmd.save"
        self._request(topic, payload, _ok)

    def _delete(self) -> None:
        if bool(getattr(self, "_current_is_one_shot", False)):
            return
        if not self._current_agent_id:
            return
        aid = self._current_agent_id
        if QMessageBox.question(
            self,
            "Delete agent",
            f"Delete '{aid}'? This removes its Config/agents definition.\n\n(This does not delete old session logs that already exist.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        def _ok(res: Dict[str, Any]) -> None:
            self._new_agent()
            self.refresh_list()
            self._show_toast("Agent deleted")
        self._request("agents.cmd.delete", {"agent_id": str(aid)}, _ok)

    def _duplicate(self) -> None:
        if bool(getattr(self, "_current_is_one_shot", False)):
            return
        if not self._current_agent_id:
            return

        # Snapshot current fields before we clear the form.
        base_id = str(self._current_agent_id)
        base_name = (self.name_in.text() or "").strip()
        base_desc = (self.desc_in.toPlainText() or "").strip()
        base_prompt = self.prompt_edit.toPlainText() or ""
        base_tools = self._collect_tool_selection()
        base_model = self._collect_model()

        suggested = f"{base_id}_copy"

        self._new_agent()
        self.id_in.setText(suggested)
        self.name_in.setText(f"{(base_name or base_id)} Copy")
        try:
            self.desc_in.setPlainText(base_desc)
        except Exception:
            pass
        try:
            self._apply_tool_selection_to_tree(base_tools)
        except Exception:
            pass
        self.prompt_edit.setPlainText(base_prompt)

        # Preserve model settings when duplicating
        try:
            self._set_model_controls(base_model)

            self._show_toast("Agent duplicated (remember to change the ID before saving)")
        except Exception:
            pass

    def closeEvent(self, event):
        # Persist position
        try:
            from PyQt6.QtCore import QSettings

            settings = QSettings("ai-agent", "widget")
            settings.setValue("agents_studio_window_pos", self.pos())
        except Exception:
            pass
        return super().closeEvent(event)

    def _preview_prompt(self) -> None:
        """Open a read-only preview of the effective system prompt (base + tool-group chapters)."""
        is_one_shot = bool(getattr(self, "_current_is_one_shot", False))
        aid = ("one_shot" if is_one_shot else (self._current_agent_id or (self.id_in.text() or "").strip() or "")).strip()
        if not aid:
            QMessageBox.warning(self, "Agents Studio", "Select an agent first")
            return

        # Match the most likely runtime context.
        role = str(getattr(self, "_current_role", "subagent") or "subagent").strip().lower()
        context_mode = "primary" if role == "primary" else "subagent_persistent"

        def _ok(res: Dict[str, Any]) -> None:
            txt = str(res.get("prompt") or "")
            dn = str(res.get("display_name") or aid)
            cm = str(res.get("context_mode") or context_mode)

            dlg = QDialog(self)
            dlg.setWindowTitle(f"Prompt preview — {dn} ({aid})")
            dlg.setModal(False)
            dlg.resize(900, 700)

            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(12, 12, 12, 12)
            lay.setSpacing(8)

            meta = QLabel(f"Context: {cm}    |    role: {role}")
            meta.setStyleSheet("color: #b5b5b5;")
            lay.addWidget(meta)

            sel = QLabel("")
            sel.setStyleSheet("color: #b5b5b5;")
            lay.addWidget(sel)

            edit = QPlainTextEdit()
            edit.setReadOnly(True)
            edit.setPlainText(txt)
            edit.setStyleSheet(
                "QPlainTextEdit { padding: 10px; font-family: Consolas, 'Courier New', monospace; font-size: 10pt; }"
            )

            total_chars = len(txt)

            def _update_sel():
                try:
                    cur = edit.textCursor()
                    s = cur.selectedText() if cur is not None else ""
                    # Qt uses U+2029 for newlines in selectedText.
                    if isinstance(s, str):
                        s = s.replace("\u2029", "\n")
                    n = len(s or "")
                except Exception:
                    n = 0
                sel.setText(f"Selected: {n} / Total: {total_chars} chars")

            try:
                edit.selectionChanged.connect(_update_sel)
            except Exception:
                pass
            _update_sel()

            lay.addWidget(edit, 1)

            close_btn = QPushButton("Close")
            close_btn.clicked.connect(dlg.close)
            lay.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

            dlg.show()

        if is_one_shot:
            self._request(
                "agents.cmd.one_shot.prompt.preview",
                {},
                _ok,
            )
        else:
            self._request(
                "agents.cmd.prompt.preview",
                {"agent_id": str(aid), "context_mode": str(context_mode)},
                _ok,
            )
