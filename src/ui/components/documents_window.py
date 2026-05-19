"""Documents/Collections management window for RAG system."""

import uuid
from typing import Optional, List, Dict, Any
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QLineEdit, QTextEdit, QFileDialog, QSplitter, QWidget,
    QProgressDialog, QAbstractItemView, QComboBox, QSpinBox,
    QGroupBox, QFormLayout, QScrollArea, QListWidget, QListWidgetItem, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent
from ...appcore.runtime_context import Runtime
from PyQt6.QtGui import QFont
from ..screen_utils import validate_window_position


# Supported document extensions
SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx"}


class ChunksViewerDialog(QDialog):
    """Dialog to view document chunks in a collection."""
    
    def __init__(self, parent=None, collection_name: str = "", chunks: List[Dict] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Chunks - {collection_name}")
        self.setModal(False)
        self.resize(800, 600)
        self.setWindowFlags(Qt.WindowType.Window)
        
        self.chunks = chunks or []
        self._setup_ui()
        self._populate_chunks()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Info label
        self.info_label = QLabel(f"Total chunks: {len(self.chunks)}")
        self.info_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self.info_label)
        
        # Chunks table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["#", "ID", "Source", "Preview"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table, stretch=1)
        
        # Full content viewer
        content_group = QGroupBox("Selected Chunk Content")
        content_layout = QVBoxLayout(content_group)
        self.content_text = QTextEdit()
        self.content_text.setReadOnly(True)
        self.content_text.setFont(QFont("Consolas", 10))
        content_layout.addWidget(self.content_text)
        layout.addWidget(content_group, stretch=1)
        
        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        self._style_button(close_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
    
    def _style_button(self, btn, accent=False):
        if accent:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #0e639c;
                    color: white;
                    border: 1px solid #0e639c;
                    border-radius: 4px;
                    padding: 6px 16px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #1177bb; }
                QPushButton:disabled { background-color: #555; color: #888; }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #3a3a3a;
                    color: #d4d4d4;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 6px 16px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #4a4a4a; }
            """)
    
    def _populate_chunks(self):
        self.table.setRowCount(len(self.chunks))
        for i, chunk in enumerate(self.chunks):
            # Index
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            # ID
            chunk_id = chunk.get("id", "N/A")
            self.table.setItem(i, 1, QTableWidgetItem(str(chunk_id)[:20]))
            # Source
            metadata = chunk.get("metadata", {})
            source = metadata.get("source", "Unknown")
            self.table.setItem(i, 2, QTableWidgetItem(source))
            # Preview
            text = chunk.get("document", chunk.get("text", ""))
            preview = text[:100].replace("\n", " ") + "..." if len(text) > 100 else text.replace("\n", " ")
            self.table.setItem(i, 3, QTableWidgetItem(preview))

        self.info_label.setText(f"Total chunks: {len(self.chunks)}")

    def _on_selection_changed(self):
        selected = self.table.selectedItems()
        if selected:
            row = selected[0].row()
            if row < len(self.chunks):
                chunk = self.chunks[row]
                text = chunk.get("document", chunk.get("text", ""))
                metadata = chunk.get("metadata", {})

                display = f"=== Chunk #{row + 1} ===\n"
                display += f"ID: {chunk.get('id', 'N/A')}\n"
                display += f"Metadata: {metadata}\n"
                display += f"\n=== Content ===\n{text}"
                self.content_text.setText(display)


class AddUrlDialog(QDialog):
    """Dialog to add a URL source (e.g., Confluence page)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add URL")
        self.setModal(True)
        self.resize(720, 170)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://confluence.yoursite.com/... ")

        self.include_children_cb = QCheckBox("Include child pages")
        self.include_children_cb.setChecked(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        label = QLabel("Confluence URL")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)
        layout.addWidget(self.url_input)
        layout.addWidget(self.include_children_cb)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        add_btn = QPushButton("Add")
        cancel_btn.clicked.connect(self.reject)
        add_btn.clicked.connect(self.accept)
        btns.addWidget(cancel_btn)
        btns.addWidget(add_btn)
        layout.addLayout(btns)

    def get_result(self) -> Dict[str, Any]:
        return {
            "url": (self.url_input.text() or "").strip(),
            "include_child_pages": bool(self.include_children_cb.isChecked()),
        }



class NewCollectionDialog(QDialog):
    """Dialog for creating a new collection with documents."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Document Collection")
        self.setModal(True)
        self.resize(600, 500)
        
        self._selected_files: List[Any] = []
        self._progress_dialog: Optional[QProgressDialog] = None
        
        self._bus_unsub_reply = None
        self._bus_unsub_progress = None
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)
        
        # Title
        title = QLabel("Create New Collection")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)
        
        # Collection info group
        info_group = QGroupBox("Collection Info")
        info_layout = QFormLayout(info_group)
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., my_documents")
        info_layout.addRow("Name:", self.name_input)
        
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("e.g., Documentation for project X")
        info_layout.addRow("Description:", self.desc_input)
        
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("e.g., docs, technical (comma-separated)")
        info_layout.addRow("Tags:", self.tags_input)
        
        layout.addWidget(info_group)
        
        # Chunking settings group
        chunk_group = QGroupBox("Chunking Settings")
        chunk_layout = QFormLayout(chunk_group)
        
        self.chunk_size_spin = QSpinBox()
        self.chunk_size_spin.setRange(100, 10000)
        self.chunk_size_spin.setValue(1000)
        self.chunk_size_spin.setSuffix(" chars")
        chunk_layout.addRow("Chunk Size:", self.chunk_size_spin)
        
        self.chunk_overlap_spin = QSpinBox()
        self.chunk_overlap_spin.setRange(0, 2000)
        self.chunk_overlap_spin.setValue(200)
        self.chunk_overlap_spin.setSuffix(" chars")
        chunk_layout.addRow("Chunk Overlap:", self.chunk_overlap_spin)
        
        layout.addWidget(chunk_group)
        
        # Sources group
        docs_group = QGroupBox("Sources")
        docs_layout = QVBoxLayout(docs_group)
        
        docs_info = QLabel("Supported: .txt, .pdf, .docx, Confluence URLs")
        docs_info.setStyleSheet("color: #888; font-size: 11px;")
        docs_layout.addWidget(docs_info)
        
        # Sources list
        self.files_list = QListWidget()
        self.files_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files_list.installEventFilter(self)
        self.files_list.setMaximumHeight(120)
        self.files_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.files_list.itemSelectionChanged.connect(self._on_sources_selection_changed)
        self.files_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.files_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        docs_layout.addWidget(self.files_list)
        
        # Add files button
        files_btn_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("📁 Add Files...")
        self.add_files_btn.clicked.connect(self._on_add_files)
        self._style_button(self.add_files_btn)
        files_btn_layout.addWidget(self.add_files_btn)

        self.add_url_btn = QPushButton("🔗 Add URL...")
        self.add_url_btn.clicked.connect(self._on_add_url)
        self._style_button(self.add_url_btn)
        files_btn_layout.addWidget(self.add_url_btn)

        self.remove_selected_btn = QPushButton("Remove Selected")
        self.remove_selected_btn.clicked.connect(self._on_remove_selected)
        self._style_button(self.remove_selected_btn)
        self.remove_selected_btn.setEnabled(False)
        files_btn_layout.addWidget(self.remove_selected_btn)
        
        self.clear_files_btn = QPushButton("Clear")
        self.clear_files_btn.clicked.connect(self._on_clear_files)
        self._style_button(self.clear_files_btn)
        files_btn_layout.addWidget(self.clear_files_btn)
        files_btn_layout.addStretch()
        docs_layout.addLayout(files_btn_layout)
        
        layout.addWidget(docs_group)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self._style_button(self.cancel_btn)
        btn_layout.addWidget(self.cancel_btn)
        
        self.create_btn = QPushButton("Create Collection")
        self.create_btn.clicked.connect(self._on_create)
        self._style_button(self.create_btn, accent=True)
        btn_layout.addWidget(self.create_btn)
        
        layout.addLayout(btn_layout)
    
    def _style_button(self, btn, accent=False):
        if accent:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #0e639c;
                    color: white;
                    border: 1px solid #0e639c;
                    border-radius: 4px;
                    padding: 6px 16px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #1177bb; }
                QPushButton:disabled { background-color: #555; color: #888; }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #3a3a3a;
                    color: #d4d4d4;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 6px 16px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #4a4a4a; }
            """)
    
    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Documents",
            "",
            "Documents (*.txt *.pdf *.docx);;Text Files (*.txt);;PDF Files (*.pdf);;Word Documents (*.docx)"
        )
        if files:
            for f in files:
                if f not in self._selected_files:
                    ext = Path(f).suffix.lower()
                    if ext in SUPPORTED_EXTENSIONS:
                        self._selected_files.append(f)
            self._update_files_display()

    def _source_key(self, src: Any) -> str:
        if isinstance(src, dict):
            url = str(src.get("url") or "").strip()
            return f"URL::{url}"
        s = str(src or "").strip()
        if self._is_url(s):
            return f"URL::{s}"
        return f"FILE::{s}"

    def _source_tooltip(self, src: Any) -> str:
        if isinstance(src, dict):
            url = str(src.get("url") or "").strip()
            child = bool(src.get("include_child_pages", False))
            return url + ("\n(include child pages)" if child else "")
        return str(src or "").strip()

    def _on_add_url(self):
        dlg = AddUrlDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        payload = dlg.get_result()
        url = (payload.get("url") or "").strip()
        if not url:
            return
        if not self._is_url(url):
            QMessageBox.warning(self, "Validation Error", "URL must start with http:// or https://")
            return

        # Treat URL as a dict source so we can carry flags (e.g., include_child_pages)
        src_obj = {"url": url, "include_child_pages": bool(payload.get("include_child_pages", False))}

        # If URL already exists, update its flags instead of duplicating
        replaced = False
        for i, existing in enumerate(self._selected_files):
            if isinstance(existing, dict) and str(existing.get("url", "")).strip() == url:
                self._selected_files[i] = src_obj
                replaced = True
                break
            if isinstance(existing, str) and existing.strip() == url:
                self._selected_files[i] = src_obj
                replaced = True
                break

        if not replaced:
            self._selected_files.append(src_obj)

        self._update_files_display()

    def _on_sources_selection_changed(self):
        try:
            has_sel = bool(self.files_list.selectedItems())
        except Exception:
            has_sel = False
        if hasattr(self, "remove_selected_btn"):
            self.remove_selected_btn.setEnabled(has_sel)

    def _on_remove_selected(self):
        items = self.files_list.selectedItems() if self.files_list else []
        if not items:
            return

        to_remove_keys = set()
        for it in items:
            key = it.data(Qt.ItemDataRole.UserRole)
            if key:
                to_remove_keys.add(str(key))

        if not to_remove_keys:
            return

        self._selected_files = [s for s in self._selected_files if self._source_key(s) not in to_remove_keys]
        self._update_files_display()

    def _on_clear_files(self):
        self._selected_files = []
        self._update_files_display()
        self._on_sources_selection_changed()

    def _is_url(self, value: str) -> bool:
        v = (value or "").strip().lower()
        return v.startswith("http://") or v.startswith("https://")

    def _format_source_label(self, value: Any) -> str:
        if isinstance(value, dict):
            url = str(value.get("url") or "").strip()
            child = bool(value.get("include_child_pages", False))
            return f"URL: {url}" + (" (children)" if child else "")

        v = str(value or "").strip()
        if self._is_url(v):
            return f"URL: {v}"
        return Path(v).name

    def _update_files_display(self):
        self.files_list.clear()
        for src in self._selected_files:
            label = self._format_source_label(src)
            item = QListWidgetItem(f"• {label}")

            # Tooltip shows full thing
            tip = self._source_tooltip(src)
            item.setToolTip(tip)

            # Store a stable key so we can remove reliably (works for dict sources too)
            item.setData(Qt.ItemDataRole.UserRole, self._source_key(src))

            self.files_list.addItem(item)

        self._on_sources_selection_changed()
    
    def eventFilter(self, obj, event):
        if obj is getattr(self, "files_list", None) and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._on_remove_selected()
                return True
        return super().eventFilter(obj, event)

    def _on_create(self):
        # Validate inputs
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Collection name is required.")
            return

        # Validate name format (alphanumeric and underscores only)
        if not all(c.isalnum() or c in "_-" for c in name):
            QMessageBox.warning(
                self,
                "Validation Error",
                "Collection name can only contain letters, numbers, underscores, and hyphens.",
            )
            return

        if not self._selected_files:
            QMessageBox.warning(self, "Validation Error", "Please add at least one file or URL.")
            return

        # Disable UI during creation
        self.create_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)

        # Show progress
        self._progress_dialog = QProgressDialog(
            "Creating collection and processing documents...",
            None,  # No cancel button
            0,
            0,
            self,
        )
        self._progress_dialog.setWindowTitle("Processing")
        self._progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress_dialog.show()

        # Prepare data
        description = self.desc_input.text().strip()
        tags_text = self.tags_input.text().strip()
        tags = [t.strip() for t in tags_text.split(",") if t.strip()] if tags_text else []
        chunk_size = self.chunk_size_spin.value()
        chunk_overlap = self.chunk_overlap_spin.value()

        # Event-bus command (UI -> app): create collection + ingest files
        bus = Runtime.get_event_bus()
        reply_topic = f"documents.ui.reply.create_collection.{uuid.uuid4()}"
        progress_topic = f"documents.ui.progress.create_collection.{uuid.uuid4()}"

        self._bus_unsub_reply = None
        self._bus_unsub_progress = None

        def _cleanup_bus_subs():
            try:
                if self._bus_unsub_reply:
                    self._bus_unsub_reply()
            except Exception:
                pass
            try:
                if self._bus_unsub_progress:
                    self._bus_unsub_progress()
            except Exception:
                pass
            self._bus_unsub_reply = None
            self._bus_unsub_progress = None

        def _on_progress(ev):
            payload = getattr(ev, "payload", {}) or {}
            msg = payload.get("message") if isinstance(payload, dict) else None
            if msg and self._progress_dialog:
                self._progress_dialog.setLabelText(str(msg))

        def _on_reply(ev):
            _cleanup_bus_subs()
            payload = getattr(ev, "payload", {}) or {}
            if isinstance(payload, dict):
                self._on_create_finished(payload)
            else:
                self._on_create_finished({"status": "error", "message": "Unexpected reply payload"})

        self._bus_unsub_progress = bus.subscribe(progress_topic, _on_progress)
        self._bus_unsub_reply = bus.subscribe(reply_topic, _on_reply)

        bus.publish(
            "documents.cmd.create_collection_from_files",
            {
                "reply_topic": reply_topic,
                "progress_topic": progress_topic,
                "name": name,
                "description": description,
                "tags": tags,
                "file_paths": list(self._selected_files),
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
            },
        )
    
    
    def _on_create_finished(self, result: Dict[str, Any]):
        if self._progress_dialog:
            self._progress_dialog.close()
            self._progress_dialog = None
        
        self.create_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        
        if result.get("status") == "success":
            QMessageBox.information(
                self,
                "Success",
                f"Collection '{result.get('collection')}' created successfully!\n"
                f"Files processed: {result.get('files_processed', 0)}\n"
                f"Chunks added: {result.get('chunks_added', 0)}"
            )
            self.accept()
        else:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to create collection:\n{result.get('message', 'Unknown error')}"
            )
    
    def _on_create_error(self, error_msg: str):
        if self._progress_dialog:
            self._progress_dialog.close()
            self._progress_dialog = None
        
        self.create_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        
        QMessageBox.critical(self, "Error", f"An error occurred:\n{error_msg}")


