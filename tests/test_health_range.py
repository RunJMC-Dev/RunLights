import ast
from pathlib import Path
import unittest

class HealthRangeTests(unittest.TestCase):
    def test_fade_threshold_and_legacy_rejection(self):
        tree = ast.parse(Path(__file__).resolve().parents[1].joinpath('runlights.pyw').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_apply_input_range')
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<range>', 'exec'), ns)
        normalize = ns['_apply_input_range']
        mode = dict(inputrangemin=0, inputrangemax=40, inputrangeclamp=True)
        for health, brightness in ((100, 0), (41, 0), (40, 0), (30, 64), (20, 128), (0, 255)):
            self.assertEqual(round(255 * (1-normalize(mode, health)/100)), brightness)
        mode['inputrangeclamp'] = False
        self.assertIsNone(normalize(mode, 41))
