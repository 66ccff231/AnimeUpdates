#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把桌面版打包成单文件 exe（不依赖 pip）。

为什么有这个脚本：
  1. pip 在这台机器上会被文件沙箱拦住（它要往系统临时目录写东西），
     所以依赖是用 _getdeps.py 直接从 PyPI 下 wheel 解包的。
  2. PyInstaller 需要 PYTHONPATH 指向 _build_deps，还要把 TEMP 指到工作区内，
     这些环境变量在 .bat 里写中文路径容易踩编码坑，所以逻辑放在这里。

用法: python build_exe.py
产物: 日漫每日更新表.exe（就在本目录）
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEPS = os.path.join(HERE, "_build_deps")
TMP = os.path.join(HERE, ".buildtmp")
OUT_NAME = "日漫每日更新表"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run(cmd, **kw):
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, cwd=HERE, **kw).returncode


def main():
    os.makedirs(TMP, exist_ok=True)

    if not os.path.isdir(os.path.join(DEPS, "PyInstaller")):
        print("→ 依赖不存在，先下载 …")
        if run([sys.executable, os.path.join(HERE, "_getdeps.py")]) != 0:
            print("✗ 依赖下载失败")
            return 1

    env = dict(os.environ)
    env["PYTHONPATH"] = DEPS
    env["TEMP"] = TMP          # 让 PyInstaller 的临时文件留在工作区内
    env["TMP"] = TMP
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    print("→ 用 PyInstaller 打包（目录版，运行时不需要解包，启动更快）…")
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onedir", "--noconsole", "--clean",
        "--icon", os.path.join(HERE, "app.ico"),
        # 把图标也一起打进去，程序启动时就不必现场重算（省一秒多）
        "--add-data", os.path.join(HERE, "app.ico") + os.pathsep + ".",
        "--name", OUT_NAME,
        "--distpath", HERE,
        "--workpath", os.path.join(HERE, "_build_tmp"),
        "--specpath", HERE,
        os.path.join(HERE, "jp_anime_daily_gui.py"),
    ]
    rc = run(args, env=env)
    if rc != 0:
        print(f"✗ 打包失败，退出码 {rc}")
        return rc

    exe = os.path.join(HERE, OUT_NAME, OUT_NAME + ".exe")
    if not os.path.exists(exe):
        print("✗ 没找到产物 exe")
        return 1

    # 清理中间产物，只留下 dist 目录和可复用的 spec
    for path in (os.path.join(HERE, "_build_tmp"), os.path.join(HERE, "build")):
        shutil.rmtree(path, ignore_errors=True)

    total = sum(
        os.path.getsize(os.path.join(root, f))
        for root, _dirs, files in os.walk(os.path.join(HERE, OUT_NAME))
        for f in files
    )
    print(f"\n✓ 打包完成: {exe}")
    print(f"  单个 exe {os.path.getsize(exe)/1024/1024:.2f} MB，整包 {total/1024/1024:.1f} MB")
    print("  双击 exe 即可运行，不需要装 Python。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
