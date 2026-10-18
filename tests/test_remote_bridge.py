from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runlights.remote.activation import ProfileActivator
from runlights.remote.backends import SendInputBackend
from runlights.remote.config import DEFAULT_BINDINGS, load_remote_bridge_config
from runlights.remote.engine import RemoteBridgeEngine
from runlights.remote.key_state import KeyStateTracker
from runlights.remote.models import ActionEvent, AppProfile, DeviceDefinition, RemoteBridgeConfig
from runlights.remote.winusb_input import HidPacketDecoder


class DeviceFilteringTests(unittest.TestCase):
    def test_matches_only_configured_vid_pid(self):
        device = DeviceDefinition("g20s", 0x4842, 0x0001)
        self.assertTrue(device.matches(r"\\?\HID#VID_4842&PID_0001&MI_02&COL01#abc"))
        self.assertFalse(device.matches(r"\\?\HID#VID_046D&PID_C31C#keyboard"))
        self.assertFalse(device.matches(r"\\?\HID#VID_4842&PID_9999#other"))

    def test_optional_path_scope(self):
        keyboard = DeviceDefinition("g20s-keyboard", 0x4842, 0x0001, "MI_02")
        self.assertTrue(keyboard.matches(r"HID#VID_4842&PID_0001&MI_02&COL01"))
        self.assertFalse(keyboard.matches(r"HID#VID_4842&PID_0001&MI_03"))


class OwnedInputDecoderTests(unittest.TestCase):
    def test_decodes_navigation_without_accepting_text_keys(self):
        decoder = HidPacketDecoder()
        path = r"\\?\usb#vid_4842&pid_0001&mi_02#owned"
        # Boot keyboard layout: modifiers, reserved, six HID usages.
        events = decoder.decode(0x81, bytes([0, 0, 0x52, 0x04, 0, 0, 0, 0]), path, 1.0)
        self.assertEqual([("up", True)], [(event.control, event.pressed) for event in events])
        events = decoder.decode(0x81, bytes(8), path, 1.1)
        self.assertEqual([("up", False)], [(event.control, event.pressed) for event in events])

    def test_decodes_consumer_press_and_release(self):
        decoder = HidPacketDecoder()
        path = r"\\?\usb#vid_4842&pid_0001&mi_02#owned"
        events = decoder.decode(0x82, bytes([3, 0x41, 0x00]), path, 1.0)
        self.assertEqual("consumer_0041", events[0].control)
        self.assertTrue(events[0].pressed)
        events = decoder.decode(0x82, bytes([3, 0, 0]), path, 1.1)
        self.assertFalse(events[0].pressed)


class ForegroundProfileTests(unittest.TestCase):
    def test_activates_only_matching_foreground_executable(self):
        changes = []
        stremio = AppProfile("stremio", ("stremio-shell-ng.exe",), dict(DEFAULT_BINDINGS))
        activator = ProfileActivator([stremio], changes.append)
        self.assertIsNone(activator.update("notepad.exe"))
        self.assertEqual(stremio, activator.update(r"C:\Apps\stremio-shell-ng.exe"))
        self.assertEqual(stremio, activator.update("STREMIO-SHELL-NG.EXE"))
        self.assertIsNone(activator.update("game.exe"))
        self.assertEqual([stremio, None], changes)

    def test_specific_profile_overrides_default_profile(self):
        changes = []
        fallback = AppProfile("stock", (), {}, "sendinput", True)
        stremio = AppProfile("stremio", ("stremio-shell-ng.exe",), {})
        activator = ProfileActivator([fallback, stremio], changes.append)
        self.assertEqual(fallback, activator.update("explorer.exe"))
        self.assertEqual(stremio, activator.update("stremio-shell-ng.exe"))
        self.assertEqual(fallback, activator.update("notepad.exe"))

    def test_emergency_deactivate_overrides_default_profile(self):
        changes = []
        fallback = AppProfile("stock", (), {}, "sendinput", True)
        activator = ProfileActivator([fallback], changes.append)
        self.assertEqual(fallback, activator.update("explorer.exe"))
        activator.deactivate()
        self.assertIsNone(activator.active)
        self.assertEqual([fallback, None], changes)


