"""Build the GitHub social preview card (1280x640).

This is the image that unfurls when the repo is linked on the Home Assistant
forum, Reddit or Discord, so it has one job: say what the project is to
someone who has never heard of it, at thumbnail size.

GitHub crops the card in some contexts, so everything meaningful stays inside
a 40pt safe border. The layout keeps a much wider margin than that anyway -
the constraint that actually binds is legibility when the card renders about
400px wide in a feed.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
SAFE = 40  # GitHub's recommended crop-safe border

BG = (255, 255, 255, 255)
INK = (16, 18, 22, 255)
MUTED = (92, 99, 112, 255)
ACCENT = (31, 111, 235, 255)

FONTS_BOLD = ["/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Helvetica.ttc"]
FONTS_REG = ["/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Helvetica.ttc"]


def _font(paths: list[str], px: int, bold: bool) -> ImageFont.FreeTypeFont:
    for p in paths:
        for idx in ((1, 0) if bold else (0, 1)):
            try:
                return ImageFont.truetype(p, px, index=idx)
            except Exception:
                continue
    raise SystemExit("no usable font")


def centered(d: ImageDraw.ImageDraw, y: int, text: str, font, fill) -> int:
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.text(((W - (r - l)) / 2 - l, y), text, font=font, fill=fill)
    return y + (b - t)


def build(logo_path: str, out: str) -> None:
    im = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, H - 10, W, H], fill=ACCENT)

    logo = Image.open(logo_path).convert("RGBA")
    target_w = 430
    lw, lh = target_w, int(logo.size[1] * target_w / logo.size[0])
    logo = logo.resize((lw, lh), Image.LANCZOS)

    head_f = _font(FONTS_BOLD, 50, True)
    sub_f = _font(FONTS_REG, 28, False)
    head = "Local control for Cync / C by GE"
    subs = ["A native Home Assistant integration.",
            "No cloud. No MQTT broker. No Docker."]

    def th(text, font):
        l, t_, r, b = d.textbbox((0, 0), text, font=font)
        return b - t_

    gap_logo, gap_head, gap_sub = 44, 22, 10
    total = (lh + gap_logo + th(head, head_f) + gap_head
             + sum(th(s, sub_f) for s in subs) + gap_sub * (len(subs) - 1))

    # Centre the whole stack, biased slightly up so the accent rule at the
    # bottom does not crowd the last line.
    y = (H - total) // 2 - 12
    im.alpha_composite(logo, ((W - lw) // 2, y))
    y += lh + gap_logo
    y = centered(d, y, head, head_f, INK) + gap_head
    for i, s in enumerate(subs):
        y = centered(d, y, s, sub_f, MUTED)
        if i < len(subs) - 1:
            y += gap_sub

    # The whole point of the 40pt border is that GitHub crops to it in some
    # contexts. Fail loudly rather than shipping a card with a clipped line.
    bottom = y + SAFE
    if bottom > H - SAFE:
        raise SystemExit(
            f"content reaches y={y}, past the {SAFE}pt safe border (limit {H - SAFE})"
        )

    im.convert("RGB").save(out, optimize=True)
    print(f"  wrote {out} ({W}x{H}), content ends at y={y}, safe limit {H - SAFE}")


if __name__ == "__main__":
    import sys

    build(sys.argv[1], sys.argv[2])
