"""Rules about Webhook URLs: which hosts are Blocked addresses, and how to mask one."""

import ipaddress
from collections.abc import Collection
from urllib.parse import urlsplit

MASKED_QUERY = "?…"


def mask_url(url: str) -> str:
    """The URL without userinfo and with any query string replaced by `?…`.

    Every log line and `x-last-error` shows a Webhook URL through this.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = host if parts.port is None else f"{host}:{parts.port}"
    except ValueError:
        return "<unparseable webhook url>"  # never echo what could hold a secret
    masked = f"{parts.scheme}://{netloc}{parts.path}"
    return masked + MASKED_QUERY if "?" in url else masked


def is_blocked_address(address: str) -> bool:
    """True for a Blocked address: loopback, private, link-local, unspecified,
    multicast or reserved, IPv4 or IPv6 (an IPv4-mapped IPv6 is judged as IPv4)."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # `not is_global` also covers ranges none of the named flags do, such as
    # carrier-grade NAT (100.64.0.0/10) and IPv6 unique local addresses.
    return (
        not ip.is_global
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
    )


def is_allowed_host(host: str, allowed_hosts: Collection[str]) -> bool:
    """Exact, case-insensitive match against `WEBHOOK_ALLOWED_HOSTS`."""
    return host.rstrip(".").lower() in {h.lower() for h in allowed_hosts}


def rejected_literal(url: str, allowed_hosts: Collection[str]) -> bool:
    """True if the URL's host is `localhost` or a literal Blocked address.

    A cheap fail-fast for the API; names are not resolved there (the consumer
    resolves and checks at send time).
    """
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if is_allowed_host(host, allowed_hosts):
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return is_blocked_address(host)
    except ValueError:  # a name, not an IP
        return False
