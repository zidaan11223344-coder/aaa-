"""Cricket result-card renderer. Kept separate from the bot runtime."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:  # Keep older deployments readable until dependencies are installed.
    arabic_reshaper = None
    get_display = None

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.getenv("CRICKET_MEDIA_DIR", str(ROOT / "generated_games")))
FONT_CANDIDATES = (
    os.getenv("CRICKET_FONT", "").strip(),
    str(ROOT / "assets" / "NotoSansArabic-SemiBold.ttf"),
    str(ROOT / "assets" / "NotoSansArabic-Regular.ttf"),
    str(ROOT / "vendor" / "assets" / "NotoSansArabic-CondensedSemiBold.ttf"),
    "/usr/share/fonts/truetype/noto/NotoSansArabic-SemiCondensedMedium.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-CondensedSemiBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int):
    for candidate in FONT_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _latin_font(size: int):
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return _font(size)

def _has_arabic(value: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in str(value))


def _shape_rtl(value: str) -> str:
    """Return shaped, visually ordered text for Pillow's left-to-right API."""
    text = str(value)
    if not _has_arabic(text) or arabic_reshaper is None or get_display is None:
        return text
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _text(draw: ImageDraw.ImageDraw, xy, value: str, font, anchor="la", fill=(245, 247, 250), rtl: bool | None = None):
    if rtl is None:
        rtl = _has_arabic(value)
    try:
        if rtl:
            # Pillow's RAQM engine shapes Arabic and resolves bidirectional text
            # when given the original string and explicit RTL direction. Do not
            # pass a pre-reordered string to Pillow: that reverses it a second time.
            draw.text(xy, str(value), font=font, fill=fill, anchor=anchor, direction="rtl", language="ar")
        else:
            draw.text(xy, value, font=font, fill=fill, anchor=anchor)
    except Exception:
        if rtl:
            draw.text(xy, _shape_rtl(value), font=font, fill=fill, anchor=anchor)
        else:
            draw.text(xy, value, font=font, fill=fill, anchor=anchor)


def render_result_image(
    *,
    match_id: str,
    team1_name: str = "الفريق الأول",
    team1_players: Iterable[str] = (),
    team1_score: int = 0,
    team2_name: str = "الفريق الثاني",
    team2_players: Iterable[str] = (),
    team2_score: int = 0,
    player_scores: dict[str, int] | None = None,
    player_awards: dict[str, int] | None = None,
    winner: str = "تعادل",
    prize: int = 0,
    output_dir: str | Path | None = None,
    # Backward-compatible arguments for older integrations.
    players: Iterable[str] | None = None,
    human_score: int | None = None,
    bot_score: int | None = None,
) -> str:
    """Render a reusable two-team cricket result card."""
    target_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in str(match_id) if ch.isalnum())[:24] or "match"
    filename = f"cricket_result_{safe_id}.png"
    path = target_dir / filename

    if players is not None:
        team1_players = players
    if human_score is not None:
        team1_score = human_score
    if bot_score is not None:
        team2_score = bot_score

    p1 = [str(p).lstrip("@") for p in team1_players if str(p).strip()]
    p2 = [str(p).lstrip("@") for p in team2_players if str(p).strip()]
    scores = {str(k).lstrip("@"): int(v) for k, v in (player_scores or {}).items()}
    awards = {str(k).lstrip("@"): int(v) for k, v in (player_awards or {}).items()}

    W, H = 1500, 900
    img = Image.new("RGB", (W, H), (13, 18, 28))
    draw = ImageDraw.Draw(img)
    title = _font(60)
    team = _font(34)
    name = _font(27)
    latin_name = _latin_font(27)
    score = _font(48)
    small = _font(24)

    draw.rounded_rectangle((30, 25, W - 30, H - 25), radius=30, fill=(20, 27, 41), outline=(88, 103, 130), width=3)
    _text(draw, (W // 2, 90), "نتيجة مباراة الكركيت", title, anchor="mm", rtl=True)
    _text(draw, (W // 2, 150), f"الفائز: {winner}", team, anchor="mm", rtl=True)

    left = (55, 205, W // 2 - 18, H - 55)
    right = (W // 2 + 18, 205, W - 55, H - 55)
    draw.rounded_rectangle(left, radius=26, fill=(27, 48, 67), outline=(73, 155, 220), width=4)
    draw.rounded_rectangle(right, radius=26, fill=(45, 35, 48), outline=(191, 105, 165), width=4)

    def panel(box, title_text, players_list, total, rtl_title=True):
        cx = (box[0] + box[2]) // 2
        _text(draw, (cx, 255), title_text, team if _has_arabic(title_text) else _latin_font(34), anchor="mm", rtl=rtl_title)
        draw.text((cx - 12, 320), f"{int(total):,}", font=score, fill=(245, 247, 250), anchor="rm")
        _text(draw, (cx + 12, 320), "نقطة", score, anchor="lm", rtl=True)
        y = 390
        _text(draw, (box[0] + 82, y - 25), "اللاعب", small, anchor="lm", rtl=True)
        _text(draw, (box[2] - 38, y - 25), "النقاط والجائزة", small, anchor="rm", rtl=True)
        if not players_list:
            _text(draw, (cx, y), "لا يوجد لاعبين", small, anchor="mm", rtl=True)
        for index, player in enumerate(players_list, 1):
            value = int(scores.get(player, 0))
            player_text = str(player).lstrip("@").strip()
            player_font = name if _has_arabic(player_text) else latin_name
            # Do not mix Arabic with the Latin index/@ in one RTL draw call.
            # Drawing them separately prevents Arabic names from appearing
            # reversed or with punctuation in the wrong place.
            draw.text((box[0] + 38, y), f"{index}.", font=_latin_font(25), fill=(245, 247, 250), anchor="lm")
            _text(draw, (box[0] + 82, y), player_text, player_font, anchor="lm", rtl=_has_arabic(player_text))
            award = int(awards.get(player, 0))
            metric = f"{value} / {award:,}"
            _text(draw, (box[2] - 38, y), metric, _latin_font(23), anchor="rm", rtl=False)
            y += 65

    panel(left, str(team1_name), p1, team1_score, _has_arabic(str(team1_name)))
    panel(right, str(team2_name), p2, team2_score, _has_arabic(str(team2_name)))

    draw.line((W // 2, 225, W // 2, H - 75), fill=(115, 125, 145), width=2)
    if prize:
        draw.rounded_rectangle((W // 2 - 205, H - 125, W // 2 + 205, H - 65), radius=18, fill=(31, 67, 51), outline=(102, 170, 119), width=2)
        _text(draw, (W // 2, H - 95), f"الجائزة: {prize:,} نقطة", small, anchor="mm", rtl=True)

    img.save(path, "PNG", optimize=True)
    return filename
