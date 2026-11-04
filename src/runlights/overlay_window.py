"""Keep desktop overlays above topmost games without taking keyboard focus."""


def keep_above_foreground(hwnd: int) -> None:
    import win32con
    import win32gui

    foreground = win32gui.GetForegroundWindow()
    if not foreground or foreground == hwnd:
        return
    if not win32gui.GetWindowLong(foreground, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST:
        return
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    fl, ft, fr, fb = win32gui.GetWindowRect(foreground)
    if right <= fl or fr <= left or bottom <= ft or fb <= top:
        return
    # Only repair a covered overlay. Avoid reordering menus or other overlays
    # every second when we are already above the foreground game.
    above = win32gui.GetWindow(hwnd, win32con.GW_HWNDPREV)
    while above:
        if above == foreground:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )
            return
        above = win32gui.GetWindow(above, win32con.GW_HWNDPREV)
