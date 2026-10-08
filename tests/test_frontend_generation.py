"""Front-end run/replay requests must be dropped once a newer request supersedes them.

The chat page streams a run into the answer/trace panes while a replay (or a second run)
writes into the same panes. The two operations share mutable state, so without a per-request
generation token the slower, older response repaints the view after the newer one and the
screen shows a mix of two runs. There is no JavaScript test runner in this repository, so
this pins the mechanism by structure and, when Node is present, syntax-checks both scripts.
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / 'web' / 'app.js'
MULTIFILE_JS = ROOT / 'web' / 'multifile.js'


def function_body(source, name):
    """Return the body of `function name(...)` (or its async form) by brace matching."""
    match = re.search(r'(?:async\s+)?function\s+' + re.escape(name) + r'\s*\(', source)
    if not match:
        return ''
    start = source.index('{', match.end() - 1)
    depth = 0
    for position in range(start, len(source)):
        character = source[position]
        if character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if depth == 0:
                return source[start:position + 1]
    return source[start:]


class FrontendGenerationTests(unittest.TestCase):
    def setUp(self):
        self.source = APP_JS.read_text(encoding='utf-8')

    def test_app_js_keeps_a_module_level_request_generation(self):
        self.assertRegex(self.source, r'var\s+RUN_GENERATION\s*=\s*0\s*;')

    def test_the_generation_helpers_advance_and_compare_that_one_counter(self):
        self.assertTrue(function_body(self.source, 'beginRunGeneration'),
                        'beginRunGeneration must exist')
        self.assertRegex(function_body(self.source, 'beginRunGeneration'),
                         r'RUN_GENERATION\s*(\+=|\+\+)')
        stale = function_body(self.source, 'staleRunGeneration')
        self.assertTrue(stale, 'staleRunGeneration must exist')
        self.assertRegex(stale, r'!==\s*RUN_GENERATION|RUN_GENERATION\s*!==')

    def test_run_and_replay_each_capture_a_generation_and_drop_stale_responses(self):
        # Both writers of the shared panes must take a token at entry and check it before
        # any await-ed response is rendered; otherwise the older response wins.
        for name in ('run', 'replay'):
            body = function_body(self.source, name)
            self.assertTrue(body, name + ' must exist')
            self.assertIn('beginRunGeneration()', body, name)
            self.assertGreaterEqual(body.count('staleRunGeneration('), 2, name)

    def test_the_run_stream_is_abandoned_once_it_is_superseded(self):
        body = function_body(self.source, 'run')
        # The streaming loop reads chunk by chunk; a superseded stream must stop rather
        # than keep appending events to the pane a newer run now owns.
        self.assertRegex(body, r'reader\.read\(\)')
        self.assertRegex(body, r'staleRunGeneration\(generation\)[^\n]*reader\.cancel\(\)')

    def test_the_send_button_is_released_only_by_the_stream_that_owns_it(self):
        body = function_body(self.source, 'run')
        self.assertRegex(body, r'RUN_STREAMING\s*===\s*generation')
        self.assertIn("$('send').disabled = false", body)

    def test_the_runs_list_render_is_also_generation_guarded(self):
        body = function_body(self.source, 'loadRuns')
        self.assertTrue(body, 'loadRuns must exist')
        self.assertRegex(body, r'\+\+RUNS_GENERATION')
        self.assertGreaterEqual(body.count('generation !== RUNS_GENERATION'), 2)

    def test_multifile_submit_cannot_repaint_a_newer_detail_selection(self):
        source = MULTIFILE_JS.read_text(encoding='utf-8')
        body = function_body(source, 'submitFileBatch')
        self.assertTrue(body, 'submitFileBatch must exist')
        # The submit response renders job detail only while it still owns the detail token.
        self.assertRegex(body, r'read\s*=\s*\+\+MULTIFILE\.detailToken')
        self.assertRegex(body, r'read\s*===\s*MULTIFILE\.detailToken')

    @unittest.skipUnless(shutil.which('node'), 'node 未安装')
    def test_both_scripts_pass_node_check(self):
        node = shutil.which('node')
        for path in (APP_JS, MULTIFILE_JS):
            completed = subprocess.run([node, '--check', str(path)],
                                       capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, path.name + ': ' + completed.stderr)


if __name__ == '__main__':
    unittest.main()
