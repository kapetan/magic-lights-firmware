import os
import time as _time
import asyncio
import struct
import errno
from . import aioudp
from . import aiodns

# https://github.com/micropython/micropython-lib/blob/660ddd89dd958d8d835ecbbcb323f3cfcd891c30/micropython/net/ntptime/ntptime.py
async def time(host='pool.ntp.org', port=123, timeout_ms=5000):
    if aiodns.is_ipv4(host):
        addr = (host, port)
    else:
        candidates = await aiodns.resolve(host)
        if len(candidates) == 0:
            raise OSError(errno.EINVAL)

        addr = (candidates[0], port)

    sock = aioudp.create_socket()

    nonce = os.urandom(8)
    buffer = bytearray(48)
    buffer[0] = 0x23
    buffer[40:48] = nonce

    try:
        aioudp.sock_sendto(sock, buffer, addr)

        try:
            n = await asyncio.wait_for_ms(aioudp.sock_recv_into(sock, buffer), timeout_ms)
        except asyncio.TimeoutError as e:
            raise OSError(errno.ETIMEDOUT) from e

        if n != len(buffer) or buffer[1] == 0 or buffer[24:32] != nonce:
            raise OSError(errno.EINVAL)
    finally:
        sock.close()

    val, = struct.unpack("!I", buffer[40:44])
    epoch_year, *_ = _time.gmtime(0)

    if epoch_year == 2000:
        delta = 3155673600
    elif epoch_year == 1970:
        delta = 2208988800
    else:
        raise OSError(errno.EOPNOTSUPP)

    return val - delta
