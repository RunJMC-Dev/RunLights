import ast
from pathlib import Path
import unittest

class LoggingCallbackTests(unittest.TestCase):
    def test_output_through_polling_logger_preserves_category(self):
        tree = ast.parse(Path(__file__).resolve().parents[1].joinpath('runlights.pyw').read_text(encoding='utf-8'))
        loop = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_ocr_poll_loop')
        callback = next(n for n in loop.body if isinstance(n, ast.FunctionDef) and n.name == 'log_input')
        output = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_apply_output')
        entries = []
        ns = {'log_message_raw': lambda msg, **meta: entries.append((msg, meta))}
        exec(compile(ast.Module(body=[callback, output], type_ignores=[]), '<callbacks>', 'exec'), ns)
        # The real output path logs a missing-controller diagnostic through
        # the polling input callback; this previously stopped the entire loop.
        ns['_apply_output']({'output': 'crossfade'}, {}, 90.1, ns['log_input'])
        self.assertEqual(entries[-1], ('crossfade missing controllers', {'msg_class': 'OUTPUT'}))
        ns['log_input']('health sample')
        self.assertEqual(entries[-1][1], {'msg_class': 'INPUT'})
