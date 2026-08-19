"""Print the IPv4 address this machine is reachable at on the local network.

The launcher prints this so an address can be handed to someone on another
machine. ipconfig is not usable for it: a machine with VirtualBox, WSL or a VPN
on it lists those adapters first and none of them is reachable from the room.
Asking the routing table which interface would carry traffic outward gives the
one that is. No packet is sent, a UDP socket is only bound.
"""
from __future__ import annotations

import socket


def lan_address() -> str:
    """The address of the interface holding the default route, or empty."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


if __name__ == "__main__":
    print(lan_address())
