import win32com.client
import win32gui
import uiautomation as uia
from time import sleep

def get_foreground_explorer_path():
    hwnd_fore = win32gui.GetForegroundWindow()
    shell = win32com.client.Dispatch("Shell.Application")
    for w in shell.Windows():
        try:
            # w is an IWebBrowserApp; File Explorer windows will have Name == "File Explorer"
            if int(w.HWND) == hwnd_fore and w.Name == "File Explorer":
                # Document.Folder.Self.Path is a real filesystem path
                return w.Document.Folder.Self.Path
        except Exception:
            pass
    return None


def get_selected_items_in_foreground_explorer():
    hwnd_fore = win32gui.GetForegroundWindow()
    shell = win32com.client.Dispatch("Shell.Application")
    for w in shell.Windows():
        try:
            if int(w.HWND) == hwnd_fore and w.Name == "File Explorer":
                items = w.Document.SelectedItems()
                return [item.Path for item in items]
        except Exception:
            pass
    return []


# def get_element_under_mouse():
#     x, y = uia.GetCursorPos()
#     return uia.ControlFromPoint(x, y)

# def get_current_folder_via_uia():
#     # Find top-level Explorer window
#     win = uia.WindowControl(searchDepth=1, ClassName='CabinetWClass')
#     if not win.Exists(0, 0):
#         return None
#     # Try common patterns for the address bar; names/IDs vary across builds
#     # Strategy: find a descendant EditControl with ValuePattern that looks like a path
#     for edit in win.GetDescendants(controlType=uia.ControlType.EditControl):
#         try:
#             val = edit.GetValuePattern().Value
#             # Heuristic: starts with drive letter or UNC or shell path
#             if val and (":" in val[:3] or val.startswith("\\\\")):
#                 return val
#         except Exception:
#             continue
#     return None

# def get_hovered_listitem_and_folder():
#     el = get_element_under_mouse()
#     if not el:
#         return None, None
#     # Bubble up to a list item, if any
#     cur = el
#     hovered_item_name = None
#     while cur and cur.ControlTypeName != 'WindowControl':
#         if cur.ControlTypeName in ('ListItemControl', 'TreeItemControl'):
#             hovered_item_name = cur.Name
#             break
#         cur = cur.GetParentControl()
#     folder = get_current_folder_via_uia()
#     return hovered_item_name, folder

if __name__ == "__main__":
    while True:
        print("Foreground Explorer Path:", get_foreground_explorer_path())
        print("Selected Items:", get_selected_items_in_foreground_explorer())
        sleep(1)