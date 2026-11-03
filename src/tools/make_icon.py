"""Render the app mark: the golden headband (紧箍) crossed by the staff (金箍棒), on ink black.

Writes assets/icon.ico, assets/icon.png and assets/logo.svg from the same geometry; the header logo in
bmkong/web/index.html is an inline copy of logo.svg (paste it again after changing the mark).
Usage: python tools/make_icon.py   (needs Pillow)
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1024  # drawn large, downsampled per icon size
INK, GOLD, GOLD_DARK, RED, CLOUD = "#0e0d0b", "#d9a441", "#8c6420", "#b8322a", "#e8dfcc"
CENTER, RADIUS, BAND = (512, 512), 300, 64
# staff axis: lower left to upper right
END_A, END_B = (196, 828), (828, 196)


def ring() -> list[tuple[float, float]]:
    """Headband outline, open at the upper right where the staff leaves it (outer arc, then inner arc reversed)."""
    start, sweep, steps, (cx, cy) = 335.0, 330.0, 180, CENTER
    angles = [math.radians(start + sweep * i / steps) for i in range(steps + 1)]
    outer = [(cx + (RADIUS + BAND / 2) * math.cos(a), cy + (RADIUS + BAND / 2) * math.sin(a)) for a in angles]
    inner = [(cx + (RADIUS - BAND / 2) * math.cos(a), cy + (RADIUS - BAND / 2) * math.sin(a)) for a in angles]
    return outer + inner[::-1]


def staff() -> dict[str, list[tuple[float, float]]]:
    (x0, y0), (x1, y1) = END_A, END_B
    length = math.hypot(x1 - x0, y1 - y0)
    u = ((x1 - x0) / length, (y1 - y0) / length)
    n = (-u[1], u[0])

    def at(t, s):  # t along the axis from END_A, s across it
        return (x0 + u[0] * t + n[0] * s, y0 + u[1] * t + n[1] * s)

    def bar(t0, t1, w):
        return [at(t0, -w), at(t1, -w), at(t1, w), at(t0, w)]

    return {"rod": bar(0, length, 30), "cap_a": bar(0, 120, 42), "cap_b": bar(length - 120, length, 42),
            "line_a": bar(124, 136, 34), "line_b": bar(length - 136, length - 124, 34)}


def render() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, SIZE - 40, SIZE - 40), radius=200, fill=INK)
    parts = staff()
    d.polygon(parts["rod"], fill=RED)
    for k in ("cap_a", "cap_b"):
        d.polygon(parts[k], fill=GOLD)
    for k in ("line_a", "line_b"):
        d.polygon(parts[k], fill=GOLD_DARK)
    d.polygon(ring(), fill=GOLD, outline=INK, width=10)  # over the staff: the staff passes behind the band
    return img


def svg() -> str:
    pts = lambda p: " ".join(f"{x:.0f},{y:.0f}" for x, y in p)
    parts = staff()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">'
            f'<rect x="40" y="40" width="{SIZE - 80}" height="{SIZE - 80}" rx="200" fill="{INK}"/>'
            f'<polygon points="{pts(parts["rod"])}" fill="{RED}"/>'
            f'<polygon points="{pts(parts["cap_a"])}" fill="{GOLD}"/>'
            f'<polygon points="{pts(parts["cap_b"])}" fill="{GOLD}"/>'
            f'<polygon points="{pts(parts["line_a"])}" fill="{GOLD_DARK}"/>'
            f'<polygon points="{pts(parts["line_b"])}" fill="{GOLD_DARK}"/>'
            f'<polygon points="{pts(ring())}" fill="{GOLD}" stroke="{INK}" stroke-width="10"/></svg>')


def main():
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    img = render().resize((256, 256), Image.LANCZOS)
    img.save(out / "icon.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(out / "icon.ico", sizes=sizes)
    (out / "logo.svg").write_text(svg() + "\n", encoding="utf-8")
    print(out / "icon.ico")


if __name__ == "__main__":
    main()
