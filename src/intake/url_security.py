"""URL security: SSRF protection via private-IP blocklist and scheme validation.

All external-fetch code paths should use :func:`safe_fetch_session` or
validate URLs through :func:`is_safe_public_url` before issuing requests.
"""

import ipaddress
import socket
from urllib.parse import urlparse

# 出站请求禁止访问的私有或保留 IPv4 网段
_BLOCKED_V4_NETWORKS = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.IPv4Network("0.0.0.0/8"),        # "this network"
    ipaddress.IPv4Network("224.0.0.0/4"),      # multicast
    ipaddress.IPv4Network("240.0.0.0/4"),      # reserved (class E)
]

_BLOCKED_V6_NETWORKS = [
    ipaddress.IPv6Network("::1/128"),           # loopback
    ipaddress.IPv6Network("fc00::/7"),          # unique local
    ipaddress.IPv6Network("fe80::/10"),         # link-local
    ipaddress.IPv6Network("ff00::/8"),          # multicast
]

# 额外禁止的主机名模式，使用不区分大小写的子串匹配
_BLOCKED_HOSTNAME_PATTERNS = [
    "metadata.google.internal",
    "169.254.169.254",
]


def _resolve_ip_addresses(host: str) -> list[str]:
    """Resolve *host* to IPv4 / IPv6 address strings (best-effort)."""
    try:
        info = socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM,
                                  0, socket.AI_ADDRCONFIG)
    except (socket.gaierror, OSError):
        return []
    ips: set[str] = set()
    for family, _, _, _, sockaddr in info:
        addr = sockaddr[0]
        if family in (socket.AF_INET, socket.AF_INET6):
            ips.add(addr)
    return sorted(ips)


def is_safe_public_url(url: str) -> bool:
    """Return True if *url* resolves to a public, non-internal address.

    Blocks private IPv4 ranges, IPv6 unique-local / loopback / link-local,
    and known metadata hostname patterns.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False

    # 阻止已知的云元数据主机名
    for pattern in _BLOCKED_HOSTNAME_PATTERNS:
        if pattern in host:
            return False

    # 优先解析 IP 字面量，避免对裸 IP 执行 DNS 查询
    try:
        addr = ipaddress.ip_address(host)
        for net in _BLOCKED_V4_NETWORKS:
            if addr in net:
                return False
        for net in _BLOCKED_V6_NETWORKS:
            if addr in net:
                return False
        return True
    except ValueError:
        pass  # not an IP literal — resolve below

    # 解析主机名并检查每一个返回的 IP
    ips = _resolve_ip_addresses(host)
    if not ips:
        return False  # unresolvable — reject to be safe

    for ip_str in ips:
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in _BLOCKED_V4_NETWORKS:
            if addr in net:
                return False
        for net in _BLOCKED_V6_NETWORKS:
            if addr in net:
                return False
    return True
