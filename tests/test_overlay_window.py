import unittest
from unittest.mock import patch

import win32con

from runlights.overlay_window import keep_above_foreground


class OverlayStackingTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch.multiple(
            'win32gui', GetForegroundWindow=unittest.mock.DEFAULT,
            GetWindowLong=unittest.mock.DEFAULT, GetWindowRect=unittest.mock.DEFAULT,
            GetWindow=unittest.mock.DEFAULT, SetWindowPos=unittest.mock.DEFAULT,
        )
        self.api = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.api['GetForegroundWindow'].return_value = 20
        self.api['GetWindowLong'].return_value = win32con.WS_EX_TOPMOST
        self.api['GetWindowRect'].side_effect = [(1780, 24, 1896, 80), (0, 0, 1920, 1080)]

    def test_covered_overlay_is_raised_without_activating_or_moving(self):
        self.api['GetWindow'].side_effect = [30, 20]
        keep_above_foreground(10)
        self.api['SetWindowPos'].assert_called_once_with(
            10, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )

    def test_overlay_already_above_game_is_left_alone(self):
        self.api['GetWindow'].side_effect = [30, 0]
        keep_above_foreground(10)
        self.api['SetWindowPos'].assert_not_called()

    def test_regular_foreground_window_is_left_alone(self):
        self.api['GetWindowLong'].return_value = 0
        keep_above_foreground(10)
        self.api['SetWindowPos'].assert_not_called()

    def test_other_monitor_is_left_alone(self):
        self.api['GetWindowRect'].side_effect = [(1780, 24, 1896, 80), (1920, 0, 3840, 1080)]
        keep_above_foreground(10)
        self.api['SetWindowPos'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
