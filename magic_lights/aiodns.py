import os
import asyncio
import errno
import network
from micropython import const
from . import aioudp

_HEADER_LENGTH = const(12)

def is_ipv4(value):
    parts = value.split('.')
    return len(parts) == 4 and all(part.isdigit() and (0 <= int(part) <= 255) for part in parts)

def encode_domain(domain, encoding='utf-8'):
    domain = domain.encode(encoding)
    labels = domain.split(b'.')

    buffer = bytearray(len(domain) + 2)
    offset = 0

    for label in labels:
        label_length = len(label)
        buffer[offset] = label_length
        buffer[offset + 1:offset + 1 + label_length] = label
        offset += label_length + 1

    return buffer

def decode_domain(buffer, offset, length, encoding='utf-8'):
    labels = []
    end_offset = 0
    jumped = False

    for _ in range(127):
        ensure_offset(offset, length)
        length_or_pointer = buffer[offset]
        offset += 1

        if not jumped:
            end_offset = offset

        if length_or_pointer == 0:
            return ('.'.join(labels), end_offset)
        elif (length_or_pointer & 0xc0) == 0xc0:
            ensure_offset(offset, length)
            offset = ((length_or_pointer & 0x3f) << 8) | buffer[offset]

            if not jumped:
                end_offset += 1
                jumped = True
        elif (length_or_pointer & 0xc0) == 0:
            ensure_offset(offset + length_or_pointer - 1, length)
            label = bytearray(length_or_pointer)
            label[0:] = buffer[offset:offset + length_or_pointer]
            labels.append(label.decode(encoding))
            offset += length_or_pointer
        else:
            raise OSError(errno.EINVAL)

    raise OSError(errno.EINVAL)

def decode_ipv4_data(buffer, offset):
    return '.'.join(str(buffer[i]) for i in range(offset, offset + 4))

def decode_uint16_be(buffer, offset):
    return (buffer[offset] << 8) | buffer[offset + 1]

def ensure_offset(offset, length):
    if offset >= length:
        raise OSError(errno.EINVAL)

async def resolve(domain, ip=None, port=53, timeout_ms=5000):
    if ip is None:
        ip = network.ipconfig('dns')
        if ip == '0.0.0.0':
            raise OSError(errno.EINVAL)

    sock = aioudp.create_socket()

    id = os.urandom(2)
    domain_buffer = encode_domain(domain)
    query = bytearray(16 + len(domain_buffer))
    query[0:2] = id
    query[2] = 0x01 # recursion desired bit set
    query[5] = 0x01 # question count set to 1
    query[_HEADER_LENGTH:_HEADER_LENGTH + len(domain_buffer)] = domain_buffer
    query[_HEADER_LENGTH + len(domain_buffer) + 1] = 0x01 # record type set to A
    query[_HEADER_LENGTH + len(domain_buffer) + 3] = 0x01 # query class set to IN

    try:
        aioudp.sock_sendto(sock, query, (ip, port))

        response = bytearray(512) # dns response limit

        try:
            n = await asyncio.wait_for_ms(aioudp.sock_recv_into(sock, response), timeout_ms)
        except asyncio.TimeoutError as e:
            raise OSError(errno.ETIMEDOUT) from e

        response_code = response[3] & 0x0f

        if n < _HEADER_LENGTH or response[0:2] != id or response_code != 0:
            raise OSError(errno.EINVAL)

        qdcount = decode_uint16_be(response, 4)
        ancount = decode_uint16_be(response, 6)

        offset = _HEADER_LENGTH

        for _ in range(qdcount):
            _, offset = decode_domain(response, offset, n)
            offset += 4 # skip qtype and qclass

        addresses = []

        for _ in range(ancount):
            _, offset = decode_domain(response, offset, n)

            ensure_offset(offset + 9, n)

            rtype = decode_uint16_be(response, offset)
            rdlength = decode_uint16_be(response, offset + 8)
            offset += 10

            ensure_offset(offset + rdlength - 1, n)

            if rtype == 0x01 and rdlength == 4:
                rdata = decode_ipv4_data(response, offset)
                addresses.append(rdata)

            offset += rdlength

        return addresses
    finally:
        sock.close()
