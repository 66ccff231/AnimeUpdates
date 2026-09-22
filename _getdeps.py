#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
不依赖 pip，直接把 PyInstaller 及其依赖的 wheel 下载并解包到本地目录。

为什么不用 pip：
  pip 会往系统临时目录 / 下载缓存里写文件，而这台机器的文件沙箱只允许写工作区，
  于是 pip 报 Errno 13。wheel 本身就是 zip，自己下载解包可以完全待在工作区内。

用法: python _getdeps.py
"""

import json
import os
import sys
import urllib.request
import zipfile

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_build_deps")

# pyinstaller 在 Windows 上需要的运行时依赖
PACKAGES = [
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
    "packaging",
    "pefile",
    "pywin32-ctypes",
    "setuptools",
]

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) wheel-fetcher"}


def score(filename):
    """给 wheel 打个分，越小越合适；9 表示不适用。"""
    n = filename.lower()
    if not n.endswith(".whl"):
        return 9
    if n.endswith("-py3-none-any.whl") or n.endswith("-py2.py3-none-any.whl"):
        return 0
    if "cp312" in n and "win_amd64" in n:
        return 1
    if "py3-none-win_amd64" in n:
        return 2
    if "none-any" in n:
        return 3
    return 9


def pick_wheel(meta):
    files = [f for f in meta["urls"] if not f.get("yanked")]
    files.sort(key=lambda f: (score(f["filename"]), f["filename"]))
    for f in files:
        if score(f["filename"]) < 9:
            return f
    return None


def main():
    os.makedirs(TARGET, exist_ok=True)
    got, failed = [], []
    for name in PACKAGES:
        try:
            with urllib.request.urlopen(
                f"https://pypi.org/pypi/{name}/json", timeout=30
            ) as r:
                meta = json.load(r)
            wheel = pick_wheel(meta)
            if not wheel:
                failed.append((name, "没有可用的 wheel"))
                continue
            url = wheel["url"]
            print(f"  {name:28} {wheel['filename']}")
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=120
            ) as r:
                blob = r.read()
            path = os.path.join(TARGET, wheel["filename"])
            with open(path, "wb") as f:
                f.write(blob)
            with zipfile.ZipFile(path) as z:
                z.extractall(TARGET)
            os.remove(path)
            got.append(name)
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))

    print(f"\n成功 {len(got)} 个: {', '.join(got)}")
    if failed:
        print("失败:")
        for n, why in failed:
            print(f"  {n}: {why}")
    print(f"\n解包目录: {TARGET}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
