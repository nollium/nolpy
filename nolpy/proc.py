"""
proc: Simple shell interaction utility for nolpy.
"""

import subprocess
import os
import fcntl
import selectors
import sys
from typing import Union, Optional


class ProcessError(Exception):
    """Custom exception for process-related failures."""

    pass


class ProcessProxy:
    """
    A unified wrapper for subprocess interaction.
    Provides a file-like interface where write() goes to stdin
    and read() reads from stdout.
    """

    def __init__(self, proc: subprocess.Popen, auto_drain: bool = True):
        self._proc = proc
        self._stdin = proc.stdin
        self._stdout = proc.stdout
        self._auto_drain = auto_drain
        self._closed = False

    def write(self, data: Union[bytes, str, bytearray]) -> int:
        """Writes to process stdin and flushes immediately."""
        if self._stdin is None or self._closed:
            return 0
        if isinstance(data, str):
            data = data.encode("latin-1")
        elif isinstance(data, bytearray):
            data = bytes(data)
        n = self._stdin.write(data)
        self._stdin.flush()
        return n

    def send(self, data: Union[bytes, str, bytearray]) -> int:
        """Shim for write."""
        return self.write(data)

    def sendall(self, data: Union[bytes, str, bytearray]) -> None:
        """Shim for write (already flushes)."""
        self.write(data)

    def read(self, n: int = -1) -> bytes:
        """Reads from process stdout."""
        if self._stdout is None or self._closed:
            return b""
        return self._stdout.read(n)

    def recv(self, n: int) -> bytes:
        """Shim for read."""
        return self.read(n)

    def readline(self, limit: int = -1) -> bytes:
        """Reads a line from process stdout."""
        if self._stdout is None or self._closed:
            return b""
        return self._stdout.readline(limit)

    def drain(self) -> None:
        """Reads and discards all available data from stdout/stderr without blocking."""
        if self._closed:
            return

        def _drain_stream(stream):
            if not stream:
                return
            try:
                # Set non-blocking
                fd = stream.fileno()
                fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
                try:
                    while True:
                        if not stream.read(4096):
                            break
                except (IOError, ValueError):
                    pass
                finally:
                    # Restore blocking
                    fcntl.fcntl(fd, fcntl.F_SETFL, fl)
            except (IOError, ValueError):
                pass

        _drain_stream(self._stdout)
        _drain_stream(self._proc.stderr)

    def print(self, timeout: float = 0.2) -> None:
        """
        Multiplexes stdout and stderr and prints them to the terminal.
        If a read blocks for more than the specified timeout (default 200ms),
        the operation is cancelled (returns from the function).
        """
        if self._closed:
            return

        sel = selectors.DefaultSelector()

        def _make_nonblocking(stream):
            if not stream:
                return None
            fd = stream.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            return fd

        fds_active = []
        if self._stdout:
            _make_nonblocking(self._stdout)
            sel.register(self._stdout, selectors.EVENT_READ, sys.stdout.buffer)
            fds_active.append(self._stdout)
        if self._proc.stderr:
            _make_nonblocking(self._proc.stderr)
            sel.register(self._proc.stderr, selectors.EVENT_READ, sys.stderr.buffer)
            fds_active.append(self._proc.stderr)

        try:
            while fds_active:
                events = sel.select(timeout=timeout)
                if not events:
                    # Non-blocking timeout reached: "cancel the read"
                    break

                for key, _ in events:
                    try:
                        data = key.fileobj.read(4096)
                    except Exception:
                        data = None

                    if data:
                        key.data.write(data)
                        key.data.flush()
                    else:
                        sel.unregister(key.fileobj)
                        if key.fileobj in fds_active:
                            fds_active.remove(key.fileobj)

                if self._proc.poll() is not None and not fds_active:
                    break
        finally:
            sel.close()

    def waitprint(self) -> None:
        """Waits for the process to exit and then prints all output."""
        self.wait()
        self.print()

    def close(self) -> None:
        """Closes the process and its streams."""
        if not self._closed:
            if self._auto_drain:
                self.drain()

            if self._stdin:
                self._stdin.close()
            if self._stdout:
                self._stdout.close()
            if self._proc.stderr:
                self._proc.stderr.close()

            # Try to terminate nicely, then kill if needed
            try:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    self._proc.wait(timeout=0.1)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    if self._proc.poll() is None:
                        self._proc.kill()
                except ProcessLookupError:
                    pass
            self._closed = True

    def __getattr__(self, name):
        # Fallback to stdout for attributes if possible, else proc
        if self._stdout and hasattr(self._stdout, name):
            return getattr(self._stdout, name)
        return getattr(self._proc, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        # In __del__, we must be careful not to trigger exceptions during shutdown
        try:
            if not self._closed:
                self.close()
        except Exception:
            pass


def sh(cmd: str, env: Optional[dict] = None, auto_drain: bool = True) -> ProcessProxy:
    """
    Spawns a shell command and returns a ProcessProxy for interaction.

    Args:
        cmd (str): The command to run (via shell).
        env (dict): Optional environment variables.
        auto_drain (bool): If True, discards available output on close.

    Returns:
        ProcessProxy: An interactive wrapper for the process.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env or os.environ.copy(),
            bufsize=0,  # Unbuffered
        )
        return ProcessProxy(proc, auto_drain=auto_drain)
    except ValueError as e:
        if "null byte" in str(e):
            raise ProcessError(
                f"Command contains null bytes, which is not allowed by the OS: {cmd!r}"
            ) from e
        raise


def ex(
    path: Union[str, bytes, bytearray],
    *args: Union[str, bytes, bytearray],
    argv0: Optional[Union[str, bytes, bytearray]] = None,
    env: Optional[dict] = None,
    auto_drain: bool = True,
) -> ProcessProxy:
    """
    Spawns a process directly (no shell) and returns a ProcessProxy.

    Args:
        path: Path to the executable.
        *args: Command line arguments.
        argv0: Optional override for argv[0]. Defaults to path basename.
        env: Optional environment variables.
        auto_drain: If True, discards available output on close.
    """

    def _to_bytes(s):
        if isinstance(s, bytearray):
            return bytes(s)
        return s.encode("latin-1") if isinstance(s, str) else s

    bin_path = _to_bytes(path)
    if argv0 is None:
        argv0 = os.path.basename(bin_path)

    cmd_args = [_to_bytes(argv0)] + [_to_bytes(a) for a in args]

    try:
        proc = subprocess.Popen(
            cmd_args,
            executable=bin_path,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env or os.environ.copy(),
            bufsize=0,
        )
        return ProcessProxy(proc, auto_drain=auto_drain)
    except ValueError as e:
        if "null byte" in str(e):
            raise ProcessError(
                f"Arguments or path contain null bytes, which is not allowed by the OS: {cmd_args!r}"
            ) from e
        raise


def wine_exec(
    path: Union[str, bytes, bytearray],
    *args: Union[str, bytes, bytearray],
    env: Optional[dict] = None,
    auto_drain: bool = True,
) -> ProcessProxy:
    """Shortcut to execute a binary via Wine."""
    return ex("wine", path, *args, env=env, auto_drain=auto_drain)


wex = wine_exec

from shlex import quote

qx = quote
