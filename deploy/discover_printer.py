#!/usr/bin/env python3
"""Scan the local /24 for an ESC/POS printer on TCP 9100.

Prints ``tcp:HOST:9100`` on success (exit 0). Soft-fail: prints ``dummy`` and
a warning on stderr (exit 0) when nothing is found — installers keep working.

Usage:
    python deploy/discover_printer.py
    python deploy/discover_printer.py --timeout 0.15 --port 9100
"""
from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed


def _local_ipv4s() -> list[str]:
    hosts: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            hosts.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in hosts and not ip.startswith("127."):
                hosts.append(ip)
    except OSError:
        pass
    return [h for h in hosts if not h.startswith("127.")]


def _probe(host: str, port: int, timeout: float) -> str | None:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return host
    except OSError:
        return None


def discover(port: int = 9100, timeout: float = 0.2, workers: int = 64) -> str | None:
    locals_ = _local_ipv4s()
    if not locals_:
        return None
    seen: set[str] = set()
    candidates: list[str] = []
    for local in locals_:
        try:
            net = ipaddress.ip_network(f"{local}/24", strict=False)
        except ValueError:
            continue
        for host in net.hosts():
            h = str(host)
            if h in seen or h == local:
                continue
            seen.add(h)
            candidates.append(h)
    if not candidates:
        return None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_probe, h, port, timeout): h for h in candidates}
        for fut in as_completed(futures):
            hit = fut.result()
            if hit:
                # Cancel remaining work best-effort
                for other in futures:
                    other.cancel()
                return hit
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="Find a LAN ESC/POS printer on TCP 9100.")
    p.add_argument("--port", type=int, default=9100)
    p.add_argument("--timeout", type=float, default=0.2, help="Per-host TCP connect timeout (s)")
    p.add_argument("--strict", action="store_true", help="Exit 1 if none found (default: print dummy)")
    args = p.parse_args()
    host = discover(port=args.port, timeout=args.timeout)
    if host:
        print(f"tcp:{host}:{args.port}")
        return 0
    print(
        f"WARNING: no open TCP {args.port} on local /24 — using dummy printer.",
        file=sys.stderr,
    )
    print("dummy")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
