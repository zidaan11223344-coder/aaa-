# -*- coding: utf-8 -*-
"""
alsfer_bot — بوت Giant Chat المطور
• تشغيل الموسيقى من يوتيوب (بصمة صوتية)
• نظام ألعاب متكامل مع صور PNG
• نظام نقاط، توب، زواج، ومضاربة
• نظام إدارة (ماستر، طرد، حظر، ردود مخصصة)
"""

import asyncio
import json
import logging
import re
import os
import sys
import time
import uuid
import random
import tempfile
import shutil
import base64
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
import requests

# ----------------------------- الذكاء الاصطناعي المحلي -----------------------------
# لا يستخدم OpenAI ولا يحتاج إلى مفتاح API.
try:
    from llama_cpp import Llama
    LOCAL_LLAMACPP_AVAILABLE = True
except Exception:
    Llama = None
    LOCAL_LLAMACPP_AVAILABLE = False
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    Image = ImageDraw = ImageFont = None
    PIL_AVAILABLE = False
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    arabic_reshaper = None
    get_display = None
try:
    import yt_dlp
except ImportError:
    yt_dlp = None
from supabase import create_client

# ----------------------------- إعداد السجلات -----------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join("logs", "bot.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("alsfer")

# ----------------------------- الإعدادات -----------------------------
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
POINTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "points.json")
GIFT_POINTS_LOCK = asyncio.Lock()
REPLIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replies.json")
MASTERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masters.json")
BANS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bans.json")
ROOMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rooms.json")
MODERATION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moderation.json")
WELCOME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "welcome.json")
PUBLISHED_POSTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "published_posts.json")
SOCIAL_EVENTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_events.json")
VIP_USERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vip_users.json")
CUSTOM_GAMES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_games.json")
CUSTOM_COMMANDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_commands.json")
REPAIR_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repair_state.json")
TESTING_GAMES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", "testing", "games.json")
TESTING_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", "testing", "active_tests.json")
GAME_DESIGN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", "designer_state.json")
APPROVED_GAMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", "approved")


os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open(CONFIG_PATH, encoding="utf-8") as f:
    C = json.load(f)

# يمكن تشغيل البوت على Railway بدون وضع أسرار الحساب داخل config.json.
# Environment Variables لها الأولوية على القيم الموجودة في الملف.
for _key, _env in (
    ("supabase_url", "SUPABASE_URL"),
    ("supabase_key", "SUPABASE_KEY"),
    ("username", "GIANT_USERNAME"),
    ("password", "GIANT_PASSWORD"),
    ("owner_username", "OWNER_USERNAME"),
):
    if os.environ.get(_env):
        C[_key] = os.environ[_env]

REQUIRED = ["supabase_url", "supabase_key", "username", "password"]
missing = [k for k in REQUIRED if not str(C.get(k, "")).strip()]
if missing:
    log.error("نقص في إعدادات Giant Chat: %s", ", ".join(missing))
    sys.exit(1)

USERNAME = C["username"].strip()
PASSWORD = C["password"]
OWNER = (C.get("owner_username") or USERNAME).strip().lower()
# التوثيق قابل للتشغيل/الإيقاف من المالك. الافتراضي ON حفاظاً على السلوك الآمن الحالي.
VERIFICATION_ENABLED = bool(C.get("verification_enabled", True))

# ----------------------------- إعداد الذكاء المحلي -----------------------------
# نموذج GGUF يُنزّل تلقائياً داخل Railway عند أول استخدام للذكاء.
LOCAL_AI_MODEL_URL = os.environ.get(
    "LOCAL_AI_MODEL_URL",
    "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
).strip()
LOCAL_AI_MODEL_PATH = Path(
    os.environ.get(
        "LOCAL_AI_MODEL_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "qwen2.5-0.5b-instruct-q4_k_m.gguf")
    )
)
LOCAL_AI_CTX = max(1024, int(os.environ.get("LOCAL_AI_CTX", "4096")))
LOCAL_AI_THREADS = max(1, int(os.environ.get("LOCAL_AI_THREADS", str(max(1, (os.cpu_count() or 2) - 1)))))
LOCAL_AI_MAX_TOKENS = max(128, int(os.environ.get("LOCAL_AI_MAX_TOKENS", "700")))
LOCAL_AI_DOWNLOAD_LOCK = asyncio.Lock()
LOCAL_AI_LOAD_LOCK = asyncio.Lock()
LOCAL_AI_MODEL = None
LOCAL_AI_LOAD_ERROR = ""

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BACKUP_CHAT_ID = os.environ.get("TELEGRAM_BACKUP_CHAT_ID", "").strip()
AI_MAX_LOG_LINES = max(20, int(os.environ.get("AI_MAX_LOG_LINES", "120")))
POLL = max(1.0, float(C.get("poll_seconds", 2)))
SEARCH_URL = C.get("music_search_url") or "https://giant-chat-app.lovable.app/api/public/search-track"
YOUTUBE_COOKIES_PATH = str(C.get("youtube_cookies_path", "youtube_cookies.txt")).strip()
# أسرار cookies يمكن حفظها كمتغيرات Railway، ولا يجب رفعها إلى GitHub.
YOUTUBE_COOKIES_ENV = os.environ.get("YOUTUBE_COOKIES", "").strip()
# Optional multiple YouTube sessions. Use only cookies belonging to accounts you control.
# The bot never prints the cookie contents.
YOUTUBE_COOKIE_ENVS = [
    (name, os.environ.get(name, "").strip())
    for name in ["YOUTUBE_COOKIES", *[f"YOUTUBE_COOKIES_{i}" for i in range(1, 11)]]
]
YOUTUBE_COOKIE_FILES = []
YOUTUBE_COOKIE_INDEX = 0
TIKTOK_COOKIES_ENV = os.environ.get("TIKTOK_COOKIES", "").strip()
SPOTIFY_COOKIES_ENV = os.environ.get("SPOTIFY_COOKIES", "").strip()
YOUTUBE_PO_TOKEN = os.environ.get("YOUTUBE_PO_TOKEN", "").strip()


def _normalize_cookie_text(raw):
    """تنظيف محتوى ملف cookies القادم من متغيرات Railway.

    الأخطاء الشائعة: أسطر مكتوبة كـ \n نصية، مسافات بدل TAB، أو غياب ترويسة
    Netscape. yt-dlp يرفض الملف في كل هذه الحالات ويظهر الخطأ كأنه فشل يوتيوب.
    """
    text = str(raw or "")
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    text = text.replace("\\t", "\t").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.lstrip().startswith("#"):
            lines.append(line)
            continue
        if "\t" not in line:
            parts = re.split(r"\s{1,}", line.strip())
            if len(parts) >= 7:
                line = "\t".join(parts[:6] + [" ".join(parts[6:])])
        lines.append(line)
    if not lines:
        return ""
    if not lines[0].startswith("# Netscape HTTP Cookie File"):
        lines.insert(0, "# Netscape HTTP Cookie File")
    return "\n".join(lines) + "\n"


def _write_cookie_file(raw, path):
    """كتابة ملف cookies صالح وإرجاع مساره، أو None إذا لم يكن صالحاً."""
    content = _normalize_cookie_text(raw)
    data_lines = [l for l in content.split("\n") if l and not l.startswith("#")]
    if not data_lines:
        return None
    try:
        p = Path(path)
        p.write_text(content, encoding="utf-8")
        return str(p)
    except Exception as _e:
        log.warning("تعذر إنشاء ملف cookies %s: %s", path, _e)
        return None


# Load up to 11 manually exported cookie sets from Railway variables.
# Files are stored only in /tmp and are never logged.
for _idx, (_name, _raw) in enumerate(YOUTUBE_COOKIE_ENVS):
    if not _raw:
        continue
    _path = f"/tmp/youtube_cookies_{_idx}.txt"
    _fixed = _write_cookie_file(_raw, _path)
    if _fixed:
        YOUTUBE_COOKIE_FILES.append(_fixed)
    else:
        log.warning("%s موجود لكنه غير صالح بصيغة Netscape؛ تم تجاهله.", _name)

if YOUTUBE_COOKIE_FILES:
    YOUTUBE_COOKIES_PATH = YOUTUBE_COOKIE_FILES[0]
elif YOUTUBE_COOKIES_PATH and os.path.isfile(YOUTUBE_COOKIES_PATH):
    _fixed = _write_cookie_file(Path(YOUTUBE_COOKIES_PATH).read_text(encoding="utf-8", errors="ignore"),
                                "/tmp/youtube_cookies_0.txt")
    if _fixed:
        YOUTUBE_COOKIES_PATH = _fixed
        YOUTUBE_COOKIE_FILES.append(_fixed)

TIKTOK_COOKIES_PATH = "/tmp/tiktok_cookies.txt"
if TIKTOK_COOKIES_ENV:
    if not _write_cookie_file(TIKTOK_COOKIES_ENV, TIKTOK_COOKIES_PATH):
        log.warning("TIKTOK_COOKIES غير صالح؛ سيعمل TikTok بدون cookies.")


def has_youtube_cookies():
    return bool(YOUTUBE_COOKIE_FILES) or (bool(YOUTUBE_COOKIES_PATH) and os.path.isfile(YOUTUBE_COOKIES_PATH))


def get_youtube_cookie_files():
    """Return the configured YouTube cookie files without exposing their contents."""
    files = [p for p in YOUTUBE_COOKIE_FILES if os.path.isfile(p)]
    if not files and YOUTUBE_COOKIES_PATH and os.path.isfile(YOUTUBE_COOKIES_PATH):
        files = [YOUTUBE_COOKIES_PATH]
    return files


def youtube_cookie_status():
    if not has_youtube_cookies():
        return False, "لم يتم العثور على ملف Cookies صالح في Railway (YOUTUBE_COOKIES أو YOUTUBE_COOKIES_1..YOUTUBE_COOKIES_10)."
    try:
        text = Path(YOUTUBE_COOKIES_PATH).read_text(encoding="utf-8", errors="ignore")
        rows = []
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            if len(line.split("\t")) >= 7:
                rows.append(line)
        if not rows:
            return False, "ملف YOUTUBE_COOKIES موجود لكنه لا يحتوي أسطر Netscape صحيحة (7 حقول مفصولة بـ TAB)."
        return True, f"Cookies صالحة شكلياً: {len(rows)} سجل."
    except Exception as e:
        return False, f"تعذر قراءة ملف Cookies: {type(e).__name__}: {e}"


def yt_base_options(source_label="YouTube", cookie_file=None):
    """خيارات yt-dlp موحّدة لكل مصادر الصوت.

    مهم: عند استخدام cookies يجب عدم استخدام عميل android/ios لأن يوتيوب
    يتجاهل الجلسة معهما ويعيد «Sign in to confirm you're not a bot».
    """
    options = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "socket_timeout": 35, "retries": 5, "fragment_retries": 5,
        "extractor_retries": 4, "file_access_retries": 3,
        "cachedir": False, "geo_bypass": True, "overwrites": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        },
    }
    if source_label == "YouTube":
        # YouTube في 2026 يفرض PO Tokens على بعض عملاء GVS.
        # لا نستخدم mweb افتراضياً لأنه أكثر عرضة لـ403 بدون PO Token.
        clients = str(os.environ.get("YOUTUBE_PLAYER_CLIENTS") or C.get("youtube_player_clients", "default,web_embedded")).strip()
        client_list = [x.strip() for x in clients.split(",") if x.strip()]
        if not client_list:
            client_list = ["default", "web_embedded"]
        if cookie_file and os.path.isfile(cookie_file):
            options["cookiefile"] = cookie_file
        elif has_youtube_cookies():
            options["cookiefile"] = get_youtube_cookie_files()[0]
        ex = {"youtube": {"player_client": client_list}}
        if YOUTUBE_PO_TOKEN:
            # الصيغة التي يفهمها yt-dlp: client.gvs+TOKEN أو client.player+TOKEN.
            ex["youtube"]["po_token"] = YOUTUBE_PO_TOKEN
        options["extractor_args"] = ex
    elif source_label == "TikTok" and os.path.isfile(TIKTOK_COOKIES_PATH):
        options["cookiefile"] = TIKTOK_COOKIES_PATH
    return options


PIPED_APIS = [x.strip().rstrip("/") for x in C.get("piped_apis", [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.leptons.xyz",
    "https://piped-api.privacy.com.de",
    "https://pipedapi.adminforge.de",
]) if str(x).strip()]
MUSIC_MAX_DURATION = int(C.get("music_max_duration_seconds", 900))

