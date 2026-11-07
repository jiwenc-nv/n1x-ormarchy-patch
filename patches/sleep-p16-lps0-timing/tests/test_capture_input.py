"""Exercise the stop prompt on a real terminal without arming sleep tracing."""
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import time
import unittest


CAPTURE = Path(__file__).resolve().parents[1] / 'capture-sleep.py'
PROMPT = b'Type done and press Enter after the test to stop capturing: '


class CaptureInputTests(unittest.TestCase):
    def test_pasted_newlines_do_not_end_capture(self):
        master, slave = pty.openpty()
        # Reproduce an extra blank line queued with the command before input().
        os.write(master, b'\n\n')
        process = subprocess.Popen(
            [sys.executable, '-c',
             'import runpy, sys; '
             'runpy.run_path(sys.argv[1])["wait_for_done"](); '
             'print("CAPTURE_STOPPED", flush=True)', str(CAPTURE)],
            stdin=slave, stdout=slave, stderr=slave,
        )
        os.close(slave)
        received = b''

        def wait_for(marker, count=1):
            nonlocal received
            deadline = time.monotonic() + 5
            while received.count(marker) < count:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, received.decode(errors='replace'))
                ready, _, _ = select.select([master], [], [], remaining)
                self.assertTrue(ready, received.decode(errors='replace'))
                received += os.read(master, 4096)

        try:
            wait_for(PROMPT, 3)
            self.assertIsNone(process.poll())
            self.assertNotIn(b'CAPTURE_STOPPED', received)
            os.write(master, b'not done\n')
            wait_for(PROMPT, 4)
            self.assertIsNone(process.poll())
            os.write(master, b'done\n')
            wait_for(b'CAPTURE_STOPPED')
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)


if __name__ == '__main__':
    unittest.main()
