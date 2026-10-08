"""Generate app icons for the PWA / Android wrapper."""
from PIL import Image, ImageDraw


def make_icon(size: int, path: str) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(11, 15, 23))
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r,
                        outline=(56, 189, 248), width=max(2, size // 64))
    # candlesticks motif
    u = size / 16.0
    candles = [
        (4.2, 9.5, 5.8, 12.5, (239, 68, 68)),
        (6.6, 7.0, 8.2, 11.0, (34, 197, 94)),
        (9.0, 5.0, 10.6, 9.0, (34, 197, 94)),
        (11.4, 3.2, 13.0, 6.6, (34, 197, 94)),
    ]
    for x0, y0, x1, y1, color in candles:
        cx = (x0 + x1) / 2 * u
        d.line([(cx, (y0 - 1.0) * u), (cx, (y1 + 1.0) * u)], fill=color,
               width=max(1, size // 96))
        d.rounded_rectangle([x0 * u, y0 * u, x1 * u, y1 * u],
                            radius=max(1, size // 96), fill=color)
    img.save(path)


if __name__ == "__main__":
    import os
    os.makedirs("ui/static/icons", exist_ok=True)
    make_icon(192, "ui/static/icons/icon-192.png")
    make_icon(512, "ui/static/icons/icon-512.png")
    print("icons written")
