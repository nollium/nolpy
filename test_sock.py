import unittest
from unittest.mock import MagicMock, patch
import socket
import ssl
from nolpy.sock import sock_open, SocketURLError, FlushProxy


class TestPySockURL(unittest.TestCase):
    def test_sock_open_invalid_url(self):
        with self.assertRaisesRegex(SocketURLError, "Invalid URL"):
            sock_open("tcp://invalid-url")  # Missing port

    @patch("socket.create_connection")
    def test_sock_open_tcp(self, mock_create_connection):
        mock_sock = MagicMock(spec=socket.socket)
        mock_create_connection.return_value = mock_sock
        mock_sock.makefile.return_value = MagicMock()
        mock_sock.makefile.return_value.read.return_value = b""

        result = sock_open("tcp://example.com:80")

        self.assertIsInstance(result, FlushProxy)
        mock_create_connection.assert_called_once_with(
            ("example.com", 80), timeout=10.0
        )
        mock_sock.setsockopt.assert_called_once_with(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
        )
        mock_sock.makefile.assert_called_once_with(mode="rwb", buffering=0)

    @patch("socket.create_connection")
    @patch("ssl.create_default_context")
    def test_sock_open_tls_insecure(self, mock_ssl_context, mock_create_connection):
        mock_sock = MagicMock(spec=socket.socket)
        mock_create_connection.return_value = mock_sock

        mock_context_instance = mock_ssl_context.return_value
        mock_tls_sock = MagicMock(spec=ssl.SSLSocket)
        mock_context_instance.wrap_socket.return_value = mock_tls_sock

        mock_tls_sock.makefile.return_value = MagicMock()
        mock_tls_sock.makefile.return_value.read.return_value = b""

        result = sock_open("tls://example.com:443")

        self.assertIsInstance(result, FlushProxy)
        self.assertFalse(mock_context_instance.check_hostname)
        self.assertEqual(mock_context_instance.verify_mode, ssl.CERT_NONE)
        mock_context_instance.wrap_socket.assert_called_with(
            mock_sock, server_hostname="example.com"
        )

    @patch("socket.create_connection")
    @patch("ssl.create_default_context")
    def test_sock_open_tls_verified(self, mock_ssl_context, mock_create_connection):
        mock_sock = MagicMock(spec=socket.socket)
        mock_create_connection.return_value = mock_sock

        mock_context_instance = mock_ssl_context.return_value
        mock_tls_sock = MagicMock(spec=ssl.SSLSocket)
        mock_context_instance.wrap_socket.return_value = mock_tls_sock

        mock_tls_sock.makefile.return_value = MagicMock()
        mock_tls_sock.makefile.return_value.read.return_value = b""

        result = sock_open("tls://example.com:443", verify=True)

        self.assertIsInstance(result, FlushProxy)
        mock_context_instance.wrap_socket.assert_called_with(
            mock_sock, server_hostname="example.com"
        )

    def test_flush_proxy_shims(self):
        mock_obj = MagicMock()
        mock_obj.read.return_value = b""
        proxy = FlushProxy(mock_obj)

        data = b"hello"
        proxy.write(data)
        mock_obj.write.assert_called_with(data)
        mock_obj.flush.assert_called()

        proxy.send(data)
        self.assertEqual(mock_obj.write.call_count, 2)

        proxy.sendall(data)
        self.assertEqual(mock_obj.write.call_count, 3)

        proxy.read(10)
        mock_obj.read.assert_called_with(10)

        proxy.recv(10)
        self.assertEqual(mock_obj.read.call_count, 2)

        proxy.readline()
        mock_obj.readline.assert_called()

    @patch("socket.create_connection")
    def test_sock_open_connection_error(self, mock_create_connection):
        mock_create_connection.side_effect = socket.error("Connection refused")

        with self.assertRaisesRegex(SocketURLError, "Failed to connect"):
            sock_open("tcp://example.com:80")

    @patch("socket.create_connection")
    @patch("ssl.create_default_context")
    def test_sock_open_tls_error(self, mock_ssl_context, mock_create_connection):
        mock_sock = MagicMock()
        mock_create_connection.return_value = mock_sock

        mock_context_instance = mock_ssl_context.return_value
        mock_context_instance.wrap_socket.side_effect = ssl.SSLError("Handshake failed")

        with self.assertRaisesRegex(SocketURLError, "TLS handshake failed"):
            sock_open("tls://example.com:443")

        mock_sock.close.assert_called()

    def test_flush_proxy_drain(self):
        mock_obj = MagicMock()
        mock_raw = mock_obj.raw = MagicMock()
        mock_raw.gettimeout.return_value = 10.0
        mock_obj.read.side_effect = [b"data", b""]

        proxy = FlushProxy(mock_obj)
        proxy.drain()

        self.assertEqual(mock_obj.read.call_count, 2)
        mock_raw.settimeout.assert_any_call(0.0)
        mock_raw.settimeout.assert_any_call(10.0)

    @patch("socket.create_connection")
    def test_sock_open_auto_drain(self, mock_create_connection):
        mock_sock = MagicMock()
        mock_create_connection.return_value = mock_sock
        mock_makefile = mock_sock.makefile.return_value = MagicMock()
        mock_raw = mock_makefile.raw = MagicMock()
        mock_raw.gettimeout.return_value = 10.0
        mock_makefile.read.return_value = b""

        s = sock_open("tcp://example.com:80")
        s.close()
        mock_makefile.read.assert_called()

        mock_makefile.read.reset_mock()
        s = sock_open("tcp://example.com:80", auto_drain=False)
        s.close()
        mock_makefile.read.assert_not_called()

    def test_flush_proxy_string_write(self):
        mock_obj = MagicMock()
        mock_obj.read.return_value = b""
        proxy = FlushProxy(mock_obj)

        test_str = "hello"
        proxy.write(test_str)

        mock_obj.write.assert_called_with(test_str.encode("latin-1"))
        mock_obj.flush.assert_called()

    def test_read_http_res_content_length(self):
        mock_obj = MagicMock()
        raw_res = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
        expected = raw_res

        def side_effect(n=-1):
            nonlocal raw_res
            if n == -1 or n > 1000:
                idx = raw_res.find(b"\n")
                if idx == -1:
                    chunk = raw_res
                    raw_res = b""
                    return chunk
                chunk = raw_res[: idx + 1]
                raw_res = raw_res[idx + 1 :]
                return chunk
            chunk = raw_res[:n]
            raw_res = raw_res[n:]
            return chunk

        mock_obj.readline.side_effect = side_effect
        mock_obj.read.side_effect = side_effect

        proxy = FlushProxy(mock_obj, auto_drain=False)
        res = proxy.read_http_res()

        self.assertEqual(res, expected)
        mock_obj.close.assert_not_called()

    def test_read_http_res_chunked(self):
        mock_obj = MagicMock()
        raw_res = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
        expected = raw_res

        def side_effect(n=-1):
            nonlocal raw_res
            if n == -1 or n > 1000:
                idx = raw_res.find(b"\n")
                if idx == -1:
                    chunk = raw_res
                    raw_res = b""
                    return chunk
                chunk = raw_res[: idx + 1]
                raw_res = raw_res[idx + 1 :]
                return chunk
            chunk = raw_res[:n]
            raw_res = raw_res[n:]
            return chunk

        mock_obj.readline.side_effect = side_effect
        mock_obj.read.side_effect = side_effect

        proxy = FlushProxy(mock_obj, auto_drain=False)
        res = proxy.read_http_res()

        self.assertEqual(res, expected)
        mock_obj.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
