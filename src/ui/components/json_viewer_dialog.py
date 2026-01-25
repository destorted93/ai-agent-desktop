"""Base class for JSON viewer dialogs with search, edit, and save functionality."""

import os
import re
import json
import datetime
from abc import abstractmethod
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QTextEdit, 
    QLineEdit, QLabel, QDialog, QMessageBox, QFileDialog
)
from PyQt6.QtGui import (
    QTextCursor, QFont, QColor, QSyntaxHighlighter, 
    QTextCharFormat, QTextDocument
)
from PyQt6.QtCore import Qt, QEvent, pyqtSignal


class JsonSyntaxHighlighter(QSyntaxHighlighter):
    """JSON syntax highlighter."""

    def __init__(self, document):
        super().__init__(document)
        self._key_format = QTextCharFormat()
        self._key_format.setForeground(QColor("#9cdcfe"))
        self._string_format = QTextCharFormat()
        self._string_format.setForeground(QColor("#ce9178"))
        self._number_format = QTextCharFormat()
        self._number_format.setForeground(QColor("#b5cea8"))
        self._bool_format = QTextCharFormat()
        self._bool_format.setForeground(QColor("#569cd6"))
        self._bool_format.setFontWeight(QFont.Weight.DemiBold)
        self._null_format = QTextCharFormat()
        self._null_format.setForeground(QColor("#c586c0"))
        self._null_format.setFontWeight(QFont.Weight.DemiBold)
        self._punct_format = QTextCharFormat()
        self._punct_format.setForeground(QColor("#d4d4d4"))
        self._re_key = re.compile(r'"([^"\\]|\\.)*"(?=\s*:)')
        self._re_string = re.compile(r'"([^"\\]|\\.)*"')
        self._re_number = re.compile(r'\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b')
        self._re_bool = re.compile(r'\b(?:true|false)\b')
        self._re_null = re.compile(r'\bnull\b')
        self._re_punct = re.compile(r'[\{\}\[\]:,]')

    def highlightBlock(self, text: str):
        for m in self._re_punct.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._punct_format)
        for m in self._re_number.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._number_format)
        for m in self._re_bool.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._bool_format)
        for m in self._re_null.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._null_format)
        for m in self._re_string.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._string_format)
        for m in self._re_key.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._key_format)


