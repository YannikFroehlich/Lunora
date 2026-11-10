"""Fetching a URL a user typed in, without letting it reach the server's own network.

The boundary against SSRF: only http(s) on the default port, no credentials, no internal host
names, and every address actually connected to (first request and each redirect) must be global,
so DNS rebinding can't reach 127.0.0.1 either. The whole fetch, TLS handshake and headers
included, is cut at a deadline. Error messages are German, safe to show and never contain the URL.
"""

import http.client
import ipaddress
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 4
FETCH_TIMEOUT_SECONDS = 10
FETCH_DEADLINE_SECONDS = 30


class FetchError(Exception):
    """A failure whose German message is safe to show and store: it never contains the URL."""


def normalize_url(raw_url):
    """The URL to fetch, or FetchError when it isn't http(s) on the default port of a public host.
    The addresses a name resolves to are checked when connecting."""
    url = (raw_url or "").strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FetchError("Die Adresse ist ungültig.") from None
    if parts.scheme.lower() not in DEFAULT_PORTS or not parts.hostname:
        raise FetchError("Bitte gib eine https- oder http-Adresse ein.")
    if parts.username or parts.password:
        raise FetchError("Die Adresse darf keine Zugangsdaten enthalten.")
    if port not in (None, DEFAULT_PORTS[parts.scheme.lower()]):
        raise FetchError("Nur Adressen ohne eigenen Port werden unterstützt.")
    host = _ascii_host(parts.hostname)
    try:
        is_public = ipaddress.ip_address(host).is_global
    except ValueError:
        is_public = "." in host and not host.endswith((".localhost", ".local", ".internal", ".lan"))
    if not is_public:
        raise FetchError("Die Adresse muss auf einen öffentlichen Server zeigen.")
    return url


def _ascii_host(hostname):
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise FetchError("Die Adresse ist ungültig.") from None


def _public_connection(address, timeout=None, source_address=None):
    """socket.create_connection for http.client that only connects to public addresses. The check
    runs on the addresses actually connected to, so a name re-resolving to 127.0.0.1 (DNS
    rebinding) or a redirect to an internal host can't reach the server's own network."""
    host, port = address
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise FetchError("Der Server der Adresse wurde nicht gefunden.") from None
    if not infos or not all(ipaddress.ip_address(info[4][0]).is_global for info in infos):
        raise FetchError("Die Adresse muss auf einen öffentlichen Server zeigen.")
    error = None
    for family, socket_type, proto, _name, sockaddr in infos:
        sock = socket.socket(family, socket_type, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sockaddr)
        except OSError as connect_error:
            sock.close()
            error = connect_error
            continue
        return sock
    raise error


class _DeadlineCut:
    """Cuts a connection at the fetch deadline. Socket timeouts only limit single reads, so a server
    sending one byte at a time could otherwise hold a fetch open for days, in the TLS handshake and
    the headers as well as the body. Shuts down a dup of the socket because TLS wrapping detaches
    the original."""

    def __init__(self, deadline):
        self.deadline = deadline
        self.watched = None
        self.timer = None

    def connect(self, address, timeout=None, source_address=None):
        sock = _public_connection(address, timeout, source_address)
        self.watched = sock.dup()
        self.timer = threading.Timer(max(self.deadline - time.monotonic(), 0), self._cut)
        self.timer.daemon = True
        self.timer.start()
        return sock

    def _cut(self):
        try:
            self.watched.shutdown(socket.SHUT_RDWR)
        except OSError:  # Already closed.
            pass

    def release(self):
        if self.timer:
            self.timer.cancel()
            self.watched.close()


def read_capped(response, max_bytes, too_large):
    """The whole body, or FetchError(too_large) once it grows past `max_bytes`."""
    chunks = []
    size = 0
    while chunk := response.read(65536):
        size += len(chunk)
        if size > max_bytes:
            raise FetchError(too_large)
        chunks.append(chunk)
    return b"".join(chunks)


def fetch(url, read, *, user_agent, accept, server="Der Server"):
    """GET `url`, following up to MAX_REDIRECTS redirects (each one checked again), and return
    `read(response)` for a 200 answer. `read` runs before the deadline cut is released, so a slow
    body is cut too; `server` names the other side in error messages ("Der Kalender-Server")."""
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    for _redirect in range(MAX_REDIRECTS + 1):
        url = normalize_url(url)
        parts = urlsplit(url)
        connection_class = (
            http.client.HTTPSConnection if parts.scheme.lower() == "https" else http.client.HTTPConnection
        )
        # An explicit port, or http.client would read the end of an IPv6 literal as one.
        connection = connection_class(
            _ascii_host(parts.hostname), DEFAULT_PORTS[parts.scheme.lower()], timeout=FETCH_TIMEOUT_SECONDS
        )
        # http.client opens its socket through this attribute.
        cut = _DeadlineCut(deadline)
        connection._create_connection = cut.connect
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            connection.request("GET", path, headers={"User-Agent": user_agent, "Accept": accept})
            response = connection.getresponse()
            if response.status in REDIRECT_STATUSES and response.getheader("Location"):
                url = urljoin(url, response.getheader("Location"))
                continue
            if response.status != 200:
                raise FetchError(f"{server} antwortet mit Fehler {response.status}.")
            result = read(response)
            # A connection cut at the deadline ends like a complete download.
            if time.monotonic() > deadline:
                raise FetchError(f"{server} antwortet zu langsam.")
            return result
        except (OSError, http.client.HTTPException):
            raise FetchError(f"{server} ist nicht erreichbar.") from None
        finally:
            connection.close()
            cut.release()
    raise FetchError("Die Adresse leitet zu oft weiter.")
