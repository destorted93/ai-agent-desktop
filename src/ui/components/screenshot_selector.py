from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QPen, QPixmap
from PyQt6.QtCore import Qt, pyqtSignal, QRect


class ScreenshotSelector(QWidget):
    """Overlay widget for selecting a screen area."""
    screenshot_selected = pyqtSignal(QPixmap)
    screenshot_cancelled = pyqtSignal()
    
    def __init__(self, screenshot):
        super().__init__()
        self.screenshot = screenshot
        self.dpr = max(1.0, float(screenshot.devicePixelRatio()))
        self.start_pos = None
        self.end_pos = None
        self.selecting = False
        
        # Set up fullscreen transparent overlay
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        
    def paintEvent(self, event):
        """Draw the screenshot with selection overlay."""
        painter = QPainter(self)
        
        # Draw the screenshot (Qt will scale by devicePixelRatio automatically)
        painter.drawPixmap(0, 0, self.screenshot)
        
        # Draw semi-transparent overlay
        overlay_color = QColor(0, 0, 0, 100)
        painter.fillRect(self.rect(), overlay_color)
        
        # If selecting, draw the selection rectangle
        if self.start_pos and self.end_pos:
            selection_rect = QRect(self.start_pos, self.end_pos).normalized()
            
            # Map to pixmap pixels for source rect (handles HiDPI correctly)
            src_rect = QRect(
                int(selection_rect.x() * self.dpr),
                int(selection_rect.y() * self.dpr),
                int(selection_rect.width() * self.dpr),
                int(selection_rect.height() * self.dpr),
            )
            
            # Clear the selection area (show original screenshot)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(selection_rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            
            # Draw the un-dimmed selection preview with accurate source rect
            painter.drawPixmap(selection_rect.topLeft(), self.screenshot, src_rect)
            
            # Draw selection border
            pen = QPen(QColor(0, 150, 255), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(selection_rect)
            
            # Draw dimensions text
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                selection_rect.x(),
                selection_rect.y() - 5,
                f"{selection_rect.width()}x{selection_rect.height()}"
            )
    
    def mousePressEvent(self, event):
        """Start selection."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.selecting = True
            self.update()
    
    def mouseMoveEvent(self, event):
        """Update selection."""
        if self.selecting:
            self.end_pos = event.pos()
            self.update()
    
    def mouseReleaseEvent(self, event):
        """Finish selection."""
        if event.button() == Qt.MouseButton.LeftButton and self.selecting:
            self.selecting = False
            self.end_pos = event.pos()
            
            # Get the selected area
            selection_rect = QRect(self.start_pos, self.end_pos).normalized()
            
            # Must have some minimum size
            if selection_rect.width() > 10 and selection_rect.height() > 10:
                # Copy using source rect in pixmap pixel coords (HiDPI safe)
                src_rect = QRect(
                    int(selection_rect.x() * self.dpr),
                    int(selection_rect.y() * self.dpr),
                    int(selection_rect.width() * self.dpr),
                    int(selection_rect.height() * self.dpr),
                )
                selected_pixmap = self.screenshot.copy(src_rect)
                # Normalize DPR on the cropped pixmap so downstream uses logical pixels
                selected_pixmap.setDevicePixelRatio(1.0)
                self.screenshot_selected.emit(selected_pixmap)
                self.close()
            else:
                # Too small, cancel
                self.screenshot_cancelled.emit()
                self.close()
    
    def keyPressEvent(self, event):
        """Cancel selection on Escape."""
        if event.key() == Qt.Key.Key_Escape:
            self.screenshot_cancelled.emit()
            self.close()