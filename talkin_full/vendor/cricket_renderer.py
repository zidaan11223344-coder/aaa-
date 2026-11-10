"""Standalone cricket scoreboard renderer.

The game engine never depends on Talkin UI code. It emits structured scoreboard
information; this module turns that data into one shareable PNG.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:  # Keep the renderer usable until dependencies are installed.
    arabic_reshaper = None
    get_display = None

FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoKufiArabic-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoKufiArabic-Regular.ttf"


def _font(size: int, bold: bool = False):
    preferred = FONT_BOLD if bold else FONT_REGULAR
    fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(preferred if os.path.exists(preferred) else fallback, size)


def _shape_rtl(text: str) -> str:
    """Shape Arabic glyphs and reorder bidi text for Pillow's left-to-right draw API."""
    value = str(text)
    if arabic_reshaper is None or get_display is None:
        return value
    try:
        return get_display(arabic_reshaper.reshape(value))
    except Exception:
        return value


def _rtl(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, anchor="ra"):
    draw.text(xy, _shape_rtl(text), font=font, fill=fill, anchor=anchor)


def _fit_name(name: str, limit: int = 18) -> str:
    name = str(name or "لاعب").strip().lstrip("@")
    return name if len(name) <= limit else name[: limit - 1] + "…"


def render_scoreboard(scoreboard: dict[str, Any], output_dir: str | Path) -> Path:
    """Render and atomically save a final two-team cricket scoreboard."""
    payload = json.dumps(scoreboard, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"cricket_score_{digest}.png"
    if target.exists() and target.stat().st_size > 0:
        return target

    W, H = 1400, 900
    image = Image.new("RGB", (W, H), (12, 18, 30))
    draw = ImageDraw.Draw(image)

    # Header
    draw.rounded_rectangle((28, 24, W - 28, 150), radius=28, fill=(23, 32, 51), outline=(96, 165, 250), width=3)
    _rtl(draw, (W // 2, 65), "نتيجة مباراة الكركيت", _font(38, True), (255, 255, 255), anchor="ma")
    winner = str(scoreboard.get("winner") or "تعادل")
    _rtl(draw, (W // 2, 112), f"الفائز: {winner}", _font(24, True), (255, 209, 102), anchor="ma")

    # Two team panels.
    mid = W // 2
    panels = [(28, mid - 12, scoreboard.get("attack") or {}, "الهجوم", (36, 66, 90)),
              (mid + 12, W - 28, scoreboard.get("defense") or {}, "الدفاع", (66, 45, 78))]
    for left, right, team, fallback_label, panel_fill in panels:
        draw.rounded_rectangle((left, 175, right, H - 28), radius=26, fill=panel_fill, outline=(180, 190, 205), width=2)
        name = str(team.get("name") or fallback_label)
        score = int(team.get("score") or 0)
        _rtl(draw, (right - 24, 220), f"فريق {name}", _font(28, True), (255, 255, 255))
        draw.rounded_rectangle((left + 24, 248, right - 24, 335), radius=20, fill=(9, 14, 24))
        _rtl(draw, (right - 48, 291), f"النقاط: {score}", _font(34, True), (255, 209, 102))

        y = 365
        _rtl(draw, (right - 42, y), "اللاعب", _font(20, True), (255, 255, 255))
        _rtl(draw, ((left + right) // 2, y), "الإحصائيات", _font(20, True), (255, 255, 255), anchor="ma")
        draw.line((left + 28, y + 30, right - 28, y + 30), fill=(160, 170, 185), width=2)
        y += 64
        players = team.get("players") or []
        for player in players[:4]:
            username = _fit_name(player.get("username"))
            runs = int(player.get("runs") or 0)
            wickets = int(player.get("wickets") or 0)
            balls = int(player.get("balls") or 0)
            outs = int(player.get("outs") or 0)
            _rtl(draw, (right - 42, y), f"@{username}", _font(22, True), (242, 245, 249))
            stat = f"{runs} جري • {wickets} ويكيت • {balls} كرة"
            if outs:
                stat += " • OUT"
            _rtl(draw, ((left + right) // 2, y), stat, _font(17), (220, 230, 240), anchor="ma")
            y += 78
        if not players:
            _rtl(draw, (right - 42, y), "لا توجد بيانات لاعبين", _font(20), (210, 220, 230))

    # Footer prize.
    prize = int(scoreboard.get("prize") or 0)
    awards = scoreboard.get("awards") or []
    footer = f"جائزة المباراة: {prize:,} نقطة"
    if awards:
        footer += "  •  تم توزيعها على الفريق الفائز"
    _rtl(draw, (W // 2, H - 10), footer, _font(19, True), (180, 220, 255), anchor="ms")

    temp = target.with_suffix(".tmp.png")
    image.save(temp, "PNG", optimize=True)
    os.replace(temp, target)
    return target
