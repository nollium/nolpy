"""
pysock-url: A unified, Pythonic interface for TCP and TLS streams using URL-based addressing.
"""

import socket
import ssl
import urllib.parse
import http.client
from typing import BinaryIO, Optional, Union


class SocketURLError(Exception):
    """Custom exception for pysock_url errors."""

    pass


class FlushProxy:
    """A wrapper for binary file objects that flushes after every write and supports auto-draining."""

    def __init__(self, obj: BinaryIO, auto_drain: bool = True):
        self._obj = obj
        self._auto_drain = auto_drain
        self._closed = False

    def write(self, data: Union[bytes, str, bytearray]) -> int:
        if isinstance(data, str):
            data = data.encode("latin-1")
        elif isinstance(data, bytearray):
            data = bytes(data)
        n = self._obj.write(data)
        self._obj.flush()
        return n

    def read_http_res(self) -> bytes:
        """
        Reads the next HTTP response from the socket and returns the raw bytes.
        Supports Content-Length and Transfer-Encoding: chunked.
        Does NOT close the underlying connection.
        """

        class RecordingFile:
            """Wraps a file object to record every byte read."""

            def __init__(self, fp):
                self.fp = fp
                self.recorded = b""

            def read(self, n=-1):
                chunk = self.fp.read(n)
                self.recorded += chunk
                return chunk

            def readline(self, limit=-1):
                chunk = self.fp.readline(limit)
                self.recorded += chunk
                return chunk

            def __getattr__(self, name):
                return getattr(self.fp, name)

            def close(self):
                # Prevent closing the underlying stream
                pass

        class NoCloseSocket:
            """Wrapper for HTTPResponse that uses our RecordingFile."""

            def __init__(self, recorder):
                self.recorder = recorder

            def makefile(self, *args, **kwargs):
                return self.recorder

            def close(self):
                pass

        recorder = RecordingFile(self._obj)
        # Use stdlib HTTPResponse to handle the state machine of "where the response ends"
        resp = http.client.HTTPResponse(NoCloseSocket(recorder))  # type: ignore
        try:
            resp.begin()
            # This reads the body (potentially multiple reads for chunked/multiple chunks)
            resp.read()
            return recorder.recorded
        finally:
            # We explicitly don't close resp to avoid any risk of closing recorder.fp
            pass

    def drain(self) -> None:
        """Reads and discards all available data without blocking."""
        try:
            # Access underlying socket via the RawIO object
            raw_sock = getattr(self._obj, "raw", self._obj)
            if hasattr(raw_sock, "settimeout"):
                original_timeout = raw_sock.gettimeout()
                raw_sock.settimeout(0.0)  # Non-blocking
                try:
                    while True:
                        if not self._obj.read(4096):
                            break
                except (socket.error, BlockingIOError, OSError):
                    pass
                finally:
                    raw_sock.settimeout(original_timeout)
        except Exception:
            pass

    def send(self, data: Union[bytes, str, bytearray]) -> int:
        """Shim for write."""
        return self.write(data)

    def sendall(self, data: Union[bytes, str, bytearray]) -> None:
        """Shim for write (already flushes)."""
        self.write(data)

    def read(self, n: int = -1) -> bytes:
        return self._obj.read(n)

    def recv(self, n: int) -> bytes:
        """Shim for read."""
        return self.read(n)

    def readline(self, limit: int = -1) -> bytes:
        return self._obj.readline(limit)

    def close(self) -> None:
        if not self._closed:
            if self._auto_drain:
                self.drain()
            self._obj.close()
            self._closed = True

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def sock_open(
    url: str, timeout: float = 10.0, auto_drain: bool = True, verify: bool = False
) -> Union[BinaryIO, FlushProxy]:
    """
    Opens a TCP or TLS connection based on the provided URL and returns a file-like object.

    Supported schemes:
    - tcp://<host>:<port> -> Raw TCP socket.
    - tls://<host>:<port> -> TLS wrapped socket.
    - ssl://<host>:<port> -> Alias for tls://.

    Args:
        url (str): The URL to connect to.
        timeout (float): Connection timeout in seconds.
        auto_drain (bool): If True, discards available data on close. Default is True.
        verify (bool): If True, verifies SSL certificates. Default is False.

    Returns:
        Union[BinaryIO, FlushProxy]: A binary file-like object supporting read, write, and readline.
                                    Automatically flushes on write.

    Raises:
        SocketURLError: If the URL scheme is unsupported or a connection error occurs.
    """
    parsed = urllib.parse.urlparse(url)
    scheme: str = parsed.scheme.lower()
    host: Optional[str] = parsed.hostname
    port: Optional[int] = parsed.port

    if not host or port is None:
        raise SocketURLError(f"Invalid URL: {url}. Host and port are required.")

    sock: Union[socket.socket, ssl.SSLSocket]
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        # Yeet Nagle's algorithm for instant packet delivery
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (socket.error, socket.timeout) as e:
        raise SocketURLError(f"Failed to connect to {host}:{port}: {e}")

    try:
        if scheme in ("tls", "ssl"):
            if not verify:
                # Insecure context
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            else:
                context = ssl.create_default_context()

            try:
                sock = context.wrap_socket(sock, server_hostname=host)
            except ssl.SSLError as e:
                sock.close()
                raise SocketURLError(f"TLS handshake failed: {e}")
        elif scheme != "tcp":
            sock.close()
            raise SocketURLError(
                f"Unsupported scheme: {scheme}. Use 'tcp', 'tls', or 'ssl'."
            )

        # Create a file-like object
        # mode='rwb' for read/write binary, buffering=0 for unbuffered access
        return FlushProxy(sock.makefile(mode="rwb", buffering=0), auto_drain=auto_drain)  # type: ignore
    except Exception:
        sock.close()
        raise


sopen = sock_open
