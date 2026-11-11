import asyncio
import select
import errno
import socket

def create_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    return sock

def sock_sendto(sock, data, address):
    return sock.sendto(data, address)

def sock_recv(sock, nbytes, poll_ms=10):
    buf = bytearray(nbytes)
    return sock_recv_into(sock, buf, poll_ms)

async def sock_recv_into(sock, buf, poll_ms=10):
    poller = select.poll()

    try:
        poller.register(sock, select.POLLIN)

        while True:
            for _, event, *_ in poller.ipoll(0):
                if event & select.POLLIN:
                    return sock.readinto(buf)
                elif event & (select.POLLHUP | select.POLLERR):
                    raise OSError(errno.EIO)

            await asyncio.sleep_ms(poll_ms)
    finally:
        poller.unregister(sock)

# class Reader:
#     def __init__(self, socket):
#         self._socket = socket
#         self.poller = None

#     def __enter__(self):
#         self.poller = select.poll()
#         self.poller.register(self.socket, select.POLLIN)
#         return self

#     def __exit__(self, exc_type, exc_val, exc_tb):
#         self.poller.unregister(self.socket)