class SendInputMappingTests(unittest.TestCase):
    def test_key_actions_are_data_driven_and_support_chords(self):
        self.assertEqual((0x28,), SendInputBackend._keys_for_action("key_down"))
        self.assertEqual((0x10, 0x09), SendInputBackend._keys_for_action("key_shift+tab"))
        self.assertEqual((0xAF,), SendInputBackend._keys_for_action("key_volume_up"))
        self.assertEqual((0x11, ord("L")), SendInputBackend._keys_for_action("key_ctrl+l"))
        self.assertEqual((0x7B,), SendInputBackend._keys_for_action("key_f12"))
        self.assertIsNone(SendInputBackend._keys_for_action("button_a"))


class KeyStateTests(unittest.TestCase):
    def test_deduplicates_down_and_repeats_at_configured_rate(self):
        tracker = KeyStateTracker(repeat_delay_ms=400, repeat_interval_ms=100)
        self.assertEqual([ActionEvent("dpad_down", True, 1.0)], tracker.update("dpad_down", True, 1.0))
        self.assertEqual([], tracker.update("dpad_down", True, 1.1))
        self.assertEqual([], tracker.repeats(1.39))
        repeats = tracker.repeats(1.61)
        self.assertEqual(3, len(repeats))
        self.assertTrue(all(event.repeat for event in repeats))
        self.assertEqual(
            [ActionEvent("dpad_down", False, 1.7)],
            tracker.update("dpad_down", False, 1.7),
        )
        self.assertEqual([], tracker.repeats(2.0))

    def test_release_all_clears_every_held_action(self):
        tracker = KeyStateTracker()
        tracker.update("button_a", True, 1.0)
        tracker.update("dpad_left", True, 1.1)
        releases = tracker.release_all(2.0)
        self.assertEqual({"button_a", "dpad_left"}, {event.action for event in releases})
        self.assertTrue(all(not event.pressed for event in releases))
        self.assertFalse(tracker.held_actions)


class _FakeBackend:
    name = "fake"

    def __init__(self):
        self.events: list[ActionEvent] = []
        self.connected = False
        self.disconnected = False

    def connect(self):
        self.connected = True

    def emit(self, event):
        self.events.append(event)

    def disconnect(self):
        self.disconnected = True


class CleanupTests(unittest.TestCase):
    def test_output_exists_only_while_profile_is_foreground(self):
        profile = AppProfile("stremio", ("stremio-shell-ng.exe",), dict(DEFAULT_BINDINGS))
        config = RemoteBridgeConfig(
            enabled=True,
            devices=[DeviceDefinition("g20s", 0x4842, 0x0001)],
            profiles=[profile],
        )
        with tempfile.TemporaryDirectory() as directory:
            engine = RemoteBridgeEngine(config, Path(directory), lambda _message: None)
            backend = _FakeBackend()
            with patch("runlights.remote.engine.create_backend", return_value=backend):
                engine.activator.update("stremio-shell-ng.exe")
                self.assertTrue(backend.connected)
                self.assertIs(engine.backend, backend)
                engine.activator.update("explorer.exe")
                self.assertTrue(backend.disconnected)
                self.assertIsNone(engine.backend)

    def test_deactivate_releases_buttons_before_disconnect(self):
        config = RemoteBridgeConfig(
            enabled=True,
            devices=[DeviceDefinition("g20s", 0x4842, 0x0001)],
            profiles=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            engine = RemoteBridgeEngine(config, Path(directory), lambda _message: None)
            backend = _FakeBackend()
            engine.backend = backend
            engine.tracker.update("button_a", True, 1.0)
            engine._deactivate()
            self.assertTrue(backend.disconnected)
            self.assertEqual("button_a", backend.events[-1].action)
            self.assertFalse(backend.events[-1].pressed)
            self.assertIsNone(engine.backend)

    def test_default_config_is_safe_and_data_driven(self):
        config = load_remote_bridge_config({"remote_bridge": {"enabled": True}})
        self.assertEqual("diagnostic", config.backend)
        self.assertEqual("observe", config.input_mode)
        self.assertEqual(0x4842, config.selected_device().vid)
        self.assertEqual("stremio", config.profiles[0].id)
        self.assertEqual("button_a", config.profiles[0].bindings["consumer_0041"])
        self.assertEqual("passthrough", config.profiles[0].bindings["consumer_00e2"])


if __name__ == "__main__":
    unittest.main()
