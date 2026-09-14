"""Régressions sans matériel ni Ollama ; Qt utilise la plateforme offscreen."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / 'src'

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SRC / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

popup = load('popup', 'ctrlcc-popup.py')
daemon = load('daemon', 'ctrlcc_daemon.py')

class StabilityTests(unittest.TestCase):
    def qt_process(self, action):
        script = '''
import importlib.util, resource, time
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
spec = importlib.util.spec_from_file_location('popup', %r)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
p.translate = lambda *args, **kwargs: (time.sleep(.4) or ('anglais', 'français', 'Bonjour'))
p.PasteInjector.prepare = lambda self: None
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
w = p.Popup('Hello')
w.show()
%s
QTimer.singleShot(900, app.quit)
app.exec()
''' % (str(SRC / 'ctrlcc-popup.py'), action)
        return subprocess.run([sys.executable, '-c', script], capture_output=True,
                              env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, timeout=5)

    def test_retranslation_during_request_does_not_abort(self):
        result = self.qt_process('QTimer.singleShot(20, w.start_translation)')
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_exit_during_request_does_not_abort(self):
        result = self.qt_process('QTimer.singleShot(20, app.quit)')
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_obsolete_result_cannot_replace_new_translation(self):
        result = self.qt_process("""
p.translate = lambda *args, **kwargs: ('anglais', 'français', 'Nouveau')
QTimer.singleShot(20, w.start_translation)
QTimer.singleShot(650, lambda: print(w.result.toPlainText()))
""")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().strip(), 'Nouveau')

    def test_autoclose_waits_for_translation_result(self):
        result = self.qt_process("""
QTimer.singleShot(20, lambda: print('pending', w.autoclose.isActive()))
QTimer.singleShot(650, lambda: print('done', w.autoclose.isActive()))
""")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().strip(), 'pending False\ndone True')

    def test_stream_yields_translation_before_completion(self):
        chunks = []
        payloads = [
            {'message': {'content': 'Bon'}, 'done': False},
            {'message': {'content': 'jour'}, 'done': False},
            {'message': {'content': ''}, 'done': True, 'eval_count': 2},
        ]
        response = io.BytesIO(b''.join(json.dumps(x).encode() + b'\n' for x in payloads))
        with patch.object(popup.urllib.request, 'urlopen', return_value=response):
            text = popup.ollama_chat('test', 'translate', 'hello', on_chunk=chunks.append)
        self.assertEqual(text, 'Bonjour')
        self.assertEqual(chunks, ['Bon', 'Bonjour'])

    def test_stream_failure_is_not_silent_success(self):
        response = io.BytesIO(b'{"error":"model unavailable"}\n')
        with patch.object(popup.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'model unavailable'):
                popup.ollama_chat('test', 'translate', 'hello', on_chunk=lambda _: None)

    def test_truncated_stream_is_rejected(self):
        response = io.BytesIO(b'{"message":{"content":"Bon"},"done":false}\n')
        with patch.object(popup.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'interrompu'):
                popup.ollama_chat('test', 'translate', 'hello', on_chunk=lambda _: None)

    def test_clipboard_work_does_not_block_trigger_submission(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def slow_trigger(holder):
            calls.append(1)
            started.set()
            release.wait(2)
            if len(calls) == 2:
                finished.set()
        with patch.object(daemon, 'trigger', slow_trigger):
            worker = daemon.TriggerWorker()
            worker.submit()
            self.assertTrue(started.wait(1))
            before = time.monotonic()
            for _ in range(20):
                worker.submit()
            self.assertLess(time.monotonic() - before, .1)
            release.set()
            self.assertTrue(finished.wait(1))
            self.assertEqual(len(calls), 2)

    def test_malformed_language_response_does_not_crash(self):
        self.assertEqual(popup.normalize_lang('.,!'), '')

if __name__ == '__main__':
    unittest.main()
