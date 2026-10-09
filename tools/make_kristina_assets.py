"""One-off asset builder: chroma-key Kristina sprites and square backgrounds."""
import os

import numpy as np
from PIL import Image

SRC = "ui/static/kristina/src"
OUT = "ui/static/kristina"
os.makedirs(OUT, exist_ok=True)


def chroma_key(path_in: str, path_out: str, target_h: int = 560) -> None:
    im = Image.open(path_in).convert("RGBA")
    arr = np.asarray(im).astype(np.int16)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    greenness = g - np.maximum(r, b)
    alpha = np.full(greenness.shape, 255, dtype=np.int16)
    soft = (greenness > 25) & (greenness <= 60)
    alpha[greenness > 60] = 0
    alpha[soft] = (255 * (1 - (greenness[soft] - 25) / 35.0)).astype(np.int16)
    # remove green spill on soft edges
    g2 = np.where(soft, np.minimum(255, np.maximum(r, b) + 10), g)
    arr[:, :, 1] = g2
    arr[:, :, 3] = np.clip(alpha, 0, 255)
    im = Image.fromarray(arr.astype(np.uint8), "RGBA")
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    ratio = target_h / im.height
    im = im.resize((int(im.width * ratio), target_h), Image.LANCZOS)
    im.save(path_out, "PNG", optimize=True)
    print("saved", path_out, im.size, os.path.getsize(path_out))


def square_bg(path_in: str, path_out: str, size: int = 512) -> None:
    im = Image.open(path_in).convert("RGB")
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2,
                  (w - s) // 2 + s, (h - s) // 2 + s))
    im = im.resize((size, size), Image.LANCZOS)
    im.save(path_out, "JPEG", quality=85, optimize=True)
    print("saved", path_out, os.path.getsize(path_out))


for tool in ("pickaxe", "shovel", "drill"):
    chroma_key(f"{SRC}/girl_{tool}.jpg", f"{OUT}/kristina_{tool}.png")
for bg in ("mountains", "cave", "snow"):
    square_bg(f"{SRC}/bg_{bg}.jpg", f"{OUT}/bg_{bg}.jpg")
