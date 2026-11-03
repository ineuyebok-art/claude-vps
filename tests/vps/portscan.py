#!/usr/bin/env python3
"""Scans all TCP ports of a host from outside. Fails unless only port 22 is open."""
import selectors
import socket
import sys

host = sys.argv[1]
sel = selectors.DefaultSelector()
open_ports = []
ports = iter(range(1, 65536))
active = 0
done = False
while not done or active:
    while active < 800 and not done:
        port = next(ports, None)
        if port is None:
            done = True
            break
        sock = socket.socket()
        sock.setblocking(False)
        sock.connect_ex((host, port))
        sel.register(sock, selectors.EVENT_WRITE, port)
        active += 1
    events = sel.select(timeout=0.4)
    if not events and active:
        for key in list(sel.get_map().values()):
            sel.unregister(key.fileobj)
            key.fileobj.close()
        active = 0
    for key, _ in events:
        if key.fileobj.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
            open_ports.append(key.data)
        sel.unregister(key.fileobj)
        key.fileobj.close()
        active -= 1
print("open ports:", sorted(open_ports))
sys.exit(0 if sorted(open_ports) == [22] else 1)
