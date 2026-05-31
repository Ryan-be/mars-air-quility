"""Generate Mars-themed PWA icons at multiple sizes.

Idempotent — overwrites existing icons in static/icons/. Run from repo
root: `poetry run python scripts/generate_pwa_icons.py`.

Sizes:
  - 180 (apple-touch-icon)
  - 192 (manifest standard)
  - 512 (manifest standard)
  - 512 maskable (manifest, full bleed with safe centre area)

Design: dark space background (#080c11), Mars planet centred (red disc
with a polar cap hint), dashed orbit ring, small "MLSS" wordmark below.
Matches the inline SVG in templates/base.html.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


_OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"


def _draw_icon(size: int, *, maskable: bool = False) -> Image.Image:
    bg = (8, 12, 17)  # #080c11
    img = Image.new("RGBA", (size, size), bg + (255,))
    draw = ImageDraw.Draw(img)

    # Safe area: for maskable icons leave a 20% padding around the focal
    # element so iOS / Android cropping doesn't clip Mars.
    pad = int(size * 0.2) if maskable else int(size * 0.08)
    inner = size - 2 * pad
    cx = cy = size // 2

    # Orbit ring (subtle, behind Mars).
    if not maskable:
        ring_r = inner // 2 + int(size * 0.03)
        bbox = [cx - ring_r, cy - ring_r // 3, cx + ring_r, cy + ring_r // 3]
        for i, t in enumerate([1.0, 0.7, 0.4]):
            offset = i * max(1, size // 256)
            draw.arc(
                [bbox[0] - offset, bbox[1] - offset,
                 bbox[2] + offset, bbox[3] + offset],
                start=10, end=170,
                fill=(77, 172, 255, int(160 * t)),
                width=max(1, size // 128),
            )

    # Mars body — red gradient disc.
    mars_r = inner // 2
    for r in range(mars_r, 0, -1):
        # Radial gradient: lighter centre, darker edge.
        ratio = r / mars_r
        red   = int(180 - 50 * (1 - ratio))   # 180 outer -> 130 inner-ish
        green = int(60 + 50 * (1 - ratio))    # 60 outer -> 110 inner-ish
        blue  = int(20 + 30 * (1 - ratio))
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(min(red, 255), min(green, 200), min(blue, 80), 255),
        )

    # Polar cap suggestion: thin arc near the top.
    cap_r = mars_r - max(2, size // 64)
    draw.arc(
        [cx - cap_r // 2, cy - cap_r, cx + cap_r // 2, cy - cap_r + cap_r // 2],
        start=200, end=340,
        fill=(232, 213, 196, 220),
        width=max(1, size // 96),
    )

    # Wordmark
    if size >= 192 and not maskable:
        try:
            font = ImageFont.truetype("arial.ttf", max(10, size // 12))
        except OSError:
            font = ImageFont.load_default()
        text = "MLSS"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, _ = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((size - tw) // 2, cy + mars_r + max(4, size // 40)),
            text, fill=(220, 230, 240, 255), font=font,
        )

    return img


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    configs = [
        (180, False, "icon-180.png"),
        (192, False, "icon-192.png"),
        (512, False, "icon-512.png"),
        (512, True,  "icon-512-maskable.png"),
    ]
    for size, maskable, name in configs:
        img = _draw_icon(size, maskable=maskable)
        out = _OUT_DIR / name
        img.save(out, format="PNG", optimize=True)
        print(f"  wrote {out} ({size}x{size}{' maskable' if maskable else ''})")


if __name__ == "__main__":
    main()
