"""Utility functions for screen and window positioning."""

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint, QRect
from typing import Optional, Tuple


def is_position_on_screen(x: int, y: int, window_width: int = 100, window_height: int = 100) -> bool:
    """Check if a position is visible on any available screen.
    
    Args:
        x: X coordinate
        y: Y coordinate
        window_width: Width of the window (to ensure it's not just a corner visible)
        window_height: Height of the window
        
    Returns:
        True if the position is on a visible screen, False otherwise
    """
    # Check if position is on any available screen
    screens = QApplication.screens()
    for screen in screens:
        screen_geometry = screen.geometry()
        
        # Create a rect for the window at this position
        window_rect = QRect(x, y, window_width, window_height)
        
        # Check if the window would be at least partially visible.
        # We require up to 100x100px visible, but never more than the window size
        # (otherwise small windows would *always* be considered off-screen).
        if screen_geometry.intersects(window_rect):
            intersection = screen_geometry.intersected(window_rect)
            min_w = min(100, max(1, int(window_width)))
            min_h = min(100, max(1, int(window_height)))
            if intersection.width() >= min_w and intersection.height() >= min_h:
                return True
    
    return False


def validate_window_position(pos: Optional[Tuple[int, int]], 
                            window_width: int = 100, 
                            window_height: int = 100) -> Optional[Tuple[int, int]]:
    """Validate that a saved window position is still on-screen.
    
    If the position is off-screen (e.g., after disconnecting a monitor),
    returns None so the window can use its default positioning.
    
    Args:
        pos: Saved position as (x, y) tuple, or None
        window_width: Width of the window
        window_height: Height of the window
        
    Returns:
        The position tuple if valid, None if off-screen or invalid
    """
    if pos is None:
        return None
    
    try:
        # Handle various position formats from QSettings
        if isinstance(pos, (tuple, list)):
            x, y = int(pos[0]), int(pos[1])
        else:
            # Handle string format like "(100, 200)"
            x, y = map(int, str(pos).strip('()').split(','))
        
        # Check if this position is on any available screen
        if is_position_on_screen(x, y, window_width, window_height):
            return (x, y)
        else:
            # Position is off-screen, return None to use default
            return None
            
    except (ValueError, TypeError, AttributeError):
        # Invalid position format
        return None
