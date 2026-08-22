"""محرك الألعاب المتعددة الأنواع لبوت Giant Chat.

كل لعبة مخصّصة تحمل الحقل game_type ويحدد طريقة اللعب:
  chance  : حظ فوري (السلوك القديم)
  quiz    : سؤال وجواب، أول إجابة صحيحة تفوز
  math    : مسألة حسابية عشوائية، أسرع إجابة تفوز
  guess   : تخمين رقم مع تلميحات (أكبر/أصغر)
  fastest : أسرع من يكتب الكلمة المعروضة
  word    : ترتيب حروف كلمة مبعثرة
  duel    : مواجهة بين لاعبين، من ينضم أولاً يواجه صاحب التحدي

الوحدة مستقلة تماماً عن الشبكة: تُرجع نصوصاً ونتائج، والبوت يتولى الإرسال.
"""
from __future__ import annotations

import random
import re
import time

GAME_TYPES = {
    "chance": "حظ فوري",
    "quiz": "سؤال وجواب",
    "math": "مسألة حسابية",
    "guess": "تخمين رقم",
    "fastest": "أسرع كتابة",
    "word": "ترتيب كلمة",
    "duel": "مواجهة بين لاعبين",
}

DEFAULT_TIMEOUT = 60

# rid -> session
SESSIONS: dict = {}

_ARABIC_DIACRITICS = re.compile(r"[\u064b-\u0652\u0640]")


def normalize_answer(value) -> str:
    text = str(value or "").strip().lower()
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def game_type_of(game: dict) -> str:
    value = str((game or {}).get("game_type") or "chance").strip().lower()
    return value if value in GAME_TYPES else "chance"


def _int(value, default, lo=None, hi=None):
    try:
        n = int(float(str(value).strip()))
    except Exception:
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _shuffle_word(word: str) -> str:
    chars = list(word)
    for _ in range(8):
        random.shuffle(chars)
        if "".join(chars) != word:
            break
    return " ".join(chars)


def get_session(rid):
    session = SESSIONS.get(rid)
    if not session:
        return None
    if time.time() > session.get("expires_at", 0):
        SESSIONS.pop(rid, None)
        return None
    return session


def cancel_session(rid):
    return SESSIONS.pop(rid, None)


def expired_sessions():
    """يرجع الجلسات المنتهية ويزيلها (يستدعيها البوت دورياً)."""
    out = []
    for rid, session in list(SESSIONS.items()):
        if time.time() > session.get("expires_at", 0):
            SESSIONS.pop(rid, None)
            out.append((rid, session))
    return out


def start_session(rid, game: dict, uid, username):
    """يبدأ جولة تفاعلية ويعيد (نص_البداية, جلسة) أو (None, None) للألعاب الفورية."""
    gtype = game_type_of(game)
    if gtype == "chance":
        return None, None

    timeout = _int(game.get("timeout_seconds"), DEFAULT_TIMEOUT, 15, 300)
    title = str(game.get("title") or game.get("command") or "لعبة")
    session = {
        "rid": rid,
        "command": game.get("command"),
        "game": game,
        "type": gtype,
        "starter_id": uid,
        "starter_name": username,
        "started_at": time.time(),
        "expires_at": time.time() + timeout,
        "timeout": timeout,
        "answers": [],
    }

    if gtype in ("quiz",):
        pool = [q for q in (game.get("questions") or []) if isinstance(q, dict) and q.get("q") and q.get("a")]
        if not pool:
            pool = [{"q": "ما هي عاصمة اليمن؟", "a": "صنعاء"}]
        item = random.choice(pool)
        session["answer"] = [normalize_answer(item.get("a"))] + [
            normalize_answer(x) for x in (item.get("alt") or [])
        ]
        session["question"] = str(item.get("q"))
        intro = f"🧠 {title}\n❓ {session['question']}\n⏱️ لديكم {timeout} ثانية — أول إجابة صحيحة تفوز!"

    elif gtype == "math":
        a, b = random.randint(2, 40), random.randint(2, 40)
        op = random.choice(["+", "-", "×"])
        value = a + b if op == "+" else (a - b if op == "-" else a * b)
        session["answer"] = [normalize_answer(value)]
        session["question"] = f"{a} {op} {b} = ؟"
        intro = f"➗ {title}\n❓ {session['question']}\n⏱️ {timeout} ثانية — أسرع إجابة صحيحة تفوز!"

    elif gtype == "guess":
        top = _int(game.get("max_number"), 50, 5, 1000)
        session["secret"] = random.randint(1, top)
        session["max_number"] = top
        intro = f"🔢 {title}\n❓ اخترت رقماً بين 1 و{top}. خمّنوه!\n⏱️ {timeout} ثانية وسأعطيكم تلميحات."

    elif gtype == "fastest":
        words = [w for w in (game.get("words") or []) if str(w).strip()] or [
            "انتصار", "صاروخ", "بستان", "مغامرة", "عاصفة", "نجمة", "قهوة", "جبل",
        ]
        word = str(random.choice(words)).strip()
        session["answer"] = [normalize_answer(word)]
        session["question"] = word
        intro = f"⚡ {title}\n✍️ اكتب هذه الكلمة أولاً: «{word}»\n⏱️ {timeout} ثانية!"

    elif gtype == "word":
        words = [w for w in (game.get("words") or []) if str(w).strip()] or [
            "مدرسة", "سيارة", "برنامج", "شمس", "بحر", "قلعة", "غيمة",
        ]
        word = str(random.choice(words)).strip()
        session["answer"] = [normalize_answer(word)]
        session["question"] = _shuffle_word(word)
        intro = f"🔤 {title}\n🧩 رتب الحروف: {session['question']}\n⏱️ {timeout} ثانية — أول إجابة صحيحة تفوز!"

    elif gtype == "duel":
        session["expires_at"] = time.time() + timeout
        intro = (
            f"⚔️ {title}\n👤 @{username} أطلق تحدياً!\n"
            f"✍️ اكتب «انضم» خلال {timeout} ثانية لمواجهته."
        )

    else:
        return None, None

    SESSIONS[rid] = session
    return intro, session


