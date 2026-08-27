import ipaddress
import socket
from urllib.parse import urlparse


BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
}
BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".home",
    ".lan",
)


def validate_calendar_url(url, *, resolve_dns=False):
    """Return a normalized calendar URL or raise ValueError for unsafe targets."""
    normalized_url = _normalize_calendar_url(url)
    parsed = urlparse(normalized_url)

    if parsed.scheme != "https":
        raise ValueError("Bitte nutze einen HTTPS-Kalenderlink.")
    if parsed.username or parsed.password:
        raise ValueError("Kalenderlinks dürfen keine Zugangsdaten in der URL enthalten.")
    if not parsed.hostname:
        raise ValueError("Bitte füge einen gültigen Kalenderlink ein.")

    host = _normalize_hostname(parsed.hostname)
    port = _safe_port(parsed)
    _validate_hostname(host)

    if not parsed.path.lower().endswith(".ics"):
        raise ValueError("Bitte nutze einen Google-iCal-Link oder eine direkte .ics-URL.")

    if resolve_dns:
        _validate_resolved_addresses(host, port or 443)

    return normalized_url


def _normalize_calendar_url(url):
    clean_url = (url or "").strip()
    if clean_url.startswith("webcal://"):
        return "https://" + clean_url.removeprefix("webcal://")
    return clean_url


def _normalize_hostname(hostname):
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("Der Hostname des Kalenderlinks ist ungültig.") from error


def _safe_port(parsed_url):
    try:
        port = parsed_url.port
    except ValueError as error:
        raise ValueError("Der Port des Kalenderlinks ist ungültig.") from error

    if port and port != 443:
        raise ValueError("Kalenderlinks dürfen nur den Standard-HTTPS-Port nutzen.")
    return port


def _validate_hostname(host):
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise ValueError("Der Kalenderlink darf nicht auf interne Hostnamen zeigen.")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("Der Kalenderlink muss auf einen öffentlichen Hostnamen zeigen.")
        return

    _validate_ip_address(address)


def _validate_resolved_addresses(host, port):
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("Der Hostname des Kalenderlinks konnte nicht aufgelöst werden.") from error

    if not addresses:
        raise ValueError("Der Hostname des Kalenderlinks konnte nicht aufgelöst werden.")

    for _family, _type, _proto, _canonname, sockaddr in addresses:
        _validate_ip_address(ipaddress.ip_address(sockaddr[0]))


def _validate_ip_address(address):
    if not address.is_global:
        raise ValueError("Der Kalenderlink darf nicht auf interne Netzwerkadressen zeigen.")
