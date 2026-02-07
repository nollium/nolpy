import unittest
import sys
from nolpy.proc import sh, ex
import time


class TestProc(unittest.TestCase):
    def test_sh_basic(self):
        # Simple echo test
        p = sh("echo 'hello'")
        out = p.read().strip()
        self.assertEqual(out, b"hello")
        p.close()

    def test_sh_interactive(self):
        # Test interactive cat
        p = sh("cat")
        p.write(b"interact\n")
        # Give a small delay if needed, but cat is instant
        out = p.readline().strip()
        self.assertEqual(out, b"interact")
        p.close()

    def test_sh_stderr(self):
        # Test reading stderr (via attribute proxying)
        p = sh("echo 'error' >&2")
        # stderr is accessible via p.stderr because of __getattr__
        err = p.stderr.read().strip()
        self.assertEqual(err, b"error")
        p.close()

    def test_context_manager(self):
        with sh("cat") as p:
            p.write("context\n")
            out = p.readline().strip()
            self.assertEqual(out, b"context")
        # Should be closed automatically

    def test_sh_string_sugar(self):
        with sh("cat") as p:
            p.write("sugar")  # string instead of bytes
            p.stdin.close()  # EOF
            out = p.read()
            self.assertEqual(out, b"sugar")

    def test_sendall_shim(self):
        with sh("cat") as p:
            p.sendall("shim")
            p.stdin.close()
            out = p.read()
            self.assertEqual(out, b"shim")

    def test_auto_drain(self):
        p = sh("echo 'background output'")
        p.close()  # Should drain stdout internally

    def test_print_timeout(self):
        # Capture sys.stdout/stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        class Buffer:
            def __init__(self):
                self.data = b""

            def write(self, d):
                self.data += d

            def flush(self):
                pass

            @property
            def buffer(self):
                return self

        stdout_buf = Buffer()
        stderr_buf = Buffer()

        sys.stdout = stdout_buf
        sys.stderr = stderr_buf

        try:
            # Command that prints then sleeps
            cmd = "echo 'immediate'; sleep 0.5; echo 'delayed'"
            with sh(cmd) as p:
                start = time.time()
                p.print(timeout=0.1)
                end = time.time()

                # Should have printed immediate
                self.assertIn(b"immediate", stdout_buf.data)
                # Should have returned after ~0.1s because of timeout, NOT 0.5s
                self.assertLess(end - start, 0.4)
                self.assertNotIn(b"delayed", stdout_buf.data)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def test_ex_basic(self):
        # Use 'ls' as a simple binary test
        with ex("/bin/ls", "-d", "/") as p:
            out = p.read().strip()
            self.assertEqual(out, b"/")

    def test_ex_argv0(self):
        # Test custom argv0
        # We can use /bin/bash -c 'echo $0' to check argv0
        with ex("/bin/bash", "-c", "echo $0", argv0="custom_name") as p:
            out = p.read().strip()
            self.assertEqual(out, b"custom_name")

    def test_waitprint(self):
        # Capture sys.stdout
        old_stdout = sys.stdout

        class Buffer:
            def __init__(self):
                self.data = b""

            def write(self, d):
                self.data += d

            def flush(self):
                pass

            @property
            def buffer(self):
                return self

        stdout_buf = Buffer()
        sys.stdout = stdout_buf

        try:
            with ex("echo", "finished") as p:
                p.waitprint()

            self.assertIn(b"finished", stdout_buf.data)
        finally:
            sys.stdout = old_stdout


if __name__ == "__main__":
    unittest.main()
