"""RCF1 v1: raw GRAY8 frames. Shared host/simulator framing, not a mission protocol."""
import struct
import time
import zlib

HEADER = struct.Struct('<4sIHHII')
MAGIC = b'RCF1'
MAX_BYTES = 640 * 480


def packet(data, width, height, sequence):
    if len(data) != width * height or not 0 < len(data) <= MAX_BYTES:
        raise ValueError('Invalid GRAY8 dimensions')
    return HEADER.pack(MAGIC, sequence, width, height, len(data),
                       zlib.crc32(data) & 0xffffffff) + data


def receive(stream, timeout=10, stop=None, timings=None):
    started = time.perf_counter()
    deadline = time.monotonic() + timeout

    def exact(size):
        data = bytearray()
        while len(data) < size:
            if stop is not None and stop.is_set():
                raise InterruptedError('Preview stopped')
            if time.monotonic() >= deadline:
                raise TimeoutError('Incomplete image packet; reconnect before retrying')
            part = stream.read(size - len(data))
            if part:
                data.extend(part)
        return bytes(data)

    # Ignore startup console text, but stop after a bounded amount of noise.
    prefix = bytearray()
    for _ in range(4096):
        prefix.extend(exact(1))
        if prefix[-4:] == MAGIC:
            break
    else:
        raise ValueError('Image header not found')
    header = MAGIC + exact(HEADER.size - 4)
    _, sequence, width, height, size, checksum = HEADER.unpack(header)
    if not (0 < width <= 640 and 0 < height <= 480 and size == width * height):
        raise ValueError('Invalid image dimensions/length')
    header_done = time.perf_counter()
    data = exact(size)
    payload_done = time.perf_counter()
    if zlib.crc32(data) & 0xffffffff != checksum:
        raise ValueError('Image CRC mismatch; frame discarded')
    if timings is not None:
        timings.update(header_wait_ms=(header_done-started)*1000,
                       payload_receive_ms=(payload_done-header_done)*1000,
                       crc_ms=(time.perf_counter()-payload_done)*1000)
    return sequence, width, height, data
