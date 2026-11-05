"""Draw the launcher icon (an RJ45 port on link-up green) into launcher_ui/app.ico."""
from pathlib import Path

from PIL import Image, ImageDraw

GREEN = (13, 118, 88, 255)
WHITE = (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    scale = 4  # draw large, then downsample for smooth edges
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=GREEN)
    # Port outline: wide body with a narrower latch notch at the bottom.
    w = max(2, int(s * 0.07))
    left, right, top, mid, bottom = s * 0.17, s * 0.83, s * 0.24, s * 0.62, s * 0.78
    notch_l, notch_r = s * 0.34, s * 0.66
    points = [(left, top), (right, top), (right, mid), (notch_r, mid), (notch_r, bottom), (notch_l, bottom), (notch_l, mid), (left, mid), (left, top)]
    d.line(points, fill=WHITE, width=w, joint="curve")
    if size >= 24:
        for i in range(4):
            x = s * (0.32 + i * 0.12)
            d.line([(x, top + s * 0.1), (x, top + s * 0.22)], fill=WHITE, width=max(2, int(w * 0.8)))
    return image.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "launcher_ui" / "app.ico"
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    images = [draw(size) for size in sizes]
    images[-1].save(target, format="ICO", sizes=[(n, n) for n in sizes], append_images=images[:-1])
    draw(256).save(target.with_suffix(".png"))
    print("wrote", target)
