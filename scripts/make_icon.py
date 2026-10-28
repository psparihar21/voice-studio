"""Draw the app icon (same shapes as voice_studio/web/icon.svg) into assets/icon.png and assets/voice-studio.ico.
Needs Pillow + numpy: python scripts/make_icon.py"""
import os

import numpy as np
from PIL import Image, ImageDraw

S = 1024  # draw big, scale down for clean edges
k = S / 64
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


def rr(d, x, y, w, h, r, fill):
    d.rounded_rectangle([x * k, y * k, (x + w) * k, (y + h) * k], radius=r * k, fill=fill)


def main():
    t = (np.add.outer(np.arange(S), np.arange(S)) / (2 * S - 2))[..., None]  # 0 top-left -> 1 bottom-right
    a, b = np.array([255, 90, 95]), np.array([123, 92, 255])
    grad = Image.fromarray((a * (1 - t) + b * t).astype("uint8"), "RGB").convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=15 * k, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    fg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(fg)
    white = (255, 255, 255, 255)
    rr(d, 25, 11, 14, 25, 7, white)
    for x, y, h, o in ((7, 27, 10, .75), (13, 22, 20, .9), (47, 22, 20, .9), (53, 27, 10, .75)):
        rr(d, x, y, 4, h, 2, (255, 255, 255, int(255 * o)))
    w = int(3.5 * k)
    h = w / 2  # PIL grows arc width inward, so widen the box to centre the stroke on the radius like SVG
    d.arc([20 * k - h, 19 * k - h, 44 * k + h, 43 * k + h], 0, 180, fill=white, width=w)  # the mic cradle
    d.line([32 * k, 43 * k, 32 * k, 50 * k], fill=white, width=w)
    d.line([25 * k, 51 * k, 39 * k, 51 * k], fill=white, width=w)
    for x, y in ((20, 31), (44, 31), (32, 50), (25, 51), (39, 51)):  # round caps
        d.ellipse([x * k - w / 2, y * k - w / 2, x * k + w / 2, y * k + w / 2], fill=white)
    img = Image.alpha_composite(img, fg)

    os.makedirs(OUT, exist_ok=True)
    img.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "icon.png"))
    img.save(os.path.join(OUT, "voice-studio.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote assets/icon.png and assets/voice-studio.ico")


if __name__ == "__main__":
    main()
