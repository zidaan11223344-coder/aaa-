"""Standalone master-account runner with dynamic bot loader."""
import os
import sys
import importlib.util
import traceback
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_MASTER = BASE_DIR / ".env.master"
ENV_MAIN = BASE_DIR / ".env"

print(f"[MASTER] BASE_DIR={BASE_DIR}", flush=True)
print(f"[MASTER] .env.master exists: {ENV_MASTER.exists()}", flush=True)
print(f"[MASTER] .env exists: {ENV_MAIN.exists()}", flush=True)

# Load the dedicated master environment first, then the normal environment.
load_dotenv(ENV_MASTER, override=False)
load_dotenv(ENV_MAIN, override=False)

master_id = os.getenv("MASTER_ID", "").strip() or os.getenv("BOT_MASTER", "").strip()
master_pwd = os.getenv("MASTER_PWD", "").strip() or os.getenv("BOT_PWD", "").strip()

if not master_id or not master_pwd:
    print("[MASTER][ERROR] Missing MASTER_ID or MASTER_PWD.", flush=True)
    print("[MASTER] Expected them in .env.master or the host environment.", flush=True)
    raise SystemExit(2)

print(f"[MASTER] MASTER_ID loaded: {master_id}", flush=True)
print("[MASTER] MASTER_PWD loaded: YES", flush=True)

os.environ["BOT_ID"] = master_id
os.environ["BOT_PWD"] = master_pwd
os.environ["BOT_MASTER"] = master_id
os.environ["MASTER_SERVICE_ENABLED"] = "1"
os.environ["TELEGRAM_POLL_ENABLED"] = "0"  # The primary bot alone consumes getUpdates.
os.environ["GROUP_TO_JOIN"] = ""
os.environ["FIRST_ROOM"] = ""
os.environ.setdefault("PRIMARY_BOT_ID", os.getenv("PRIMARY_BOT_ID", "").strip())

# Dynamic loader to support bot.py, bot_1_1.py, or any renamed variant
TalkinBot = None
try:
    from bot import TalkinBot
except ImportError:
    pass

if TalkinBot is None:
    candidate_names = ["bot.py", "bot_1_1.py", "bot (1) (1).py", "main.py"]
    for c in candidate_names:
        p = BASE_DIR / c
        if p.exists():
            spec = importlib.util.spec_from_file_location("bot", str(p))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["bot"] = mod
            spec.loader.exec_module(mod)
            TalkinBot = getattr(mod, "TalkinBot", None)
            if TalkinBot:
                break

if TalkinBot is None:
    print("[MASTER][ERROR] Could not locate TalkinBot in bot.py or bot_1_1.py", flush=True)
    raise SystemExit(3)

if __name__ == "__main__":
    print(f"[MASTER] Starting master bot account: {master_id}", flush=True)
    print("[MASTER] Creating TalkinBot instance...", flush=True)
    try:
        bot_instance = TalkinBot()
        print("[MASTER] TalkinBot instance created.", flush=True)
        print("[MASTER] Calling start()...", flush=True)
        bot_instance.start()
        print("[MASTER] start() returned normally.", flush=True)
    except SystemExit:
        print("[MASTER][ERROR] SystemExit raised by master bot:", flush=True)
        traceback.print_exc()
        raise
    except Exception:
        print("[MASTER][ERROR] Master bot crashed:", flush=True)
        traceback.print_exc()
        raise
