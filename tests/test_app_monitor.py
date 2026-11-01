import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runlights.core.app_monitor import ApplicationMonitorModule, ApplicationStarted, ApplicationStopped
from runlights.core.events import EventBus


class MonitorReloadTests(unittest.TestCase):
    def test_add_running_game_then_remove_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('', encoding='utf-8')
            bus = EventBus()
            started, stopped = threading.Event(), threading.Event()
            bus.subscribe(ApplicationStarted, lambda event: started.set() if event.app_id == 'OnceHuman' else None)
            bus.subscribe(ApplicationStopped, lambda event: stopped.set() if event.app_id == 'OnceHuman' else None)
            monitor = ApplicationMonitorModule([], bus, lambda _: None, poll_seconds=0.01, config_path=path)
            with patch('psutil.process_iter', return_value=[SimpleNamespace(info={'name': 'ONCE_HUMAN.exe'})]), \
                 patch('win32gui.GetForegroundWindow', return_value=0):
                monitor.start()
                try:
                    path.write_text('[[application]]\nid="OnceHuman"\nprocesses=["once_human.EXE"]\n', encoding='utf-8')
                    self.assertTrue(started.wait(2), 'Already-running game was not detected after adding it')
                    path.write_text('', encoding='utf-8')
                    self.assertTrue(stopped.wait(2), 'Removed game remained active')
                finally:
                    monitor.stop()

    def test_invalid_config_retains_last_good_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('not valid TOML [', encoding='utf-8')
            monitor = ApplicationMonitorModule(
                [{'id': 'OnceHuman', 'processes': ['ONCE_HUMAN.exe']}],
                EventBus(), lambda _: None, config_path=path,
            )
            monitor._refresh_applications()
            self.assertEqual(monitor._process_to_app, {'once_human.exe': 'OnceHuman'})
