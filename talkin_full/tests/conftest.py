# -*- coding: utf-8 -*-
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_TMP = tempfile.mkdtemp(prefix="talkin_giant_test_")
os.environ.setdefault("BOT_ID", "testbot")
os.environ.setdefault("BOT_USERNAME", "testbot")
os.environ.setdefault("BOT_PWD", "x")
os.environ.setdefault("BOT_MASTER", "owner")
os.environ.setdefault("GROUP_TO_JOIN", "room1")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("BOT_DATA_DIR", _TMP)
os.environ.setdefault("BOT_ASSETS_DIR", str(REPO_ROOT / "assets"))
os.environ.setdefault("VERIFICATION_ENABLED", "0")
os.environ.setdefault("ASSET_HTTP_ENABLED", "0")
os.environ.setdefault("PUBLIC_MEDIA_URL", "http://127.0.0.1:8080")

for path in (str(REPO_ROOT),):
    if path not in sys.path:
        sys.path.insert(0, path)