class DocumentsWindow(QDialog):
    """Main window for managing document collections."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Document Collections")
        self.setModal(False)
        self.resize(900, 600)
        self.setWindowFlags(Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        
        # Restore last position if available
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        saved_pos = settings.value("documents_window_pos", None)
        validated_pos = validate_window_position(saved_pos, 900, 600)
        if validated_pos is not None:
            x, y = validated_pos
            self.move(x, y)
        
        self._collections: List[Dict] = []
        self._chunks_dialog: Optional[ChunksViewerDialog] = None
        
        # Event-bus driven refresh (keeps UI in sync when collections change)
        self._needs_refresh = False
        self._refresh_pending = False
        self._bus_unsub = Runtime.get_event_bus().subscribe(
            "vectordb.collections.changed",
            self._on_collections_changed_event,
        )
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        
        # Title
        title_layout = QHBoxLayout()
        title = QLabel("Document Collections")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_layout.addWidget(title)
        title_layout.addStretch()
        
        # Refresh button
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self.refresh_collections)
        self._style_button(self.refresh_btn)
        title_layout.addWidget(self.refresh_btn)
        
        layout.addLayout(title_layout)
        
        # Description
        desc = QLabel("Manage your document collections for RAG (Retrieval-Augmented Generation).")
        desc.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(desc)
        
        # Collections table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Name", "Description", "Chunks", "Created", "Tags"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table, stretch=1)
        
        # Action buttons
        btn_layout = QHBoxLayout()
        
        self.new_btn = QPushButton("➕ New Collection")
        self.new_btn.clicked.connect(self._on_new_collection)
        self._style_button(self.new_btn, accent=True)
        btn_layout.addWidget(self.new_btn)
        
        self.view_chunks_btn = QPushButton("👁️ View Chunks")
        self.view_chunks_btn.clicked.connect(self._on_view_chunks)
        self.view_chunks_btn.setEnabled(False)
        self._style_button(self.view_chunks_btn)
        btn_layout.addWidget(self.view_chunks_btn)

        self.edit_btn = QPushButton("✏️ Edit")
        self.edit_btn.clicked.connect(self._on_edit_collection)
        self.edit_btn.setEnabled(False)
        self._style_button(self.edit_btn)
        btn_layout.addWidget(self.edit_btn)
        
        self.delete_btn = QPushButton("🗑️ Delete")
        self.delete_btn.clicked.connect(self._on_delete_collection)
        self.delete_btn.setEnabled(False)
        self._style_button(self.delete_btn)
        btn_layout.addWidget(self.delete_btn)
        
        btn_layout.addStretch()
        
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        self._style_button(self.close_btn)
        btn_layout.addWidget(self.close_btn)
        
        layout.addLayout(btn_layout)
        
        # Status bar
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.status_label)
    
    def _style_button(self, btn, accent=False):
        if accent:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #0e639c;
                    color: white;
                    border: 1px solid #0e639c;
                    border-radius: 4px;
                    padding: 6px 16px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #1177bb; }
                QPushButton:disabled { background-color: #555; color: #888; }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #3a3a3a;
                    color: #d4d4d4;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 6px 16px;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #4a4a4a; }
                QPushButton:disabled { background-color: #2a2a2a; color: #666; }
            """)
    
    def showEvent(self, event):
        """Called when window is shown."""
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        saved_pos = settings.value("documents_window_pos", None)
        validated_pos = validate_window_position(saved_pos, 900, 600)
        if validated_pos is not None:
            x, y = validated_pos
            self.move(x, y)

        super().showEvent(event)

        # If collections changed while we were hidden, refresh now.
        if getattr(self, "_needs_refresh", False):
            self._needs_refresh = False
            QTimer.singleShot(0, self.refresh_collections)
    
    def refresh_collections(self):
        """Refresh the collections list (event-bus request)."""
        self._set_status("Loading collections...")
        self.refresh_btn.setEnabled(False)

        bus = Runtime.get_event_bus()
        reply_topic = f"documents.ui.reply.list_collections.{uuid.uuid4()}"

        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            self.refresh_btn.setEnabled(True)
            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict):
                self._set_status("Error: Unexpected reply payload")
                return

            if payload.get("status") != "success":
                self._set_status(f"Error: {payload.get('message', 'Unknown error')}")
                return

            collections = payload.get("collections", [])
            if not isinstance(collections, list):
                collections = []

            self._collections = [c for c in collections if isinstance(c, dict)]
            self._populate_table()
            self._set_status(f"Loaded {len(self._collections)} collection(s)")

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish("documents.cmd.list_collections", {"reply_topic": reply_topic})
    

    def _on_collections_changed_event(self, event):
        """Handle vectordb.collections.changed events (delivered on UI thread via bus.pump())."""
        try:
            if not self.isVisible():
                self._needs_refresh = True
                return

            # Debounce bursts (create + add docs + metadata update etc.)
            if self._refresh_pending:
                return
            self._refresh_pending = True
            QTimer.singleShot(150, self._refresh_from_bus)
        except Exception:
            # Never let event handling crash the window.
            pass

    def _refresh_from_bus(self):
        self._refresh_pending = False
        if self.isVisible():
            self.refresh_collections()
        else:
            self._needs_refresh = True

    
    def _populate_table(self):
        self.table.setRowCount(len(self._collections))
        
        for i, col in enumerate(self._collections):
            # Name
            name_item = QTableWidgetItem(col.get("name", ""))
            self.table.setItem(i, 0, name_item)
            
            # Description
            desc_item = QTableWidgetItem(col.get("description", ""))
            self.table.setItem(i, 1, desc_item)
            
            # Chunks count
            count_item = QTableWidgetItem(str(col.get("count", 0)))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 2, count_item)
            
            # Created date
            created = col.get("created_at", "")
            if created:
                # Format ISO date
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    created = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass
            created_item = QTableWidgetItem(created)
            self.table.setItem(i, 3, created_item)
            
            # Tags
            tags = col.get("tags", [])
            tags_str = ", ".join(tags) if tags else ""
            tags_item = QTableWidgetItem(tags_str)
            self.table.setItem(i, 4, tags_item)
        
        self._on_selection_changed()
    
    def _on_selection_changed(self):
        has_selection = len(self.table.selectedItems()) > 0
        self.view_chunks_btn.setEnabled(has_selection)
        self.edit_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)
    
    def _get_selected_collection_name(self) -> Optional[str]:
        selected = self.table.selectedItems()
        if selected:
            row = selected[0].row()
            if row < len(self._collections):
                return self._collections[row].get("name")
        return None
    
    def _on_new_collection(self):
        dialog = NewCollectionDialog(self)
        dialog.exec()
    
    def _on_view_chunks(self):
        collection_name = self._get_selected_collection_name()
        if not collection_name:
            return

        self._set_status(f"Loading chunks for '{collection_name}'...")
        self.view_chunks_btn.setEnabled(False)
        self.edit_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)

        bus = Runtime.get_event_bus()
        reply_topic = f"documents.ui.reply.get_chunks.{uuid.uuid4()}"

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

            self._on_chunks_loaded(collection_name, result)

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish(
            "documents.cmd.get_chunks",
            {"reply_topic": reply_topic, "collection_name": collection_name},
        )

    def _on_edit_collection(self):
        collection_name = self._get_selected_collection_name()
        if not collection_name:
            return
        
        # For simplicity, we will just show a message box here.
        # In a full implementation, you would create an EditCollectionDialog similar to NewCollectionDialog.
        QMessageBox.information(
            self,
            "Edit Collection",
            f"Editing collection '{collection_name}' is not implemented yet."
        )
    
    def _on_chunks_loaded(self, collection_name: str, result: Dict[str, Any]):
        self.view_chunks_btn.setEnabled(True)
        self.edit_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        
        if result.get("status") != "success":
            self._set_status(f"Error: {result.get('message', 'Unknown error')}")
            return
        
        documents = result.get("documents", [])
        self._set_status(f"Loaded {len(documents)} chunks from '{collection_name}'")
        
        # Show chunks dialog
        self._chunks_dialog = ChunksViewerDialog(
            self,
            collection_name=collection_name,
            chunks=documents
        )
        self._chunks_dialog.show()
    
    def _on_chunks_load_error(self, error_msg: str):
        self.view_chunks_btn.setEnabled(True)
        self.edit_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        self._set_status(f"Error loading chunks: {error_msg}")
    
    def _on_delete_collection(self):
        collection_name = self._get_selected_collection_name()
        if not collection_name:
            return

        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete collection '{collection_name}'?\n\n"
            "This will permanently remove all documents and embeddings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_status(f"Deleting '{collection_name}'...")
        self.delete_btn.setEnabled(False)

        bus = Runtime.get_event_bus()
        reply_topic = f"documents.ui.reply.delete_collection.{uuid.uuid4()}"

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
            self._on_delete_finished(result)

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish(
            "documents.cmd.delete_collection",
            {"reply_topic": reply_topic, "name": collection_name},
        )
    
    def _on_delete_finished(self, result: Dict[str, Any]):
        self.delete_btn.setEnabled(True)
        
        if result.get("status") == "success":
            self._set_status(f"Collection deleted successfully")
            self.refresh_collections()
        else:
            self._set_status(f"Error: {result.get('message', 'Unknown error')}")
    
    def _on_delete_error(self, error_msg: str):
        self.delete_btn.setEnabled(True)
        self._set_status(f"Error deleting collection: {error_msg}")
    
    def _set_status(self, message: str):
        self.status_label.setText(message)
        print(f"[DocumentsWindow] {message}")
    
    def closeEvent(self, event):
        """Save window position when closing."""
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("documents_window_pos", (self.pos().x(), self.pos().y()))
        self.hide()
        event.ignore()

    def __del__(self):
        # Best-effort unsubscribe (prevents zombie listeners)
        try:
            if getattr(self, "_bus_unsub", None):
                self._bus_unsub()
        except Exception:
            pass
    
    def hideEvent(self, event):
        """Save window position when hiding."""
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("documents_window_pos", (self.pos().x(), self.pos().y()))
        super().hideEvent(event)
