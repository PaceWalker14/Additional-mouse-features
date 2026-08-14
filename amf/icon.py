"""A generated app icon, so the project carries no binary assets."""

from __future__ import annotations

import os
import struct

from .config import CONFIG_DIR

SIZE = 32
ACCENT = (0x7C, 0x8A, 0xFF)   # RGB
BODY = (0xEC, 0xEE, 0xF6)


def _pixels() -> list[tuple[int, int, int, int]]:
    """Draw a small mouse: rounded body, split top, wheel in the middle."""
    px = [(0, 0, 0, 0)] * (SIZE * SIZE)
    cx = (SIZE - 1) / 2.0
    left, right = 6.5, SIZE - 7.5
    top, bottom = 3.0, SIZE - 3.0
    rx = (right - left) / 2.0
    ry = (bottom - top) / 2.0
    mid_y = top + ry * 0.72

    for y in range(SIZE):
        for x in range(SIZE):
            # Superellipse gives the tall rounded mouse silhouette.
            nx = (x + 0.5 - (left + rx)) / rx
            ny = (y + 0.5 - (top + ry)) / ry
            d = abs(nx) ** 2.4 + abs(ny) ** 2.15
            if d > 1.0:
                continue
            edge = d > 0.80
            colour = ACCENT if edge else BODY
            alpha = 255

            # Divider between the two top buttons.
            if y + 0.5 < mid_y and abs(x + 0.5 - cx) < 0.75 and not edge:
                colour = ACCENT
            # Horizontal split line.
            if abs(y + 0.5 - mid_y) < 0.75 and not edge:
                colour = ACCENT
            # Scroll wheel.
            if abs(x + 0.5 - cx) < 1.6 and mid_y - 6.0 < y + 0.5 < mid_y - 1.6:
                colour = ACCENT

            px[y * SIZE + x] = (colour[0], colour[1], colour[2], alpha)
    return px


def ico_bytes() -> bytes:
    px = _pixels()
    stride_and = ((SIZE + 31) // 32) * 4

    xor = bytearray()
    for y in range(SIZE - 1, -1, -1):          # DIBs are stored bottom-up
        for x in range(SIZE):
            r, g, b, a = px[y * SIZE + x]
            xor += bytes((b, g, r, a))
    and_mask = bytes(stride_and * SIZE)        # alpha channel does the masking

    header = struct.pack("<IiiHHIIiiII", 40, SIZE, SIZE * 2, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
    image = header + bytes(xor) + and_mask

    ico = struct.pack("<HHH", 0, 1, 1)
    ico += struct.pack("<BBBBHHII", SIZE, SIZE, 0, 0, 1, 32, len(image), 22)
    return ico + image


def icon_path() -> str:
    """Write the icon to the config directory once and return its path."""
    path = os.path.join(CONFIG_DIR, "app.ico")
    data = ico_bytes()
    try:
        if os.path.exists(path) and os.path.getsize(path) == len(data):
            return path
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    except OSError:
        return ""
    return path
