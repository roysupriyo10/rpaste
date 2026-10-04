#!/usr/bin/env python3
"""Stage complete PNG transfers as private, immutable image files."""

from pathlib import Path
import struct
import sys
import tempfile
import zlib

from rpaste_common import MAX_IMAGE, PNG_SIGNATURE


def validate_png(data):
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("clipboard data is not a PNG image")
    offset = 8
    first = True
    while offset + 12 <= len(data):
        length = struct.unpack_from("!I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            break
        tag = data[offset + 4 : offset + 8]
        payload = data[offset + 4 : end - 4]
        checksum = struct.unpack_from("!I", data, end - 4)[0]
        if zlib.crc32(payload) != checksum:
            raise ValueError("clipboard PNG has a damaged chunk")
        if first and (tag != b"IHDR" or length != 13):
            raise ValueError("clipboard PNG has no valid header")
        first = False
        if tag == b"IEND" and length == 0 and end == len(data):
            return
        offset = end
    raise ValueError("clipboard PNG transfer is incomplete")


def stage(state, stream):
    data = stream.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise ValueError("clipboard image exceeds 64 MiB")
    validate_png(data)
    images = Path(state) / "images"
    images.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        prefix="clipboard-", suffix=".png", dir=images, delete=False
    ) as image:
        image.write(data)
        path = Path(image.name)
    # Replace the pointer atomically; failures never displace the last image.
    pointer = images / (path.stem + ".link")
    try:
        pointer.symlink_to(path.name)
        pointer.replace(images / "latest.png")
    finally:
        pointer.unlink(missing_ok=True)
    return path


if __name__ == "__main__":
    try:
        print(stage(sys.argv[1], sys.stdin.buffer), end="")
    except (OSError, ValueError) as error:
        print(f"rpaste: {error}", file=sys.stderr)
        sys.exit(1)