# رابط عام لملفات الصوت التي سيشغلها تطبيق Giant Chat.
# على Railway يفضل استخدام RAILWAY_PUBLIC_DOMAIN تلقائياً، أو ضع PUBLIC_BASE_URL يدوياً.
# استخدم نطاق Railway الحالي أولاً حتى لا يبقى رابط قديم من Environment منسوخ من مشروع آخر.
_RAILWAY_DOMAIN = str(os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip().strip("/")
PUBLIC_BASE_URL = str(
    (f"https://{_RAILWAY_DOMAIN}" if _RAILWAY_DOMAIN else "")
    or os.environ.get("PUBLIC_BASE_URL")
    or C.get("music_public_base_url")
).rstrip("/")
MEDIA_PATH = "/media"
MEDIA_SERVER_PORT = int(os.environ.get("PORT", "8080"))

def create_supabase_client(url, key):
    """إنشاء عميل يدعم مفاتيح Supabase الجديدة sb_publishable_.

    supabase-py 2.15 يتحقق محليًا من أن المفتاح JWT، بينما publishable
    ليس JWT. نستخدم قيمة JWT شكلية فقط لتجاوز الفحص المحلي، ثم نستبدل
    رأس الاتصال الحقيقي إلى apiKey بالمفتاح publishable.
    """
    if str(key).startswith("sb_publishable_"):
        placeholder_jwt = "a.b.c"
        client = create_client(url, placeholder_jwt)
        client.supabase_key = key
        headers = client.options.headers
        headers["apiKey"] = key
        headers.pop("Authorization", None)
        return client
    return create_client(url, key)


sb = create_supabase_client(C["supabase_url"], C["supabase_key"])

BOT_ID = None
AUTH_ACCESS_TOKEN = None
rooms = {}          # room_id -> room_name
last_room = {}      # room_id -> last created_at seen
seen_dm = set()
kaf_games = {}
war_games = {}       # حرب عالمية واحدة: لاعبان من أي غرفتين
GLOBAL_WAR_KEY = "__global_war__"  # مفتاح ثابت لمباراة حرب واحدة مشتركة بين جميع الغرف
last_music_started = 0.0
music_queue = asyncio.Queue()      # room_id, query, source, requester_id, requester_name
music_state = {}     # room_id -> آخر أغنية شغّلها البوت
music_last_by_user = {}  # user_id -> آخر طلب أغنية، فاصل مستقل دقيقتان لكل مستخدم
music_tasks = {}      # room_id -> مهمة البحث/التشغيل الخلفية
publish_pending = {}  # (room_id, user_id) -> {created_at, description}
SOCIAL_SEEN = set()
SOCIAL_WEBHOOK_TOKEN = str(os.environ.get("SOCIAL_WEBHOOK_TOKEN") or C.get("social_webhook_token", "")).strip()
http: aiohttp.ClientSession = None
media_runner = None
media_site = None
# حالة تشغيل البوت: تُحدّث باستمرار حتى لا نعتمد على last_seen قديم.
BOT_STARTED_AT = time.time()
LAST_HEARTBEAT_AT = 0.0
LAST_DB_OK_AT = 0.0
NETWORK_ONLINE = True

# صور الألعاب PNG
# كتالوج البوت المستقل: لا يقرأ جدول هدايا التطبيق ولا يعرض هداياه.
# تبقى UUIDs هنا كمعرّفات داخلية فقط، ولا تظهر للمستخدم.
BOT_GIFTS = {
    "1": {"id": "2d0d35fa-d0bf-40e1-ace9-938bb49e9a63", "name": "وردة", "emoji": "🌹", "cost_points": 10, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f339.png"},
    "2": {"id": "157c16af-e01c-48fb-b718-be279406f967", "name": "قلب", "emoji": "❤️", "cost_points": 20, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/2764.png"},
    "3": {"id": "056dd4c2-58d2-48a9-8ec7-95169ed1ac54", "name": "قبلة", "emoji": "😘", "cost_points": 30, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f618.png"},
    "4": {"id": "f9a3c396-0e60-4761-8ae8-d3a4dd6ca096", "name": "دب", "emoji": "🧸", "cost_points": 50, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f9f8.png"},
    "5": {"id": "5566a755-c78d-4d74-aae9-2da599adae1a", "name": "كعكة", "emoji": "🎂", "cost_points": 80, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f382.png"},
    "6": {"id": "6bab6899-db41-494b-8fad-8eebf5af8b17", "name": "ألعاب نارية", "emoji": "🎆", "cost_points": 150, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f386.png"},
    "7": {"id": "416557d0-0297-4a42-8709-7232ace2c65a", "name": "برق", "emoji": "⚡", "cost_points": 200, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/26a1.png"},
    "8": {"id": "d255facd-8b2f-407e-8706-33a9fe6ffb00", "name": "تاج", "emoji": "👑", "cost_points": 500, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f451.png"},
    "9": {"id": "2ac92587-7b58-418a-93d4-cecaf70dc90c", "name": "أميرة", "emoji": "👸", "cost_points": 800, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f478.png"},
    "10": {"id": "21595a25-4fed-4d9a-a200-fda8a16c6af1", "name": "سيارة", "emoji": "🏎️", "cost_points": 1000, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f3ce.png"},
    "11": {"id": "f8f5b161-e49f-4f30-9365-4e66af6e0918", "name": "طائرة", "emoji": "✈️", "cost_points": 1500, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/2708.png"},
    "12": {"id": "cfa01a67-d54e-4a9f-b11a-dbfa04ad4a4a", "name": "تنين", "emoji": "🐉", "cost_points": 3000, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f409.png"},
    "13": {"id": "4e3b32a3-17a8-41ef-bc9a-cef4c21e10f7", "name": "سفينة فضاء", "emoji": "🚀", "cost_points": 5000, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f680.png"},
    "14": {"id": "1aa63f2b-2fbc-40cb-b0af-3c1200724774", "name": "قصر", "emoji": "🏰", "cost_points": 8000, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f3f0.png"}
}

# صور مباشرة ثابتة بصيغة PNG؛ تُرسل بالطريقة نفسها المستخدمة للهدايا.
TWEMOJI = "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/"
GAME_BASE_URL = str(C.get("game_public_base_url", "")).rstrip("/")
def game_asset(filename):
    base = PUBLIC_BASE_URL or GAME_BASE_URL
    if base:
        return f"{base}/assets/{quote(filename)}"
    return f"assets/{filename}"

GAME_IMAGES = {
    "race": game_asset("game_race.jpg"),
    "bribe": game_asset("game_bribe.jpg"),
    "basket": game_asset("game_basket.jpg"),
    "drone": game_asset("game_drone.jpg"),
    "frog": game_asset("game_frog.jpg"),
    "cards": game_asset("game_cards.jpg"),
    "ball": game_asset("game_ball.jpg"),
    "boxing": game_asset("defense_action.jpg"),
    "fight": game_asset("fight_action.jpg"),
    "job": game_asset("game_job.jpg"),
    "meet": game_asset("game_meet.jpg"),
    "slap": game_asset("slap_action.jpg"),
    "volcano": game_asset("game_volcano.jpg"),
    "ghost": game_asset("game_ghost.jpg"),
    "bet": game_asset("game_bet.jpg"),
    "war": game_asset("war_game.png"),
    "rob": game_asset("game_rob.jpg"),
    "luck": game_asset("game_luck.jpg"),
    "dice": game_asset("game_dice.jpg"),
    "marriage": game_asset("game_marriage.jpg"),
    "challenge": game_asset("game_challenge.jpg"),
    "mine": game_asset("game_mine.jpg")
}

# ----------------------------- أدوات البيانات -----------------------------
def load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_points(): return load_json(POINTS_PATH, {})
def save_points(p): save_json(POINTS_PATH, p)
def load_replies(): return load_json(REPLIES_PATH, {})
def save_replies(r): save_json(REPLIES_PATH, r)
def load_masters(): return load_json(MASTERS_PATH, [])
def save_masters(m): save_json(MASTERS_PATH, m)
def load_bans(): return load_json(BANS_PATH, {})
def save_bans(b): save_json(BANS_PATH, b)
def load_rooms_saved(): return load_json(ROOMS_PATH, {})
def save_rooms_saved(r): save_json(ROOMS_PATH, r)
def load_moderation(): return load_json(MODERATION_PATH, {"enabled": {}, "words": []})
def save_moderation(x): save_json(MODERATION_PATH, x)
def load_welcome(): return load_json(WELCOME_PATH, {})
def save_welcome(x): save_json(WELCOME_PATH, x)
def load_published_posts(): return load_json(PUBLISHED_POSTS_PATH, {})
def save_published_posts(x): save_json(PUBLISHED_POSTS_PATH, x)
def load_social_events(): return load_json(SOCIAL_EVENTS_PATH, {})
def save_social_events(x): save_json(SOCIAL_EVENTS_PATH, x)

def load_vip_users():
    data = load_json(VIP_USERS_PATH, {})
    if isinstance(data, list):
        return {str(x).strip().lower(): {"username": str(x).strip()} for x in data if str(x).strip()}
    return data if isinstance(data, dict) else {}

def save_vip_users(x): save_json(VIP_USERS_PATH, x)

async def is_vip(uid, username):
    # عند إيقاف التوثيق يصبح الجميع مخولين للخدمات المحمية.
    if not VERIFICATION_ENABLED:
        return True
    if str(username or '').strip().lower() == OWNER:
        return True
    data = load_vip_users()
    key_uid = str(uid)
    key_name = str(username or '').strip().lower()
    if key_uid in data:
        return True
    for key, item in data.items():
        if str(key).lower() == key_name:
            return True
        if isinstance(item, dict):
            if str(item.get("id", "")).strip() == key_uid:
                return True
            if str(item.get("username", "")).strip().lower() == key_name:
                return True
    return False

async def require_vip(uid, username, feature="هذه الخدمة"):
    if not VERIFICATION_ENABLED:
        return None
    if await is_vip(uid, username):
        return None
    return (f"🔒 @{username} هذه {feature} تتطلب توثيق الحساب من صاحب البوت.\n"
            f"📌 طريقة التوثيق: صاحب البوت يكتب vip@اسم_المستخدم")

async def set_verification_enabled(enabled):
    global VERIFICATION_ENABLED
    VERIFICATION_ENABLED = bool(enabled)
    C["verification_enabled"] = VERIFICATION_ENABLED
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(C, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("failed to persist verification setting")
    return VERIFICATION_ENABLED

async def grant_vip_by_username(target_username):
    target = str(target_username or '').replace('@', '').strip()
    if not target:
        return False, "❌ الصيغة: vip@اسم المستخدم"
    rows, err = await table_select(lambda: sb.table("profiles").select("id,username").eq("username", target).limit(1).execute())
    if err:
        return False, f"❌ تعذر البحث عن المستخدم: {err}"
    if not rows:
        return False, f"❌ المستخدم @{target} غير موجود."
    row = rows[0]
    data = load_vip_users()
    data[str(row.get("id"))] = {"id": str(row.get("id")), "username": str(row.get("username") or target), "granted_at": now_iso()}
    save_vip_users(data)
    return True, f"✅ تم توثيق @{row.get('username') if row.get('username') else target} VIP.\n🎵 يمكنه تشغيل/مشاركة الأغاني.\n🎮 ويمكنه استخدام الألعاب."

def normalize_text(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())

async def check_forbidden_word(rid, text):
    mod = load_moderation()
    if not mod.get("enabled", {}).get(str(rid), False) or not text:
        return None
    normalized = normalize_text(text)
    for word in mod.get("words", []):
        if normalize_text(word) and normalize_text(word) in normalized:
            return f"🚫 تم منع الرسالة بسبب الكلمة الممنوعة: {word}"
    return None

async def all_room_ids():
    """Return every room visible to the bot, not only rooms currently cached."""
    ids = set(rooms.keys())
    try:
        rows, _ = await table_select(lambda: sb.table("rooms").select("id,name").execute())
        for row in rows or []:
            rid = row.get("id")
            if rid:
                ids.add(rid)
                rooms.setdefault(rid, row.get("name") or "الغرفة")
    except Exception:
        log.exception("failed to load all rooms")
    return list(ids)

async def broadcast_text(text, exclude_rid=None):
    for room_id in await all_room_ids():
        if room_id == exclude_rid:
            continue
        try:
            await room_send(room_id, text)
        except Exception:
            log.exception("broadcast text failed for room %s", room_id)

async def broadcast_media(text, media_url, m_type="image", duration_ms=None, exclude_rid=None):
    sent = 0
    for room_id in await all_room_ids():
        if room_id == exclude_rid:
            continue
        try:
            await room_send_media(room_id, text, media_url, m_type=m_type, duration_ms=duration_ms)
            sent += 1
        except Exception:
            log.exception("broadcast media failed for room %s", room_id)
    return sent


async def game_cooldown(uid, username):
    """فاصل الألعاب مستقل لكل مستخدم، وليس فاصلًا عالميًا."""
    seconds = int(C.get("game_cooldown_seconds", 30))
    return check_cooldown(uid, username, "game", seconds)

async def is_banned(rid, uid):
    bans = load_bans()
    return uid in bans.get(rid, [])

async def is_master(uid, username):
    if username.lower() == OWNER: return True
    masters = load_masters()
    return uid in masters or username.lower() in [str(m).lower() for m in masters]

def get_user_data(uid, username):
    points = load_points()
    if uid not in points:
        points[uid] = {"username": username, "points": 0, "cooldowns": {}, "married_to": None}
    else:
        points[uid]["username"] = username
    return points, points[uid]

def add_points(uid, username, amount):
    points, user_data = get_user_data(uid, username)
    user_data["points"] += amount
    points[uid] = user_data
    save_points(points)

def check_cooldown(uid, username, command, seconds):
    points, user_data = get_user_data(uid, username)
    cooldowns = user_data.get("cooldowns", {})
    last_time = cooldowns.get(command, 0)
    now = time.time()
    if now - last_time < seconds:
        return False, int(seconds - (now - last_time))
    cooldowns[command] = now
    user_data["cooldowns"] = cooldowns
    points[uid] = user_data
    save_points(points)
    return True, 0

# ----------------------------- أدوات النظام -----------------------------
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

async def run(fn):
    def safe():
        try: return fn(), None
        except Exception as e: return None, getattr(e, "message", None) or str(e)
    return await asyncio.to_thread(safe)

async def table_select(builder_fn):
    res, err = await run(builder_fn)
    if err: return None, err
    return (getattr(res, "data", None) or []), None

async def rpc(name, args):
    res, err = await run(lambda: sb.rpc(name, args).execute())
    if err: return None, err
    return getattr(res, "data", None), None

async def username_of(uid):
    rows, _ = await table_select(lambda: sb.table("profiles").select("username").eq("id", uid).limit(1).execute())
    return (rows[0].get("username") if rows else "") or ""

# ----------------------------- إرسال الرسائل -----------------------------
async def get_gifts_catalog():
    """إرجاع كتالوج البوت فقط، دون قراءة هدايا التطبيق."""
    return [{"_display_id": number, "_internal_id": gift["id"], **gift} for number, gift in BOT_GIFTS.items()]


GIFT_ASSET_BASE = "https://files.manuscdn.com/user_upload_by_module/session_file/310519663845522163/"
GIFT_TEMPLATE_FILES = {
    "1": "assets/gift_template_rose.webp",
    "2": "assets/gift_template_heart.webp",
    "3": "assets/gift_template_kiss.webp",
    "4": "assets/gift_template_present.webp",
    "5": "assets/gift_template_present.webp",
    "6": "assets/gift_template_heart.webp",
    "7": "assets/gift_template_present.webp",
    "8": "assets/gift_template_crown.webp",
    "9": "assets/gift_template_crown.webp",
    "10": "assets/gift_template_present.webp",
    "11": "assets/gift_template_present.webp",
    "12": "assets/gift_template_crown.webp",
    "13": "assets/gift_template_crown.webp",
    "14": "assets/gift_template_crown.webp",
}
BASE_DIR = Path(__file__).resolve().parent
GIFT_BUCKET = str(C.get("gift_image_bucket", "bot-gifts")).strip()

# تخزين الوسائط الدائمة: روابط googlevideo مؤقتة لا تُرسل إلى التطبيق.
MUSIC_BUCKET = str(C.get("music_bucket", "bot-music")).strip()
MUSIC_STORAGE = str(C.get("music_storage", "supabase")).strip().lower()
MUSIC_LOCAL_DIR = BASE_DIR / str(C.get("music_local_dir", "generated_music"))
MUSIC_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_PUBLIC_BASE_URL = str(C.get("music_public_base_url", "")).rstrip("/")
PUBLISH_BUCKET = str(C.get("publish_bucket", "bot-publish")).strip()
PUBLISH_STORAGE = str(C.get("publish_storage", "supabase")).strip().lower()
PUBLISH_LOCAL_DIR = BASE_DIR / str(C.get("publish_local_dir", "published_media"))
PUBLISH_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
PUBLISH_PUBLIC_BASE_URL = str(C.get("publish_public_base_url", "")).rstrip("/")
GAME_BUCKET = str(C.get("game_bucket", "bot-games")).strip()
GIFT_RENDER_DIR = BASE_DIR / "generated_gifts"
GIFT_RENDER_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_GIFT_FONT = str(Path(__file__).resolve().parent / "assets" / "Amiri-Bold.ttf")
FONT_PATH = str(C.get("gift_font", DEFAULT_GIFT_FONT))
if not Path(FONT_PATH).exists():
    FONT_PATH = DEFAULT_GIFT_FONT

def shape_text(value):
    text = str(value)
    if arabic_reshaper and get_display and any("\u0600" <= ch <= "\u06ff" for ch in text):
        return get_display(arabic_reshaper.reshape(text))
    return text

def fit_font(text, max_width, start_size=32, min_size=16):
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow غير مثبتة؛ ثبّت Pillow لإنشاء صور الهدايا بأسماء المرسل والمستقبل")
    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(FONT_PATH, size)
        if font.getbbox(text)[2] <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(FONT_PATH, min_size)

def render_gift_image(gift, sender_name, receiver_name):
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow غير مثبتة؛ لن تظهر أسماء FROM وTO داخل الصورة")
    template = Path(__file__).resolve().parent / GIFT_TEMPLATE_FILES.get(str(gift["display_id"]), "assets/gift_template_present.webp")
    if not template.exists():
        return None
    image = Image.open(template).convert("RGBA")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    # خانتا FROM وTO في الجزء السفلي من القالب؛ يمكن تخصيصهما من config.json.
    from_y = int(float(C.get("gift_from_y", height * 0.78)))
    to_y = int(float(C.get("gift_to_y", height * 0.88)))
    box_left = int(float(C.get("gift_box_left", width * 0.12)))
    box_right = int(float(C.get("gift_box_right", width * 0.88)))
    max_width = max(100, box_right - box_left - 24)
    line_color = tuple(C.get("gift_text_color", [255, 255, 255]))
    shadow = (0, 0, 0, 180)
    for label, name, y in (("FROM:", sender_name, from_y), ("TO:", receiver_name, to_y)):
        text = shape_text(f"{label} @{name}")
        font = fit_font(text, max_width)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, y + 2), text, font=font, fill=shadow, stroke_width=2, stroke_fill=shadow)
        draw.text((x, y), text, font=font, fill=line_color, stroke_width=1, stroke_fill=(20, 20, 20, 220))
    path = GIFT_RENDER_DIR / f"gift_{gift['display_id']}_{uuid.uuid4().hex}.png"
    image.save(path, "PNG", optimize=True)
    return path

def publish_gift_image(local_path):
    """حفظ صورة الهدية وإرجاع رابط عام من Railway."""
    base_url = str(
        os.environ.get("PUBLIC_BASE_URL")
        or C.get("gift_public_base_url", "")
        or PUBLIC_BASE_URL
    ).rstrip("/")
    if not base_url:
        raise RuntimeError("لم يتم العثور على رابط عام. اضبط PUBLIC_BASE_URL أو استخدم RAILWAY_PUBLIC_DOMAIN.")

    path = Path(local_path).resolve()
    render_dir = GIFT_RENDER_DIR.resolve()
    if not path.exists() or render_dir not in path.parents:
        raise RuntimeError("مسار صورة الهدية غير صالح")

    # حذف الصور الأقدم من 30 دقيقة لتقليل مساحة التخزين المحلي.
    now = time.time()
    for old_file in render_dir.glob("gift_*.png"):
        try:
            if now - old_file.stat().st_mtime > 1800:
                old_file.unlink()
        except OSError:
            log.warning("تعذر حذف صورة قديمة: %s", old_file)

    return f"{base_url}/gifts/{quote(path.name)}"

GIFT_ASSETS = {
    "1": GIFT_ASSET_BASE + "ALvAmhVifZhRCjXC.png",   # وردة
    "2": GIFT_ASSET_BASE + "zeYNOhSVCkKIauQY.png",   # قلب
    "3": GIFT_ASSET_BASE + "fJSahjkgdxRpJYGo.png",   # قبلة
    "4": GIFT_ASSET_BASE + "OgZcddjIHykSdWuW.png",   # دب/هدية
    "5": GIFT_ASSET_BASE + "OgZcddjIHykSdWuW.png",   # كعكة
    "6": GIFT_ASSET_BASE + "zeYNOhSVCkKIauQY.png",   # ألعاب نارية
    "7": GIFT_ASSET_BASE + "zeYNOhSVCkKIauQY.png",   # برق
    "8": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png",   # تاج
    "9": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png",   # أميرة
    "10": GIFT_ASSET_BASE + "OgZcddjIHykSdWuW.png",  # سيارة
    "11": GIFT_ASSET_BASE + "OgZcddjIHykSdWuW.png",  # طائرة
    "12": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png",  # تنين
    "13": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png",  # سفينة فضاء
    "14": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png"   # قصر
}


def gift_view(gift):
    internal_id = str(gift.get("_internal_id", gift.get("id", "")))
    display_id = str(gift.get("_display_id", gift.get("display_id", "")))
    return {
        "id": internal_id,
        "display_id": display_id,
        "name": gift.get("name") or gift.get("gift_name") or f"هدية رقم {display_id}",
        "emoji": gift.get("emoji") or "🎁",
        "cost_points": gift.get("cost_points", gift.get("cost", 0)),
        "image_url": GIFT_ASSETS.get(display_id) or gift.get("image_url") or gift.get("image") or gift.get("media_url")
    }


async def gift_catalog_message():
    gifts = [gift_view(g) for g in await get_gifts_catalog()]
    if not gifts:
        return "📭 لا توجد هدايا متاحة حالياً."
    lines = ["🎁 كتالوج الهدايا", "━━━━━━━━━━━━━━"]
    for g in gifts:
        lines.append(f"{g['display_id']} {g['emoji']} {g['name']} | 💰 {g['cost_points']} نقطة")
    lines.append("━━━━━━━━━━━━━━")
    lines.append("للإرسال: gv@رقم_الهدية@اسم_الحساب")
    return "\n".join(lines)


async def send_gift_command(rid, sender_uid, sender_name, raw_text):
    parts = [part.strip() for part in raw_text.split("@", 2)]
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return "❌ الصيغة الصحيحة: gv@رقم_الهدية@اسم_الحساب"

    gift_id, receiver_name = parts[1], parts[2].lstrip("@").strip()
    gifts = [gift_view(g) for g in await get_gifts_catalog()]
    gift = next((g for g in gifts if str(g["display_id"]) == gift_id), None)
    if not gift:
        return "❌ رقم الهدية غير موجود. اكتب `gv` لعرض الهدايا المتاحة."

    receiver_rows, _ = await table_select(lambda: sb.table("profiles").select("id,username").eq("username", receiver_name).limit(1).execute())
    if not receiver_rows:
        return f"❌ الحساب @{receiver_name} غير موجود."
    receiver = receiver_rows[0]
    receiver_name = receiver.get("username") or receiver_name

    # نظام الهدايا مستقل عن نظام هدايا التطبيق:
    # الخصم يتم من نفس points.json الذي تستخدمه الألعاب، ولا نستدعي RPC send_gift.
    try:
        cost = int(gift.get("cost_points") or 0)
    except (TypeError, ValueError):
        cost = 0
    if cost < 0:
        return "❌ قيمة الهدية غير صالحة."

    # قفل عملية الخصم حتى لا يستطيع مستخدم إرسال هديتين متزامنتين
    # واستعمال نفس الرصيد قبل حفظ التغيير.
    async with GIFT_POINTS_LOCK:
        points, sender_data = get_user_data(sender_uid, sender_name)
        balance = int(sender_data.get("points", 0) or 0)
        if balance < cost:
            return f"❌ نقاطك غير كافية. رصيدك: {balance} | سعر الهدية: {cost} نقطة."
        sender_data["points"] = balance - cost
        points[sender_uid] = sender_data
        save_points(points)
        remaining_points = sender_data["points"]

    image_url = None
    # لا نرسل القالب الثابت هنا؛ المطلوب صورة تحمل اسمي FROM وTO.
    try:
        rendered = await asyncio.to_thread(render_gift_image, gift, sender_name, receiver_name)
        if not rendered:
            raise RuntimeError("Pillow غير مثبتة أو تعذر إنشاء الصورة الديناميكية")
        image_url = await asyncio.to_thread(publish_gift_image, rendered)
        if not image_url:
            raise RuntimeError("لم يُرجع Storage رابط الصورة")
    except Exception as exc:
        log.exception("dynamic gift image failed: %s", exc)
        reason = str(exc).replace("\n", " ")[:180]
        await room_send(rid, f"⚠️ تم تسجيل الهدية، لكن تعذر إنشاء صورة الأسماء.\n🔎 السبب: {reason}")
    # أرسل الصورة الديناميكية فقط عندما تنجح، حتى لا تظهر خانات FROM وTO فارغة.
    if image_url:
        await room_send_media(rid, f"{gift['emoji']} {gift['name']}", image_url, m_type="image")
    await room_send(rid, f"🎁 أرسل @{sender_name} إلى @{receiver_name} هدية {gift['name']} {gift['emoji']}")
    card = (
        f"{gift['emoji']} 🎁 {gift['name']}\n"
        f"👤 المرسل: @{sender_name}\n"
        f"🎯 المستقبل: @{receiver_name}\n"
        f"💰 القيمة: {gift['cost_points']} نقطة\n"
        f"💳 رصيدك المتبقي: {remaining_points} نقطة"
    )
    await room_send(rid, card)
    # إشعارات خاصة للطرفين: لا تبقى معلومات الهدية داخل الغرفة فقط.
    try:
        await dm_send(receiver_rows[0]["id"], f"🎁 @{sender_name} أرسل لك {gift['emoji']} {gift['name']} بقيمة {gift['cost_points']} نقطة.")
        await dm_send(sender_uid, f"✅ تم إرسال {gift['emoji']} {gift['name']} إلى @{receiver_name} بقيمة {gift['cost_points']} نقطة.")
    except Exception:
        log.exception("gift private notification failed")
    # الهدية تُنفّذ مرة واحدة في الغرفة الأصلية، ثم يُنشر إعلانها وصورتها في كل غرف البوت الأخرى.
    if image_url:
        await broadcast_media(f"🎁 هدية جديدة: {gift['emoji']} {gift['name']} | @{sender_name} ➜ @{receiver_name}",
                              image_url, m_type="image", exclude_rid=rid)
    await broadcast_text(card, exclude_rid=rid)
    return None


async def room_send(rid, text):
    await run(lambda: sb.table("room_messages").insert({
        "room_id": rid, "user_id": BOT_ID, "content": text, "message_type": "text"
    }).execute())

async def room_send_media(rid, text, media_url, m_type="text", duration_ms=None):
    payload = {
        "room_id": rid,
        "user_id": BOT_ID,
        "content": text,
        "message_type": m_type,
        "media_url": media_url,
        "media_duration_ms": duration_ms,
    }
    await run(lambda: sb.table("room_messages").insert(payload).execute())

async def dm_send(uid, text):
    envelope = {
        "v": 1, "id": str(uuid.uuid4()), "content": text, "message_type": "text",
        "media_url": None, "media_duration_ms": None, "reply_to_id": None, "created_at": now_iso()
    }
    await run(lambda: sb.table("dm_relay").insert({
        "sender_id": BOT_ID, "recipient_id": uid, "envelope": envelope
    }).execute())

async def dm_send_media(uid, text, media_url, m_type="image"):
    envelope = {
        "v": 1, "id": str(uuid.uuid4()), "content": text or "", "message_type": m_type,
        "media_url": media_url, "media_duration_ms": None, "reply_to_id": None, "created_at": now_iso()
    }
    await run(lambda: sb.table("dm_relay").insert({
        "sender_id": BOT_ID, "recipient_id": uid, "envelope": envelope
    }).execute())


def _code4():
    return ''.join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))

async def register_social_codes(post_id, owner_id, owner_name, kind, title="", room_id=None):
    # كود واحد عشوائي من 4 خانات لكل منشور/أغنية، وتستخدمه جميع التفاعلات.
    code = _code4()
    codes = {
        "like": code, "love": code, "dislike": code,
        "comment": code, "report": code,
    }
    posts = load_published_posts()
    item = posts.get(str(post_id), {})
    item.update({
        "post_id": str(post_id), "owner_id": str(owner_id), "owner_name": owner_name,
        "type": kind, "title": title, "source_room_id": str(room_id) if room_id else item.get("source_room_id"),
        "reaction_codes": codes,
    })
    posts[str(post_id)] = item
    save_published_posts(posts)
    return codes

async def handle_social_reaction(rid, text, uid, p_name):
    # Supported formats, matching the UI shown in the supplied screenshots:
    # lk@AB12 / lv@AB12 / dl@AB12 / cm@AB12 text / report@AB12 text
    raw = str(text or "").strip()
    m = re.match(r"^(lk|lv|dl|cm|report)@([A-Za-z0-9]{4})(?:\s+(.*))?$", raw, re.I | re.S)
    if not m:
        return None
    action, code, extra = m.group(1).lower(), m.group(2).upper(), (m.group(3) or "").strip()
    posts = load_published_posts()
    found = None
    post = None
    for pid, item in posts.items():
        codes = item.get("reaction_codes") or {}
        for key, value in codes.items():
            if str(value).upper() == code:
                found = key; post = item; post_id = pid; break
        if found: break
    if not post:
        return "❌ كود التفاعل غير صالح أو انتهت صلاحيته."

    owner_id = str(post.get("owner_id") or "")
    if not owner_id:
        return "❌ تعذر تحديد صاحب المنشور."
    if owner_id == str(uid):
        return "⚠️ لا يمكنك تسجيل تفاعل على منشورك بنفس حسابك."

    labels = {"like":"👍 إعجاب", "love":"❤️ أحببتة", "dislike":"👎 عدم إعجاب", "comment":"💬 تعليق", "report":"🚨 إبلاغ"}
    if action in ("cm", "report") and not extra:
        return f"❌ اكتب: {action}@{code} النص"
    action_key = {"lk":"like", "lv":"love", "dl":"dislike", "cm":"comment", "report":"report"}[action]
    event = {
        "id": str(uuid.uuid4()), "post_id": str(post_id), "type": action_key,
        "actor_id": str(uid), "actor_name": p_name, "owner_id": owner_id,
        "text": extra, "room_id": str(rid), "created_at": now_iso(),
    }
    events = load_social_events()
    events[event["id"]] = event
    save_social_events(events)

    title = post.get("title") or ("منشور صورة" if post.get("type") == "image" else "أغنية")
    room_name = rooms.get(rid, "الغرفة")
    if action_key == "comment":
        notification = (f"💬 تعليق جديد على منشورك\n🎵/🖼️ {title}\n"
                        f"👤 من: @{p_name}\n🏠 الغرفة: {room_name}\n📝 {extra}")
    elif action_key == "report":
        notification = (f"🚨 بلاغ على منشورك\n🎵/🖼️ {title}\n"
                        f"👤 من: @{p_name}\n🏠 الغرفة: {room_name}\n📝 السبب: {extra}")
    else:
        notification = (f"{labels[action_key]} على منشورك\n"
                        f"🎵/🖼️ {title}\n👤 من: @{p_name}\n🏠 الغرفة: {room_name}")
    try:
        await dm_send(owner_id, notification)
    except Exception:
        log.exception("social private notification failed")
    return f"✅ تم تسجيل {labels[action_key]} وإرسال الإشعار إلى خاص الناشر."

async def telegram_find_chat_id():
    """Return the configured Telegram backup chat, or fall back to the latest private chat."""
    if not TELEGRAM_BOT_TOKEN:
        return None, "⚠️ أضف TELEGRAM_BOT_TOKEN في Railway Variables."
    if TELEGRAM_BACKUP_CHAT_ID:
        return TELEGRAM_BACKUP_CHAT_ID, None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        async with http.get(url, params={"limit": 100, "allowed_updates": json.dumps(["message"])},
                            timeout=aiohttp.ClientTimeout(total=30)) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400 or not body.get("ok"):
                return None, f"❌ تعذر الوصول إلى Telegram API: HTTP {resp.status}"
            updates = body.get("result") or []
            for update in reversed(updates):
                msg = update.get("message") or {}
                chat = msg.get("chat") or {}
                if chat.get("id") is not None and chat.get("type") in ("private", "group", "supergroup"):
                    return str(chat["id"]), None
            return None, "⚠️ لم أجد محادثة Telegram. ضع TELEGRAM_BACKUP_CHAT_ID في Railway أو أرسل رسالة من المجموعة إلى البوت ثم أعد المحاولة."
    except Exception as exc:
        return None, f"❌ تعذر تحديد محادثة Telegram: {type(exc).__name__}: {exc}"


def _backup_excluded(rel: Path) -> bool:
    """Exclude secrets/cache/runtime junk while keeping the actual bot project and data."""
    parts = set(rel.parts)
    name = rel.name
    # Never put credentials, cookies, local secrets or Python caches into Telegram backups.
    if name in {
        ".env", ".env.local", ".env.production", "youtube_cookies.txt", "spotify_cookies.txt",
        "serviceAccountKey.json", "firebase_credentials.json", "credentials.json",
    }:
        return True
    if any(part in {"__pycache__", ".git", ".venv", "venv", "node_modules"} for part in rel.parts):
        return True
    # Generated temporary backup archives must never be recursively backed up.
    if any(part.startswith("bot_backup_") for part in rel.parts):
        return True
    # Common compiled/temp files are not useful for recovery.
    if name.endswith((".pyc", ".pyo", ".tmp", ".part")):
        return True
    return False


def _collect_backup_files(root: Path):
    """Collect all project files, including JSON state and assets, except secrets/cache."""
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _backup_excluded(rel):
            continue
        try:
            files.append((path, rel.as_posix(), path.stat().st_size))
        except OSError:
            log.warning("backup: cannot stat %s", path)
    files.sort(key=lambda item: item[1])
    return files


def _write_backup_archive(root: Path, archive: Path):
    files = _collect_backup_files(root)
    total_bytes = sum(size for _, _, size in files)
    manifest = {
        "created_at": now_iso(),
        "project": root.name,
        "files": len(files),
        "bytes": total_bytes,
        "excluded": [
            "environment secrets (.env)",
            "API/service-account credential files",
            "YouTube/Spotify cookie files",
            "Python caches (__pycache__, *.pyc)",
            "git/virtualenv/node_modules directories",
            "temporary backup archives",
        ],
    }
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("BACKUP_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path, rel, _ in files:
            try:
                z.write(path, arcname=rel)
            except OSError:
                log.warning("backup: skipped unreadable file %s", path)
    return len(files), total_bytes


async def _telegram_send_document(chat_id: str, path: Path, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    form.add_field("caption", caption)
    with path.open("rb") as fh:
        form.add_field("document", fh, filename=path.name, content_type="application/zip")
        async with http.post(url, data=form, timeout=aiohttp.ClientTimeout(total=180)) as resp:
            body = await resp.text()
            if resp.status >= 400:
                return False, f"HTTP {resp.status}: {body[:500]}"
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}
            if not payload.get("ok", False):
                return False, body[:500]
    return True, "ok"


async def telegram_backup():
    """Create a complete recoverable project backup and send it to the configured Telegram group."""
    if not TELEGRAM_BOT_TOKEN:
        return False, "⚠️ أضف TELEGRAM_BOT_TOKEN في Railway Variables."
    chat_id, chat_error = await telegram_find_chat_id()
    if not chat_id:
        return False, chat_error

    tmp = Path(tempfile.mkdtemp(prefix="bot_backup_"))
    archive = tmp / f"bot_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.zip"
    try:
        count, total_bytes = await asyncio.to_thread(_write_backup_archive, BASE_DIR, archive)
        # Telegram's Bot API has a practical per-file limit. Keep a safety margin and split if needed.
        max_part = 45 * 1024 * 1024
        if archive.stat().st_size <= max_part:
            ok, err = await _telegram_send_document(
                chat_id, archive,
                f"📦 النسخة الاحتياطية الكاملة للبوت\n📁 الملفات: {count}\n💾 البيانات: {total_bytes / 1024 / 1024:.2f} MB\n🔐 تم استبعاد الأسرار وملفات الكوكيز فقط."
            )
            if not ok:
                return False, f"❌ فشل رفع النسخة إلى Telegram: {err}"
            return True, "✅ تم إنشاء وإرسال النسخة الاحتياطية الكاملة إلى مجموعة Telegram."

        # Split the ZIP bytes into Telegram-safe parts. The original ZIP remains recoverable by concatenating parts.
        size = archive.stat().st_size
        part_paths = []
        with archive.open("rb") as src:
            index = 1
            while True:
                chunk = src.read(max_part)
                if not chunk:
                    break
                part = tmp / f"{archive.stem}.part{index:03d}.zip.part"
                part.write_bytes(chunk)
                part_paths.append(part)
                index += 1
        total_parts = len(part_paths)
        for index, part in enumerate(part_paths, 1):
            ok, err = await _telegram_send_document(
                chat_id, part,
                f"📦 النسخة الاحتياطية الكاملة — الجزء {index}/{total_parts}\n"
                f"📁 إجمالي الملفات: {count}\n"
                f"ℹ️ اجمع الأجزاء بالترتيب لإعادة ملف ZIP الأصلي."
            )
            if not ok:
                return False, f"❌ فشل رفع الجزء {index}/{total_parts}: {err}"
        return True, f"✅ أُرسلت النسخة الاحتياطية الكاملة إلى المجموعة على {total_parts} أجزاء."
    except Exception as exc:
        log.exception("telegram backup failed")
        return False, f"❌ تعذر إنشاء النسخة الاحتياطية: {type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

async def share_music_to_user(sender_uid, target_name, current):
    target = str(target_name or "").strip().lstrip("@")
    if not target or not current:
        return "❌ الصيغة: مشاركة@اسم_الشخص"
    rows, _ = await table_select(lambda: sb.table("profiles").select("id,username").ilike("username", target).limit(1).execute())
    if not rows:
        return f"❌ الحساب @{target} غير موجود."
    receiver = rows[0]
    title = current.get("title", "المقطع")
    artist = current.get("artist", "")
    media = current.get("audio_url") or current.get("youtube_url") or current.get("tiktok_url")
    if not media:
        return "❌ لا يوجد ملف صوتي أو رابط صالح للمشاركة."
    await dm_send(receiver["id"], f"🎵 تمت مشاركة أغنية معك من @{await username_of(sender_uid)}\n🎶 {title} — {artist}")
    await dm_send_media(receiver["id"], f"▶️ {title}", media, "voice")
    return f"✅ تمت مشاركة «{title}» مع @{receiver.get('username') or target} في الخاص."

async def user_presence(uid, username):
    rows, _ = await table_select(lambda: sb.table("room_members").select("room_id").eq("user_id", uid).execute())
    room_ids = [r.get("room_id") for r in rows or [] if r.get("room_id")]
    names = []
    if room_ids:
        rooms_rows, _ = await table_select(lambda: sb.table("rooms").select("id,name").in_("id", room_ids).execute())
        names = [r.get("name") or str(r.get("id")) for r in rooms_rows or []]
    if names:
        return f"🟢 @{username} متصل حالياً\n🏠 الغرف: " + ", ".join(names)
    return f"⚪ @{username} غير ظاهر حالياً في أي غرفة متصلة بالبوت."

async def _master_user_ids():
    """Resolve saved master usernames/IDs to profile IDs for private diagnostics."""
    result = set()
    for master in load_masters():
        value = str(master).strip()
        if not value:
            continue
        # Masters may already be stored as UUID/user IDs.
        result.add(value)
        try:
            rows, _ = await table_select(
                lambda v=value: sb.table("profiles").select("id").ilike("username", v).limit(5).execute()
            )
            for row in rows or []:
                if row.get("id"):
                    result.add(str(row["id"]))
        except Exception:
            log.exception("failed to resolve master %s", value)
    # The owner is always a diagnostic recipient.
    try:
        rows, _ = await table_select(
            lambda: sb.table("profiles").select("id").ilike("username", OWNER).limit(5).execute()
        )
        for row in rows or []:
            if row.get("id"):
                result.add(str(row["id"]))
    except Exception:
        pass
    return result

async def report_music_error_to_masters(rid, source, query, error, stage="تشغيل"):
    """Send the real music failure privately to every master/owner.
    Secrets such as cookie values are never included.
    """
    raw = str(error or "خطأ غير معروف").replace("\x1b", "")
    raw = re.sub(r"\[[0-9;]*m", "", raw)
    raw = raw.strip()
    if len(raw) > 1800:
        raw = raw[:1800] + "…"
    room_name = rooms.get(rid, str(rid))
    msg = (
        "🛠️ تشخيص فشل تشغيل الأغنية\n"
        f"📍 المرحلة: {stage}\n"
        f"🎵 المصدر: {source}\n"
        f"🔎 الطلب: {query}\n"
        f"🏠 الغرفة: {room_name}\n"
        f"🕒 الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "━━━━━━━━━━━━━━\n"
        f"❌ الخطأ الحقيقي:\n{raw}"
    )
    for master_id in await _master_user_ids():
        try:
            await dm_send(master_id, msg)
        except Exception:
            log.exception("failed to send music diagnostic to master %s", master_id)

# ----------------------------- الموسيقى -----------------------------
async def _yt_extract(search_query):
    """البحث عن فيديو YouTube بدون محاولة تنزيله.
    نبدأ بـ yt-dlp ببحث flat حتى لا نفشل بسبب حظر استخراج صيغ الفيديو،
    ثم نجرب Piped كاحتياط. نعيد سبب الفشل الحقيقي للتشخيص.
    """
    q = str(search_query).strip()
    if q.lower().startswith("ytsearch1:"):
        q = q.split(":", 1)[1].strip()
    errors = []

    if yt_dlp is not None:
        def extract():
            options = yt_base_options("YouTube")
            options.update({
                "skip_download": True,
                "extract_flat": True,
                "default_search": "ytsearch1",
            })
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearch1:{q}", download=False)
                entry = (info.get("entries") or [None])[0] if info else info
                if not entry:
                    return None
                vid = entry.get("id")
                url = entry.get("webpage_url") or entry.get("original_url")
                if not url and vid:
                    url = f"https://www.youtube.com/watch?v={vid}"
                return {
                    "id": vid,
                    "title": entry.get("title") or "المقطع",
                    "artist": entry.get("uploader") or entry.get("channel") or "YouTube",
                    "youtube_url": url,
                    "thumbnail": entry.get("thumbnail"),
                    "duration": entry.get("duration") or 0,
                }
        try:
            track = await asyncio.to_thread(extract)
            if track and track.get("youtube_url"):
                return track, None
            errors.append("yt-dlp: اتصلت بيوتيوب لكن البحث لم يُرجع نتائج.")
        except Exception as e:
            errors.append(f"yt-dlp: {type(e).__name__}: {e}")
            log.warning("yt-dlp YouTube search failed: %s", e)
    else:
        errors.append("yt-dlp غير مثبت داخل الحاوية.")

    for api in PIPED_APIS:
        try:
            async with http.get(
                f"{api}/search", params={"q": q, "filter": "videos"},
                timeout=aiohttp.ClientTimeout(total=12),
                headers={"User-Agent": "Mozilla/5.0"}
            ) as resp:
                if resp.status != 200:
                    errors.append(f"Piped {api}: HTTP {resp.status}")
                    continue
                data = await resp.json(content_type=None)
            items = data.get("items") or []
            item = next((x for x in items if x.get("url") or x.get("id")), None)
            if item:
                vid = item.get("id") or str(item.get("url", "")).split("v=")[-1]
                return {
                    "id": vid,
                    "title": item.get("title") or "المقطع",
                    "artist": item.get("uploaderName") or item.get("uploader") or "YouTube",
                    "youtube_url": f"https://www.youtube.com/watch?v={vid}",
                    "thumbnail": item.get("thumbnail"),
                    "duration": item.get("duration") or 0,
                    "piped_api": api,
                }, None
        except Exception as e:
            errors.append(f"Piped {api}: {type(e).__name__}: {e}")
            log.warning("Piped search failed %s: %s", api, e)

    return None, " | ".join(errors[-5:]) if errors else "لم توجد نتائج من YouTube أو المصادر الاحتياطية."

async def _yt_download_audio(page_url, source_label, piped_api=None, video_id=None):
    """تنزيل الصوت مع تشخيص منفصل لكل محاولة."""
    temp_dir = Path(tempfile.mkdtemp(prefix="bot_audio_"))
    errors = []
    try:
        # إذا كانت Cookies موجودة، نستخدم yt-dlp أولاً حتى يستفيد من جلسة YouTube.
        prefer_ytdlp = source_label == "YouTube" and has_youtube_cookies()

        def download_with_format(fmt, suffix="audio", use_cookies=True, clients=None, cookie_file=None):
            options = yt_base_options(source_label, cookie_file=cookie_file)
            if source_label == "YouTube":
                # بعض جلسات YouTube في أغسطس 2026 تعطي "The page needs to be reloaded"
                # عند تمرير Cookies مع tv/web_safari. نجرّب أولاً بدون cookies، ثم
                # جلسة cookies باستخدام default + web_embedded.
                if not use_cookies:
                    options.pop("cookiefile", None)
                if clients:
                    options["extractor_args"] = {"youtube": {"player_client": clients}}
            options.update({
                "format": fmt,
                "outtmpl": str(temp_dir / f"{suffix}.%(ext)s"),
                "noplaylist": True,
                "sleep_interval": 0.5,
                "max_sleep_interval": 1.5,
            })
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([page_url])

        async def try_ytdlp():
            if yt_dlp is None:
                errors.append("yt-dlp غير مثبت داخل Railway.")
                return None
            formats = [
                "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                "bestaudio/best",
                "best[ext=mp4]/best",
            ]
            attempts = []
            if source_label == "YouTube":
                # محاولة 1: بدون cookies؛ هذا يتجنب مشكلة YouTube الحالية مع بعض الجلسات المسجلة.
                for idx, fmt in enumerate(formats):
                    attempts.append((idx, fmt, False, ["default", "web_embedded"], None, -1))
                # Try each configured YouTube session separately.
                cookie_files = get_youtube_cookie_files()
                base = len(attempts)
                for account_idx, cookie_file in enumerate(cookie_files):
                    for j, fmt in enumerate(formats):
                        attempts.append((
                            base + account_idx * len(formats) + j,
                            fmt, True, ["default", "web_embedded"], cookie_file, account_idx
                        ))
            else:
                attempts = [(idx, fmt, True, None, None, 0) for idx, fmt in enumerate(formats)]
            for idx, fmt, use_cookies, clients, cookie_file, account_idx in attempts:
                try:
                    for p in temp_dir.glob("*"):
                        if p.is_file() and p.suffix not in (".part", ".ytdl"):
                            try: p.unlink()
                            except OSError: pass
                    await asyncio.to_thread(
                        download_with_format, fmt, f"audio_{idx}", use_cookies, clients, cookie_file
                    )
                    files = [p for p in temp_dir.iterdir() if p.is_file() and p.suffix not in (".part", ".ytdl") and p.stat().st_size > 4096]
                    if files:
                        return max(files, key=lambda p: p.stat().st_size)
                except Exception as e:
                    cookie_tag = f"account-{account_idx + 1}" if use_cookies else "بدون-cookies"
                    errors.append(f"yt-dlp [{fmt}][{cookie_tag}]: {type(e).__name__}: {e}")
                    log.warning("yt-dlp audio failed (%s,%s): %s", fmt, cookie_tag, e)
            return None

        async def try_piped():
            if not (piped_api and video_id):
                return None
            try:
                async with http.get(f"{piped_api}/streams/{video_id}", timeout=aiohttp.ClientTimeout(total=25), headers={"User-Agent":"Mozilla/5.0"}) as resp:
                    if resp.status != 200:
                        errors.append(f"Piped {piped_api}: HTTP {resp.status}")
                        return None
                    info = await resp.json(content_type=None)
                streams = sorted(info.get("audioStreams") or [], key=lambda x: float(x.get("bitrate") or 0), reverse=True)
                for stream in streams:
                    url = stream.get("url")
                    if not url: continue
                    try:
                        ext = ".m4a" if "mp4" in str(stream.get("mimeType", "")) else ".webm"
                        out = temp_dir / f"audio{ext}"
                        async with http.get(url, timeout=aiohttp.ClientTimeout(total=120)) as ar:
                            if ar.status != 200:
                                continue
                            with out.open("wb") as f:
                                async for chunk in ar.content.iter_chunked(1024 * 256): f.write(chunk)
                        if out.is_file() and out.stat().st_size > 4096:
                            return out
                    except Exception as e:
                        errors.append(f"Piped audio stream: {type(e).__name__}: {e}")
            except Exception as e:
                errors.append(f"Piped {piped_api}: {type(e).__name__}: {e}")
            return None

        if prefer_ytdlp:
            out = await try_ytdlp()
            if out: return out, None
            out = await try_piped()
            if out: return out, None
        else:
            out = await try_piped()
            if out: return out, None
            out = await try_ytdlp()
            if out: return out, None

        return None, "تعذر تنزيل الصوت. " + " | ".join(errors[-6:])
    except Exception as e:
        log.exception("%s audio download failed", source_label)
        return None, f"{type(e).__name__}: {e}"

async def _upload_bytes_storage(local_path, bucket, prefix, content_type):
    """رفع ملف إلى Supabase Storage وإرجاع رابط ثابت/عام."""
    if not bucket:
        raise RuntimeError("اسم Storage bucket غير مضبوط")

    filename = f"{prefix}/{uuid.uuid4().hex}{local_path.suffix.lower() or '.bin'}"
    data = local_path.read_bytes()

    def upload():
        storage = sb.storage.from_(bucket)
        # upsert يمنع فشل الرفع بسبب إعادة استخدام اسم الملف.
        storage.upload(
            filename,
            data,
            {"content-type": content_type, "upsert": "true"},
        )
        return storage.get_public_url(filename)

    return await asyncio.to_thread(upload)


async def prepare_game_assets():
    """Publish local game images to Supabase Storage so every client can see them.
    Falls back to game_public_base_url when configured."""
    if not GAME_BUCKET:
        return
    for key, url in list(GAME_IMAGES.items()):
        if not isinstance(url, str) or not url.startswith("assets/"):
            continue
        local = BASE_DIR / url
        if not local.is_file():
            continue
        try:
            content_type = "image/png" if local.suffix.lower() == ".png" else "image/jpeg"
            public_url = await _upload_bytes_storage(local, GAME_BUCKET, "games", content_type)
            GAME_IMAGES[key] = public_url
        except Exception as e:
            log.warning("تعذر رفع صورة اللعبة %s: %s", key, e)
            if GAME_BASE_URL or PUBLIC_BASE_URL:
                GAME_IMAGES[key] = f"{GAME_BASE_URL or PUBLIC_BASE_URL}/assets/{quote(local.name)}"
        if (not str(GAME_IMAGES.get(key, "")).startswith(("http://", "https://"))
                and (GAME_BASE_URL or PUBLIC_BASE_URL)):
            GAME_IMAGES[key] = f"{GAME_BASE_URL or PUBLIC_BASE_URL}/assets/{quote(local.name)}"

async def _store_media(local_path, kind="music", content_type=None):
    """تجهيز رابط عام ثابت للوسائط.

    في Railway نستخدم خادم HTTP صغير داخل نفس الخدمة، لأن Giant Chat يحتاج
    رابطاً عاماً يمكن للمتصفح/التطبيق الوصول إليه مباشرة. Supabase يبقى
    خياراً احتياطياً إذا لم يوجد رابط عام.
    """
    if content_type is None:
        ext = local_path.suffix.lower()
        content_type = {
            ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
            ".webm": "audio/webm", ".ogg": "audio/ogg",
            ".wav": "audio/wav",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
        }.get(ext, "application/octet-stream")

    if kind == "music":
        storage_mode = MUSIC_STORAGE
        bucket = MUSIC_BUCKET
        local_dir = MUSIC_LOCAL_DIR
        base_url = PUBLIC_BASE_URL or MUSIC_PUBLIC_BASE_URL
    elif kind == "game":
        storage_mode = str(C.get("game_storage", "supabase")).strip().lower()
        bucket = GAME_BUCKET
        local_dir = BASE_DIR / str(C.get("game_local_dir", "generated_games"))
        base_url = PUBLIC_BASE_URL or GAME_BASE_URL
    else:
        storage_mode = PUBLISH_STORAGE
        bucket = PUBLISH_BUCKET
        local_dir = PUBLISH_LOCAL_DIR
        base_url = PUBLIC_BASE_URL or PUBLISH_PUBLIC_BASE_URL

    # Railway/local public server: لا يحتاج bucket عام ولا سياسة Storage.
    if kind == "music" and base_url and storage_mode in ("railway", "local", "auto", "supabase"):
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex}{local_path.suffix.lower()}"
            target = local_dir / filename
            shutil.copy2(local_path, target)
            return f"{base_url}{MEDIA_PATH}/{quote(filename)}"
        except Exception as e:
            log.warning("public local media failed: %s", e)

    if storage_mode in ("supabase", "auto"):
        try:
            return await _upload_bytes_storage(local_path, bucket, kind, content_type)
        except Exception as e:
            log.warning("Supabase Storage upload failed (%s): %s", kind, e)
            if storage_mode == "supabase" and not base_url:
                raise

    if base_url:
        target = local_dir / f"{uuid.uuid4().hex}{local_path.suffix.lower()}"
        shutil.copy2(local_path, target)
        route = {"game": "/games", "publish": "/published", "music": MEDIA_PATH}.get(kind, "/media")
        return f"{base_url}{route}/{quote(target.name)}"

    raise RuntimeError(
        f"تعذر نشر ملف {kind}: لم يتم تحديد PUBLIC_BASE_URL/Railway domain "
        f"ولم ينجح Supabase Storage."
    )


async def handle_social_event(event):
    """إشعارات اجتماعية خاصة متوافقة مع أحداث Giant Chat/ZBot."""
    if not isinstance(event, dict):
        return {"handled": False}
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    etype = str(event.get("event") or event.get("type") or data.get("type") or "").lower().strip()
    event_id = str(event.get("id") or data.get("id") or uuid.uuid4())
    if event_id in SOCIAL_SEEN:
        return {"handled": True, "duplicate": True}
    SOCIAL_SEEN.add(event_id)
    if len(SOCIAL_SEEN) > 5000:
        SOCIAL_SEEN.clear()
        SOCIAL_SEEN.add(event_id)

    actor_id = data.get("actor_id") or data.get("sender_id") or data.get("user_id")
    actor_name = data.get("actor_name") or data.get("sender_name") or data.get("username") or "مستخدم"
    owner_id = data.get("owner_id") or data.get("post_owner_id") or data.get("receiver_id") or data.get("to_user_id")
    owner_name = data.get("owner_name") or data.get("post_owner_name") or data.get("receiver_name")
    post_id = str(data.get("post_id") or data.get("publication_id") or "")

    if etype in ("member_joined", "member_join", "join"):
        rid = data.get("room_id")
        uid = data.get("user_id") or data.get("member_id")
        name = data.get("username") or data.get("member_name") or actor_name
        if rid and uid and str(uid) != str(BOT_ID):
            welcome = load_welcome().get(str(rid), {})
            if welcome.get("enabled", True):
                msgs = welcome.get("messages") or ["🤖 بوت العملاق يرحب بك يا @{name} 🌟"]
                msg = random.choice(msgs).replace("{name}", name).replace("@name", "@" + name)
                await room_send(rid, msg)
            return {"handled": True, "kind": "member_joined"}

    # المنشورات: أعجب/عدم إعجاب/أحببته/تعليق.
    if etype in ("post_like", "like", "reaction", "post_reaction", "post_dislike", "post_comment", "comment"):
        reaction = str(data.get("reaction") or data.get("action") or etype).lower()
        if not owner_id and post_id:
            owner_id = load_published_posts().get(post_id, {}).get("owner_id")
        if not owner_id or str(owner_id) == str(actor_id):
            return {"handled": False, "reason": "owner_not_found"}
        if "comment" in reaction or etype in ("post_comment", "comment"):
            body = str(data.get("comment") or data.get("content") or data.get("text") or "").strip()
            notice = f"💬 @{actor_name} علّق على منشورك" + (f": {body}" if body else ".")
        elif "dislike" in reaction or "عدم" in reaction:
            notice = f"👎 @{actor_name} لم يعجبه منشورك."
        elif "love" in reaction or "احب" in reaction:
            notice = f"💖 @{actor_name} أحب منشورك."
        else:
            notice = f"❤️ @{actor_name} أعجب بمنشورك."
        await dm_send(owner_id, notice)
        return {"handled": True, "kind": "post_interaction", "owner_id": str(owner_id)}

    if etype in ("gift_sent", "gift", "gift_received", "send_gift"):
        receiver = owner_id or data.get("gift_receiver_id")
        receiver_name = owner_name or data.get("gift_receiver_name") or "المستخدم"
        gift_name = data.get("gift_name") or data.get("name") or "هدية"
        emoji = data.get("gift_emoji") or data.get("emoji") or "🎁"
        if receiver and str(receiver) != str(actor_id):
            await dm_send(receiver, f"{emoji} 🎁 @{actor_name} أرسل لك {gift_name}.")
        if actor_id and receiver_name:
            await dm_send(actor_id, f"✅ تم إرسال {emoji} {gift_name} إلى @{receiver_name}.")
        return {"handled": True, "kind": "gift"}

    return {"handled": False, "kind": etype}


async def start_media_server():
    """تشغيل خادم ملفات الصوت داخل Railway على PORT."""
    global media_runner, media_site

    app = web.Application()
    media_dir = MUSIC_LOCAL_DIR
    media_dir.mkdir(parents=True, exist_ok=True)

    async def media_handler(request):
        name = os.path.basename(request.match_info.get("name", ""))
        if not name or name != request.match_info.get("name", ""):
            raise web.HTTPBadRequest(text="invalid media name")
        path = media_dir / name
        if not path.is_file():
            raise web.HTTPNotFound()
        ctype = {
            ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
            ".webm": "audio/webm", ".ogg": "audio/ogg", ".wav": "audio/wav",
        }.get(path.suffix.lower(), "application/octet-stream")
        return web.FileResponse(path, headers={
            "Content-Type": ctype,
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        })

    app.router.add_get(f"{MEDIA_PATH}/{{name}}", media_handler)

    async def public_asset_handler(request):
        rel = request.match_info.get("path", "")
        safe = Path(rel)
        if ".." in safe.parts:
            raise web.HTTPBadRequest()
        file_path = BASE_DIR / "assets" / safe
        if not file_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path)

    async def gift_handler(request):
        name = os.path.basename(request.match_info.get("name", ""))
        file_path = GIFT_RENDER_DIR / name
        if not file_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path)

    async def health_handler(request):
        return web.json_response({"ok": True, "media": MEDIA_PATH})

    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/assets/{path:.*}", public_asset_handler)
    async def game_handler(request):
        name = os.path.basename(request.match_info.get("name", ""))
        file_path = BASE_DIR / str(C.get("game_local_dir", "generated_games")) / name
        if not file_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path, headers={"Cache-Control": "public, max-age=86400"})

    async def published_handler(request):
        name = os.path.basename(request.match_info.get("name", ""))
        file_path = PUBLISH_LOCAL_DIR / name
        if not file_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path, headers={"Cache-Control": "public, max-age=86400"})

    async def social_webhook(request):
        if SOCIAL_WEBHOOK_TOKEN and request.headers.get("X-Social-Token", "") != SOCIAL_WEBHOOK_TOKEN:
            raise web.HTTPUnauthorized()
        try:
            payload = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid json")
        result = await handle_social_event(payload)
        return web.json_response({"ok": True, **result})

    app.router.add_get("/gifts/{name}", gift_handler)
    app.router.add_get("/games/{name}", game_handler)
    app.router.add_get("/published/{name}", published_handler)
    app.router.add_post("/webhook", social_webhook)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    media_runner = runner
    media_site = web.TCPSite(runner, "0.0.0.0", MEDIA_SERVER_PORT)
    await media_site.start()
    log.info("خادم ملفات الموسيقى يعمل على 0.0.0.0:%s | PUBLIC_BASE_URL=%s",
             MEDIA_SERVER_PORT, PUBLIC_BASE_URL or "(غير مضبوط)")


async def stop_media_server():
    global media_runner, media_site
    try:
        if media_site:
            await media_site.stop()
        if media_runner:
            await media_runner.cleanup()
    finally:
        media_site = None
        media_runner = None


async def _convert_audio_to_mp3(local_path):
    """تحويل الصوت إلى MP3، وهو الأكثر توافقاً مع مشغل الصوت في تطبيقات الدردشة."""
    if local_path is None or local_path.suffix.lower() == ".mp3":
        return local_path, None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("ffmpeg غير مثبت؛ سيتم استخدام الملف الأصلي %s", local_path.suffix)
        return local_path, None

    out = local_path.with_suffix(".mp3")

    def convert():
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(local_path), "-vn",
            "-ac", "2", "-ar", "44100",
            "-codec:a", "libmp3lame", "-b:a", "128k",
            str(out),
        ]
        subprocess.run(cmd, check=True, timeout=180)

    try:
        await asyncio.to_thread(convert)
        if out.is_file() and out.stat().st_size > 4096:
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass
            return out, None
        return local_path, "فشل تحويل الصوت إلى MP3"
    except Exception as e:
        log.warning("ffmpeg conversion failed: %s", e)
        return local_path, None


async def _audio_duration_ms(local_path):
    """استخراج مدة الملف فعلياً، حتى لا نرسل رسالة صوت بمدة صفر."""
    if not local_path or not Path(local_path).is_file():
        return 0
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(local_path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        value = float((proc.stdout or "").strip() or 0)
        return max(0, int(value * 1000))
    except Exception:
        return 0

async def _validate_public_media_url(url, expected_kind="audio"):
    """تأكد أن رابط Railway يعيد ملفاً فعلياً قبل إرساله إلى Giant Chat."""
    if not url or not str(url).startswith(("http://", "https://")):
        return False, "رابط الوسائط غير صالح"
    try:
        headers = {"Range": "bytes=0-4095", "User-Agent": "GiantChat-Bot/1.0"}
        async with http.get(str(url), headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status not in (200, 206):
                return False, f"رابط الوسائط أعاد HTTP {resp.status}"
            ctype = str(resp.headers.get("Content-Type") or "").lower()
            data = await resp.content.read(4096)
            if len(data) < 256:
                return False, "رابط الوسائط أعاد ملفاً فارغاً أو ناقصاً"
            if expected_kind == "audio" and not (ctype.startswith("audio/") or "octet-stream" in ctype):
                return False, f"نوع الملف غير صوتي: {ctype or 'unknown'}"
            return True, None
    except Exception as exc:
        return False, f"تعذر فحص رابط الوسائط: {type(exc).__name__}: {exc}"

async def _prepare_music_track(track, source_label):
    if not track:
        return None, "لم أجد المقطع المطلوب"
    if MUSIC_MAX_DURATION and float(track.get("duration") or 0) > MUSIC_MAX_DURATION:
        return None, f"مدة الأغنية طويلة جداً (الحد {MUSIC_MAX_DURATION // 60} دقيقة)."
    page_url = track.get("youtube_url")
    if not page_url:
        return None, "تعذر الحصول على رابط الصفحة الأصلية للمقطع"

    local_path, err = await _yt_download_audio(page_url, source_label, track.get("piped_api"), track.get("id"))
    if err:
        return None, err
    try:
        if not local_path or not Path(local_path).is_file() or Path(local_path).stat().st_size <= 4096:
            return None, "تم تنزيل الصوت لكن الملف فارغ أو تالف، لذلك لم يتم إرسال بصمة صوت."
        local_path, convert_err = await _convert_audio_to_mp3(local_path)
        if convert_err:
            log.warning(convert_err)
        if not local_path or not Path(local_path).is_file() or Path(local_path).stat().st_size <= 4096:
            return None, "فشل تجهيز ملف الصوت بعد التحويل؛ تم منع إرسال صوت فارغ."
        duration_ms = await _audio_duration_ms(local_path)
        if duration_ms <= 0:
            duration_ms = int(float(track.get("duration") or 0) * 1000)
        if duration_ms <= 0:
            return None, "تعذر قراءة مدة الصوت؛ تم منع إرسال رسالة صوت غير صالحة."
        audio_url = await _store_media(local_path, "music")
        valid, url_err = await _validate_public_media_url(audio_url, "audio")
        if not valid:
            return None, f"تم تجهيز الصوت لكن رابط التشغيل غير صالح: {url_err}"
        track["audio_url"] = audio_url
        track["duration_ms"] = duration_ms
        track["duration"] = duration_ms / 1000.0
        # MP3/WebM/M4A يحدد نوع الملف الذي أرسلناه، وvoice هو نوع رسالة Giant Chat.
        track["media_format"] = local_path.suffix.lower().lstrip(".")
        return track, None
    finally:
        try:
            shutil.rmtree(local_path.parent, ignore_errors=True)
        except Exception:
            pass


async def search_spotify(query):
    """Resolve a Spotify track to metadata, then use a public YouTube copy for
    the actual audio bytes. Spotify itself does not expose downloadable audio."""
    q = str(query or "").strip()
    if not q:
        return None, "اكتب اسم الأغنية بعد .تشغيل"

    spotify_url = None
    if re.match(r"https?://open\.spotify\.com/(?:intl-[^/]+/)?track/[A-Za-z0-9]+", q):
        spotify_url = q
    else:
        # Discover a public Spotify track URL through search engines.
        headers = {"User-Agent": "Mozilla/5.0"}
        for engine, params in (
            ("https://www.google.com/search", {"q": f'site:open.spotify.com/track "{q}"'}),
            ("https://www.bing.com/search", {"q": f'site:open.spotify.com/track "{q}"'}),
        ):
            try:
                async with http.get(engine, params=params, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text(errors="ignore")
                urls = re.findall(r'https?://open\.spotify\.com/(?:intl-[^/]+/)?track/[A-Za-z0-9]+', html)
                if urls:
                    spotify_url = urls[0].split("&")[0]
                    break
            except Exception as e:
                log.warning("Spotify discovery failed: %s", e)

    title = q
    artist = "Spotify"
    if spotify_url:
        try:
            async with http.get(
                "https://open.spotify.com/oembed",
                params={"url": spotify_url},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    title = data.get("title") or title
                    artist = data.get("author_name") or artist
        except Exception as e:
            log.warning("Spotify oEmbed failed: %s", e)

    # Spotify supplies metadata/link; audio is obtained from a playable public copy.
    track = None
    spotify_queries = [f"{title} {artist}", f"{title} {artist} audio", f"{title} {artist} official"]
    for sq in spotify_queries:
        try:
            track, _search_err = await _yt_extract(sq)
            if track:
                break
        except Exception as e:
            log.warning("Spotify->YouTube search failed (%s): %s", sq, e)
    if not track:
        # Keep the Spotify URL so the room can still open it without downloading.
        if spotify_url:
            return {
                "title": title,
                "artist": artist,
                "spotify_url": spotify_url,
                "source": "Spotify",
                "youtube_url": None,
                "audio_url": None,
            }, None
        return None, "تعذر العثور على نسخة صوتية للمقطع من Spotify، ولم يوجد رابط Spotify مباشر."
    track["spotify_url"] = spotify_url
    track["spotify_title"] = title
    track["spotify_artist"] = artist
    track["source"] = "Spotify"
    return track, None


async def _extract_direct_media_url(url, source_label):
    """Extract metadata from a direct YouTube/TikTok media page URL."""
    u = str(url or "").strip()
    if not re.match(r"^https?://", u, re.I):
        return None, "الرابط غير صالح"
    if yt_dlp is None:
        return None, "مكتبة yt-dlp غير مثبتة."

    def extract():
        options = yt_base_options(source_label)
        options.update({"skip_download": True, "format": "bestaudio/best"})
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(u, download=False)
        if not info:
            return None
        return {
            "id": info.get("id"),
            "title": info.get("title") or "المقطع",
            "artist": info.get("uploader") or info.get("creator") or source_label,
            "youtube_url": info.get("webpage_url") if source_label == "YouTube" else None,
            "tiktok_url": info.get("webpage_url") if source_label == "TikTok" else None,
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration") or 0,
            "source": source_label,
        }
    try:
        return await asyncio.to_thread(extract), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

async def search_track(query):
    """YouTube search with several query variants. Returns the direct YouTube URL
    even when audio download later fails, so the client can open/play it."""
    q = str(query or "").strip()
    if not q:
        return None, "اكتب اسم الأغنية بعد تشغيل"
    # تشغيل رابط YouTube مباشرة: لا نبحث عنه كنص.
    if re.match(r"^https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", q, re.I):
        return await _extract_direct_media_url(q, "YouTube")
    # دعم وضع الرابط مع الأمر تشغيل أيضاً إذا كان رابط TikTok.
    if re.match(r"^https?://(?:(?:www\.)?tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/", q, re.I):
        return await _extract_direct_media_url(q, "TikTok")
    variants = [
        q,
        f"{q} official",
        f"{q} audio",
        f"{q} lyrics",
    ]
    errors = []
    for variant in variants:
        try:
            track, search_err = await _yt_extract(variant)
            if track and track.get("youtube_url"):
                track["source"] = "YouTube"
                track["search_query"] = variant
                return track, None
            if search_err:
                errors.append(f"{variant}: {search_err}")
        except Exception as e:
            errors.append(f"{variant}: {type(e).__name__}: {e}")
            log.warning("youtube search error (%s): %s", variant, e)
    detail = " | ".join(errors[-4:]) if errors else "لا توجد نتائج من مصادر البحث"
    return None, f"لم أجد الأغنية المطلوبة على يوتيوب. تفاصيل الاتصال/البحث: {detail}"


async def search_tiktok(query):
    """Find a TikTok video. Direct TikTok URLs are preferred. For text search,
    use search engines to discover a public TikTok URL, then yt-dlp extracts audio."""
    if yt_dlp is None:
        return None, "مكتبة yt-dlp غير مثبتة."
    try:
        direct_url = query.strip()
        urls = []
        if direct_url.startswith(("https://www.tiktok.com/", "https://tiktok.com/", "https://vm.tiktok.com/", "https://vt.tiktok.com/")):
            urls = [direct_url]
        if not urls:
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
            # Try TikTok itself first.
            for search_url in (
                "https://www.tiktok.com/search",
                "https://www.google.com/search",
                "https://www.bing.com/search",
            ):
                try:
                    params = {"q": query if "tiktok.com" in search_url else f'site:tiktok.com "{query}"'}
                    async with http.get(search_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text(errors="ignore")
                    pattern = r'https?://(?:www\.)?tiktok\.com/@[^"\\ <]+/video/\d+'
                    urls = re.findall(pattern, html)
                    if urls:
                        break
                except Exception as e:
                    log.warning("TikTok search source failed %s: %s", search_url, e)
        if not urls:
            return None, "لم أجد فيديو TikTok. إذا كان لديك رابط TikTok أرسله بعد «تيك»."

        def extract():
            options = yt_base_options("TikTok")
            options.update({"skip_download": True, "format": "bestaudio/best"})
            info = None
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(urls[0], download=False)
            return {
                "id": info.get("id"), "title": info.get("title") or query,
                "artist": info.get("uploader") or info.get("creator") or "TikTok",
                "youtube_url": info.get("webpage_url") or urls[0],
                "tiktok_url": info.get("webpage_url") or urls[0],
                "thumbnail": info.get("thumbnail"), "duration": info.get("duration") or 0,
            }
        track = await asyncio.to_thread(extract)
        return (track, None) if track else (None, "تعذر استخراج فيديو TikTok")
    except Exception as e:
        log.warning("tiktok search error: %s", e)
        return None, "تعذر الوصول إلى TikTok من الخادم. إذا كان الخادم PythonAnywhere المجاني فلن تعمل هذه الميزة بسبب قيود الإنترنت الخارجية."


async def render_music_card(track, requester_name, source_room):
    """بطاقة أغنية بنفس فكرة بطاقات بوت سهم: صورة كبيرة + معلومات الطلب والتفاعل."""
    if not PIL_AVAILABLE:
        return None
    outdir = BASE_DIR / "generated_music_cards"
    outdir.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (900, 980), (245, 247, 250))
    d = ImageDraw.Draw(canvas)
    thumb = None
    thumb_url = track.get("thumbnail")
    if thumb_url:
        try:
            async with http.get(thumb_url, timeout=aiohttp.ClientTimeout(total=12), headers={"User-Agent":"Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    import io
                    thumb = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            thumb = None
    if thumb is None:
        thumb = Image.new("RGB", (900, 560), (28, 42, 65))
        td = ImageDraw.Draw(thumb)
        td.text((450, 280), "🎵 GENAT CHAT", fill=(255,255,255), anchor="mm")
    thumb.thumbnail((860, 570), Image.LANCZOS)
    canvas.paste(thumb, ((900-thumb.width)//2, 20))
    font_path = BASE_DIR / "assets" / "Amiri-Bold.ttf"
    try:
        f_title=ImageFont.truetype(str(font_path), 42); f_line=ImageFont.truetype(str(font_path), 31); f_small=ImageFont.truetype(str(font_path), 26)
    except Exception:
        f_title=f_line=f_small=ImageFont.load_default()
    d.rounded_rectangle((55, 620, 845, 940), radius=28, fill=(255,255,255), outline=(215,218,223), width=3)
    _draw_game_text(d, (450, 665), "🎵 تشغيل | أغنية", f_title, fill=(35,35,45))
    _draw_game_text(d, (450, 720), str(track.get("title") or "الأغنية"), f_line, fill=(45,45,45))
    _draw_game_text(d, (450, 770), f"👤 الطلب بواسطة: @{requester_name}", f_small, fill=(65,65,65))
    _draw_game_text(d, (450, 815), f"🏠 الغرفة: {source_room}", f_small, fill=(65,65,65))
    _draw_game_text(d, (450, 870), "❤️ إعجاب   👎 عدم إعجاب   💖 أحببته   💬 تعليق", f_small, fill=(65,65,65))
    _draw_game_text(d, (450, 915), "▶️ اضغط تشغيل من مشغل الصوت", f_small, fill=(65,65,65))
    path=outdir/f"music_{uuid.uuid4().hex}.jpg"
    canvas.save(path, quality=92, optimize=True)
    return path

async def play_track(rid, track, source_label, requester_id, requester_name):
    if not track:
        return False, "لم أجد المقطع المطلوب"
    source_room = rooms.get(rid, "الغرفة")
    track, err = await _prepare_music_track(track, source_label)
    if err:
        return False, err
    track.update({"requester_id": str(requester_id), "requester_name": requester_name, "source_room": source_room})
    music_state[rid] = track
    title = track.get("title", "المقطع")
    artist = track.get("artist", source_label)
    media_url = track.get("audio_url")
    if not media_url:
        direct_url = track.get("youtube_url") or track.get("spotify_url") or track.get("tiktok_url")
        if direct_url:
            await room_send(rid, f"🎵 @{requester_name} — جاري تشغيل: {title}\n🏠 الغرفة: {source_room}\n▶️ {direct_url}")
            return True, None
        return False, "تم الوصول للنتيجة لكن لم يتم إنشاء ملف صوتي ولا رابط تشغيل مباشر."

    # تسجيل الأغنية كمنشور بدون إنشاء صورة للأغنية، حتى تبقى التفاعلات
    # (إعجاب/حب/تعليق) مرتبطة بصاحب الطلب عبر post_id.
    post_id = str(uuid.uuid4())
    posts = load_published_posts()
    posts[post_id] = {
        "post_id": post_id, "owner_id": str(requester_id), "owner_name": requester_name,
        "source_room_id": str(rid), "type": "music", "title": title,
        "media_url": media_url, "audio_url": media_url, "created_at": now_iso()
    }
    save_published_posts(posts)
    codes = await register_social_codes(post_id, requester_id, requester_name, "music", title, rid)

    # نفس شكل بطاقة SONG BROADCAST في الصورة المرسلة، مع كود واحد متغير لكل أغنية.
    caption = (
        "🎵 SONG BROADCAST\n"
        ".sa Music name\n"
        f"🎤 {requester_name}\n"
        f"{title}\n\n"
        f"⏱️ Source: {source_label}\n"
        f"🆔 {codes['like']}\n"
        f"💬 Room: {source_room}\n"
        "━━━━━━━━━━━━━\n"
        f"👍 lk@{codes['like']}\n"
        f"❤️ lv@{codes['like']}\n"
        f"👎 dl@{codes['like']}\n"
        f"💬 cm@{codes['like']} msg\n"
        f"🚨 report@{codes['like']} msg"
    )
    targets = await all_room_ids()
    for target_rid in targets:
        try:
            await room_send(target_rid, caption)
            duration_ms = int(track.get("duration_ms") or (float(track.get("duration") or 0) * 1000))
            if duration_ms <= 0:
                raise RuntimeError("مدة الصوت صفر؛ تم منع إرسال بصمة صوت فارغة")
            await room_send_media(
                target_rid,
                f"▶️ تشغيل | {title}",
                media_url, m_type="voice", duration_ms=duration_ms,
            )
        except Exception as exc:
            log.exception("music broadcast failed room=%s", target_rid)
            await report_music_error_to_masters(
                target_rid, source_label, title,
                f"{type(exc).__name__}: {exc}",
                stage="إرسال تفاصيل/رسالة الصوت إلى الغرف"
            )
    return True, None

def friendly_music_error(error):
    """رسالة مفهومة للمستخدم، مع إبقاء الخطأ الخام للماستر."""
    e = str(error or "").lower()
    if "the page needs to be reloaded" in e:
        return "❌ اتصلت بيوتيوب، لكن جلسة YouTube الحالية أعادت: The page needs to be reloaded. تم تجربة العملاء بدون Cookies ثم default/web_embedded؛ إذا استمر الخطأ فحدّث Cookies أو استخدم YOUTUBE_PLAYER_CLIENTS=default,web_embedded."
    if any(x in e for x in ("sign in to confirm", "not a bot", "captcha", "botguard", "po token", "http error 403", "403 forbidden")):
        return "❌ اتصلت بيوتيوب، لكن يوتيوب رفض الوصول/تحميل الصوت. السبب: تحقق/حظر جلسة YouTube أو PO Token أو Cookies غير صالحة."
    if any(x in e for x in ("clientconnectorerror", "cannot connect", "connection refused", "name or service not known", "temporary failure in name resolution", "timeout", "timed out")):
        return "❌ لم أستطع التواصل مع يوتيوب من خادم Railway. فشل اتصال الشبكة قبل تحميل الأغنية."
    if "لم يُرجع نتائج" in e or "no results" in e:
        return "❌ تم الاتصال بمصدر البحث، لكن يوتيوب لم يُرجع نتيجة مطابقة للأغنية المطلوبة."
    if "ffmpeg" in e or "تحويل الصوت" in e:
        return "❌ تم الحصول على الصوت، لكن فشل تحويله إلى MP3 بواسطة FFmpeg."
    if "public_base_url" in e or "رابط عام" in e or "/media/" in e:
        return "❌ تم تجهيز الأغنية، لكن تعذر إنشاء رابط عام لملف الصوت. تحقق من PUBLIC_BASE_URL وPublic Domain في Railway."
    if "room_messages" in e or "message_type" in e or "voice" in e:
        return "❌ تم تجهيز ملف الصوت، لكن فشل إرسال رسالة الصوت إلى جينات شات."
    return f"❌ تعذر تشغيل الأغنية. السبب: {str(error)[:700]}"


async def music_worker_queue():
    global last_music_started
    interval = max(0, int(C.get("music_interval_seconds", 0)))
    while True:
        item = await music_queue.get()
        rid, query, source, requester_id, requester_name = item
        try:
            wait = interval - (time.time() - last_music_started)
            if wait > 0:
                await asyncio.sleep(wait)
            if rid not in rooms:
                continue

            last_music_started = time.time()
            if source == "TikTok":
                track, err = await search_tiktok(query)
            elif source == "Spotify":
                track, err = await search_spotify(query)
            else:
                track, err = await search_track(query)

            used_source = source
            if err and source in ("YouTube", "Spotify"):
                # مصدر احتياطي: إذا فشل يوتيوب/سبوتيفاي نجرب TikTok الذي يعمل على Railway.
                await report_music_error_to_masters(rid, source, query, err, stage="البحث")
                alt_track, alt_err = await search_tiktok(query)
                if alt_track and not alt_err:
                    track, err, used_source = alt_track, None, "TikTok"

            if err:
                await room_send(rid, friendly_music_error(err))
                await report_music_error_to_masters(rid, source, query, err, stage="البحث/الاتصال")
            else:
                ok, out = await play_track(rid, track, used_source, requester_id, requester_name)
                if not ok and out:
                    await room_send(rid, friendly_music_error(out))
                    await report_music_error_to_masters(rid, used_source, query, out, stage="التنزيل/التجهيز/الإرسال")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("music queue worker failed")
            try:
                detail = f"{type(exc).__name__}: {exc}"
                await room_send(rid, friendly_music_error(detail))
                await report_music_error_to_masters(rid, source, query, detail, stage="استثناء غير متوقع")
            except Exception:
                pass
        finally:
            music_queue.task_done()


async def cancel_music_task(rid):
    task = music_tasks.pop(rid, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def skip(rid):
    await cancel_music_task(rid)
    music_state.pop(rid, None)
    return True, "⏭️ تم التخطي بواسطة البوت"


async def stop(rid):
    await cancel_music_task(rid)
    music_state.pop(rid, None)
    return True, "⏹️ تم إيقاف الأغنية بواسطة البوت"

# ----------------------------- أوامر الغرفة -----------------------------
HELP_GAMES = """━━━━━━━━ 🎮 أوامر الألعاب ━━━━━━━━
⚔️ حرب — يبدأ/ينضم للعبة الحرب، ثم اكتب رقماً من 1 إلى 6
🖐️ كف — تحدي كف
🥊 قتال — قتال سريع
🏁 سباق — سباق
💰 رشوة — رشوة
🏀 سلة — كرة سلة
💣 قصف — قصف
🐸 اضرب — اضرب الضفدع
🃏 ورق — ورق
⚽ سدد — تسديد
🥊 ملاكمة — ملاكمة
💼 عمل — وظيفة
🌋 بركان — بركان
👻 شبح — صيد الشبح
🎲 مضاربة رقم — مراهنة
🎲 حظ / نرد / تعدين / زواج
━━━━━━━━━━━━━━━━━━━━
كل لعبة ترسل الصورة ثم تفاصيلها كنص فقط.
🔐 التوثيق قابل للتشغيل/الإيقاف من المالك. عند التفعيل: vip@اسم_المستخدم.
"""

HELP_ROOM = """━━━━━━━━ 🤖 جميع أوامر البوت ━━━━━━━━
[1] الحساب والنقاط
points / نقاطي — عرض نقاطك
توب — أفضل 10 لاعبين
dp@الاسم — صورة المستخدم
p@الاسم — البروفايل
st@الاسم — حالة المستخدم

[2] الموسيقى
🔒 تشغيل/مشاركة الأغاني تحتاج توثيق VIP من صاحب البوت.
تشغيل اسم الأغنية — YouTube
تيك اسم الأغنية — TikTok
.تشغيل اسم الأغنية — Spotify (يبحث عن النسخة الصوتية)
مشاركة — مشاركة الأغنية الحالية
تخطي — تخطي الأغنية
ايقاف — إيقاف الصوت

[3] الألعاب
العاب — عرض أوامر الألعاب
""" + HELP_GAMES + """

[4] الرتب والإدارة
o@الاسم — مالك
m@الاسم — عضوية
n@الاسم — إزالة رتبة
a@الاسم — إشراف
mas@الاسم — ماستر
umas@الاسم — إزالة ماستر
المسترات — قائمة الماسترات
k@الاسم — طرد
b@الاسم — حظر
ip@الاسم — حظر IP

[5] الهدايا
🔐 توثيق VIP: vip@اسم_المستخدم (صاحب البوت فقط)
gv — عرض الهدايا
gv@رقم_الهدية@اسم_الحساب — إرسال هدية

[6] الترحيب والردود
+wc رسالة — إضافة ترحيب
+wc رسالة %id% — ترحيب مع الاسم
clear@wc — حذف الترحيبات
l@wc — عرض الترحيبات
wc@on / wc@off — تفعيل/تعطيل
+r@كلمة@رد — إضافة رد

[7] فلتر الكلمات
mf@on — تشغيل الفلتر
mf@off — إيقاف الفلتر
+mf@كلمة — إضافة كلمة ممنوعة
-mf@كلمة — إزالة كلمة
l@mf — عرض الكلمات
clear@mf — حذف الكلمات

[8] النشر — للماستر
نشر نص — نشر النص في جميع الغرف
نشر@ — اطلب الصورة ثم أرسلها، وسيتم نشرها في جميع الغرف
نشرصورة رابط — نشر صورة برابط

[9] اللغة
lang@ar — العربية
lang@en — English

.help / help — عرض جميع الأوامر
.more / .next — عرض القائمة التالية
━━━━━━━━━━━━━━━━━━━━"""



async def _draw_game_text(draw, xy, text, font, fill=(30,30,30), anchor="ma"):
    """رسم عربي بشكل صحيح عندما تتوفر arabic_reshaper/python-bidi."""
    text = str(text)
    if arabic_reshaper and get_display:
        try:
            text = get_display(arabic_reshaper.reshape(text))
        except Exception:
            pass
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def render_game_card_sync(game_key, title, lines):
    """إنشاء صورة اللعبة فقط.

    تفاصيل النتيجة لا تُرسم داخل الصورة؛ تُرسل كنص مستقل بعد الصورة حتى تبقى
    صور الألعاب كما هي في مجلد assets، وبنفس الأسلوب الذي طلبه المستخدم.
    """
    if not PIL_AVAILABLE:
        return None

    local_map = {
        "slap": "assets/slap_action.jpg", "war": "assets/war_game.png",
        "fight": "assets/fight_action.jpg", "boxing": "assets/defense_action.jpg"
    }
    generated = BASE_DIR / "assets" / f"game_{game_key}.jpg"
    src = BASE_DIR / local_map.get(game_key, f"assets/game_{game_key}.jpg")
    if generated.is_file() and game_key not in local_map:
        src = generated

    try:
        if src.is_file():
            im = Image.open(src).convert("RGB")
        else:
            im = Image.new("RGB", (900, 560), (240, 243, 247))
    except Exception:
        im = Image.new("RGB", (900, 560), (240, 243, 247))

    im.thumbnail((900, 700), Image.LANCZOS)
    canvas = Image.new("RGB", (900, im.height), (245, 247, 250))
    canvas.paste(im, ((900 - im.width) // 2, 0))

    outdir = BASE_DIR / "generated_games"
    outdir.mkdir(exist_ok=True)
    path = outdir / f"game_{game_key}_{uuid.uuid4().hex}.jpg"
    canvas.save(path, quality=92, optimize=True)
    return path


async def send_game_card(rid, game_key, title, lines, fallback_text=None):
    """أرسل صورة اللعبة أولاً ثم تفاصيلها كنص مستقل."""
    path = await asyncio.to_thread(render_game_card_sync, game_key, title, lines)
    if path:
        try:
            url = await _store_media(path, "game", "image/jpeg")
            # الصورة وحدها: لا نضع اسم اللعبة أو النتيجة داخل الصورة.
            await room_send_media(rid, "", url, m_type="image")
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            # التفاصيل بعد الصورة مباشرة، كنص قابل للقراءة والنسخ.
            details = "\n".join(lines)
            if details:
                await room_send(rid, f"{title}\n{details}")
            return
        except Exception as exc:
            log.warning("game card upload failed: %s", exc)
    if fallback_text:
        await room_send(rid, fallback_text)


async def handle_room(rid, text, uid, media_url=None, message_type=None):
    if await is_banned(rid, uid): return None
    p_name = await username_of(uid)
    lower_text = text.strip().lower()

    social_reply = await handle_social_reaction(rid, text, uid, p_name)
    if social_reply is not None:
        return social_reply

    admin_prefixes = ("+mf@", "-mf@", "clear@mf", "l@mf", "mf@on", "mf@off",
                      "+wc ", "clear@wc", "l@wc", "wc@on", "wc@off", "mas@")
    if not lower_text.startswith(admin_prefixes):
        blocked = await check_forbidden_word(rid, text)
        if blocked:
            return blocked

    # ---------------- الذكاء الاصطناعي داخل الغرفة ----------------
    # يدعم ai@السؤال وذكاء@السؤال، وكذلك التحيات المباشرة البسيطة.
    # هذا الاستدعاء محلي بالكامل عبر Qwen/llama.cpp ولا يستخدم OpenAI.
    ai_text = text.strip()
    ai_low = normalize_text(ai_text)
    ai_prompt = None
    for prefix in ("ai@", "ذكاء@", "الذكاء@"):
        if ai_low.startswith(prefix):
            ai_prompt = ai_text[len(prefix):].strip()
            break
    if ai_prompt is None and any(x in ai_low for x in ("مرحبا من انت", "مرحبا من أنت", "اهلا من انت", "أهلا من أنت")):
        ai_prompt = ai_text
    if ai_prompt is not None:
        if not ai_prompt:
            return "🤖 اكتب سؤالك هكذا: ai@مرحبا، من أنت؟"
        answer, ai_err = await ai_response(ai_prompt, 700)
        if ai_err:
            log.error("room local AI error: %s", ai_err)
            return "❌ تعذر تشغيل الذكاء الاصطناعي المحلي حالياً."
        return "🤖 " + answer

    if ai_low in ("ai status", "حالة ai", "حالة الذكاء"):
        return local_ai_status_text()

    replies = load_replies()
    if text.strip() in replies: return replies[text.strip()]

    # الأوامر التي ينشئها الماستر ديناميكياً. تعمل فوراً بدون تعديل الكود.
    custom_reply = await execute_custom_command(rid, uid, p_name, text)
    if custom_reply is not None:
        return custom_reply

    if text.startswith("نشر ") or text.startswith("broadcast "):
        vip_error = await require_vip(uid, p_name, "نظام النشر")
        if vip_error: return vip_error
        msg = text.split(maxsplit=1)[1].strip()
        await broadcast_text("📢 " + msg)
        return "✅ تم نشر الرسالة في كل الغرف."
    if text.startswith("نشرصورة ") or text.startswith("broadcast_image "):
        vip_error = await require_vip(uid, p_name, "نظام النشر")
        if vip_error: return vip_error
        url = text.split(maxsplit=1)[1].strip()
        await broadcast_media("📢", url, m_type="image")
        return "✅ تم نشر الصورة في كل الغرف."

    # نشر@: الماستر يطلب صورة في رسالة لاحقة، ثم ينشرها في كل الغرف.
    publish_key = (rid, uid)
    if text.strip() == "نشر@" or text.strip().startswith("نشر@") or text.strip() == "publish@" or text.strip().startswith("publish@"):
        vip_error = await require_vip(uid, p_name, "نظام النشر")
        if vip_error: return vip_error
        description = ""
        if "@" in text:
            description = text.split("@", 1)[1].strip()
        publish_pending[publish_key] = {"created_at": time.time(), "description": description}
        return "🖼️ أرسل الصورة الآن خلال دقيقتين، وسيتم نشرها في كل الغرف مع الوصف الذي كتبته."

    async def cache_publish_media(source_url):
        """Copy an incoming image to this bot's public storage so it remains
        accessible after the original message URL expires."""
        if not source_url:
            return None
        temp_dir = Path(tempfile.mkdtemp(prefix="bot_publish_"))
        try:
            suffix = ".jpg"
            low = str(source_url).lower()
            for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                if ext in low:
                    suffix = ext
                    break
            local = temp_dir / f"image{suffix}"
            async with http.get(
                source_url,
                timeout=aiohttp.ClientTimeout(total=45),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status != 200:
                    return None
                with local.open("wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 256):
                        f.write(chunk)
            if local.stat().st_size < 512:
                return None
            return await _store_media(
                local,
                "publish",
                {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp","gif":"image/gif"}.get(suffix.lstrip("."), "image/jpeg")
            )
        except Exception as e:
            log.warning("publish image cache failed: %s", e)
            return None
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    pending = publish_pending.get(publish_key)
    if pending is not None:
        pending_at = pending.get("created_at", 0) if isinstance(pending, dict) else pending
        description = pending.get("description", "") if isinstance(pending, dict) else ""
        if time.time() - pending_at > 120:
            publish_pending.pop(publish_key, None)
        elif message_type in ("image", "photo", "sticker") and media_url:
            publish_pending.pop(publish_key, None)
            source_room = rooms.get(rid, "الغرفة")
            # Re-host the image on the bot's public Railway endpoint when possible.
            public_media_url = await cache_publish_media(media_url) or media_url
            post_id = str(uuid.uuid4())
            posts = load_published_posts()
            posts[post_id] = {"post_id": post_id, "owner_id": str(uid), "owner_name": p_name, "source_room_id": str(rid), "media_url": public_media_url, "type": "image", "title": description or "منشور صورة", "created_at": now_iso()}
            save_published_posts(posts)
            codes = await register_social_codes(post_id, uid, p_name, "image", description or "منشور صورة", rid)
            published = 0
            for target_rid in await all_room_ids():
                try:
                    caption = (
                        "✨════════════✨\n"
                        "🖼️ منشور الصورة من:\n"
                        f"@{p_name}\n"
                        "📝 الوصف: " + (description or "بدون وصف") + "\n"
                        "━━━━━━━━━━━━━\n"
                        f"👍 {codes['like']}@Like\n"
                        f"❤️ {codes['love']}@loved\n"
                        f"👎 {codes['dislike']}@Dislike\n"
                        f"💬 {codes['comment']}@msg\n"
                        f"🚨 {codes['report']}@report\n"
                        "✨════════════✨"
                    )
                    await room_send_media(target_rid, caption, public_media_url, m_type="image")
                    await room_send(target_rid, "❤️ إعجاب | 👎 عدم إعجاب | ↩️ رد على الصورة")
                    published += 1
                except Exception:
                    log.exception("publish@ failed for room %s", target_rid)
            return f"✅ تم نشر الصورة في {published} غرفة."
        elif media_url:
            return "⚠️ الملف المرسل ليس صورة. أرسل صورة بعد أمر نشر@."

    if text == "المسترات":
        masters = load_masters()
        return "👑 قائمة الماسترز:\n" + "\n".join([f"• @{m}" for m in masters]) if masters else "👤 المالك فقط هو الماستر حالياً."

    # توثيق VIP للأغاني والألعاب: المالك فقط يملك أمر vip@.
    if lower_text.startswith("vip@"): 
        if str(p_name).strip().lower() != OWNER:
            return "🚫 توثيق VIP متاح لصاحب البوت فقط."
        target = text.split("@", 1)[1].strip()
        ok, msg = await grant_vip_by_username(target)
        return msg

    if lower_text.startswith("unvip@"): 
        if str(p_name).strip().lower() != OWNER:
            return "🚫 إزالة توثيق VIP متاحة لصاحب البوت فقط."
        target = text.split("@", 1)[1].strip().lower().lstrip("@")
        data = load_vip_users()
        removed = []
        for key, item in list(data.items()):
            name = item.get("username", "") if isinstance(item, dict) else str(item)
            if key.lower() == target or str(name).lower() == target:
                removed.append(name or key)
                data.pop(key, None)
        save_vip_users(data)
        return (f"✅ تم إلغاء توثيق @{removed[0] if removed else target}." if removed else f"⚠️ @{target} غير موثّق VIP.")

    if lower_text in ("vips", "vip", "الموثقين"):
        if str(p_name).strip().lower() != OWNER:
            return "🚫 قائمة VIP لصاحب البوت فقط."
        data = load_vip_users()
        names = []
        for item in data.values():
            if isinstance(item, dict):
                names.append(str(item.get("username") or item.get("id") or ""))
            else:
                names.append(str(item))
        return "👑 موثقو VIP:\n" + ("\n".join(f"• @{n}" for n in names if n) if names else "لا يوجد موثقون.")

    if text.startswith("mas@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        target = text.replace("mas@", "").strip()
        masters = load_masters()
        if target not in masters:
            masters.append(target); save_masters(masters)
            return f"✅ تم إضافة @{target} كـ ماستر."
        return f"⚠️ @{target} ماستر بالفعل."

    if text.startswith("+r@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        parts = text.split("@")
        if len(parts) >= 3:
            replies[parts[1].strip()] = parts[2].strip(); save_replies(replies)
            return f"✅ تم إضافة الرد لـ: {parts[1].strip()}"
        return "❌ الصيغة: +r@الكلمة@الرد"

    # ---------------- فلتر الكلمات الممنوعة ----------------
    if lower_text in ("mf@on", "mf on"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        mod = load_moderation(); mod.setdefault("enabled", {})[str(rid)] = True; save_moderation(mod)
        return "✅ تم تفعيل فلتر الألفاظ في هذه الغرفة."
    if lower_text in ("mf@off", "mf off"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        mod = load_moderation(); mod.setdefault("enabled", {})[str(rid)] = False; save_moderation(mod)
        return "⛔ تم تعطيل فلتر الألفاظ في هذه الغرفة."
    if lower_text == "clear@mf":
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        mod = load_moderation(); mod["words"] = []; save_moderation(mod)
        return "🧹 تم حذف جميع الكلمات الممنوعة."
    if lower_text == "l@mf":
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        words = load_moderation().get("words", [])
        return "🚫 الكلمات الممنوعة:\n" + ("\n".join(f"{i+1}. {w}" for i,w in enumerate(words)) if words else "لا توجد كلمات.")
    if lower_text.startswith("+mf@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        word = text.split("@", 1)[1].strip()
        if not word: return "❌ الصيغة: +mf@كلمة"
        mod = load_moderation(); words = mod.setdefault("words", [])
        if word not in words: words.append(word)
        save_moderation(mod)
        return f"✅ تمت إضافة الكلمة الممنوعة: {word}"
    if lower_text.startswith("-mf@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        word = text.split("@", 1)[1].strip()
        mod = load_moderation(); mod["words"] = [w for w in mod.get("words", []) if normalize_text(w) != normalize_text(word)]
        save_moderation(mod)
        return f"✅ تمت إزالة الكلمة: {word}"

    # ---------------- رسائل الترحيب ----------------
    if lower_text.startswith("+wc "):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        msg = text.split(" ", 1)[1].strip()
        data = load_welcome(); item = data.setdefault(str(rid), {"enabled": False, "messages": []})
        if msg not in item["messages"]: item["messages"].append(msg)
        save_welcome(data)
        return "✅ تمت إضافة رسالة الترحيب."
    if lower_text == "clear@wc":
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        data = load_welcome(); data.pop(str(rid), None); save_welcome(data)
        return "🧹 تم حذف رسائل الترحيب."
    if lower_text == "l@wc":
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        msgs = load_welcome().get(str(rid), {}).get("messages", [])
        return "👋 رسائل الترحيب:\n" + ("\n".join(f"{i+1}. {m}" for i,m in enumerate(msgs)) if msgs else "لا توجد رسائل.")
    if lower_text in ("wc@on", "wc on"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        data = load_welcome(); data.setdefault(str(rid), {"enabled": False, "messages": []})["enabled"] = True; save_welcome(data)
        return "✅ تم تفعيل رسائل الترحيب."
    if lower_text in ("wc@off", "wc off"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        data = load_welcome(); data.setdefault(str(rid), {"enabled": False, "messages": []})["enabled"] = False; save_welcome(data)
        return "⛔ تم تعطيل رسائل الترحيب."

    if text.strip().lower() in ("العاب", "ألعاب", "games", "gamehelp"):
        return HELP_GAMES

    if text.strip().lower() in ("gv", "هدايا", "الهدايا", "gifts"):
        return await gift_catalog_message()

    if text.strip().lower().startswith("gv@"):
        return await send_gift_command(rid, uid, p_name, text.strip())

    parts = text.split(maxsplit=1)
    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

    GAME_COMMANDS = {"عمل","job","كف","slap","مضاربة","bet","حرب","war","سرقة","rob","قتال","fight",
                     "سباق","race","رشوة","سلة","قصف","اضرب","ورق","سدد","ملاكمة","بركان","شبح","حظ","نرد","تعدين","زواج","marriage"}

    async def require_game_cooldown(game_command):
        ok_cd, rem_cd = check_cooldown(uid, p_name, f"game:{game_command}", int(C.get("game_cooldown_seconds", 30)))
        if not ok_cd:
            return f"⏳ @{p_name} انتظر {rem_cd} ثانية قبل إعادة لعبة «{game_command}». الفاصل 30 ثانية لهذه اللعبة فقط."
        return None

    async def require_music_cooldown():
        now = time.time(); last = music_last_by_user.get(str(uid), 0.0); interval = int(C.get("music_interval_seconds", 120))
        remaining = int(interval - (now-last)) if now-last < interval else 0
        if remaining > 0:
            return f"⏳ @{p_name} انتظر {remaining} ثانية قبل طلب أغنية أخرى. فاصل الأغاني دقيقتان لك."
        music_last_by_user[str(uid)] = now
        return None

    # كل أوامر الألعاب محمية بتوثيق VIP من صاحب البوت.
    if cmd in GAME_COMMANDS:
        vip_error = await require_vip(uid, p_name, "أوامر الألعاب")
        if vip_error:
            return vip_error

    if cmd == ".sa":
        cmd = "تشغيل"

    if text.strip().lower().startswith("is@"):
        target = text.split("@", 1)[1].strip().lstrip("@")
        rows, _ = await table_select(lambda: sb.table("profiles").select("id,username").ilike("username", target).limit(1).execute())
        if not rows:
            return f"❌ الحساب @{target} غير موجود."
        return await user_presence(rows[0]["id"], rows[0].get("username") or target)

    if text.strip().lower().startswith("مشاركة@"):
        vip_error = await require_vip(uid, p_name, "مشاركة الأغاني")
        if vip_error: return vip_error
        target = text.split("@", 1)[1].strip()
        return await share_music_to_user(uid, target, music_state.get(rid))

    if cmd in ("تشغيل", "play", "شغل"):
        vip_error = await require_vip(uid, p_name, "تشغيل الأغاني")
        if vip_error: return vip_error
        if not arg: return "❌ اكتب: تشغيل اسم الأغنية"
        cd = await require_music_cooldown()
        if cd: return cd
        await music_queue.put((rid, arg, "YouTube", uid, p_name))
        return f"🎵 @{p_name} جاري تنفيذ طلبك…\n🔎 البحث عن: {arg}\n🏠 الغرفة: {rooms.get(rid, 'الغرفة')}"

    if cmd in ("مشاركة", "share"):
        vip_error = await require_vip(uid, p_name, "مشاركة الأغاني")
        if vip_error: return vip_error
        current = music_state.get(rid)
        if not current:
            return "❌ لا توجد أغنية حالياً للمشاركة."
        return f"🎵 مشاركة الأغنية\n🎶 {current.get('title','المقطع')} — {current.get('artist','')}\n🔗 {current.get('spotify_url') or current.get('youtube_url') or ''}"

    if cmd in (".تشغيل", "spotify", "سبوتيفاي"):
        vip_error = await require_vip(uid, p_name, "تشغيل الأغاني")
        if vip_error: return vip_error
        if not arg:
            return "❌ اكتب: .تشغيل اسم الأغنية أو .تشغيل رابط Spotify"
        cd = await require_music_cooldown()
        if cd: return cd
        await music_queue.put((rid, arg, "Spotify", uid, p_name))
        return f"🎵 @{p_name} جاري تنفيذ طلبك من Spotify…\n🏠 الغرفة: {rooms.get(rid, 'الغرفة')}"

    if cmd in ("تيك", ".تيك", "tiktok", "tik"):
        vip_error = await require_vip(uid, p_name, "تشغيل الأغاني")
        if vip_error: return vip_error
        if not arg: return "❌ اكتب: تيك اسم الأغنية"
        cd = await require_music_cooldown()
        if cd: return cd
        await music_queue.put((rid, arg, "TikTok", uid, p_name))
        return f"🎵 @{p_name} جاري تنفيذ طلبك من TikTok…\n🏠 الغرفة: {rooms.get(rid, 'الغرفة')}"

    # لعبة الحرب العالمية: لاعبَان من أي غرفتين، وكل الحالة مشتركة بين جميع الغرف.
    if cmd in ("حرب", "war"):
        key = GLOBAL_WAR_KEY
        game = war_games.get(key)
        now = time.time()
        if game and now >= game.get("expires_at", 0):
            war_games.pop(key, None)
            game = None
            await broadcast_text("⌛ انتهت لعبة الحرب تلقائياً بسبب انتهاء المهلة. اكتب «حرب» لبدء لعبة جديدة.")
        if not game:
            cd_error = await require_game_cooldown(cmd)
            if cd_error: return cd_error
            war_games[key] = {"p1": uid, "p1_name": p_name, "p1_room": rid, "p2": None, "p2_name": None, "p2_room": None,
                              "ship": random.randint(1, 6), "tries": {str(uid): 0}, "guesses": {str(uid): []},
                              "turn": uid, "created_at": now, "expires_at": now + 120}
            await broadcast_media(f"🚢 حرب عالمية بدأت!\n👤 اللاعب الأول: @{p_name}\n⏳ جاري انتظار الخصم...\n🎯 اكتب «حرب» من أي غرفة للانضمام.", GAME_IMAGES["war"], m_type="image")
            return None
        if game["p1"] == uid:
            return "⚠️ أنت داخل لعبة حرب بالفعل وتنتظر الخصم." if game.get("p2") is None else "⚠️ أنت داخل لعبة حرب بالفعل."
        if game.get("p2") is None:
            game["p2"], game["p2_name"], game["p2_room"] = uid, p_name, rid
            game["tries"][str(uid)] = 0; game["guesses"][str(uid)] = []
            game["turn"] = game["p1"]; game["expires_at"] = now + 120
            await broadcast_media(f"⚔️ بدأت حرب عالمية بين @{game['p1_name']} و@{p_name}!\n🏠 قد يكون اللاعبان في غرفتين مختلفتين.\n🎯 دور @{game['p1_name']} — اختر رقماً من 1 إلى 6.", GAME_IMAGES["war"], m_type="image")
            return None
        return "⚠️ الحرب ممتلئة. انتظر انتهاء المباراة."

    if game := war_games.get(GLOBAL_WAR_KEY):
        now = time.time()
        if now >= game.get("expires_at", 0):
            war_games.pop(GLOBAL_WAR_KEY, None)
            return "⌛ انتهت الحرب بسبب انتهاء المهلة. اكتب «حرب» لبدء لعبة جديدة."
        if text.isdigit() and 1 <= int(text) <= 6:
            if game.get("p2") is None: return "⏳ انتظر اللاعب الثاني."
            if uid not in (game["p1"], game["p2"]): return "🚫 هذه اللعبة بين لاعبين آخرين."
            if game["turn"] != uid: return "⏳ انتظر دور خصمك."
            n = int(text); skey = str(uid)
            if n in game["guesses"].setdefault(skey, []): return "⚠️ لقد اخترت هذا الرقم من قبل."
            game["guesses"][skey].append(n); game["tries"][skey] += 1
            if n == game["ship"]:
                add_points(uid, p_name, 60)
                winner_room = rooms.get(rid, "الغرفة")
                await send_game_card(rid, "war", "⚔️ حرب | Battle", [f"🏆 الفائز: @{p_name} (+60)", f"💥 السفينة دُمّرت بواسطة @{p_name}", f"🚢 موقع السفينة: {game['ship']}"])
                await broadcast_text(f"🏆⚔️ انتهت الحرب العالمية!\n🎉 الفائز: @{p_name} (+60)\n🚢 موقع السفينة: {game['ship']}\n🏠 الغرفة: {winner_room}")
                war_games.pop(GLOBAL_WAR_KEY, None)
                return None
            other = game["p2"] if uid == game["p1"] else game["p1"]
            other_key = str(other); current_tries = game["tries"].get(skey, 0); other_tries = game["tries"].get(other_key, 0)
            if current_tries >= 3 and other_tries >= 3:
                await broadcast_text(f"🤝 انتهت الحرب العالمية دون فائز. 🚢 موقع السفينة: {game['ship']}")
                war_games.pop(GLOBAL_WAR_KEY, None); return None
            if other_tries >= 3:
                game["turn"] = uid; next_name = p_name; remaining = 3-current_tries
            else:
                game["turn"] = other; next_name = game["p2_name"] if uid == game["p1"] else game["p1_name"]; remaining = 3-other_tries
            game["expires_at"] = now + 120
            await broadcast_text(f"❌ @{p_name} اختار {n} ولم يجد السفينة.\n🔄 دور @{next_name} | المحاولات المتبقية: {remaining}\n🎯 اختر رقماً من 1 إلى 6")
            return None

    if cmd in ("سرقة", "rob"):
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        win = random.randint(1, 100) <= 40
        add_points(uid, p_name, 25 if win else -15)
        await send_game_card(rid, "rob", "💰 Rob | سرقة", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {'+25' if win else '-15'} نقطة"], f"💰 {'نجحت السرقة!' if win else 'فشلت السرقة..'} @{p_name}")
        return None

    if cmd in ("قتال", "fight"):
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        win = random.choice([True, False])
        add_points(uid, p_name, 15 if win else -5)
        await send_game_card(rid, "fight", "🥊 Fight | قتال", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {'+15' if win else '-5'} نقطة"], f"🥊 {'هزمت خصمك!' if win else 'تلقيت ضربة قاضية..'} @{p_name}")
        return None

    if cmd in ("عمل", "job"):
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        salary = random.randint(50, 150); add_points(uid, p_name, salary)
        await send_game_card(rid, "job", "💼 Work | عمل", [f"👤 اللاعب: @{p_name}", f"💵 الراتب: +{salary} نقطة", "🏆 النتيجة: فوز"], f"💼 عمل @{p_name} +{salary} نقطة")
        return None

    if cmd in ("سباق", "race"):
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        win = random.choice([True, False])
        add_points(uid, p_name, 30 if win else -10)
        await send_game_card(rid, "race", "🏁 Race | سباق", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {'+30' if win else '-10'} نقطة"], f"🏁 {'فزت بالسباق!' if win else 'تعطلت سيارتك..'} @{p_name}")
        return None

    if cmd in ("كف", "slap"):
        game = kaf_games.get(f"slap_{rid}")
        if not game:
            cd_error = await require_game_cooldown(cmd)
            if cd_error:
                return cd_error
            kaf_games[f"slap_{rid}"] = {"player1": uid, "p1_name": p_name}
            await send_game_card(rid, "slap", "👏💢 Slap | كف 💢👏", [f"👤 @{p_name}", "⏳ جاري انتظار الخصم", "🎮 اكتب كف للانضمام"], f"⏳ @{p_name} جاري انتظار الخصم...")
        else:
            if game["player1"] == uid: return "⚠️ أنت تنتظر منافس!"
            p1_name = game["p1_name"]
            winner = random.choice([p1_name, p_name])
            kaf_games.pop(f"slap_{rid}")
            add_points(uid if winner == p_name else game["player1"], winner, 15)
            await send_game_card(rid, "slap", "👏💢 Slap | كف 💢👏", [f"🥊 @{p1_name} × @{p_name}", f"🏅 Winner | الفائز: @{winner} (+15)", "💔 Loser | الخاسر: اللاعب الآخر (-10)"], f"👏💢 Slap | كف 💢👏\n🏆 الفائز: @{winner}")
        return None

    if cmd in ("مضاربة", "bet"):
        try: amount = int(arg)
        except: return "❌ اكتب: مضاربة [عدد النقاط]"
        points, user_data = get_user_data(uid, p_name)
        if user_data["points"] < amount: return f"⚠️ نقاطك لا تكفي ({user_data['points']})"
        game_key = f"bet_{rid}"
        game = kaf_games.get(game_key)
        if not game:
            cd_error = await require_game_cooldown(cmd)
            if cd_error:
                return cd_error
            kaf_games[game_key] = {"player1": uid, "p1_name": p_name, "amount": amount}
            await send_game_card(rid, "bet", "🎲 Bet | مضاربة", [f"👤 اللاعب: @{p_name}", f"💰 الرهان: {amount} نقطة", "⏳ جاري انتظار الخصم"], f"🎲 @{p_name} يراهن بـ {amount} نقطة")
            async def bot_bet():
                await asyncio.sleep(30)
                g = kaf_games.get(game_key)
                if g and g["player1"] == uid:
                    win = random.choice([True, False])
                    kaf_games.pop(game_key)
                    add_points(uid, p_name, amount if win else -amount)
                    await send_game_card(rid, "bet", "🎲 Bet | مضاربة", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {amount if win else -amount} نقطة"], f"🤖 {'فزت على البوت!' if win else 'خسرت ضد البوت..'} @{p_name}")
            asyncio.create_task(bot_bet())
        else:
            if game["player1"] == uid: return "⚠️ أنت صاحب الرهان!"
            if amount != game["amount"]: return f"❌ الرهان هو {game['amount']} ن."
            p1_name = game["p1_name"]
            winner = random.choice([p1_name, p_name])
            kaf_games.pop(game_key)
            add_points(uid if winner == p_name else game["player1"], winner, amount)
            add_points(game["player1"] if winner == p_name else uid, p1_name if winner == p_name else p_name, -amount)
            await send_game_card(rid, "bet", "🎲 Bet | مضاربة", [f"🥊 @{p1_name} × @{p_name}", f"🏆 Winner | الفائز: @{winner}", f"💰 الرهان: {amount} نقطة"], f"🎲 تمت المضاربة بين @{p1_name} و @{p_name}..\n🏆 الفائز: @{winner}")
        return None

    if cmd in ("طرد", "kick"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        target = arg.replace("@", "").strip()
        rows, _ = await table_select(lambda: sb.table("profiles").select("id").eq("username", target).limit(1).execute())
        if not rows: return "❌ المستخدم غير موجود."
        await rpc("room_leave", {"_room": rid, "_user": rows[0]["id"]})
        return f"👞 تم طرد @{target}."

    if cmd in ("حظر", "ban"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        target = arg.replace("@", "").strip()
        rows, _ = await table_select(lambda: sb.table("profiles").select("id").eq("username", target).limit(1).execute())
        if not rows: return "❌ المستخدم غير موجود."
        tid = rows[0]["id"]; bans = load_bans()
        if rid not in bans: bans[rid] = []
        if tid not in bans[rid]:
            bans[rid].append(tid); save_bans(bans)
            await rpc("room_leave", {"_room": rid, "_user": tid})
            return f"🚫 تم حظر @{target}."
        return "⚠️ محظور بالفعل."

    if cmd == "نقاطي":
        p, d = get_user_data(uid, p_name)
        return f"👤 @{p_name} ➔ ✨ {d['points']} نقطة"

    if cmd == "توب":
        pts = load_points()
        sorted_u = sorted(pts.items(), key=lambda x: x[1].get("points", 0), reverse=True)[:10]
        if not sorted_u: return "📭 القائمة فارغة."
        msg = "🏆 ━━━━━━ TOP 10 ━━━━━━ 🏆\n"
        emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for i, (u, d) in enumerate(sorted_u):
            msg += f"{emojis[i]} @{d['username']} ➔ {d['points']} ن\n"
        return msg + "━━━━━━━━━━━━━━━━━━━━"

    # بقية الألعاب مع صور
    games_map = {
        "رشوة": ("bribe", 100, -50, 30, "💰 نجحت الرشوة!", "👮 تم القبض عليك!"),
        "سلة": ("basket", 15, 0, 50, "🏀 رمية ثلاثية!", "🏀 ضاعت الكرة.."),
        "قصف": ("drone", 20, 0, 100, "💣 انفجار هائل!", ""),
        "اضرب": ("frog", 10, 0, 50, "🐸 ضربة موفقة!", "🐸 هرب الضفدع.."),
        "ورق": ("cards", 40, 0, 20, "🃏 ورقة الجوكر!", "🃏 ورقة ضعيفة.."),
        "سدد": ("ball", 20, 0, 50, "⚽ جـووووول!", "⚽ ضاعت الكرة.."),
        "ملاكمة": ("boxing", 30, -10, 50, "🥊 ضربة قاضية!", "🥊 سقطت في الحلبة.."),
        "بركان": ("volcano", 0, -20, 0, "", "🌋 ثوران بركاني!"),
        "شبح": ("ghost", 50, 0, 50, "👻 أمسكت بالشبح!", "👻 أخافك الشبح.."),
        "حظ": ("luck", 50, -30, 50, "🎲 حظ سعيد!", "📉 حظ سيء.."),
        "نرد": ("dice", 15, -10, 50, "🎲 فوز بالنرد!", "🎲 خسارة بالنرد..")
    }
    
    # ألعاب الاختبار: لا تعمل إلا بعد تشغيلها من خاص المالك، والمالك يُتحقق منه
    # بالـUID أو اسم الحساب عبر is_master() بدل مقارنة UID باسم مستخدم.
    active_tests = load_active_tests()
    testing_games = load_testing_games()
    if cmd in active_tests and cmd in testing_games:
        if not await is_master(uid, p_name):
            return "🔐 لعبة الاختبار متاحة للمالك/الماستر فقط."
        custom = testing_games[cmd]
        cd_error = await require_game_cooldown(cmd)
        if cd_error:
            return cd_error
        await room_send(rid, f"🔎 جاري البحث عن {custom.get('title', cmd)}...")
        await asyncio.sleep(0.5)
        try:
            win_chance = max(1, min(100, int(custom.get("win_chance", 50))))
        except Exception:
            win_chance = 50
        win = random.randint(1, 100) <= win_chance
        try:
            delta = int(custom.get("win_points", 20)) if win else int(custom.get("lose_points", -5))
        except Exception:
            delta = 20 if win else -5
        add_points(uid, p_name, delta)
        if custom.get("image_url"):
            try:
                await room_send_media(rid, "", custom.get("image_url"), m_type="image")
            except Exception:
                pass
        await send_custom_game_result(rid, custom, p_name, win)
        msg = custom.get("win_message") if win else custom.get("lose_message")
        return f"🧪 اختبار: {msg or ('🎉 فوز!' if win else '😅 خسارة!')} @{p_name}\n💰 {'+' if delta >= 0 else ''}{delta} نقطة"

    custom_games = load_custom_games()
    custom = custom_games.get(cmd)
    if custom:
        vip_error = await require_vip(uid, p_name, "أوامر الألعاب")
        if vip_error:
            return vip_error
        cd_error = await require_game_cooldown(cmd)
        if cd_error:
            return cd_error
        await room_send(rid, f"🔎 جاري البحث عن {custom.get('title', cmd)}...")
        await asyncio.sleep(0.5)
        win = random.randint(1, 100) <= int(custom.get("win_chance", 50))
        delta = int(custom.get("win_points", 20)) if win else int(custom.get("lose_points", -5))
        add_points(uid, p_name, delta)
        if custom.get("image_url"):
            try:
                await room_send_media(rid, "", custom.get("image_url"), m_type="image")
            except Exception:
                pass
        await send_custom_game_result(rid, custom, p_name, win)
        msg = custom.get("win_message") if win else custom.get("lose_message")
        return f"{msg} @{p_name}\n💰 {'+' if delta >= 0 else ''}{delta} نقطة"

    if cmd in games_map:
        cd_error = await require_game_cooldown(cmd)
        if cd_error:
            return cd_error
        key, win_p, lose_p, chance, win_m, lose_m = games_map[cmd]
        win = random.randint(1, 100) <= chance
        add_points(uid, p_name, win_p if win else lose_p)
        await send_game_card(rid, key, f"🎮 {cmd}", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {win_p if win else lose_p} نقطة"], f"{win_m if win else lose_m} @{p_name}\n💰 النتيجة: {win_p if win else lose_p} ن.")
        return None

    if cmd == "تعدين":
        cd_error = await require_game_cooldown(cmd)
        if cd_error:
            return cd_error
        found = random.randint(200, 500); add_points(uid, p_name, found)
        await send_game_card(rid, "mine", "⛏️ Mine | تعدين", [f"👤 اللاعب: @{p_name}", "🏆 Winner | الفائز", f"💰 النتيجة: +{found} نقطة"], f"⛏️ وجدت ذهباً! @{p_name} +{found} ن.")
        return None

    if cmd == "زواج":
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        pts, d = get_user_data(uid, p_name)
        if d.get("married_to"): return f"💍 متزوج من @{d['married_to']}"
        others = [u["username"] for i, u in pts.items() if i != uid]
        if not others: return "💔 لا أحد للزواج."
        partner = random.choice(others); d["married_to"] = partner
        pts[uid] = d; save_json(POINTS_PATH, pts)
        await send_game_card(rid, "marriage", "💍 Marriage | زواج", [f"👤 اللاعب: @{p_name}", f"❤️ الشريك: @{partner}", "🏆 تمت العملية بنجاح"], f"❤️ مبروك زواج @{p_name} من @{partner} 💍")
        return None

    if cmd in ("تخطي", "skip"):
        ok, out = await skip(rid); return out
    if cmd in ("ايقاف", "stop"):
        ok, out = await stop(rid); return out
    if cmd in ("مساعدة", "help", ".help"): return HELP_ROOM
    
    return None

# ----------------------------- الحلقات -----------------------------
# المطور المنفصل: الألعاب الجديدة تُحفظ في testing ولا تدخل التشغيل حتى اعتمادها.
try:
    from ai_developer.developer import GameDeveloper
except Exception:
    GameDeveloper = None

# ----------------------------- الذكاء الاصطناعي / الصيانة -----------------------------
Path(TESTING_GAMES_PATH).parent.mkdir(parents=True, exist_ok=True)
Path(APPROVED_GAMES_DIR).mkdir(parents=True, exist_ok=True)

def load_custom_games():
    # التشغيل يقرأ الألعاب المعتمدة فقط.
    data = load_json(CUSTOM_GAMES_PATH, {})
    return data if isinstance(data, dict) else {}

def save_custom_games(data):
    save_json(CUSTOM_GAMES_PATH, data)

def load_custom_commands():
    data = load_json(CUSTOM_COMMANDS_PATH, {})
    return data if isinstance(data, dict) else {}

def save_custom_commands(data):
    save_json(CUSTOM_COMMANDS_PATH, data)

def _command_key(text):
    return normalize_text(text).strip()[:80]

def add_custom_command_definition(command, response):
    key = _command_key(command)
    if not key:
        raise ValueError("اسم الأمر فارغ")
    if not response:
        raise ValueError("الرد فارغ")
    data = load_custom_commands()
    data[key] = {
        "command": key,
        "response": str(response).strip()[:2000],
        "enabled": True,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    save_custom_commands(data)
    return data[key]

def delete_custom_command_definition(command):
    key = _command_key(command)
    data = load_custom_commands()
    existed = data.pop(key, None)
    save_custom_commands(data)
    return existed

def render_custom_game_cover_sync(game):
    if not PIL_AVAILABLE:
        return None
    title = str(game.get("title") or game.get("command") or "لعبة")[:80]
    try:
        img = Image.new("RGB", (1000, 560), (22, 30, 45))
        draw = ImageDraw.Draw(img)
        font = None
        for fp in (str(BASE_DIR / "assets" / "Amiri-Bold.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            try:
                if Path(fp).is_file():
                    font = ImageFont.truetype(fp, 64)
                    break
            except Exception:
                pass
        font = font or ImageFont.load_default()
        shaped = shape_text(title)
        box = draw.textbbox((0,0), shaped, font=font)
        x=(1000-(box[2]-box[0]))//2
        draw.text((x+3,170+3), shaped, font=font, fill=(0,0,0))
        draw.text((x,170), shaped, font=font, fill=(255,255,255))
        sub=shape_text("🎮 لعبة جديدة — Giant Chat")
        box=draw.textbbox((0,0), sub, font=font)
        x=(1000-(box[2]-box[0]))//2
        draw.text((x,330), sub, font=font, fill=(220,230,240))
        outdir=BASE_DIR / "generated_games"; outdir.mkdir(exist_ok=True)
        path=outdir / f"cover_{_command_key(title).replace(' ','_')}_{uuid.uuid4().hex}.jpg"
        img.save(path, quality=90, optimize=True)
        return path
    except Exception as exc:
        log.warning("custom game cover failed: %s", exc)
        return None

def render_custom_game_result_sync(game, username, won):
    if not PIL_AVAILABLE:
        return None
    title = str(game.get("title") or game.get("command") or "لعبة")[:80]
    result = "🎉 تم الفوز!" if won else "😔 حظاً سعيداً"
    try:
        bg = (18, 25, 38) if won else (35, 38, 48)
        img = Image.new("RGB", (1000, 620), bg)
        draw = ImageDraw.Draw(img)
        font_big = None; font_mid = None
        for fp in (str(BASE_DIR / "assets" / "Amiri-Bold.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            try:
                if Path(fp).is_file():
                    font_big = ImageFont.truetype(fp, 58)
                    font_mid = ImageFont.truetype(fp, 38)
                    break
            except Exception:
                pass
        font_big = font_big or ImageFont.load_default()
        font_mid = font_mid or font_big
        def centered(text, y, font):
            shaped = shape_text(text)
            box = draw.textbbox((0,0), shaped, font=font)
            x = (1000 - (box[2]-box[0])) // 2
            draw.text((x+3,y+3), shaped, font=font, fill=(0,0,0))
            draw.text((x,y), shaped, font=font, fill=(255,255,255))
        centered(title, 100, font_big)
        centered(result, 245, font_mid)
        centered("الفائز: @" + str(username) if won else "اللاعب: @" + str(username), 340, font_mid)
        centered("Giant Chat", 500, font_mid)
        outdir = BASE_DIR / "generated_games"
        outdir.mkdir(exist_ok=True)
        path = outdir / f"result_{_command_key(title).replace(' ','_')}_{uuid.uuid4().hex}.jpg"
        img.save(path, quality=90, optimize=True)
        return path
    except Exception as exc:
        log.warning("custom game result image failed: %s", exc)
        return None

async def send_custom_game_result(rid, game, username, won):
    path = await asyncio.to_thread(render_custom_game_result_sync, game, username, won)
    if path:
        try:
            url = await _store_media(path, "game", "image/jpeg")
            await room_send_media(rid, "", url, m_type="image")
            await broadcast_media(
                f"🎮 {game.get('title', game.get('command','لعبة'))} — {'🎉 فاز' if won else '😔 لم يفز'} @{username}",
                url, m_type="image", exclude_rid=rid
            )
        except Exception:
            log.exception("failed to publish custom game result image")
        finally:
            try: path.unlink(missing_ok=True)
            except Exception: pass

async def execute_custom_command(rid, uid, username, text):
    key = _command_key(text)
    item = load_custom_commands().get(key)
    if not item or not item.get("enabled", True):
        return None
    response = str(item.get("response") or "").strip()
    response = response.replace("{user}", "@" + username).replace("{username}", username)
    response = response.replace("{room}", str(rooms.get(rid, rid)))
    if response.startswith("نشر:") or response.startswith("broadcast:"):
        payload = response.split(":",1)[1].strip()
        await broadcast_text(payload)
        return "✅ تم تنفيذ الأمر ونشره في جميع الغرف."
    if response.startswith("نقاط:"):
        try: amount=int(re.search(r"-?\d+", response).group(0))
        except Exception: amount=0
        add_points(uid, username, amount)
        return f"✅ تم تنفيذ الأمر. {'+' if amount >= 0 else ''}{amount} نقطة."
    if response.startswith("خاص:"):
        await dm_send(uid, response.split(":",1)[1].strip())
        return "✅ تم تنفيذ الأمر وإرسال الرد في الخاص."
    return response

def load_testing_games():
    data = load_json(TESTING_GAMES_PATH, {})
    return data if isinstance(data, dict) else {}

def save_testing_games(data):
    Path(TESTING_GAMES_PATH).parent.mkdir(parents=True, exist_ok=True)
    save_json(TESTING_GAMES_PATH, data)

def load_active_tests():
    data = load_json(TESTING_STATE_PATH, {})
    return data if isinstance(data, dict) else {}

def save_active_tests(data):
    Path(TESTING_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    save_json(TESTING_STATE_PATH, data)

def activate_test_game(command):
    key = normalize_text(command).strip()
    testing = load_testing_games()
    if key not in testing:
        return False, f"❌ لا توجد لعبة اختبار باسم «{key}»."
    active = load_active_tests()
    active[key] = {"enabled": True, "activated_at": now_iso()}
    save_active_tests(active)
    title = testing[key].get("title", key)
    return True, f"🧪 تم تشغيل اختبار «{title}».\n🎮 اكتب «{key}» داخل أي غرفة لتجربتها.\n🔐 الاختبار متاح لصاحب البوت فقط."

def deactivate_test_game(command):
    key = normalize_text(command).strip()
    active = load_active_tests()
    if key not in active:
        return False, f"ℹ️ لعبة «{key}» ليست في وضع الاختبار."
    active.pop(key, None)
    save_active_tests(active)
    return True, f"🛑 تم إيقاف اختبار «{key}»."

def approve_testing_game(command):
    testing = load_testing_games()
    key = str(command).strip().lower()
    item = testing.get(key)
    if not item:
        return False, f"❌ لا توجد لعبة اختبار باسم {key}."
    approved = load_custom_games()
    approved[key] = item
    save_custom_games(approved)
    testing.pop(key, None)
    save_testing_games(testing)
    return True, f"✅ تم اعتماد اللعبة «{item.get('title', key)}» ونقلها من testing إلى approved."


def _safe_ai_text(text, limit=12000):
    text = str(text or "")
    text = re.sub(r"(?i)(youtube_cookies(?:_\d+)?|authorization|api[_-]?key|bearer)\s*[:=]\s*[^\s]+", r"\1=[REDACTED]", text)
    return text[-limit:]

def _tail_log(lines=120):
    path = Path("logs/bot.log")
    if not path.is_file():
        return "لا يوجد ملف سجل حالياً."
    try:
        data = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(data[-lines:])
    except Exception as exc:
        return f"تعذر قراءة السجل: {type(exc).__name__}: {exc}"

async def _download_local_ai_model():
    """تنزيل نموذج GGUF مرة واحدة إلى مساحة Railway المحلية."""
    if LOCAL_AI_MODEL_PATH.is_file() and LOCAL_AI_MODEL_PATH.stat().st_size > 50 * 1024 * 1024:
        return True, None

    async with LOCAL_AI_DOWNLOAD_LOCK:
        if LOCAL_AI_MODEL_PATH.is_file() and LOCAL_AI_MODEL_PATH.stat().st_size > 50 * 1024 * 1024:
            return True, None
        LOCAL_AI_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOCAL_AI_MODEL_PATH.with_suffix(LOCAL_AI_MODEL_PATH.suffix + ".part")
        try:
            log.info("تنزيل نموذج الذكاء المحلي إلى %s", LOCAL_AI_MODEL_PATH)
            async with http.get(
                LOCAL_AI_MODEL_URL,
                timeout=aiohttp.ClientTimeout(total=1800),
                headers={"User-Agent": "GiantChat-LocalAI/1.0"},
            ) as resp:
                if resp.status != 200:
                    return False, f"❌ تعذر تنزيل نموذج الذكاء المحلي: HTTP {resp.status}"
                total = int(resp.headers.get("Content-Length") or 0)
                written = 0
                with tmp.open("wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)
                if written < 50 * 1024 * 1024:
                    tmp.unlink(missing_ok=True)
                    return False, "❌ ملف نموذج الذكاء المحلي ناقص أو تالف."
                tmp.replace(LOCAL_AI_MODEL_PATH)
                log.info("تم تنزيل نموذج الذكاء المحلي: %.1f MB%s",
                         written / 1024 / 1024,
                         f" / المتوقع {total / 1024 / 1024:.1f} MB" if total else "")
                return True, None
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            log.exception("local AI model download failed")
            return False, f"❌ فشل تنزيل نموذج الذكاء المحلي: {type(exc).__name__}: {exc}"


async def _load_local_ai():
    """تحميل نموذج GGUF في الذاكرة عند الحاجة فقط."""
    global LOCAL_AI_MODEL, LOCAL_AI_LOAD_ERROR
    if LOCAL_AI_MODEL is not None:
        return LOCAL_AI_MODEL, None
    if LOCAL_AI_LOAD_ERROR:
        return None, LOCAL_AI_LOAD_ERROR

    async with LOCAL_AI_LOAD_LOCK:
        if LOCAL_AI_MODEL is not None:
            return LOCAL_AI_MODEL, None
        if LOCAL_AI_LOAD_ERROR:
            return None, LOCAL_AI_LOAD_ERROR
        if not LOCAL_LLAMACPP_AVAILABLE:
            LOCAL_AI_LOAD_ERROR = "❌ مكتبة llama-cpp-python غير مثبتة. أضفها إلى requirements.txt ثم أعد Deploy."
            return None, LOCAL_AI_LOAD_ERROR

        ok, err = await _download_local_ai_model()
        if not ok:
            LOCAL_AI_LOAD_ERROR = err or "❌ تعذر تجهيز نموذج الذكاء المحلي."
            return None, LOCAL_AI_LOAD_ERROR

        try:
            LOCAL_AI_MODEL = await asyncio.to_thread(
                lambda: Llama(
                    model_path=str(LOCAL_AI_MODEL_PATH),
                    n_ctx=LOCAL_AI_CTX,
                    n_threads=LOCAL_AI_THREADS,
                    verbose=False,
                )
            )
            log.info("تم تحميل نموذج الذكاء المحلي Qwen GGUF بنجاح.")
            return LOCAL_AI_MODEL, None
        except Exception as exc:
            LOCAL_AI_LOAD_ERROR = f"❌ تعذر تحميل نموذج GGUF: {type(exc).__name__}: {exc}"
            log.exception("local AI model load failed")
            return None, LOCAL_AI_LOAD_ERROR


def local_ai_status_text():
    if LOCAL_AI_MODEL is not None:
        return "✅ الذكاء الاصطناعي المحلي يعمل بدون API."
    if not LOCAL_LLAMACPP_AVAILABLE:
        return "⚠️ الذكاء المحلي يحتاج llama-cpp-python. أعد Deploy بعد تثبيت المتطلبات."
    if LOCAL_AI_MODEL_PATH.is_file():
        return "🟡 نموذج الذكاء المحلي موجود وسيتم تحميله عند أول طلب."
    return "🟡 الذكاء المحلي جاهز للتنزيل عند أول طلب."


async def ai_response(prompt, max_output=2500):
    """توليد النص محلياً باستخدام نموذج GGUF؛ لا يوجد اتصال بخدمة ذكاء خارجية."""
    model, err = await _load_local_ai()
    if err:
        return None, err
    system = (
        "أنت مساعد صيانة وتشغيل لبوت Giant Chat مكتوب بلغة Python. "
        "أجب بالعربية بوضوح واختصار. لا تطلب كلمات مرور أو Cookies أو مفاتيح سرية. "
        "إذا كان السؤال عن البوت، اعتمد على المعلومات الموجودة في الطلب فقط."
    )
    try:
        def generate():
            result = model.create_chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": str(prompt)[:12000]},
                ],
                max_tokens=min(int(max_output), LOCAL_AI_MAX_TOKENS),
                temperature=0.25,
            )
            return ((result.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        text = await asyncio.to_thread(generate)
        return (text, None) if text else (None, "❌ نموذج الذكاء المحلي لم يُرجع نصاً.")
    except Exception as exc:
        log.exception("local AI generation failed")
        return None, f"❌ تعذر تشغيل الذكاء الاصطناعي المحلي: {type(exc).__name__}: {exc}"

async def ai_diagnose(problem=""):
    report = {
        "python": sys.version.split()[0],
        "yt_dlp": getattr(yt_dlp, "version", None) if yt_dlp else "غير مثبت",
        "pillow": PIL_AVAILABLE,
        "youtube_cookie_sets": len(YOUTUBE_COOKIE_FILES),
        "rooms": len(rooms),
        "custom_games": len(load_custom_games()),
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }
    prompt = f"""أنت مهندس صيانة لبوت Python يعمل على Railway. لا تقترح استخراج كلمات مرور أو Cookies أو أسرار. حلل المشكلة واقترح إصلاحات آمنة وقابلة للتنفيذ.
المشكلة التي ذكرها المالك: {_safe_ai_text(problem, 1200) or 'افحص حالة البوت بالكامل'}
حالة التشغيل: {json.dumps(report, ensure_ascii=False)}
آخر السجل:
{_safe_ai_text(_tail_log(AI_MAX_LOG_LINES), 10000)}
أعد الرد بالعربية بهذا الترتيب: التشخيص، السبب المحتمل، الأمر الذي يجب تنفيذه، وطريقة التحقق. لا تطبع أي سر أو قيمة Cookie أو API key."""
    return await ai_response(prompt, 2200)

async def run_repair_check(kind):
    kind = normalize_text(kind)
    if kind in ("فحص", "check", "عام", "all"):
        compile_ok = True
        compile_error = ""
        try:
            compile(Path(__file__).read_text(encoding="utf-8"), str(Path(__file__)))
        except Exception as exc:
            compile_ok = False; compile_error = f"{type(exc).__name__}: {exc}"
        return ("🧪 فحص البوت\n"
                f"• Python syntax: {'✅' if compile_ok else '❌ ' + compile_error}\n"
                f"• yt-dlp: {'✅' if yt_dlp else '❌ غير مثبت'}\n"
                f"• FFmpeg: {'✅' if shutil.which('ffmpeg') else '❌ غير موجود'}\n"
                f"• Pillow: {'✅' if PIL_AVAILABLE else '❌ غير مثبت'}\n"
                f"• YouTube cookie sets: {len(YOUTUBE_COOKIE_FILES)}\n"
                f"• الألعاب المضافة بالذكاء: {len(load_custom_games())}\n"
                f"• الغرف الحالية: {len(rooms)}")
    if kind in ("موسيقى", "music", "اغاني"):
        checks = []
        checks.append(f"yt-dlp: {'OK' if yt_dlp else 'MISSING'}")
        checks.append(f"ffmpeg: {'OK' if shutil.which('ffmpeg') else 'MISSING'}")
        checks.append(f"YouTube sessions: {len(YOUTUBE_COOKIE_FILES)}")
        return "🎵 فحص الموسيقى\n• " + "\n• ".join(checks) + "\n💡 إذا كان البحث يفشل أرسل: اصلاح ذكي مشكلة الموسيقى"
    if kind in ("العاب", "ألعاب", "games"):
        asset_dir = BASE_DIR / "assets"
        imgs = list(asset_dir.glob("game_*.jpg")) + list(asset_dir.glob("game_*.png")) if asset_dir.is_dir() else []
        return f"🎮 فحص الألعاب\n• صور الألعاب المحلية: {len(imgs)}\n• الألعاب المضافة بالذكاء: {len(load_custom_games())}\n• Pillow: {'✅' if PIL_AVAILABLE else '❌'}"
    if kind in ("صور", "صورة", "images"):
        asset_dir = BASE_DIR / "assets"
        imgs = [x for x in asset_dir.iterdir() if x.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")] if asset_dir.is_dir() else []
        return f"🖼️ فحص الصور\n• ملفات الصور: {len(imgs)}\n• Pillow: {'✅' if PIL_AVAILABLE else '❌'}\n• رابط Railway العام: {'✅' if PUBLIC_BASE_URL else '⚠️ غير مضبوط'}"
    if kind in ("سجل", "logs", "log"):
        return "📋 آخر السجل:\n" + _safe_ai_text(_tail_log(80), 7000)
    return "❌ الأمر غير معروف. اكتب: اصلاح"

async def generate_ai_image(prompt, filename_prefix="ai"):
    """إنشاء بطاقة محلية بسيطة من الوصف، بدون خدمة صور خارجية."""
    if not PIL_AVAILABLE:
        return None, "❌ Pillow غير مثبت لإنشاء البطاقة المحلية."
    try:
        outdir = BASE_DIR / "generated_ai"
        outdir.mkdir(exist_ok=True)
        path = outdir / f"{filename_prefix}_{uuid.uuid4().hex}.jpg"

        def render():
            img = Image.new("RGB", (900, 560), (245, 247, 250))
            draw = ImageDraw.Draw(img)
            font = None
            for fp in (
                str(BASE_DIR / "assets" / "Amiri-Bold.ttf"),
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ):
                try:
                    if Path(fp).is_file():
                        font = ImageFont.truetype(fp, 34)
                        break
                except Exception:
                    pass
            font = font or ImageFont.load_default()
            draw.text((450, 90), "Giant Chat — AI محلي", anchor="mm", font=font, fill=(20, 20, 20))
            draw.multiline_text((450, 280), str(prompt or "تصميم محلي")[:500],
                                anchor="mm", align="center", font=font, fill=(40, 40, 40), spacing=12)
            img.save(path, quality=90, optimize=True)
        await asyncio.to_thread(render)
        return path, None
    except Exception as exc:
        return None, f"❌ تعذر إنشاء البطاقة المحلية: {type(exc).__name__}: {exc}"


# ----------------------------- مصمم الألعاب الذكي -----------------------------
def load_game_design_state():
    data = load_json(GAME_DESIGN_PATH, {})
    return data if isinstance(data, dict) else {}

def save_game_design_state(data):
    Path(GAME_DESIGN_PATH).parent.mkdir(parents=True, exist_ok=True)
    save_json(GAME_DESIGN_PATH, data)

def clear_game_design(uid):
    data = load_game_design_state(); data.pop(str(uid), None); save_game_design_state(data)

def get_game_design(uid): return load_game_design_state().get(str(uid))
def set_game_design(uid, state):
    data=load_game_design_state(); data[str(uid)]=state; save_game_design_state(data)

GAME_DESIGN_CATEGORIES={"1":"حظ وجوائز","2":"تحدي سرعة","3":"منافسة بين لاعبين","4":"تخمين وأسئلة","5":"لعبة جماعية","6":"كلمات وذكاء","7":"مغامرة ومفاجآت"}

async def brainstorm_game_ideas(theme):
    prompt=("أنت مصمم ألعاب لبوت دردشة عربي اسمه Giant Chat. "
            "اقترح 3 أفكار ألعاب مختلفة وقابلة للتنفيذ في غرفة دردشة. "
            f"التصنيف: {theme}. أعد JSON فقط بهذا الشكل: "
            '{"ideas":[{"name":"...","summary":"...","players":"...","core":"..."}]}')
    raw, err = await ai_response(prompt, 900)
    if err:
        return [{"name":"تحدي الحظ","summary":"نتيجة عشوائية مع جائزة.","players":"1","core":"احتمال فوز"},
                {"name":"المواجهة","summary":"لاعبان يتنافسان.","players":"2","core":"مواجهة ثم فائز"},
                {"name":"صندوق الأسرار","summary":"اختيار صندوق بمفاجأة.","players":"1","core":"صناديق وجائزة"}]
    try:
        m=re.search(r'\{.*\}', raw, re.S); obj=json.loads(m.group(0) if m else raw)
        out=[]
        for x in (obj.get('ideas') or [])[:3]:
            if isinstance(x,dict): out.append({"name":str(x.get("name") or "لعبة جديدة")[:60],"summary":str(x.get("summary") or "")[:300],"players":str(x.get("players") or "1")[:30],"core":str(x.get("core") or "")[:300]})
        return out or [{"name":"تحدي جديد","summary":"لعبة دردشة بسيطة.","players":"1","core":"نتيجة عشوائية"}]
    except Exception: return [{"name":"تحدي جديد","summary":"لعبة دردشة بسيطة.","players":"1","core":"نتيجة عشوائية"}]

async def make_game_spec_from_design(state):
    idea=state.get("idea") or {}; theme=state.get("theme") or "متنوعة"
    prompt=("حوّل فكرة اللعبة إلى تعريف جاهز لبوت Giant Chat. "
            f"الفكرة: {idea.get('name')} — {idea.get('summary')} — {idea.get('core')}. "
            f"التصنيف: {theme}. تفاصيل الماستر: {state.get('details','')}. "
            "أعد JSON فقط بالمفاتيح command,title,win_chance,win_points,lose_points,win_message,lose_message,image_prompt. "
            "command كلمة عربية قصيرة بدون مسافات. win_chance رقم 1-100. لا تستخدم كود أو HTML.")
    raw, err=await ai_response(prompt,900)
    if err:
        name=idea.get('name') or 'لعبة جديدة'
        return {"command":re.sub(r"[^\w\u0600-\u06ff-]","",name.replace(" ",""))[:24] or "لعبة","title":name,"win_chance":50,"win_points":50,"lose_points":0,"win_message":"🎉 فزت!","lose_message":"😔 حظاً سعيداً، حاول مرة أخرى.","image_prompt":f"غلاف لعبة {name} في Giant Chat"}
    try:
        m=re.search(r'\{.*\}',raw,re.S); obj=json.loads(m.group(0) if m else raw)
        def aiint(v,d,lo=None,hi=None):
            try:
                if isinstance(v,(int,float)): n=int(v)
                else:
                    mm=re.search(r'-?\d+',str(v or '')); n=int(mm.group(0)) if mm else d
            except Exception: n=d
            if lo is not None: n=max(lo,n)
            if hi is not None: n=min(hi,n)
            return n
        return {"command":re.sub(r"[^\w\u0600-\u06ff-]","",str(obj.get("command") or idea.get("name") or "لعبة").replace(" ",""))[:24].lower(),"title":str(obj.get("title") or idea.get("name") or "لعبة جديدة")[:80],"win_chance":aiint(obj.get("win_chance"),50,1,100),"win_points":aiint(obj.get("win_points"),50,-1000000,1000000),"lose_points":aiint(obj.get("lose_points"),0,-1000000,1000000),"win_message":str(obj.get("win_message") or "🎉 فوز!")[:200],"lose_message":str(obj.get("lose_message") or "😔 حظاً سعيداً")[:200],"image_prompt":str(obj.get("image_prompt") or "غلاف لعبة جديدة")[:1000]}
    except Exception:
        name=idea.get('name') or 'لعبة جديدة'
        return {"command":re.sub(r"[^\w\u0600-\u06ff-]","",name.replace(" ",""))[:24] or "لعبة","title":name,"win_chance":50,"win_points":50,"lose_points":0,"win_message":"🎉 فوز!","lose_message":"😔 حظاً سعيداً","image_prompt":f"غلاف لعبة {name}"}

async def finalize_designed_game(uid,spec):
    command=spec.get("command") or "لعبة"; existing={}; existing.update(load_testing_games()); existing.update(load_custom_games()); base=command; n=2
    while command in existing: command=(base+str(n))[:24]; n+=1
    spec["command"]=command; spec["status"]="testing"; spec["created_at"]=now_iso(); spec["designer"]="master"
    try:
        cover=await asyncio.to_thread(render_custom_game_cover_sync,spec)
        if cover: spec["image_url"]=await _store_media(cover,"game","image/jpeg"); cover.unlink(missing_ok=True)
    except Exception as exc: spec["image_error"]=f"{type(exc).__name__}: {exc}"[:500]
    testing=load_testing_games(); testing[command]=spec; save_testing_games(testing); clear_game_design(uid)
    return (f"🧪 تم إنشاء اللعبة «{spec['title']}» في بيئة الاختبار.\n🎮 الأمر: {command}\n"
            f"🎯 الفوز: {spec['win_chance']}% | 💰 الجائزة: {spec['win_points']} نقطة\n"
            f"🖼️ الصورة: {'✅ جاهزة' if spec.get('image_url') else '⚠️ لم ترفع'}\n"
            f"➡️ تشغيل الاختبار: تشغيل اختبار@{command}\n➡️ الاعتماد بعد النجاح: اعتماد لعبة {command}")

async def handle_game_designer(sender,text):
    low=normalize_text(text); state=get_game_design(sender)
    if low in ("الغاء تصميم اللعبة","إلغاء تصميم اللعبة","الغاء اللعبة الجديدة","إلغاء"):
        if state: clear_game_design(sender); return "🛑 تم إلغاء جلسة تصميم اللعبة."
        return None
    if not state and low in ("اخترع لعبة جديدة","إخترع لعبة جديدة","صمم لعبة جديدة","فكر معي لعبة","ابتكر لعبة جديدة"):
        set_game_design(sender,{"stage":"category","created_at":now_iso()})
        return "🧠 لنبتكر لعبة جديدة معاً. اختر النوع:\n1️⃣ حظ وجوائز\n2️⃣ تحدي سرعة\n3️⃣ منافسة بين لاعبين\n4️⃣ تخمين وأسئلة\n5️⃣ لعبة جماعية\n6️⃣ كلمات وذكاء\n7️⃣ مغامرة ومفاجآت\n💡 أو اكتب فكرتك مباشرة."
    if not state: return None
    stage=state.get("stage")
    if stage=="category":
        theme=GAME_DESIGN_CATEGORIES.get(low.strip(),str(text).strip()[:100]); ideas=await brainstorm_game_ideas(theme); state.update({"stage":"idea","theme":theme,"ideas":ideas}); set_game_design(sender,state)
        return "🧠 التصنيف: "+theme+"\n"+"\n".join(f"{i}️⃣ {x['name']} — {x['summary']}" for i,x in enumerate(ideas,1))+"\n✏️ أو اكتب فكرتك بنفسك."
    if stage=="idea":
        ideas=state.get("ideas") or []; chosen=ideas[int(low)-1] if low.isdigit() and 1<=int(low)<=len(ideas) else {"name":str(text).strip()[:60],"summary":"فكرة الماستر","players":"1","core":str(text).strip()[:300]}
        state.update({"stage":"details","idea":chosen}); set_game_design(sender,state)
        return f"🎮 الفكرة: {chosen['name']}\n📝 {chosen['summary']}\n\nأعطني التفاصيل: عدد اللاعبين، المهلة، الجائزة، طريقة الفوز... أو اكتب «نفذها»."
    if stage=="details":
        state["details"]="إعدادات مناسبة ومتوازنة." if low in ("نفذها","نفذ","أنشئها","انشئها") else str(text).strip()[:1000]
        spec=await make_game_spec_from_design(state); state.update({"stage":"confirm","draft":spec}); set_game_design(sender,state)
        return (f"📝 مسودة اللعبة:\n🎮 {spec['title']}\n🔤 الأمر: {spec['command']}\n🎯 الفوز: {spec['win_chance']}%\n💰 الجائزة: {spec['win_points']} نقطة\n📉 الخسارة: {spec['lose_points']} نقطة\n\n✅ اكتب «اعتمد التصميم» لإنشائها في testing.\n✏️ اكتب «عدل ...» للتعديل.\n🛑 الغاء تصميم اللعبة للإلغاء.")
    if stage=="confirm":
        if low in ("اعتمد التصميم","اعتمد اللعبة","نفذها","نفذ","انشئ اللعبة","أنشئ اللعبة"): return await finalize_designed_game(sender,state.get("draft") or {})
        if low.startswith("عدل "):
            state["stage"]="details"; state["details"]=text.split(None,1)[1].strip()[:1000]; set_game_design(sender,state); return "✏️ تم تسجيل التعديل. أرسل «نفذها»."
        return "⏳ أنت في مرحلة المراجعة. اكتب «اعتمد التصميم» أو «عدل ...»."
    return None

async def add_ai_game(uid, description):
    if not description:
        return "❌ الصيغة: اضف لعبة اسم_اللعبة | وصف اللعبة"
    parts = [x.strip() for x in description.split("|", 1)]
    name = parts[0][:40]
    desc = parts[1][:800] if len(parts) > 1 else name

    # منع إنشاء نفس اللعبة مرة أخرى كلما أرسل الماستر أمر الإضافة.
    requested_key = re.sub(r"[^\w\u0600-\u06ff-]", "", name.replace(" ", ""))[:24].lower()
    existing = {}
    existing.update(load_testing_games())
    existing.update(load_custom_games())
    if requested_key and requested_key in existing:
        old_game = existing[requested_key]
        return (f"ℹ️ اللعبة «{old_game.get('title', name)}» موجودة بالفعل.\n"
                f"🎮 الأمر: {requested_key}\n"
                f"📌 الحالة: {old_game.get('status', 'موجودة')}\n"
                "🚫 لن أنشئ نسخة ثانية ولن أكرر إضافة الجوائز.")

    prompt = f"""أنشئ تعريف لعبة نصية بسيطة وآمنة لبوت دردشة. اسم اللعبة: {name}. الوصف: {desc}. أعد JSON فقط بالمفاتيح: command,title,win_chance,win_points,lose_points,win_message,lose_message,image_prompt. command كلمة عربية قصيرة بدون مسافات. win_chance رقم 1-100 (لا تجعله 100 إلا إذا طلب المالك ذلك صراحة)، والنقاط أرقام صحيحة. لا تضع HTML أو كود Python أو أوامر نظام."""
    raw, err = await ai_response(prompt, 900)
    # إذا تعذر تحميل الذكاء المحلي، لا يتوقف نظام إضافة الألعاب؛ نستخدم تعريفاً آمناً افتراضياً.
    if err:
        raw = json.dumps({
            "command": re.sub(r"[^\w\u0600-\u06ff-]", "", name.replace(" ", ""))[:24] or "لعبة",
            "title": name, "win_chance": 50, "win_points": 20, "lose_points": -5,
            "win_message": "🎉 تم الفوز!", "lose_message": "😔 حظاً سعيداً، جرب مرة أخرى.",
            "image_prompt": f"بطاقة لعبة {name} في Giant Chat"
        }, ensure_ascii=False)
    try:
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        command = re.sub(r"[^\w\u0600-\u06ff-]", "", str(data.get("command") or name.replace(" ", "")))[:24].lower()
        if not command: return "❌ لم يتم توليد أمر صالح للعبة."
        def _ai_int(value, default, minimum=None, maximum=None):
            if isinstance(value, bool): num = int(value)
            elif isinstance(value, (int, float)): num = int(value)
            else:
                s = str(value or "").strip().replace("٪", "%")
                m = re.search(r"-?\d+(?:[.,]\d+)?", s)
                try: num = int(float(m.group(0).replace(",", "."))) if m else default
                except (TypeError, ValueError): num = default
            if minimum is not None: num = max(minimum, num)
            if maximum is not None: num = min(maximum, num)
            return num
        data = {
            "command": command, "title": str(data.get("title") or name)[:80],
            "win_chance": _ai_int(data.get("win_chance", 50), 50, 1, 100),
            "win_points": _ai_int(data.get("win_points", 20), 20, -1000000, 1000000),
            "lose_points": _ai_int(data.get("lose_points", -5), -5, -1000000, 1000000),
            "win_message": str(data.get("win_message") or "🎉 فوز!")[:200],
            "lose_message": str(data.get("lose_message") or "😅 خسارة!")[:200],
            "image_prompt": str(data.get("image_prompt") or f"بطاقة لعبة {name} في Giant Chat")[:1000],
        }

        # لعبة «مليون»: المليون جائزة الفوز فقط، وليس جائزة مضمونة كل مرة.
        # النتيجة عشوائية بنسبة 50% ما لم يطلب الماستر نسبة أخرى صراحة.
        if normalize_text(name) == "مليون" or command == "مليون":
            data["title"] = "مليون"
            data["win_chance"] = 50
            data["win_points"] = 1000000
            data["lose_points"] = 0
            data["win_message"] = "🎉 تم الحصول على مليون!"
            data["lose_message"] = "😔 حظًا سعيدًا، جرب في المرة القادمة."
            data["image_prompt"] = "بطاقة لعبة مليون فاخرة، رقم 1,000,000، أسلوب ألعاب دردشة، بدون كتابة اسم اللاعب"
        # ضع اللعبة في بيئة الاختبار فقط، وليس custom_games.json.
        testing = load_testing_games()
        data["status"] = "testing"
        data["created_at"] = now_iso()
        # صورة غلاف خاصة باللعبة تُحفظ في public game storage.
        try:
            cover = await asyncio.to_thread(render_custom_game_cover_sync, data)
            if cover:
                data["image_url"] = await _store_media(cover, "game", "image/jpeg")
                cover.unlink(missing_ok=True)
        except Exception as cover_exc:
            # لا نفشل إنشاء اللعبة بسبب الصورة، لكن نسجل السبب لكي يمكن إصلاحه.
            data["image_error"] = f"{type(cover_exc).__name__}: {cover_exc}"[:500]
            log.warning("game cover creation skipped: %s", cover_exc)
        testing[command] = data
        save_testing_games(testing)
        return (f"🧪 تم إنشاء اللعبة «{data['title']}» في بيئة الاختبار.\n"
                f"🎮 الأمر: {command}\n"
                f"🧪 الحالة: testing\n"
                f"🖼️ صورة اللعبة: {'✅ جاهزة' if data.get('image_url') else '⚠️ لم تُرفع — راجع PUBLIC_BASE_URL/Storage'}\n"
                f"➡️ لتجربتها: تشغيل اختبار@{command}\n"
                f"➡️ بعد نجاح الاختبار: اعتماد لعبة {command}\n"
                f"ℹ️ لا تعمل في الغرف قبل تشغيل وضع الاختبار أو اعتمادها.")
    except Exception as exc:
        private_error = ("⚠️ خطأ في اعتماد لعبة بالذكاء الاصطناعي\n"
                         f"النوع: {type(exc).__name__}\nالتفاصيل: {exc}\nاسم اللعبة: {name}")
        try: await dm_send(uid, private_error)
        except Exception: log.exception("تعذر إرسال خطأ إنشاء اللعبة إلى المالك في الخاص")
        return "❌ تعذر اعتماد تعريف اللعبة من الذكاء الاصطناعي. تم إرسال تفاصيل الخطأ إلى المالك في الخاص."

def _repair_json_file(path, default):
    p = Path(path)
    if not p.exists():
        save_json(str(p), default)
        return "created"
    try:
        with p.open("r", encoding="utf-8") as f:
            json.load(f)
        return "ok"
    except Exception:
        try:
            backup = p.with_suffix(p.suffix + f".broken.{int(time.time())}")
            shutil.copy2(p, backup)
        except Exception:
            pass
        save_json(str(p), default)
        return "repaired"

def self_repair_sync():
    results=[]
    for path, default in [
        (CONFIG_PATH, dict(C)), (POINTS_PATH, {}), (REPLIES_PATH, {}), (MASTERS_PATH, []),
        (BANS_PATH, {}), (ROOMS_PATH, {}), (MODERATION_PATH, {"enabled":{},"words":[]}),
        (WELCOME_PATH, {}), (PUBLISHED_POSTS_PATH, {}), (SOCIAL_EVENTS_PATH, {}),
        (VIP_USERS_PATH, {}), (CUSTOM_GAMES_PATH, {}), (CUSTOM_COMMANDS_PATH, {}),
        (TESTING_GAMES_PATH, {}), (TESTING_STATE_PATH, {}),
    ]:
        try:
            state=_repair_json_file(path, default)
            if state != "ok": results.append(f"{Path(path).name}: {state}")
        except Exception as exc:
            results.append(f"{Path(path).name}: failed {type(exc).__name__}")
    for d in ["logs", "generated_games", "generated_music", "published_media", "games/testing", "games/approved"]:
        try:
            Path(BASE_DIR / d).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            results.append(f"dir {d}: failed {type(exc).__name__}")
    active=load_active_tests(); testing=load_testing_games()
    stale=[k for k in active if k not in testing]
    if stale:
        for k in stale: active.pop(k,None)
        save_active_tests(active); results.append("removed stale tests")
    compile_ok=True; compile_error=""
    try:
        compile(Path(__file__).read_text(encoding="utf-8"), str(Path(__file__)))
    except Exception as exc:
        compile_ok=False; compile_error=f"{type(exc).__name__}: {exc}"
    return results, compile_ok, compile_error

async def delete_game_definition(command):
    key=normalize_text(command).strip()
    removed=[]
    testing=load_testing_games(); active=load_active_tests(); approved=load_custom_games()
    if key in testing:
        testing.pop(key,None); removed.append("testing")
    if key in active:
        active.pop(key,None); removed.append("active")
    if key in approved:
        approved.pop(key,None); removed.append("approved")
    save_testing_games(testing); save_active_tests(active); save_custom_games(approved)
    try:
        Path(APPROVED_GAMES_DIR, f"{key}.json").unlink(missing_ok=True)
    except Exception: pass
    return removed

async def handle_ai_dm(sender, text):
    if (await username_of(sender)).lower() != OWNER:
        return "🚫 نظام الصيانة والذكاء الاصطناعي متاح لصاحب البوت فقط."
    low = normalize_text(text)
    designer_reply = await handle_game_designer(sender, text)
    if designer_reply is not None:
        return designer_reply

    if low in ("اصلاح ذاتيا", "إصلاح ذاتياً", "إصلاح ذاتيا", "اصلاح البوت", "إصلاح البوت", "صيانة ذاتية"):
        results, compile_ok, compile_error = await asyncio.to_thread(self_repair_sync)
        msg = "🛠️ الإصلاح الذاتي اكتمل.\n"
        msg += "• Python: " + ("✅ سليم" if compile_ok else "❌ " + compile_error) + "\n"
        msg += "• البيانات/المجلدات: " + ("لا تحتاج إصلاحاً." if not results else "\n  • " + "\n  • ".join(results))
        return msg

    if low in ("حالة البوت", "حاله البوت", "bot status", "status"):
        age = int(max(0, time.time() - BOT_STARTED_AT))
        hb = int(max(0, time.time() - LAST_HEARTBEAT_AT)) if LAST_HEARTBEAT_AT else None
        return (f"🤖 حالة البوت الآن\n🟢 العملية: تعمل\n🌐 الشبكة: {'🟢 متصلة' if NETWORK_ONLINE else '🔴 منقطعة'}\n"
                f"🏠 الغرف: {len(rooms)}\n💓 آخر heartbeat: {hb if hb is not None else '—'} ثانية\n⏱️ مدة التشغيل: {age} ثانية")

    if low in ("الاوامر المضافة", "الأوامر المضافة", "اوامر مضافة", "custom commands"):
        cmds=load_custom_commands()
        if not cmds: return "📭 لا توجد أوامر مضافة حالياً."
        return "🧩 الأوامر المضافة:\n" + "\n".join(f"• {k} — {'🟢' if v.get('enabled',True) else '🔴'} {v.get('response','')[:80]}" for k,v in cmds.items())

    if low.startswith("اضف امر ") or low.startswith("أضف أمر ") or low.startswith("اضف أمر "):
        body=text.split(None,2)[2].strip() if len(text.split(None,2))>2 else ""
        parts=[x.strip() for x in body.split("|",1)]
        if len(parts)!=2: return "❌ الصيغة: اضف امر اسم_الامر | الرد\n💡 يدعم {user} و{username} و{room}."
        try:
            item=add_custom_command_definition(parts[0], parts[1])
            return f"✅ تمت إضافة الأمر «{item['command']}».\n🎯 عند كتابته في الغرفة سينفذ الرد مباشرة."
        except Exception as exc:
            return f"❌ تعذر إضافة الأمر: {exc}"

    if low.startswith("حذف امر ") or low.startswith("احذف امر ") or low.startswith("حذف أمر ") or low.startswith("احذف أمر "):
        body=text.split(None,2)[2].strip() if len(text.split(None,2))>2 else ""
        removed=delete_custom_command_definition(body)
        return f"🗑️ تم حذف الأمر «{_command_key(body)}»." if removed else f"ℹ️ لا يوجد أمر مضاف باسم «{_command_key(body)}»."

    if low.startswith("تعطيل امر ") or low.startswith("تعطيل أمر "):
        body=text.split(None,2)[2].strip() if len(text.split(None,2))>2 else ""
        key=_command_key(body); cmds=load_custom_commands()
        if key not in cmds: return f"❌ الأمر «{key}» غير موجود."
        cmds[key]["enabled"]=False; cmds[key]["updated_at"]=now_iso(); save_custom_commands(cmds)
        return f"⏸️ تم تعطيل الأمر «{key}»."

    if low.startswith("تفعيل امر ") or low.startswith("تفعيل أمر "):
        body=text.split(None,2)[2].strip() if len(text.split(None,2))>2 else ""
        key=_command_key(body); cmds=load_custom_commands()
        if key not in cmds: return f"❌ الأمر «{key}» غير موجود."
        cmds[key]["enabled"]=True; cmds[key]["updated_at"]=now_iso(); save_custom_commands(cmds)
        return f"▶️ تم تفعيل الأمر «{key}»."

    if low.startswith("حذف لعبة ") or low.startswith("احذف لعبة ") or low.startswith("حذف لعبه ") or low.startswith("احذف لعبه "):
        body=text.split(None,2)[2].strip() if len(text.split(None,2))>2 else ""
        removed=await delete_game_definition(body)
        return f"🗑️ تم حذف اللعبة «{normalize_text(body)}» من: {', '.join(removed)}." if removed else f"ℹ️ اللعبة «{normalize_text(body)}» غير موجودة."

    if low in ("اصلاح", "إصلاح", "ai", "ذكاء"):
        return ("🛠️ مركز إصلاح البوت بالذكاء الاصطناعي\n"
                "━━━━━━━━━━━━━━\n"
                "1️⃣ اصلاح فحص — فحص Python وFFmpeg وYouTube والصور\n"
                "2️⃣ اصلاح موسيقى — فحص مكونات الموسيقى\n"
                "3️⃣ اصلاح العاب — فحص الألعاب والصور\n"
                "4️⃣ اصلاح صور — فحص نظام الصور والرابط العام\n"
                "5️⃣ اصلاح سجل — عرض آخر أخطاء السجل\n"
                "6️⃣ اصلاح ذكي مشكلة — تحليل المشكلة بالذكاء الاصطناعي\n"
                "7️⃣ صمم وصف — إنشاء صورة بالذكاء الاصطناعي وإضافتها للوسائط\n"
                "8️⃣ اضف لعبة اسم | وصف — إنشاء لعبة في testing\n"                "🧠 اخترع لعبة جديدة — ابتكار لعبة مع الماستر خطوة بخطوة\n"                "✏️ عدّل التصميم — تعديل مسودة اللعبة\n"                "🛑 الغاء تصميم اللعبة — إلغاء جلسة التصميم\n"
                "9️⃣ اعتماد لعبة command — نقل اللعبة المعتمدة إلى التشغيل\n🔟 حذف لعبة command — حذف اللعبة من testing/active/approved\n🧩 اضف امر اسم | رد — إضافة أمر ديناميكي بدون تعديل الكود\n🗑️ حذف امر اسم — حذف الأمر\n⏸️ تعطيل امر اسم / ▶️ تفعيل امر اسم\n📋 الأوامر المضافة — عرض الأوامر الديناميكية\n🛠️ اصلاح ذاتيا — إصلاح ملفات البيانات والمجلدات وفحص الكود\n🤖 حالة البوت — حالة التشغيل والاتصال الحالية\n🧪 تشغيل اختبار@command — تشغيل لعبة testing للاختبار\n🧪 إيقاف اختبار@command — إيقاف اختبار اللعبة\n🧪 حالة اختبار@command — حالة لعبة الاختبار\n🧪 اختبارات نشطة — عرض الاختبارات الحالية\n"
                "🔐 تشغيل التوثيق / إيقاف التوثيق / حالة التوثيق\n"
                "━━━━━━━━━━━━━━\n"
                "🔐 كل هذه الأوامر خاصة بالمالك.")
    if low in ("تشغيل التوثيق", "تفعيل التوثيق", "verification on", "vip on"):
        await set_verification_enabled(True)
        return "🔐 تم تشغيل توثيق الحسابات. الألعاب والنشر والتشغيل والمشاركة والخدمات المحمية تتطلب VIP."
    if low in ("إيقاف التوثيق", "ايقاف التوثيق", "verification off", "vip off"):
        await set_verification_enabled(False)
        return "🔓 تم إيقاف توثيق الحسابات. أصبحت الخدمات المحمية متاحة للجميع."
    if low in ("حالة التوثيق", "verification status"):
        return f"🔐 توثيق الحسابات: {'مفعّل' if VERIFICATION_ENABLED else 'متوقف'}"
    if low.startswith("تشغيل اختبار@") or low.startswith("تشغيل اختبار "):
        command = text.split("@", 1)[1].strip() if "@" in text else text.split(None, 2)[2].strip()
        ok, msg = activate_test_game(command)
        return msg
    if low.startswith("إيقاف اختبار@") or low.startswith("ايقاف اختبار@") or low.startswith("إيقاف اختبار ") or low.startswith("ايقاف اختبار "):
        command = text.split("@", 1)[1].strip() if "@" in text else text.split(None, 2)[2].strip()
        ok, msg = deactivate_test_game(command)
        return msg
    if low.startswith("حالة اختبار@") or low.startswith("حالة اختبار "):
        command = text.split("@", 1)[1].strip() if "@" in text else text.split(None, 2)[2].strip()
        key = normalize_text(command).strip()
        active = load_active_tests()
        testing = load_testing_games()
        if key not in testing:
            return f"❌ لا توجد لعبة اختبار باسم «{key}»."
        return f"🧪 لعبة الاختبار: {testing[key].get('title', key)}\n🎮 الأمر: {key}\n📌 الحالة: {'🟢 تعمل للاختبار' if key in active else '🔴 متوقفة'}"
    if low in ("اختبارات نشطة", "الاختبارات النشطة", "active tests"):
        active = load_active_tests()
        return "🧪 لا توجد اختبارات نشطة." if not active else "🧪 الاختبارات النشطة:\n" + "\n".join(f"• {k}" for k in active)
    if low.startswith("اعتماد لعبة ") or low.startswith("اعتمد لعبة "):
        command = text.split(None, 2)[2].strip() if len(text.split(None, 2)) > 2 else ""
        ok, msg = approve_testing_game(command)
        return msg
    if low in ("العاب الاختبار", "ألعاب الاختبار", "testing games"):
        testing = load_testing_games()
        if not testing: return "🧪 لا توجد ألعاب في بيئة الاختبار."
        return "🧪 ألعاب الاختبار:\n" + "\n".join(f"• {k} — {v.get('title', k)}" for k,v in testing.items())

    if low.startswith("اصلاح ذكي ") or low.startswith("إصلاح ذكي "):
        problem = text.split(None, 2)[2] if len(text.split(None, 2)) > 2 else ""
        result, err = await ai_diagnose(problem)
        return err or "🤖 تشخيص الذكاء الاصطناعي:\n" + result
    if low.startswith("اصلاح ") or low.startswith("إصلاح "):
        kind = text.split(None, 1)[1]
        return await run_repair_check(kind)
    if low.startswith("صمم ") or low.startswith("صمم صورة "):
        prompt = text.split(None, 1)[1]
        if normalize_text(prompt).startswith("صورة "): prompt = prompt.split(None, 1)[1]
        path, err = await generate_ai_image(prompt, "design")
        if err: return err
        try:
            url = await _store_media(path, "publish", "image/png")
            await dm_send_media(sender, "🖼️ تم تصميم الصورة بالذكاء الاصطناعي.", url, "image")
            return "✅ أرسلت لك الصورة في الخاص."
        finally:
            try: path.unlink(missing_ok=True)
            except Exception: pass
    if low.startswith("اضف لعبة ") or low.startswith("أضف لعبة "):
        description = text.split(None, 2)[2] if len(text.split(None, 2)) > 2 else ""
        return await add_ai_game(sender, description)
    return None

async def dm_loop():
    while True:
        try:
            rows, err = await table_select(lambda: sb.table("dm_relay").select("*").eq("recipient_id", BOT_ID).limit(50).execute())
            for row in rows or []:
                env, sender = row.get("envelope") or {}, row.get("sender_id")
                text = (env.get("content") or "").strip()
                if sender and sender != BOT_ID and text:
                    parts = text.split(maxsplit=1)
                    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
                    low = normalize_text(text)
                    is_owner = (await username_of(sender)).lower() == OWNER
                    reply = ""
                    # رد فوري + مؤشر تقدم للماستر أثناء الأوامر البطيئة.
                    master_like = is_owner and bool(text)
                    progress_task = None
                    progress_stop = None
                    started_at = time.time()
                    if master_like:
                        try:
                            await dm_send(sender, "⏳ جاري تلبية طلبك... انتظرني، سأرسل لك النتيجة بعد التنفيذ.")
                            progress_stop = asyncio.Event()
                            async def _master_progress():
                                await asyncio.sleep(5)
                                while not progress_stop.is_set():
                                    elapsed = int(time.time() - started_at)
                                    await dm_send(sender, f"⏳ ما زلت أنفذ طلبك... مضى {elapsed} ثانية.")
                                    try:
                                        await asyncio.wait_for(progress_stop.wait(), timeout=5)
                                    except asyncio.TimeoutError:
                                        continue
                            progress_task = asyncio.create_task(_master_progress(), name="master-command-progress")
                        except Exception:
                            log.exception("failed to start master progress reporter")
                    if cmd in ("دخول", "join") and is_owner:
                        ok, m = await join(arg); reply = ("✅ " if ok else "❌ ") + m
                    elif cmd in ("خروج", "leave") and is_owner:
                        ok, m = await leave(arg); reply = ("✅ " if ok else "❌ ") + m
                    elif cmd in ("غرفي", "rooms"):
                        reply = "🏠 " + (", ".join(rooms.values()) if rooms else "لا توجد غرف")
                    elif low in ("نسخ احتياطي", "backup", "backup@telegram") and is_owner:
                        ok, m = await telegram_backup(); reply = m
                    elif low in ("master", "ماستر", "اوامر الماستر", "أوامر الماستر") and is_owner:
                        reply = ("👑 أوامر الماستر\n"
                                 "اصلاح — مركز الصيانة والذكاء الاصطناعي\n"
                                 "اصلاح ذكي مشكلة — تشخيص مشكلة\n"
                                 "صمم وصف — تصميم صورة AI\n"
                                 "اضف لعبة اسم | وصف — إنشاء لعبة وصورتها\n"
                                 "نسخ احتياطي — رفع نسخة آمنة إلى Telegram\n"
                                 "تشغيل التوثيق / إيقاف التوثيق / حالة التوثيق\n"
                                 "اضف لعبة اسم | وصف — وضع اللعبة في testing\n"
                                 "اعتماد لعبة command — نقل اللعبة إلى approved\n"
                                 "العاب الاختبار — عرض ألعاب الاختبار\n"
                                 "غرفي — عرض الغرف المتصلة\n"
                                 "دخول اسم / خروج اسم — إدارة الغرف")
                    elif text and (cmd in ("اصلاح", "إصلاح", "ذكاء", "ai", "صمم", "اضف", "أضف", "اعتماد", "اعتمد", "تشغيل", "إيقاف", "ايقاف", "حذف", "احذف", "تعطيل", "تفعيل", "حالة", "الأوامر", "الاوامر") or low.startswith(("اصلاح ", "إصلاح ", "صمم ", "اضف لعبة ", "أضف لعبة ", "اضف امر ", "أضف أمر ", "اضف أمر ", "حذف لعبة ", "احذف لعبة ", "حذف لعبه ", "احذف لعبه ", "حذف امر ", "احذف امر ", "حذف أمر ", "احذف أمر ", "تعطيل امر ", "تعطيل أمر ", "تفعيل امر ", "تفعيل أمر ", "اعتماد لعبة ", "اعتمد لعبة ", "تشغيل التوثيق", "إيقاف التوثيق", "ايقاف التوثيق", "حالة التوثيق", "اصلاح ذاتيا", "إصلاح ذاتياً", "حالة البوت", "الأوامر المضافة", "الاوامر المضافة"))):
                        reply = await handle_ai_dm(sender, text)
                    elif is_owner and text:
                        reply = "ℹ️ استلمت أمرك، لكن الأمر غير معروف. اكتب «أوامر الماستر» لرؤية الأوامر المتاحة."
                    if progress_stop is not None:
                        progress_stop.set()
                    if progress_task is not None:
                        progress_task.cancel()
                        try:
                            await progress_task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            log.exception("master progress reporter failed")
                    if reply: await dm_send(sender, reply)
                await run(lambda i=row["id"]: sb.table("dm_relay").delete().eq("id", i).execute())
        except Exception:
            log.exception("dm loop error")
        await asyncio.sleep(POLL)

async def room_loop():
    while True:
        try:
            for rid in list(rooms):
                since = last_room.get(rid) or now_iso()
                rows, err = await table_select(lambda r=rid, s=since: sb.table("room_messages").select("*").eq("room_id", r).gt("created_at", s).order("created_at").limit(50).execute())
                for m in rows or []:
                    last_room[rid] = m["created_at"]
                    if m.get("user_id") == BOT_ID or m.get("message_type") == "system": continue
                    text = (m.get("content") or "").strip()
                    media_url = m.get("media_url")
                    message_type = m.get("message_type")
                    # نحتاج معالجة رسالة الصورة حتى لو كان content فارغاً، لأن نشر@ ينتظر الصورة في الرسالة التالية.
                    if text or ((rid, m.get("user_id")) in publish_pending and media_url):
                        reply = await handle_room(rid, text, m.get("user_id"), media_url, message_type)
                        if reply: await room_send(rid, reply)
        except Exception:
            log.exception("room loop error")
        await asyncio.sleep(POLL)


async def heartbeat_loop():
    global LAST_HEARTBEAT_AT, LAST_DB_OK_AT
    while True:
        now = time.time()
        for rid in list(rooms):
            data, err = await rpc("room_heartbeat", {"_room": rid})
            if not err:
                LAST_DB_OK_AT = time.time()
        LAST_HEARTBEAT_AT = time.time()
        game = war_games.get(GLOBAL_WAR_KEY)
        if game and now >= game.get("expires_at", 0):
            war_games.pop(GLOBAL_WAR_KEY, None)
            try:
                await broadcast_text("⌛ انتهت لعبة الحرب العالمية تلقائياً بسبب انتهاء المهلة. اكتب «حرب» لبدء لعبة جديدة.")
            except Exception:
                log.exception("failed to announce global war timeout")
        # تنظيف طلبات نشر@ القديمة
        for key, pending in list(publish_pending.items()):
            created = pending.get("created_at", 0) if isinstance(pending, dict) else pending
            if now - created > 120:
                publish_pending.pop(key, None)
        await asyncio.sleep(10)

async def session_loop():
    while True:
        await asyncio.sleep(1800)
        await run(lambda: sb.auth.refresh_session())

async def leave_all_for_disconnect():
    saved = load_rooms_saved()
    for rid in list(rooms):
        try:
            await rpc("room_leave", {"_room": rid})
        except Exception:
            log.exception("failed to leave room on network outage: %s", rid)
    rooms.clear(); last_room.clear()
    return saved

async def restore_saved_rooms():
    saved = load_rooms_saved()
    for rid, name in saved.items():
        try:
            data, err = await rpc("room_join", {"_room": rid, "_password": C.get("room_password", "")})
            if err:
                log.warning("rejoin %s failed: %s", name, err)
                continue
            rooms[rid], last_room[rid] = name, now_iso()
        except Exception:
            log.exception("rejoin room failed: %s", name)

async def network_loop():
    global NETWORK_ONLINE
    online = True
    while True:
        try:
            async with http.get("https://www.google.com/generate_204",
                                 timeout=aiohttp.ClientTimeout(total=8)) as resp:
                ok = resp.status < 500
        except Exception:
            ok = False
        NETWORK_ONLINE = ok
        if online and not ok:
            log.warning("Internet disconnected: leaving all bot rooms")
            await leave_all_for_disconnect()
            online = False
        elif not online and ok:
            log.info("Internet restored: rejoining saved rooms")
            await restore_saved_rooms()
            online = True
        await asyncio.sleep(10)

async def main():
    global http, BOT_ID
    http = aiohttp.ClientSession()
    try:
        await start_media_server()
        try:
            await asyncio.to_thread(self_repair_sync)
        except Exception:
            log.exception("startup self-repair failed")
        email = await resolve_email()
        res, err = await run(lambda: sb.auth.sign_in_with_password({"email": email, "password": PASSWORD}))
        if err or not res.user: raise RuntimeError("فشل الدخول")
        BOT_ID = res.user.id
        cookie_ok, cookie_msg = youtube_cookie_status()
        log.info("YouTube cookies: %s | %s", "OK" if cookie_ok else "NOT-READY", cookie_msg)
        log.info("YouTube player clients: %s | PO token: %s", os.environ.get("YOUTUBE_PLAYER_CLIENTS") or C.get("youtube_player_clients", "web_safari,tv,web"), "configured" if YOUTUBE_PO_TOKEN else "not configured")
        await prepare_game_assets()
        global AUTH_ACCESS_TOKEN
        AUTH_ACCESS_TOKEN = getattr(getattr(res, "session", None), "access_token", None)
        await restore_rooms()
        # إذا كانت الغرف محفوظة من قبل، أعد الانضمام إليها حتى لو خرج البوت بسبب انقطاع الشبكة.
        if not rooms:
            await restore_saved_rooms()
        log.info("حالة الذكاء المحلي: %s", local_ai_status_text())
        log.info("البوت لا يحتاج إلى مفتاح OpenAI.")
        # تنزيل نموذج Qwen GGUF تلقائياً في الخلفية عند تشغيل Railway.
        # إذا كان الملف موجوداً فلن تتم إعادة تنزيله.
        asyncio.create_task(_download_local_ai_model(), name="local-ai-model-download")
        log.info("سيتم تجهيز نموذج الذكاء المحلي تلقائياً في الخلفية.")
        log.info("البوت جاهز كـ @%s", USERNAME)
        music_task = asyncio.create_task(music_worker_queue(), name="music-queue")
        try:
            await asyncio.gather(dm_loop(), room_loop(), heartbeat_loop(), session_loop(), network_loop())
        finally:
            music_task.cancel()
            try: await music_task
            except asyncio.CancelledError: pass
    finally:
        await stop_media_server()
        await http.close()

async def resolve_email():
    data, _ = await rpc("lookup_auth_email", {"_username": USERNAME})
    if isinstance(data, str) and "@" in data: return data
    rows, _ = await table_select(lambda: sb.table("profiles").select("auth_email").eq("username", USERNAME).limit(1).execute())
    if rows and rows[0].get("auth_email"): return rows[0]["auth_email"]
    raise RuntimeError("تعذر إيجاد البريد")

async def join(name):
    room = await find_room(name)
    if not room: return False, "الغرفة غير موجودة"
    data, err = await rpc("room_join", {"_room": room["id"], "_password": C.get("room_password", "")})
    if err: return False, err
    rooms[room["id"]], last_room[room["id"]] = room["name"], now_iso()
    saved = load_rooms_saved(); saved[room["id"]] = room["name"]; save_rooms_saved(saved)
    return True, f"تم الدخول لـ {room['name']}"

async def leave(name):
    room = await find_room(name)
    if not room: return False, "الغرفة غير موجودة"
    _, err = await rpc("room_leave", {"_room": room["id"]})
    if err: return False, err
    rooms.pop(room["id"], None); last_room.pop(room["id"], None)
    saved = load_rooms_saved(); saved.pop(room["id"], None); save_rooms_saved(saved)
    return True, f"تم الخروج من {room['name']}"

async def find_room(name):
    rows, _ = await table_select(lambda: sb.table("rooms").select("id,name").eq("name", name.strip()).limit(1).execute())
    return rows[0] if rows else None

async def restore_rooms():
    rows, _ = await table_select(lambda: sb.table("room_members").select("room_id").eq("user_id", BOT_ID).execute())
    ids = [r["room_id"] for r in rows or []]
    if ids:
        names, _ = await table_select(lambda: sb.table("rooms").select("id,name").in_("id", ids).execute())
        for r in names or []: rooms[r["id"]], last_room[r["id"]] = r["name"], now_iso()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
    except Exception as e: log.error("خطأ: %s", e); sys.exit(1)
