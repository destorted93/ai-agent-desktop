import os
import re
from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit, QLabel, QDialog, QMessageBox, QFileDialog
from PyQt6.QtGui import QTextCursor, QFont, QColor, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PyQt6.QtCore import Qt, QEvent


class ChatHistoryJsonWindow(QDialog):
    """Debug window to display raw chat history JSON."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chat History (JSON)")
        self.setModal(False)
        self.resize(900, 700)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

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
        self.search_btn.setStyleSheet("""
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
        actions.addWidget(self.search_btn)
        
        self.save_as_btn = QPushButton("Save As…")
        self.save_as_btn.setStyleSheet("""
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
        actions.addWidget(self.save_as_btn)
        
        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet("""
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
        actions.addWidget(self.close_btn)
        layout.addLayout(actions)

        # Search panel (hidden by default)
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
        
        # Previous button
        self.prev_btn = QPushButton("▲")
        self.prev_btn.setToolTip("Previous match (Shift+Enter)")
        self.prev_btn.setFixedSize(24, 24)
        self.prev_btn.clicked.connect(self.find_previous)
        self.prev_btn.setStyleSheet("""
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
        """)
        search_layout.addWidget(self.prev_btn)
        
        # Next button
        self.next_btn = QPushButton("▼")
        self.next_btn.setToolTip("Next match (Enter)")
        self.next_btn.setFixedSize(24, 24)
        self.next_btn.clicked.connect(self.find_next)
        self.next_btn.setStyleSheet("""
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
        """)
        search_layout.addWidget(self.next_btn)
        
        # Close search button
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

        # Text editor
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setAcceptRichText(False)
        self.text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text)

        # Search state
        self.search_matches = []
        self.current_match_index = -1
        self.search_highlight_format = QTextCharFormat()
        self.search_highlight_format.setBackground(QColor("#614d1e"))
        self.current_highlight_format = QTextCharFormat()
        self.current_highlight_format.setBackground(QColor("#007acc"))
        
        self._highlighter = JsonSyntaxHighlighter(self.text.document())
        self.save_as_btn.clicked.connect(self.save_as)
        self.close_btn.clicked.connect(self.close)
        
        # Install event filter for Ctrl+F and Esc shortcuts
        self.installEventFilter(self)
        self.search_input.installEventFilter(self)

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
    
    def toggle_search_panel(self):
        """Toggle search panel visibility."""
        if self.search_panel.isVisible():
            self.close_search_panel()
        else:
            self.search_panel.show()
            self.search_input.setFocus()
            # Select all text in search input for easy replacement
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
        
        # Clear previous highlights
        self.clear_search_highlights()
        
        if not search_text:
            self.match_label.setText("No matches")
            self.search_matches = []
            self.current_match_index = -1
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        
        # Get document and cursor
        document = self.text.document()
        cursor = QTextCursor(document)
        
        # Find all matches (case-insensitive)
        self.search_matches = []
        flags = QTextDocument.FindFlag(0)  # No flags = case-insensitive
        
        while True:
            cursor = document.find(search_text, cursor, flags)
            if cursor.isNull():
                break
            self.search_matches.append(cursor)
        
        # Update UI based on results
        if self.search_matches:
            # Highlight all matches
            for i, match_cursor in enumerate(self.search_matches):
                extra_selection = QTextEdit.ExtraSelection()
                extra_selection.cursor = match_cursor
                
                # First match gets special highlight
                if i == 0:
                    extra_selection.format = self.current_highlight_format
                else:
                    extra_selection.format = self.search_highlight_format
                
                # Store for later
                if i == 0:
                    self.text.setExtraSelections([extra_selection])
                else:
                    current_selections = self.text.extraSelections()
                    current_selections.append(extra_selection)
                    self.text.setExtraSelections(current_selections)
            
            # Set current match to first
            self.current_match_index = 0
            self.match_label.setText(f"{self.current_match_index + 1} of {len(self.search_matches)}")
            
            # Scroll to first match
            self.text.setTextCursor(self.search_matches[0])
            self.text.ensureCursorVisible()
            
            # Enable navigation buttons
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
        
        # Move to next match (wrap around)
        self.current_match_index = (self.current_match_index + 1) % len(self.search_matches)
        self.highlight_current_match()
    
    def find_previous(self):
        """Navigate to previous match."""
        if not self.search_matches:
            return
        
        # Move to previous match (wrap around)
        self.current_match_index = (self.current_match_index - 1) % len(self.search_matches)
        self.highlight_current_match()
    
    def highlight_current_match(self):
        """Highlight the current match and scroll to it."""
        if not self.search_matches or self.current_match_index < 0:
            return
        
        # Update match label
        self.match_label.setText(f"{self.current_match_index + 1} of {len(self.search_matches)}")
        
        # Re-highlight all matches with current one special
        selections = []
        for i, match_cursor in enumerate(self.search_matches):
            extra_selection = QTextEdit.ExtraSelection()
            extra_selection.cursor = match_cursor
            
            if i == self.current_match_index:
                extra_selection.format = self.current_highlight_format
            else:
                extra_selection.format = self.search_highlight_format
            
            selections.append(extra_selection)
        
        self.text.setExtraSelections(selections)
        
        # Scroll to current match
        self.text.setTextCursor(self.search_matches[self.current_match_index])
        self.text.ensureCursorVisible()
    
    def clear_search_highlights(self):
        """Clear all search highlights."""
        self.text.setExtraSelections([])
        self.search_matches = []
        self.current_match_index = -1
    
    def set_json_text(self, text: str):
        self.text.setPlainText(text)

    def closeEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("history_json_window_pos", (self.pos().x(), self.pos().y()))
        self.hide()
        event.ignore()

    def hideEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("history_json_window_pos", (self.pos().x(), self.pos().y()))
        super().hideEvent(event)

    def showEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = settings.value("history_json_window_pos", None)
        if pos is not None:
            try:
                x, y = map(int, str(pos).strip('()').split(','))
                self.move(x, y)
            except Exception:
                pass
        super().showEvent(event)

    def save_as(self):
        import datetime
        default_name = f"chat_history_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filters = "JSON Files (*.json);;Text Files (*.txt);;All Files (*.*)"
        file_path, selected_filter = QFileDialog.getSaveFileName(self, "Save Chat History", default_name, filters, "JSON Files (*.json)")
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