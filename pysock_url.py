"""
pysock-url: A unified, Pythonic interface for TCP and TLS streams using URL-based addressing.
"""

import socket
import ssl
import urllib.parse
from contextlib import contextmanager

class SocketURLError(Exception):
    """Custom exception for pysock_url errors."""
    pass

@contextmanager
def sock_open(url: str, timeout=10):
    """
    Opens a TCP or TLS connection based on the provided URL and returns a file-like object.
    
    Supported schemes:
    - tcp://<host>:<port> -> Raw TCP socket.
    - tls://<host>:<port> -> TLS wrapped socket.
    - ssl://<host>:<port> -> Alias for tls://.

    Args:
        url (str): The URL to connect to.
        timeout (int): Connection timeout in seconds.

    Returns:
        IO[bytes]: A file-like object supporting read, write, and readline.
    
    Raises:
        SocketURLError: If the URL scheme is unsupported or a connection error occurs.
    """
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    port = parsed.port

    if not host or not port:
        raise SocketURLError(f"Invalid URL: {url}. Host and port are required.")

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except (socket.error, socket.timeout) as e:
        raise SocketURLError(f"Failed to connect to {host}:{port}: {e}")

    file_obj = None
    try:
        if scheme in ('tls', 'ssl'):
            context = ssl.create_default_context()
            try:
                sock = context.wrap_socket(sock, server_hostname=host)
            except ssl.SSLError as e:
                sock.close()
                raise SocketURLError(f"TLS handshake failed: {e}")
        elif scheme != 'tcp':
            sock.close()
            raise SocketURLError(f"Unsupported scheme: {scheme}. Use 'tcp', 'tls', or 'ssl'.")

        # Create a file-like object
        # mode='rwb' for read/write binary, buffering=0 for unbuffered access
        file_obj = sock.makefile(mode='rwb', buffering=0)
        yield file_obj
    finally:
        if file_obj:
            file_obj.close()
        sock.close()
