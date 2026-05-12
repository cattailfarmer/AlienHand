from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct


@dataclass(frozen=True)
class Bitmap:
    width: int
    height: int
    pixels: bytes

    def rgb_at(self, x: int, y: int) -> tuple[int, int, int]:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            raise IndexError((x, y))
        offset = (y * self.width + x) * 3
        return self.pixels[offset], self.pixels[offset + 1], self.pixels[offset + 2]


def load_bmp(path: str | Path) -> Bitmap:
    data = Path(path).read_bytes()
    if data[:2] != b"BM":
        raise ValueError("Expected a BMP file")

    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40:
        raise ValueError("Unsupported BMP DIB header")

    width = struct.unpack_from("<i", data, 18)[0]
    raw_height = struct.unpack_from("<i", data, 22)[0]
    planes = struct.unpack_from("<H", data, 26)[0]
    bits = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if planes != 1 or bits not in (24, 32) or compression != 0:
        raise ValueError("Only uncompressed 24-bit or 32-bit BMP files are supported")

    top_down = raw_height < 0
    height = abs(raw_height)
    stride = ((width * bits + 31) // 32) * 4
    output = bytearray(width * height * 3)

    for row in range(height):
        src_row = row if top_down else (height - 1 - row)
        src = pixel_offset + src_row * stride
        for x in range(width):
            b = data[src + x * (bits // 8)]
            g = data[src + x * (bits // 8) + 1]
            r = data[src + x * (bits // 8) + 2]
            dst = (row * width + x) * 3
            output[dst : dst + 3] = bytes((r, g, b))

    return Bitmap(width=width, height=height, pixels=bytes(output))
