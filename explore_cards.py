"""Image rendering helpers for Giant Chat Explore posts.

The module intentionally has no network, storage, or bot dependencies. The
caller owns publication and social-post identity; this module only returns a
local image file that can be uploaded through Giant's existing media pipeline.
"""
from __future__ import annotations

from pathlib import Path
import textwrap
import uuid

try:
    from PIL import Image, ImageDraw, ImageFont, features
except ImportError:  # pragma: no cover - exercised on minimal deployments
    Image = ImageDraw = ImageFont = features = None


def _font(font_path: str | Path | None, size: int):
    if ImageFont is None:
        raise RuntimeError("Pillow is required to render Explore cards")
    if font_path:
        try:
            return ImageFont.truetype(str(font_path), size)
        except (OSError, ValueError):
            pass
    return ImageFont.load_default()


def _has_raqm() -> bool:
    try:
        return bool(features.check("raqm"))
    except Exception:
        return False


def _wrap(draw, text: str, font, max_width: int, *, rtl: bool) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        try:
            width = draw.textlength(
                candidate,
                font=font,
                direction="rtl" if rtl and _has_raqm() else None,
                language="ar" if rtl and _has_raqm() else None,
            )
        except (TypeError, ValueError):
            width = draw.textlength(candidate, font=font)
        if current and width > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_centered(draw, xy, value: str, font, fill, *, rtl: bool = False):
    options = {"anchor": "mm", "align": "center"}
    if rtl and _has_raqm():
        options.update(direction="rtl", language="ar")
    try:
        draw.text(xy, value, font=font, fill=fill, **options)
    except (TypeError, ValueError):
        options.pop("direction", None)
        options.pop("language", None)
        draw.text(xy, value, font=font, fill=fill, **options)


def render_explore_card(
    text: str,
    username: str,
    output_dir: str | Path,
    *,
    regular_font: str | Path | None = None,
    bold_font: str | Path | None = None,
) -> Path:
    """Render one 1080×1350 Arabic-friendly Explore card and return its path."""
    if Image is None:
        raise RuntimeError("Pillow is required to render Explore cards")
    caption = " ".join(str(text or "").split())[:150]
    author = " ".join(str(username or "مستخدم").strip().lstrip("@").split())[:48] or "مستخدم"
    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), (18, 24, 51))
    draw = ImageDraw.Draw(image, "RGBA")

    # Smooth enough for a mobile card while avoiding a costly per-row render.
    top, bottom = (18, 27, 62), (64, 31, 79)
    strips = 120
    for index in range(strips):
        t = index / (strips - 1)
        color = tuple(round(top[channel] * (1 - t) + bottom[channel] * t) for channel in range(3))
        y0, y1 = index * height // strips, (index + 1) * height // strips
        draw.rectangle((0, y0, width, y1), fill=(*color, 255))

    draw.ellipse((-160, -180, 440, 420), fill=(250, 110, 166, 30))
    draw.ellipse((720, 40, 1280, 600), fill=(97, 173, 255, 27))
    draw.rounded_rectangle((56, 54, width - 56, height - 54), radius=48,
                           fill=(12, 18, 42, 75), outline=(247, 210, 157, 125), width=3)

    title_font = _font(bold_font, 56)
    author_font = _font(bold_font, 42)
    body_font = _font(regular_font or bold_font, 48)
    footer_font = _font(bold_font, 30)
    _draw_centered(draw, (width // 2, 145), "اكسبلور", title_font, (255, 226, 183, 255), rtl=True)

    # A simple, crisp profile medallion is used instead of fetching an avatar;
    # this keeps rendering private, deterministic, and independent of profile URLs.
    cx, cy, radius = width // 2, 340, 94
    draw.ellipse((cx-radius-8, cy-radius-8, cx+radius+8, cy+radius+8),
                 fill=(250, 210, 153, 255))
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=(57, 76, 121, 255))
    initial = author[:1]
    _draw_centered(draw, (cx, cy), initial, _font(bold_font, 82), (255, 240, 220, 255), rtl=True)
    _draw_centered(draw, (cx, 492), f"@{author}", author_font, (245, 224, 201, 255), rtl=True)
    draw.line((300, 550, 780, 550), fill=(246, 203, 139, 155), width=3)

    panel = (112, 615, width - 112, 1156)
    draw.rounded_rectangle(panel, radius=36, fill=(8, 13, 33, 165), outline=(255, 255, 255, 65), width=2)
    lines = _wrap(draw, caption, body_font, 770, rtl=any("\u0600" <= char <= "\u06ff" for char in caption))
    if len(lines) > 7:
        lines = lines[:7]
        lines[-1] = lines[-1].rstrip("…") + "…"
    line_height = 68
    y = panel[1] + (panel[3] - panel[1] - line_height * len(lines)) // 2
    rtl = any("\u0600" <= char <= "\u06ff" for char in caption)
    for line in lines:
        _draw_centered(draw, (width // 2, y + line_height // 2), line, body_font,
                       (255, 255, 255, 255), rtl=rtl)
        y += line_height

    _draw_centered(draw, (width // 2, 1230), "Giant Chat  •  منشور اكسبلور", footer_font,
                   (255, 220, 174, 240), rtl=True)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"explore_{uuid.uuid4().hex}.jpg"
    image.save(path, "JPEG", quality=91, optimize=True, progressive=True)
    return path
