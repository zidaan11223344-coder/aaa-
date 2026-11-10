"""Elegant cricket result-card renderer for the Talkin game."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    arabic_reshaper = None
    get_display = None

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.getenv("CRICKET_MEDIA_DIR", str(ROOT / "generated_games")))
FONT_CANDIDATES = (
    os.getenv("CRICKET_FONT", "").strip(),
    str(ROOT / "assets" / "NotoSansArabic-SemiBold.ttf"),
    str(ROOT / "assets" / "NotoSansArabic-Regular.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int):
    for candidate in FONT_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _latin_font(size: int):
    for candidate in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return _font(size)


def _has_arabic(value: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in str(value))


def _shape_rtl(value: str) -> str:
    text = str(value)
    if not _has_arabic(text) or arabic_reshaper is None or get_display is None:
        return text
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _text(draw, xy, value, font, anchor="la", fill=(245, 247, 250), rtl=None):
    value = str(value)
    rtl = _has_arabic(value) if rtl is None else rtl
    try:
        draw.text(xy, value, font=font, fill=fill, anchor=anchor,
                  direction="rtl" if rtl else "ltr", language="ar" if rtl else None)
    except Exception:
        draw.text(xy, _shape_rtl(value) if rtl else value, font=font, fill=fill, anchor=anchor)


def _asset(name: str, size: tuple[int, int]) -> Image.Image | None:
    path = ROOT / "assets" / name
    if not path.is_file():
        return None
    try:
        image = Image.open(path).convert("RGBA")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        return image
    except Exception:
        return None


def _paste_cover(canvas, source: Image.Image | None, box, tint):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    if source is None:
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(box, radius=26, fill=tint)
        return
    source = source.copy()
    source.thumbnail((w - 30, h - 30), Image.Resampling.LANCZOS)
    backdrop = Image.new("RGBA", (w, h), tint)
    backdrop.alpha_composite(source, ((w - source.width) // 2, (h - source.height) // 2))
    canvas.alpha_composite(backdrop, (x1, y1))


def render_result_image(*, match_id: str, team1_name: str = "الفريق الأول",
                        team1_players: Iterable[str] = (), team1_score: int = 0,
                        team2_name: str = "الفريق الثاني", team2_players: Iterable[str] = (),
                        team2_score: int = 0, player_scores: dict[str, int] | None = None,
                        player_balls: dict[str, int] | None = None,
                        team1_label: str = "فريق الهجوم", team2_label: str = "فريق الدفاع",
                        player_awards: dict[str, int] | None = None, winner: str = "تعادل",
                        prize: int = 0, output_dir: str | Path | None = None,
                        players: Iterable[str] | None = None, human_score: int | None = None,
                        bot_score: int | None = None) -> str:
    """Render a cinematic two-team card; scores sit in high-contrast boxes above imagery."""
    target_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in str(match_id) if ch.isalnum())[:24] or "match"
    path = target_dir / f"cricket_result_{safe_id}.png"
    if players is not None:
        team1_players = players
    if human_score is not None:
        team1_score = human_score
    if bot_score is not None:
        team2_score = bot_score
    p1 = [str(p).lstrip("@") for p in team1_players if str(p).strip()]
    p2 = [str(p).lstrip("@") for p in team2_players if str(p).strip()]
    scores = {str(k).lstrip("@"): int(v) for k, v in (player_scores or {}).items()}
    balls = {str(k).lstrip("@"): int(v) for k, v in (player_balls or {}).items()}
    awards = {str(k).lstrip("@"): int(v) for k, v in (player_awards or {}).items()}

    # Four-player teams need vertical room for the hero area plus four
    # two-line player rows; the previous 960px canvas clipped the lower rows.
    W, H = 1600, 1400
    img = Image.new("RGBA", (W, H), (7, 13, 27, 255))
    bg = Image.new("RGBA", (W, H), (7, 13, 27, 255))
    bg = bg.filter(ImageFilter.GaussianBlur(1))
    img.alpha_composite(bg)
    draw = ImageDraw.Draw(img)
    gold, white = (247, 201, 92, 255), (245, 248, 252, 255)
    blue, red = (35, 157, 255, 255), (232, 62, 83, 255)
    draw.rounded_rectangle((24, 20, W - 24, H - 20), radius=34, fill=(16, 25, 43, 255), outline=(67, 91, 126, 255), width=3)
    _text(draw, (W // 2, 70), "نتيجة مباراة الكركيت", _font(58), anchor="mm", fill=gold, rtl=True)
    _text(draw, (W // 2, 132), f"الفائز: {winner}", _font(30), anchor="mm", fill=white, rtl=True)

    panels = [
        ((55, 190, W // 2 - 18, H - 90), str(team1_name), str(team1_label), p1, int(team1_score), blue, "cricket_attack_blue.png"),
        ((W // 2 + 18, 190, W - 55, H - 90), str(team2_name), str(team2_label), p2, int(team2_score), red, "cricket_defense_red.png"),
    ]
    for box, room_name, team_label, players_list, total, accent, hero in panels:
        draw.rounded_rectangle(box, radius=28, fill=(24, 35, 56, 255), outline=accent, width=5)
        _text(draw, ((box[0] + box[2]) // 2, box[1] + 29), room_name, _font(28), anchor="mm", fill=white, rtl=_has_arabic(room_name))
        _text(draw, ((box[0] + box[2]) // 2, box[1] + 62), team_label, _font(24), anchor="mm", fill=gold, rtl=_has_arabic(team_label))
        # Number is deliberately placed in a bright box above the player image.
        score_box = (box[0] + 32, box[1] + 86, box[2] - 32, box[1] + 166)
        draw.rounded_rectangle(score_box, radius=18, fill=(8, 14, 27, 245), outline=accent, width=3)
        _text(draw, ((score_box[0] + score_box[2]) // 2, score_box[1] + 44), f"{total:,} نقطة", _latin_font(42), anchor="mm", fill=white, rtl=False)
        hero_box = (box[0] + 28, box[1] + 184, box[2] - 28, box[1] + 350)
        hero_asset = _asset(hero, (hero_box[2] - hero_box[0], hero_box[3] - hero_box[1]))
        _paste_cover(img, hero_asset, hero_box, (11, 24, 45, 255) if accent == blue else (45, 15, 29, 255))
        _text(draw, ((box[0] + box[2]) // 2, box[1] + 392), "اللاعب وإحصائياته", _font(20), anchor="mm", fill=gold, rtl=True)
        y = box[1] + 430
        for index, player in enumerate(players_list, 1):
            player = str(player).strip()
            value = int(scores.get(player, 0))
            ball_count = int(balls.get(player, 0))
            award = int(awards.get(player, 0))
            row_top = y - 20
            avatar_box = (box[0] + 28, row_top + 2, box[0] + 70, row_top + 44)
            avatar = hero_asset.copy() if hero_asset is not None else Image.new("RGBA", (42, 42), accent)
            avatar.thumbnail((42, 42), Image.Resampling.LANCZOS)
            mask = Image.new("L", (42, 42), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 41, 41), fill=255)
            avatar_layer = Image.new("RGBA", (42, 42), (0, 0, 0, 0))
            avatar_layer.paste(avatar.resize((42, 42), Image.Resampling.LANCZOS), (0, 0), mask)
            img.alpha_composite(avatar_layer, (avatar_box[0], avatar_box[1]))
            draw.ellipse(avatar_box, outline=accent, width=3)
            name_box = (box[0] + 78, row_top, box[2] - 28, row_top + 26)
            stats_box = (box[0] + 78, row_top + 29, box[2] - 28, row_top + 55)
            draw.rounded_rectangle(name_box, radius=10, fill=(10, 19, 35, 245), outline=accent, width=2)
            draw.rounded_rectangle(stats_box, radius=10, fill=(18, 30, 50, 245), outline=(88, 112, 145, 255), width=2)
            _text(draw, (name_box[0] + 10, name_box[1] + 13), f"{index}. {player}", _font(17) if _has_arabic(player) else _latin_font(17), anchor="lm", fill=white, rtl=_has_arabic(player))
            _text(draw, ((stats_box[0] + stats_box[2]) // 2, stats_box[1] + 13), f"كرات {ball_count} | نقاط {value} | جائزة {award:,}", _font(14), anchor="mm", fill=white, rtl=True)
            y += 62
        if not players_list:
            _text(draw, ((box[0] + box[2]) // 2, y), "لا يوجد لاعبين", _font(23), anchor="mm", fill=white, rtl=True)
    if prize:
        draw.rounded_rectangle((W // 2 - 245, H - 70, W // 2 + 245, H - 30), radius=15, fill=(26, 84, 53, 255), outline=(107, 194, 132, 255), width=2)
        _text(draw, (W // 2, H - 50), f"الجائزة: {prize:,} نقطة", _font(22), anchor="mm", fill=white, rtl=True)
    img.convert("RGB").save(path, "PNG", optimize=True)
    return path.name


def render_ball_image(*, match_id: str, innings: int = 1, ball_no: int, number: int, out: bool = False,
                      team_asset: str | None = None,
                      output_dir: str | Path | None = None) -> str:
    """Create a per-ball card using the fixed team identity (team 1 blue, team 2 red)."""
    target_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in str(match_id) if ch.isalnum())[:20] or "match"
    # Ball numbers restart at one in innings two; include innings in the path
    # so later cards never overwrite earlier cards in the event queue.
    path = target_dir / f"cricket_result_ball_{safe_id}_i{int(innings)}_{int(ball_no)}.png"
    canvas = Image.new("RGBA", (900, 760), (8, 15, 30, 255))
    draw = ImageDraw.Draw(canvas)
    accent = (232, 62, 83, 255) if out else (35, 157, 255, 255)
    title = "خروج اللاعب — OUT" if out else "كرة الهجوم"
    _text(draw, (450, 52), title, _font(42), anchor="mm", fill=(247, 201, 92, 255), rtl=True)
    image_name = "cricket_out.png" if out else (team_asset or "cricket_attack_blue.png")
    hero = _asset(image_name, (760, 535))
    if hero is not None:
        hero.thumbnail((760, 535), Image.Resampling.LANCZOS)
        canvas.alpha_composite(hero, ((900 - hero.width) // 2, 125))
    else:
        draw.rounded_rectangle((70, 125, 830, 660), radius=25, fill=(18, 35, 60, 255))
    box = (330, 92, 570, 214)
    draw.rounded_rectangle(box, radius=24, fill=(7, 16, 31, 245), outline=accent, width=6)
    _text(draw, (450, 153), "OUT" if out else str(int(number)), _latin_font(76), anchor="mm", fill=(255, 255, 255, 255), rtl=False)
    _text(draw, (450, 708), f"الكرة رقم {int(ball_no)}", _font(28), anchor="mm", fill=(220, 230, 245, 255), rtl=True)
    canvas.convert("RGB").save(path, "PNG", optimize=True)
    return path.name