class JsonViewerDialog(QDialog):
    """Base class for JSON viewer dialogs with search, edit, and save functionality."""
    
    # Signal emitted after data is successfully loaded from file and saved to source
    data_loaded = pyqtSignal()
    
    # Subclasses should set these
    window_title = "JSON Viewer"
    settings_key = "json_viewer"
    default_filename_prefix = "data"
    
    def __init__(self, parent=None, editable=False):
        super().__init__(parent)
        self.setWindowTitle(self.window_title)
        self.setModal(False)
        self.resize(900, 700)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        
        self._editable = editable
        self._edit_mode = False
        
        self._setup_ui()
        self._setup_search()
        self._setup_connections()
        
        # Install event filter for shortcuts
        self.installEventFilter(self)
        self.search_input.installEventFilter(self)
    
    def _setup_ui(self):
        """Setup the main UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Top action bar with buttons
        actions = QHBoxLayout()
        actions.addStretch(1)
        
        # Search button
        self.search_btn = QPushButton("🔍 Search")
        self.search_btn.setToolTip("Search (Ctrl+F)")
        self.search_btn.clicked.connect(self.toggle_search_panel)
        self._style_button(self.search_btn)
        actions.addWidget(self.search_btn)
        
        # Edit button (only if editable)
        if self._editable:
            self.edit_btn = QPushButton("✏️ Edit")
            self.edit_btn.setToolTip("Toggle edit mode")
            self.edit_btn.clicked.connect(self.toggle_edit_mode)
            self._style_button(self.edit_btn)
            actions.addWidget(self.edit_btn)
            
            # Save button (hidden until edit mode)
            self.save_btn = QPushButton("💾 Save")
            self.save_btn.setToolTip("Save changes to storage")
            self.save_btn.clicked.connect(self._on_save_clicked)
            self._style_button(self.save_btn, accent=True)
            self.save_btn.hide()
            actions.addWidget(self.save_btn)
        
        # Load from file button
        self.load_btn = QPushButton("📂 Load")
        self.load_btn.setToolTip("Load JSON from file")
        self.load_btn.clicked.connect(self.load_from_file)
        self._style_button(self.load_btn)
        actions.addWidget(self.load_btn)
        
        self.save_as_btn = QPushButton("Save As…")
        self._style_button(self.save_as_btn)
        actions.addWidget(self.save_as_btn)
        
        self.close_btn = QPushButton("Close")
        self._style_button(self.close_btn)
        actions.addWidget(self.close_btn)
        layout.addLayout(actions)

        # Search panel (hidden by default)
        self._setup_search_panel(layout)

        # Text editor
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setAcceptRichText(False)
        self.text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text)
        
        self._highlighter = JsonSyntaxHighlighter(self.text.document())
    
    def _style_button(self, btn, accent=False):
        """Apply consistent button styling."""
        if accent:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #0e639c;
                    color: white;
                    border: 1px solid #0e639c;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #1177bb;
                    border-color: #1177bb;
                }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #3a3a3a;
                    color: #d4d4d4;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #4a4a4a;
                    border-color: #666;
                }
            """)
    
    def _setup_search_panel(self, layout):
        """Setup the search panel UI."""
        self.search_panel = QWidget()
        self.search_panel.hide()
        search_layout = QHBoxLayout(self.search_panel)
        search_layout.setContentsMargins(5, 5, 5, 5)
        search_layout.setSpacing(5)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find...")
        self.search_input.textChanged.connect(self.perform_search)
        self.search_input.returnPressed.connect(self.find_next)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #007acc;
            }
        """)
        search_layout.addWidget(self.search_input)
        
        # Match counter
        self.match_label = QLabel("No matches")
        self.match_label.setStyleSheet("""
            QLabel {
                color: #888;
                font-size: 11px;
                padding: 0 5px;
            }
        """)
        search_layout.addWidget(self.match_label)
        
        # Navigation buttons
        nav_btn_style = """
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 3px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:disabled {
                color: #555;
                background-color: #2a2a2a;
            }
        """
        
        self.prev_btn = QPushButton("▲")
        self.prev_btn.setToolTip("Previous match (Shift+Enter)")
        self.prev_btn.setFixedSize(24, 24)
        self.prev_btn.clicked.connect(self.find_previous)
        self.prev_btn.setStyleSheet(nav_btn_style)
        search_layout.addWidget(self.prev_btn)
        
        self.next_btn = QPushButton("▼")
        self.next_btn.setToolTip("Next match (Enter)")
        self.next_btn.setFixedSize(24, 24)
        self.next_btn.clicked.connect(self.find_next)
        self.next_btn.setStyleSheet(nav_btn_style)
        search_layout.addWidget(self.next_btn)
        
        self.close_search_btn = QPushButton("✕")
        self.close_search_btn.setToolTip("Close (Esc)")
        self.close_search_btn.setFixedSize(24, 24)
        self.close_search_btn.clicked.connect(self.close_search_panel)
        self.close_search_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 3px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
        """)
        search_layout.addWidget(self.close_search_btn)
        
        self.search_panel.setStyleSheet("""
            QWidget {
                background-color: #252526;
                border-bottom: 1px solid #3d3d3d;
            }
        """)
        layout.addWidget(self.search_panel)
    
    def _setup_search(self):
        """Initialize search state."""
        self.search_matches = []
        self.current_match_index = -1
        self.search_highlight_format = QTextCharFormat()
        self.search_highlight_format.setBackground(QColor("#614d1e"))
        self.current_highlight_format = QTextCharFormat()
        self.current_highlight_format.setBackground(QColor("#007acc"))
    
    def _setup_connections(self):
        """Setup signal connections."""
        self.save_as_btn.clicked.connect(self.save_as)
        self.close_btn.clicked.connect(self.close)

    def eventFilter(self, obj, event):
        """Handle keyboard shortcuts for search."""
        if event.type() == QEvent.Type.KeyPress:
            # Ctrl+F to open search
            if event.key() == Qt.Key.Key_F and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self.toggle_search_panel()
                return True
            
            # Esc to close search (when search input has focus)
            if obj == self.search_input and event.key() == Qt.Key.Key_Escape:
                self.close_search_panel()
                return True
            
            # Shift+Enter for previous match
            if obj == self.search_input and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                    self.find_previous()
                    return True
        
        return super().eventFilter(obj, event)
    
    # === Edit Mode ===
    
    def toggle_edit_mode(self):
        """Toggle between view and edit mode."""
        if self._edit_mode:
            self._exit_edit_mode()
        else:
            self._enter_edit_mode()
    
    def _enter_edit_mode(self):
        """Enter edit mode."""
        self._edit_mode = True
        self.text.setReadOnly(False)
        self.text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                border: 2px solid #0e639c;
            }
        """)
        if hasattr(self, 'edit_btn'):
            self.edit_btn.setText("❌ Cancel")
            self.edit_btn.setToolTip("Cancel editing")
        if hasattr(self, 'save_btn'):
            self.save_btn.show()
    
    def _exit_edit_mode(self, refresh=True):
        """Exit edit mode."""
        self._edit_mode = False
        self.text.setReadOnly(True)
        self.text.setStyleSheet("")
        if hasattr(self, 'edit_btn'):
            self.edit_btn.setText("✏️ Edit")
            self.edit_btn.setToolTip("Toggle edit mode")
        if hasattr(self, 'save_btn'):
            self.save_btn.hide()
        
        if refresh:
            # Refresh content to discard unsaved changes
            self.refresh_content()
    
    def _on_save_clicked(self):
        """Handle save button click."""
        content = self.text.toPlainText()
        
        # Validate JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            QMessageBox.warning(
                self, "Invalid JSON",
                f"The content is not valid JSON:\n\n{e}"
            )
            return
        
        # Call subclass implementation
        result = self.save_to_source(data)
        
        if result.get("status") == "success":
            self._exit_edit_mode(refresh=False)
            QMessageBox.information(self, "Saved", "Changes saved successfully.")
        else:
            QMessageBox.warning(
                self, "Save Failed",
                f"Failed to save:\n{result.get('message', 'Unknown error')}"
            )
    
    @abstractmethod
    def save_to_source(self, data) -> dict:
        """Save data back to the source. Subclasses must implement this.
        
        Args:
            data: Parsed JSON data to save
            
        Returns:
            Dict with 'status' key ('success' or 'error') and optional 'message'
        """
        pass
    
    @abstractmethod
    def refresh_content(self):
        """Refresh content from source. Subclasses must implement this."""
        pass
    
    # === Search Functions ===
    
    def toggle_search_panel(self):
        """Toggle search panel visibility."""
        if self.search_panel.isVisible():
            self.close_search_panel()
        else:
            self.search_panel.show()
            self.search_input.setFocus()
            self.search_input.selectAll()
    
    def close_search_panel(self):
        """Close search panel and clear highlights."""
        self.search_panel.hide()
        self.clear_search_highlights()
        self.search_input.clear()
        self.text.setFocus()
    
    def perform_search(self):
        """Perform search and highlight all matches."""
        search_text = self.search_input.text()
        
        self.clear_search_highlights()
        
        if not search_text:
            self.match_label.setText("No matches")
            self.search_matches = []
            self.current_match_index = -1
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        
        document = self.text.document()
        cursor = QTextCursor(document)
        
        self.search_matches = []
        flags = QTextDocument.FindFlag(0)
        
        while True:
            cursor = document.find(search_text, cursor, flags)
            if cursor.isNull():
                break
            self.search_matches.append(cursor)
        
        if self.search_matches:
            selections = []
            for i, match_cursor in enumerate(self.search_matches):
                extra_selection = QTextEdit.ExtraSelection()
                extra_selection.cursor = match_cursor
                extra_selection.format = self.current_highlight_format if i == 0 else self.search_highlight_format
                selections.append(extra_selection)
            
            self.text.setExtraSelections(selections)
            self.current_match_index = 0
            self.match_label.setText(f"1 of {len(self.search_matches)}")
            self.text.setTextCursor(self.search_matches[0])
            self.text.ensureCursorVisible()
            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
        else:
            self.match_label.setText("No matches")
            self.current_match_index = -1
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
    
    def find_next(self):
        """Navigate to next match."""
        if not self.search_matches:
            return
        self.current_match_index = (self.current_match_index + 1) % len(self.search_matches)
        self.highlight_current_match()
    
    def find_previous(self):
        """Navigate to previous match."""
        if not self.search_matches:
            return
        self.current_match_index = (self.current_match_index - 1) % len(self.search_matches)
        self.highlight_current_match()
    
    def highlight_current_match(self):
        """Highlight the current match and scroll to it."""
        if not self.search_matches or self.current_match_index < 0:
            return
        
        self.match_label.setText(f"{self.current_match_index + 1} of {len(self.search_matches)}")
        
        selections = []
        for i, match_cursor in enumerate(self.search_matches):
            extra_selection = QTextEdit.ExtraSelection()
            extra_selection.cursor = match_cursor
            extra_selection.format = self.current_highlight_format if i == self.current_match_index else self.search_highlight_format
            selections.append(extra_selection)
        
        self.text.setExtraSelections(selections)
        self.text.setTextCursor(self.search_matches[self.current_match_index])
        self.text.ensureCursorVisible()
    
    def clear_search_highlights(self):
        """Clear all search highlights."""
        self.text.setExtraSelections([])
        self.search_matches = []
        self.current_match_index = -1
    
    # === Content Methods ===
    
    def set_json_text(self, text: str):
        """Set the JSON text content."""
        self.text.setPlainText(text)
        self.scroll_to_bottom()
    
    def scroll_to_bottom(self):
        """Scroll to the bottom of the text editor."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(10, self._do_scroll)
    
    def _do_scroll(self):
        """Actually perform the scroll."""
        scrollbar = self.text.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())
    
    # === File Operations ===
    
    def load_from_file(self):
        """Load JSON content from a file and save to storage."""
        filters = "JSON Files (*.json);;All Files (*.*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load JSON File", "", filters, "JSON Files (*.json)"
        )
        if not file_path:
            return
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            QMessageBox.warning(self, "Load Failed", f"Could not read file:\n{e}")
            return
        
        # Validate JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            QMessageBox.warning(
                self, "Invalid JSON",
                f"The file does not contain valid JSON:\n\n{e}"
            )
            return
        
        # Confirm before replacing
        reply = QMessageBox.question(
            self,
            "Confirm Load",
            f"This will replace all current data with the content from:\n\n{os.path.basename(file_path)}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Save to source
        result = self.save_to_source(data)
        
        if result.get("status") == "success":
            # Refresh display
            self.refresh_content()
            # Emit signal for any connected handlers (e.g., UI reload)
            self.data_loaded.emit()
            QMessageBox.information(self, "Loaded", "Data loaded successfully from file.")
        else:
            QMessageBox.warning(
                self, "Load Failed",
                f"Failed to save loaded data:\n{result.get('message', 'Unknown error')}"
            )
    
    def save_as(self):
        """Save content to a file."""
        default_name = f"{self.default_filename_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filters = "JSON Files (*.json);;Text Files (*.txt);;All Files (*.*)"
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save As", default_name, filters, "JSON Files (*.json)"
        )
        if not file_path:
            return
        if selected_filter.startswith("JSON") and os.path.splitext(file_path)[1] == "":
            file_path += ".json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.text.toPlainText())
                if not self.text.toPlainText().endswith("\n"):
                    f.write("\n")
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save file:\n{e}")
    
    # === Window Events ===
    
    def closeEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue(f"{self.settings_key}_pos", (self.pos().x(), self.pos().y()))
        self.hide()
        event.ignore()

    def hideEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue(f"{self.settings_key}_pos", (self.pos().x(), self.pos().y()))
        super().hideEvent(event)

    def showEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = settings.value(f"{self.settings_key}_pos", None)
        if pos is not None:
            try:
                x, y = map(int, str(pos).strip('()').split(','))
                self.move(x, y)
            except Exception:
                pass
        super().showEvent(event)
