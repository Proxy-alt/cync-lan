"""Badge the upstream Cync brand icon with a LAN marker.

Base art is `core_integrations/cync` from home-assistant/brands - the same
mark Home Assistant already shows for Cync devices - so this integration is
recognisable as being about Cync hardware. The badge is what distinguishes it
from the cloud integration: this one talks to the devices over the LAN.

Two variants are produced, because the base art ships in two: a black mark
for light themes and a white one for dark. Home Assistant and HACS pick
`dark_icon.png` automatically when the user's theme is dark, and a black-on-
transparent icon on a dark background is close to invisible without it.

Sizes are driven by legibility at 32px, which is what HACS renders in its
repository list.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

SS = 4  # supersample factor for the badge; the base art is already raster

ACCENT = (31, 111, 235, 255)  # #1F6FEB - carried over from the old icon
ACCENT_TEXT = (255, 255, 255, 255)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _font(px: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            # index 1 is usually the bold face in a .ttc; fall back to 0 for
            # single-face files, where asking for index 1 raises.
            try:
                return ImageFont.truetype(path, px, index=1)
            except (OSError, ValueError):
                return ImageFont.truetype(path, px)
        except OSError:
            continue
    raise SystemExit("no usable TrueType font found")


def badge(base: Image.Image, knockout: bool = True) -> Image.Image:
    """Composite a LAN badge onto the bottom-right of `base`."""
    size = base.size[0]
    work = size * SS
    im = base.convert("RGBA").resize((work, work), Image.LANCZOS)

    # Badge geometry: bottom-right, large enough that "LAN" stays readable,
    # small enough to leave the Cync mark dominant.
    r = int(work * 0.205)
    cx = cy = work - r

    if knockout:
        # Punch a transparent ring so the badge reads as sitting *on top* of
        # the mark rather than merging into the ray it covers.
        ring = Image.new("L", (work, work), 0)
        ImageDraw.Draw(ring).ellipse(
            [cx - r - int(work * 0.035), cy - r - int(work * 0.035),
             cx + r + int(work * 0.035), cy + r + int(work * 0.035)],
            fill=255,
        )
        cleared = im.getchannel("A").point(lambda a: a)
        cleared.paste(0, (0, 0), ring)
        im.putalpha(cleared)

    d = ImageDraw.Draw(im)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)

    text = "LAN"
    f = _font(int(r * 0.80))
    left, top, right, bottom = d.textbbox((0, 0), text, font=f)
    d.text(
        (cx - (right + left) / 2, cy - (bottom + top) / 2),
        text,
        font=f,
        fill=ACCENT_TEXT,
    )

    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    import sys

    src, dest = sys.argv[1], sys.argv[2]
    for base_name, out_name in (
        ("icon.png", "icon.png"),
        ("icon@2x.png", "icon@2x.png"),
        ("dark_icon.png", "dark_icon.png"),
        ("dark_icon@2x.png", "dark_icon@2x.png"),
    ):
        out = badge(Image.open(f"{src}/{base_name}"))
        out.save(f"{dest}/{out_name}", optimize=True)
        print(f"wrote {dest}/{out_name} {out.size}")

    # Legibility proof at the size HACS actually renders.
    for name in ("icon.png", "dark_icon.png"):
        Image.open(f"{dest}/{name}").resize((32, 32), Image.LANCZOS).resize(
            (256, 256), Image.NEAREST
        ).save(f"{dest}/preview32_{name}")
        print(f"wrote {dest}/preview32_{name}")
