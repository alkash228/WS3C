from __future__ import annotations

import errno
import os
import socket
import socketserver

import samv.config as cfg
from samv.api.client import discovered_lan_ipv4
from samv.config import MAX_PORT_TRIES, PORT, RUNTIME
from samv.http.handler import SamvHandler
from samv.inf.catalog import ensure_inf_dirs
from samv.parallel import worker_count


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def addr_in_use(exc: OSError) -> bool:

    """Порт уже занят или нет."""
    if getattr(exc, "winerror", None) == 10048:
        return True
    return exc.errno == errno.EADDRINUSE


def bind_server() -> tuple[ReusableTCPServer, int]:

    """Поднимаем http на свободном порту."""
    last_exc: OSError | None = None
    for i in range(MAX_PORT_TRIES):
        port = PORT + i
        try:
            return ReusableTCPServer(("0.0.0.0", port), SamvHandler), port
        except OSError as exc:
            last_exc = exc
            if addr_in_use(exc):
                continue
            raise
    raise OSError(f"Cannot bind port range {PORT}-{PORT + MAX_PORT_TRIES - 1}: {last_exc!r}")


def main() -> None:

    """Старт WEB_samv."""
    ensure_inf_dirs()
    os.chdir(str(cfg.ROOT))
    httpd, port = bind_server()
    RUNTIME["web_port"] = port
    RUNTIME["lan_ipv4"] = discovered_lan_ipv4()
    RUNTIME["api_port"] = cfg.API_PORT_HINT
    print("--- WEB_samv ---")
    print(f"Workers: {worker_count()} (WEB_SAMV_WORKERS)")
    print(f"Root: {cfg.ROOT}")
    print(f"Data: {cfg.FOLDERS_ROOT}")
    print(f"Open: http://127.0.0.1:{port}/")
    lan = list(RUNTIME.get("lan_ipv4", []))
    if lan:
        print("LAN:")
        for ip in lan:
            print(f"  http://{ip}:{port}/")
        print(f"API hint: http://{lan[0]}:{cfg.API_PORT_HINT}/")
    print("Stop: Ctrl+C")
    print()
    with httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
