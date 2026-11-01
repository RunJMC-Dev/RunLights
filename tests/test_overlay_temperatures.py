import ast
from datetime import datetime
import json
from pathlib import Path
import time
import tomllib
import unittest
from unittest.mock import patch

from runlights.overlay_template import EXAMPLE, parse_template, render_template
from runlights.temperatures import Reading, TemperatureService, valid_temperature


class OverlayTests(unittest.TestCase):
    def test_rows_and_elapsed_beyond_one_day(self):
        rows = parse_template(EXAMPLE)
        text = render_template(rows, Reading(55, 63), 25 * 3600 + 120, datetime(2026, 1, 1, 9, 30))
        self.assertIn('09:30', text)
        self.assertIn('Played: 25:02', text)
        self.assertIn('CPU: 55 °C GPU: 63 °C', text)
        self.assertIn('font-size:7pt', text)

    def test_idle_and_missing_readings(self):
        text = render_template(parse_template(EXAMPLE), Reading(), alarm=True)
        self.assertNotIn('Played:', text)
        self.assertIn('CPU: — GPU: —', text)
        self.assertIn('⏰', text)

    def test_markup_is_validated_and_text_escaped(self):
        for text in ('<img/>', '<row>{wrong}</row>', '<row size="nan">x</row>',
                     '<row color="red">x</row>', '<row><b>x</b></row>', '<row>'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_template(text)
        text = render_template(parse_template('<row>&lt;b&gt; &amp;</row>'), Reading())
        self.assertIn('&lt;b&gt; &amp;', text)

    def test_config_roundtrip(self):
        source = Path('runlights.pyw').read_text(encoding='utf-8')
        tree = ast.parse(source)
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
                     {'_toml_escape', '_render_overlays_clock_block'}]
        namespace = {'json': json}
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'config-test', 'exec'), namespace)
        config = '\n'.join(namespace['_render_overlays_clock_block']({'template': EXAMPLE}))
        self.assertEqual(tomllib.loads(config)['overlays']['clock']['template'], EXAMPLE)


class TemperatureTests(unittest.TestCase):
    def test_invalid_readings(self):
        for value in (None, 'N/A', float('nan'), float('inf'), 999):
            self.assertIsNone(valid_temperature(value))
        self.assertEqual(valid_temperature('55.5'), 55.5)

    def test_stale_values_expire(self):
        service = TemperatureService()
        service._reading = Reading(55, 63, 'test', time.monotonic())
        self.assertEqual(service.snapshot().cpu, 55)
        with patch('runlights.temperatures.time.monotonic', return_value=time.monotonic() + 11):
            self.assertIsNone(service.snapshot().cpu)

    def test_failed_bridge_uses_fallback_and_closes(self):
        service = TemperatureService()
        def fallback():
            service._stop.set()
            return 58, None, 'WMI'
        with patch('runlights.temperatures.SensorBridge') as bridge, \
             patch('runlights.temperatures._hardware_monitor_temperatures', side_effect=fallback), \
             patch('runlights.temperatures._nvidia_temperature', return_value=64):
            bridge.return_value.read.side_effect = TimeoutError()
            service._poll()
            self.assertEqual(service.snapshot().cpu, 58)
            self.assertEqual(service.snapshot().gpu, 64)
            bridge.return_value.close.assert_called()
