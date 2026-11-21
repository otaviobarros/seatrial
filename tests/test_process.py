"""Bounded process launch: stdin, time caps, process-tree cleanup, and host stalls."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from seatrial import process

SOURCE = Path(__file__).resolve().parents[1] / 'src'


def paused_launch(child, timeout):
    """Launch child from a separate launcher process, and pause that launcher for 4 seconds."""
    script = ('import sys, os; from pathlib import Path; from seatrial import process; '
              'process.STALL_GAP = 1.0; t = Path(sys.argv[1]); '
              f'r = process.launch([sys.executable, "-c", {child!r}], t, os.environ.copy(), '
              f't / "out", t / "err", {timeout}); '
              'print(r["stalled_s"], "timeout" in r, r.get("exit"), round(r["ended"] - r["started"], 1), flush=True)')
    with tempfile.TemporaryDirectory() as tmp:
        parent = subprocess.Popen([sys.executable, '-c', script, tmp], cwd=SOURCE, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, text=True)
        time.sleep(1.0)
        os.kill(parent.pid, signal.SIGSTOP)
        time.sleep(4.0)
        os.kill(parent.pid, signal.SIGCONT)
        with parent.stdout:
            fields = parent.stdout.readline().split()
        parent.wait(timeout=15)
    return float(fields[0]), fields[1] == 'True', fields[2], float(fields[3])


class LaunchTests(unittest.TestCase):
    def test_launch_does_not_inherit_open_stdin(self):
        # An interactive caller keeps stdin open; a CLI that reads stdin must not wait on it.
        with tempfile.TemporaryDirectory() as tmp:
            script = ('import sys; from pathlib import Path; from seatrial import process; t = Path(sys.argv[1]); '
                      'r = process.launch([sys.executable, "-c", "import sys; sys.stdin.read()"], '
                      't, None, t / "out", t / "err", 5); print("exit" in r, flush=True)')
            parent = subprocess.Popen([sys.executable, '-c', script, tmp], cwd=SOURCE, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True)
            try:
                completed = parent.stdout.readline().strip()
            finally:
                parent.stdin.close()
                parent.stdout.close()
                parent.wait(timeout=10)
        self.assertEqual(completed, 'True')

    def test_timeout_kills_term_ignoring_child_and_escaped_grandchild(self):
        # Sandboxed tool commands can run in their own session, outside the process group.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            program = ('import signal, subprocess, sys, time\n'
                       'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                       'g = subprocess.Popen([sys.executable, "-c", "import signal, time; '
                       'signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"], start_new_session=True)\n'
                       'open(sys.argv[1], "w").write(str(g.pid))\n'
                       'time.sleep(60)\n')
            marker = root / 'grandchild'
            with mock.patch.object(process, 'KILL_GRACE', 1.0):
                start = time.monotonic()
                result = process.launch([sys.executable, '-c', program, str(marker)], root, os.environ.copy(),
                                        root / 'out', root / 'err', timeout=2)
                elapsed = time.monotonic() - start
            grandchild = int(marker.read_text())
        self.assertIn('timeout', result)
        self.assertLess(elapsed, 2 + 1.0 + 1.5)  # Cap, SIGTERM grace, and scheduling slack.
        self.assertNotIn('kill_failed', result)
        self.assertFalse(process._alive(grandchild))
        self.assertEqual(result['stalled_s'], 0.0)

    def test_paused_launcher_reports_host_stall(self):
        # A paused host stops the launcher too; report the overrun instead of hiding it in wall time.
        stalled, timed_out, _, wall = paused_launch('import time; time.sleep(60)', 3)
        self.assertTrue(timed_out)
        self.assertGreater(stalled, 2.0)
        self.assertLess(wall, 3 + 4.0 + 6.5)

    def test_child_finishing_during_launcher_pause_records_stall(self):
        stalled, timed_out, code, _ = paused_launch('import time; time.sleep(2)', 30)
        self.assertFalse(timed_out)
        self.assertEqual(code, '0')
        self.assertGreater(stalled, 2.0)

    def test_output_that_looks_like_completion_does_not_end_the_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            program = ('import time; print(\'{"type":"result","subtype":"success"}\', flush=True); '
                       'time.sleep(10)')
            result = process.launch([sys.executable, '-c', program], root, os.environ.copy(),
                                    root / 'out', root / 'err', timeout=1)
            self.assertIn('timeout', result)
            self.assertIn('success', (root / 'out').read_text())


if __name__ == '__main__':
    unittest.main()