def _prize(game, key, default):
    return _int(game.get(key), default, -1000000, 1000000)


def handle_message(rid, uid, username, text):
    """يعالج رسالة غرفة أثناء جولة نشطة.

    يرجع None إذا لم تكن الرسالة متعلقة بالجولة، أو dict بالنتيجة:
      {"finished": bool, "text": str, "winner": (uid, name, delta) | None}
    """
    session = get_session(rid)
    if not session:
        return None

    game = session["game"]
    gtype = session["type"]
    raw = str(text or "").strip()
    if not raw:
        return None
    guess = normalize_answer(raw)
    if not guess:
        return None

    if guess in ("الغاء", "الغي", "ايقاف اللعبه", "ايقاف اللعبة", "stop"):
        if uid == session["starter_id"]:
            cancel_session(rid)
            return {"finished": True, "text": "🛑 تم إلغاء الجولة.", "winner": None}
        return None

    win_points = _prize(game, "win_points", 30)
    lose_points = _prize(game, "lose_points", 0)
    win_message = str(game.get("win_message") or "🎉 فوز!")
    lose_message = str(game.get("lose_message") or "😅 حظاً أوفر!")

    if gtype == "duel":
        if guess in ("انضم", "تحدي", "قبلت", "join"):
            if uid == session["starter_id"]:
                return {"finished": False, "text": "😅 لا يمكنك مواجهة نفسك، انتظر خصماً."}
            attacker_roll = random.randint(1, 100)
            defender_roll = random.randint(1, 100)
            starter_wins = attacker_roll >= defender_roll
            winner_id = session["starter_id"] if starter_wins else uid
            winner_name = session["starter_name"] if starter_wins else username
            loser_name = username if starter_wins else session["starter_name"]
            loser_id = uid if starter_wins else session["starter_id"]
            cancel_session(rid)
            return {
                "finished": True,
                "winner": (winner_id, winner_name, win_points),
                "loser": (loser_id, loser_name, lose_points),
                "won": True,
                "text": (
                    f"⚔️ المواجهة: @{session['starter_name']} ({attacker_roll}) ضد @{username} ({defender_roll})\n"
                    f"🏆 {win_message} @{winner_name} (+{win_points} نقطة)\n"
                    f"💤 {lose_message} @{loser_name} ({lose_points} نقطة)"
                ),
            }
        return None

    if gtype == "guess":
        m = re.search(r"\d+", raw)
        if not m:
            return None
        number = int(m.group(0))
        secret = session["secret"]
        session["answers"].append((uid, number))
        if number == secret:
            cancel_session(rid)
            return {
                "finished": True,
                "won": True,
                "winner": (uid, username, win_points),
                "text": f"🎯 {win_message} @{username} خمّن الرقم {secret}!\n💰 +{win_points} نقطة",
            }
        if len(session["answers"]) >= _int(game.get("max_attempts"), 12, 3, 50):
            cancel_session(rid)
            return {
                "finished": True,
                "won": False,
                "winner": None,
                "text": f"⌛ انتهت المحاولات! الرقم كان {secret}. {lose_message}",
            }
        hint = "🔼 أكبر" if number < secret else "🔽 أصغر"
        return {"finished": False, "text": f"{hint} من {number} يا @{username}"}

    # quiz / math / fastest / word
    if guess in session.get("answer", []):
        cancel_session(rid)
        return {
            "finished": True,
            "won": True,
            "winner": (uid, username, win_points),
            "text": f"✅ {win_message} @{username}\n💰 +{win_points} نقطة",
        }
    return None


def timeout_text(session) -> str:
    game = session.get("game") or {}
    gtype = session.get("type")
    if gtype == "duel":
        return "⌛ لم ينضم أحد للمواجهة، انتهت الجولة."
    if gtype == "guess":
        return f"⌛ انتهى الوقت! الرقم كان {session.get('secret')}."
    answer = session.get("answer") or []
    correct = answer[0] if answer else "—"
    return f"⌛ انتهى الوقت في «{game.get('title') or game.get('command')}». الإجابة كانت: {correct}"
