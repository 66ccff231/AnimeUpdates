#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新番剧中文名索引（titles_zh.json）。

AniList 只有日文原名/罗马音/英文名，中文名来自 bangumi-data 这个开源数据集。
新番播出后数据集会持续收录，所以过一段时间跑一次这个脚本就能补上新番的中文名。

用法: python update_titles.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jp_anime_daily as core  # noqa: E402

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        core.build_zh_index()
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 更新失败：{type(exc).__name__}: {exc}")
        print("  （不影响程序使用，只是中文名索引会停留在旧版本）")
        sys.exit(1)
    print("✓ 完成")
