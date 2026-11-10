# -*- coding: utf-8 -*-
"""Run the complete Talkin bot on Giant Chat.

The package vendors the original Talkin bot (``vendor/talkin_bot.py``) and
connects it to Giant Chat's Supabase transport through :mod:`adapter` and
:mod:`giant_api`.  See ``talkin_full/README_AR.md`` for the full guide.
"""
__all__ = ["config", "giant_api", "adapter", "runner"]
