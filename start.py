# -*- coding: utf-8 -*-
"""Deployment entry point for the merged aaa repository.

BOT_MODE selects which bot runs:

* ``alsfer`` (default) — the original Giant bot in ``bot.py`` with the
  merged Giant features (explore, publishing, gifts, games and music).
* ``talkin`` — the standalone Talkin command engine running on Giant Chat.

The switch keeps both implementations deployable from the same repository
without editing files on the host.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def main() -> int:
    mode = str(os.getenv("BOT_MODE") or "alsfer").strip().lower()
    if mode in {"alsfer", "giant", "legacy", "old"}:
        print("[START] تشغيل بوت جينات القديم مع الميزات المدمجة", flush=True)
        runpy.run_path(str(ROOT / "bot.py"), run_name="__main__")
        return 0
    if mode in {"talkin", "talkin_full", "full"}:
        print("[START] تشغيل محرك Talkin المنفصل على جينات شات", flush=True)
        runpy.run_module("talkin_full.runner", run_name="__main__", alter_sys=True)
        return 0
    if mode not in {"talkin", "talkin_full", "full"}:
        print(f"[START] وضع غير معروف: {mode!r} — استخدام alsfer", flush=True)
        runpy.run_path(str(ROOT / "bot.py"), run_name="__main__")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
