"""Documents/Collections management window for RAG system."""

import os
import traceback
from typing import Optional, List, Dict, Any
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QLineEdit, QTextEdit, QFileDialog, QSplitter, QWidget,
    QProgressDialog, QAbstractItemView, QComboBox, QSpinBox,
    QGroupBox, QFormLayout, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QFont


# Supported document extensions
SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx"}


class WorkerThread(QThread):
    """Worker thread for long-running operations to avoid UI freezes."""
    
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
    
    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result if isinstance(result, dict) else {"status": "success", "data": result})
        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))


class ChunksViewerDialog(QDialog):
    """Dialog to view document chunks in a collection."""
    
    def __init__(self, parent=None, collection_name: str = "", chunks: List[Dict] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Chunks - {collection_name}")
        self.setModal(False)
        self.resize(800, 600)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        
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


class NewCollectionDialog(QDialog):
    """Dialog for creating a new collection with documents."""
    
    collection_created = pyqtSignal(dict)  # Emits result when collection is created
    
    def __init__(self, parent=None, app=None):
        super().__init__(parent)
        self._app = app
        self.setWindowTitle("New Document Collection")
        self.setModal(True)
        self.resize(600, 500)
        
        self._selected_files: List[str] = []
        self._worker: Optional[WorkerThread] = None
        self._progress_dialog: Optional[QProgressDialog] = None
        
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
        
        # Documents group
        docs_group = QGroupBox("Documents")
        docs_layout = QVBoxLayout(docs_group)
        
        docs_info = QLabel("Supported formats: .txt, .pdf, .docx")
        docs_info.setStyleSheet("color: #888; font-size: 11px;")
        docs_layout.addWidget(docs_info)
        
        # File list
        self.files_list = QTextEdit()
        self.files_list.setReadOnly(True)
        self.files_list.setPlaceholderText("No files selected. Click 'Add Files' to select documents.")
        self.files_list.setMaximumHeight(100)
        docs_layout.addWidget(self.files_list)
        
        # Add files button
        files_btn_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("📁 Add Files...")
        self.add_files_btn.clicked.connect(self._on_add_files)
        self._style_button(self.add_files_btn)
        files_btn_layout.addWidget(self.add_files_btn)
        
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
    
    def _on_clear_files(self):
        self._selected_files = []
        self._update_files_display()
    
    def _update_files_display(self):
        if self._selected_files:
            display = "\n".join([f"• {Path(f).name}" for f in self._selected_files])
            self.files_list.setText(display)
        else:
            self.files_list.clear()
    
    def _on_create(self):
        # Validate inputs
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Collection name is required.")
            return
        
        # Validate name format (alphanumeric and underscores only)
        if not all(c.isalnum() or c in "_-" for c in name):
            QMessageBox.warning(
                self, "Validation Error",
                "Collection name can only contain letters, numbers, underscores, and hyphens."
            )
            return
        
        if not self._selected_files:
            QMessageBox.warning(self, "Validation Error", "Please select at least one document.")
            return
        
        if not self._app:
            QMessageBox.critical(self, "Error", "Application not available.")
            return
        
        # Disable UI during creation
        self.create_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        
        # Show progress
        self._progress_dialog = QProgressDialog(
            "Creating collection and processing documents...",
            None,  # No cancel button for now
            0, 0,  # Indeterminate progress
            self
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
        
        # Run in worker thread to avoid blocking UI
        self._worker = WorkerThread(
            self._create_collection_sync,
            name, description, tags, chunk_size, chunk_overlap
        )
        self._worker.finished.connect(self._on_create_finished)
        self._worker.error.connect(self._on_create_error)
        self._worker.start()
    
    def _create_collection_sync(
        self,
        name: str,
        description: str,
        tags: List[str],
        chunk_size: int,
        chunk_overlap: int
    ) -> Dict[str, Any]:
        """Synchronous collection creation (runs in worker thread)."""
        # Step 1: Create the collection
        result = self._app.create_collection(name, description, "document", tags)
        if result.get("status") != "success":
            return result
        
        # Step 2: Chunk all documents
        all_chunks = []
        all_metadatas = []
        
        for file_path in self._selected_files:
            chunk_result = self._app.chunk_document(file_path, chunk_size, chunk_overlap)
            if chunk_result.get("status") != "success":
                # Cleanup: delete the collection we just created
                self._app.delete_collection(name)
                return {
                    "status": "error",
                    "message": f"Failed to chunk {Path(file_path).name}: {chunk_result.get('message', 'Unknown error')}"
                }
            
            chunks = chunk_result.get("chunks", [])
            for chunk in chunks:
                all_chunks.append(chunk.get("text", ""))
                all_metadatas.append(chunk.get("metadata", {}))
        
        if not all_chunks:
            self._app.delete_collection(name)
            return {"status": "error", "message": "No content extracted from documents."}
        
        # Step 3: Add documents with embeddings
        add_result = self._app.add_documents_to_collection(name, all_chunks, all_metadatas)
        if add_result.get("status") != "success":
            # Cleanup: delete the collection
            self._app.delete_collection(name)
            return add_result
        
        return {
            "status": "success",
            "collection": name,
            "chunks_added": len(all_chunks),
            "files_processed": len(self._selected_files)
        }
    
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
            self.collection_created.emit(result)
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
    
    def __init__(self, parent=None, app=None):
        super().__init__(parent)
        self._app = app
        self.setWindowTitle("Document Collections")
        self.setModal(False)
        self.resize(900, 600)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        
        self._collections: List[Dict] = []
        self._worker: Optional[WorkerThread] = None
        self._chunks_dialog: Optional[ChunksViewerDialog] = None
        
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
        """Called when window is shown - refresh is triggered by open_documents."""
        super().showEvent(event)
    
    def refresh_collections(self):
        """Refresh the collections list."""
        if not self._app:
            self._set_status("Error: Application not available")
            return
        
        self._set_status("Loading collections...")
        self.refresh_btn.setEnabled(False)
        
        # Run in worker thread
        self._worker = WorkerThread(self._app.get_collections)
        self._worker.finished.connect(self._on_collections_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()
    
    def _on_collections_loaded(self, result):
        self.refresh_btn.setEnabled(True)
        
        # result could be a list directly or a dict with data
        if isinstance(result, dict):
            collections = result.get("data", [])
        else:
            collections = result if isinstance(result, list) else []
        
        self._collections = collections
        self._populate_table()
        self._set_status(f"Loaded {len(collections)} collection(s)")
    
    def _on_load_error(self, error_msg: str):
        self.refresh_btn.setEnabled(True)
        self._set_status(f"Error: {error_msg}")
    
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
        dialog = NewCollectionDialog(self, app=self._app)
        dialog.collection_created.connect(lambda _: self.refresh_collections())
        dialog.exec()
    
    def _on_view_chunks(self):
        collection_name = self._get_selected_collection_name()
        if not collection_name or not self._app:
            return
        
        self._set_status(f"Loading chunks for '{collection_name}'...")
        self.view_chunks_btn.setEnabled(False)
        self.edit_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        
        # Run in worker thread
        self._worker = WorkerThread(
            self._app.get_collection_documents,
            collection_name
        )
        self._worker.finished.connect(lambda r: self._on_chunks_loaded(collection_name, r))
        self._worker.error.connect(self._on_chunks_load_error)
        self._worker.start()

    def _on_edit_collection(self):
        collection_name = self._get_selected_collection_name()
        if not collection_name or not self._app:
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
        if not collection_name or not self._app:
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete collection '{collection_name}'?\n\n"
            "This will permanently remove all documents and embeddings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self._set_status(f"Deleting '{collection_name}'...")
        self.delete_btn.setEnabled(False)
        
        # Run in worker thread
        self._worker = WorkerThread(
            self._app.delete_collection,
            collection_name
        )
        self._worker.finished.connect(self._on_delete_finished)
        self._worker.error.connect(self._on_delete_error)
        self._worker.start()
    
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
