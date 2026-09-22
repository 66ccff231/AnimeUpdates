#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日漫每日更新表 · 桌面版（tkinter 窗口程序）
==========================================

不需要安装任何东西（tkinter 是 Python 自带的），双击运行就是一个真正的窗口程序。

它复用 jp_anime_daily.py 里已经写好的抓取逻辑（Fetcher / build_entries /
group_days / 缓存读写），所以数据源、时间换算、筛选规则和命令行版完全一致。
jp_anime_daily.py 一个字都没改。

功能：
  · 顶部工具栏：刷新、时间范围、类型、最低评分、搜索、只看已播出
  · 中间表格：按日期分组，今天高亮，点表头可排序
  · 双击某一行 → 用浏览器打开 AniList 详情页
  · 右键菜单 → 打开详情 / 查中文名(bgm.tv) / 复制标题
  · 底部状态栏：数据时间、条目数、今天几集、下一部什么时候播
  · 抓取在后台线程跑，窗口不会卡死；启动先用上次的缓存秒开，再自动抓最新

用法：
    python jp_anime_daily_gui.py
    python jp_anime_daily_gui.py --no-fetch     # 只读缓存，不联网（调试用）
"""

import argparse
import csv
import ctypes
import json
import os
import queue
import struct
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from tkinter import ttk, messagebox, filedialog
import tkinter as tk

# ---------------------------------------------------------------------------
# 复用命令行版的核心逻辑
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import jp_anime_daily as core
except ImportError:  # noqa: BLE001
    raise SystemExit(
        "找不到 jp_anime_daily.py。\n"
        "请把本文件和 jp_anime_daily.py 放在同一个文件夹里再运行。"
    )

CONFIG_PATH = os.path.join(HERE, "gui_config.json")   # 记住窗口大小和筛选设置
ICON_PATH = os.path.join(HERE, "app.ico")             # 应用图标（首次运行自动生成）
APP_ID = "AniList.DailyAnime.GUI"                     # 让任务栏认出这是独立应用
APP_VERSION = "1.0"

# 可选：类型下拉框 -> build_entries 产出的中文格式名
TYPE_CHOICES = [
    ("全部类型", None),
    ("TV", "TV"),
    ("剧场版", "剧场版"),
    ("OVA", "OVA"),
    ("网络动画", "网络动画"),
    ("特别篇", "特别篇"),
    ("短篇", "短篇"),
]

# 时间范围下拉框 -> 往后几天
DAYS_CHOICES = [("今天", 0), ("3 天", 2), ("7 天", 6), ("14 天", 13), ("30 天", 29)]

COLUMNS = [
    # key, 表头, 列宽, 对齐, 是否弹性（只有中文名吸收多余宽度）
    ("local", "本地时间", 68, "center", False),
    ("jst", "JST", 58, "center", False),
    ("ep", "集数", 58, "center", False),
    ("title", "番剧名（中文）", 248, "w", True),
    ("title_ja", "原名（日文）", 178, "w", False),
    ("score", "评分", 52, "center", False),
    ("state", "状态", 58, "center", False),
    ("format", "类型", 76, "center", False),
    ("studio", "制作公司", 172, "w", False),
]

SORT_KEYS = {
    "local": lambda e: e["ts"],
    "jst": lambda e: e["ts"],
    "ep": lambda e: e["episode"] or 0,
    "title": lambda e: core.display_title(e),
    "title_ja": lambda e: e["title"],
    "score": lambda e: e["score"] or -1,
    "state": lambda e: e["ts"],
    "format": lambda e: e["format"] or "",
    "studio": lambda e: e["studio"] or "",
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def setup_dpi():
    """让高分屏上的字别发虚。必须在建窗口之前调用。"""
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass


def local_offset_hours():
    off = -time.timezone / 3600.0
    if time.daylight and time.localtime().tm_isdst:
        off = -time.altzone / 3600.0
    return off


def load_any_cache():
    """读 data/ 里最新的一份缓存，返回 (原始记录列表, 文件名)。"""
    cache_dir = core.CACHE_DIR
    if not os.path.isdir(cache_dir):
        return None, None
    files = [
        f for f in os.listdir(cache_dir)
        if f.startswith("anilist_") and f.endswith(".json")
    ]
    files.sort(
        key=lambda f: os.path.getmtime(os.path.join(cache_dir, f)), reverse=True
    )
    for name in files:
        try:
            with open(os.path.join(cache_dir, name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data.get("entries") or []
            if entries:
                return entries, name
        except Exception:  # noqa: BLE001
            continue
    return None, None


# ---------------------------------------------------------------------------
# 应用图标：不依赖 Pillow，用标准库直接写一个 .ico（圆角渐变方块 + 播放三角）
# ---------------------------------------------------------------------------


def _icon_sample(fx, fy):
    """归一化坐标 -> (r, g, b, alpha 0..1)"""
    m, rad = 0.055, 0.215                      # 外边距、圆角半径
    x0 = y0 = m
    x1 = y1 = 1.0 - m
    if not (x0 <= fx <= x1 and y0 <= fy <= y1):
        return (0, 0, 0, 0.0)
    cx = min(max(fx, x0 + rad), x1 - rad)
    cy = min(max(fy, y0 + rad), y1 - rad)
    if (fx - cx) ** 2 + (fy - cy) ** 2 > rad * rad:
        return (0, 0, 0, 0.0)

    # 左上紫 -> 右下青
    t = max(0.0, min(1.0, ((fx - x0) + (fy - y0)) / (2 * (x1 - x0))))
    base = (
        int(108 + (0 - 108) * t),
        int(92 + (184 - 92) * t),
        int(231 + (217 - 231) * t),
        1.0,
    )

    # 中间挖一个白色播放三角
    ax, ay = 0.375, 0.275
    bx, by = 0.375, 0.725
    px, py = 0.705, 0.500
    d1 = (fx - bx) * (ay - by) - (ax - bx) * (fy - by)
    d2 = (fx - px) * (by - py) - (bx - px) * (fy - py)
    d3 = (fx - ax) * (py - ay) - (px - ax) * (fy - ay)
    if not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0)):
        return (255, 255, 255, 1.0)
    return base


def _icon_rgba(size, ss=3):
    """渲染一张 size×size 的 RGBA 位图（ss×ss 超采样做抗锯齿）。"""
    n = size * ss
    cnt = ss * ss
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            ar = ag = ab = aa = 0.0
            for sy in range(ss):
                for sx in range(ss):
                    r, g, b, a = _icon_sample((x * ss + sx + 0.5) / n,
                                              (y * ss + sy + 0.5) / n)
                    ar += r * a
                    ag += g * a
                    ab += b * a
                    aa += a
            if aa > 0:
                row.append((int(ar / aa + 0.5), int(ag / aa + 0.5),
                            int(ab / aa + 0.5), int(aa / cnt * 255 + 0.5)))
            else:
                row.append((0, 0, 0, 0))
        rows.append(row)
    return rows


def _icon_dib(rows):
    """把位图打包成 ICO 里用的 BITMAPINFOHEADER + BGRA（自下而上）+ AND 掩码。"""
    size = len(rows)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         size * size * 4, 0, 0, 0, 0)
    px = bytearray()
    for y in range(size - 1, -1, -1):
        for r, g, b, a in rows[y]:
            px += bytes((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4
    return header + bytes(px) + bytes(mask_row * size)


def make_icon(path, sizes=(256, 128, 64, 48, 32, 16)):
    """生成一个多尺寸 .ico 文件。已存在则跳过。"""
    if os.path.exists(path):
        return path
    images = [(s, _icon_dib(_icon_rgba(s))) for s in sizes]
    entries = b""
    offset = 6 + 16 * len(images)
    for s, dib in images:
        dim = 0 if s >= 256 else s          # 256 在 ICO 目录项里记作 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(dib), offset)
        offset += len(dib)
    try:
        with open(path, "wb") as f:
            f.write(struct.pack("<HHH", 0, 1, len(images)) + entries
                    + b"".join(d for _, d in images))
    except OSError:
        return None
    return path


# ---------------------------------------------------------------------------
# 设置持久化：记住窗口位置、大小和筛选条件
# ---------------------------------------------------------------------------


def _icon_file():
    """找到可用的 app.ico 文件。

    打包成 exe 后 PyInstaller 会把 app.ico 放进 _internal（sys._MEIPASS），
    必须先去那里找——否则每次都找不到、现场重算一遍图标，白等一秒多。
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "app.ico"))
    candidates.append(ICON_PATH)
    for path in candidates:
        if os.path.exists(path):
            return path
    return make_icon(ICON_PATH)


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------


class AnimeApp:
    def __init__(self, root, auto_fetch=True):
        self.root = root
        self.entries = []      # 全部条目
        self.days = []         # 按日期分组
        self.nodes = {}        # tree item id -> 条目 dict（父行是 None）
        self.sort_col = None
        self.sort_desc = False
        self.fetched_at = "—"
        self.busy = False
        self.pending = False
        # 子线程绝不直接碰控件：结果丢进队列，主线程轮询取（tkinter 不是线程安全的）
        self.queue = queue.Queue()
        self.cfg = load_config()
        self.closing = False
        self._poll_id = None
        self._titles_busy = False

        self._set_app_identity()
        root.title("日漫每日更新表 · AniList")
        root.minsize(940, 520)

        self._setup_style()
        self._build_toolbar()      # 先建控件变量，菜单里的单选/复选要绑它们
        self._build_menu()
        self._build_header()
        self._build_tree()
        self._build_status()
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 必须放在菜单栏等控件都建完之后：菜单栏占客户区高度，提前设 geometry
        # 会被 Tk 重算，尺寸少 20px、位置也会被改掉。
        self._restore_geometry()

        # 先用缓存秒开，再后台抓最新的
        self._load_cache_into_view()
        self._poll_id = self.root.after(120, self._poll_queue)
        if auto_fetch:
            self.refresh()
        else:
            self._set_status("已载入本地缓存（未联网）")

    # ---------------- 应用身份 / 窗口记忆 / 菜单（让它像个真 app）----------------

    def _set_app_identity(self):
        """让 Windows 任务栏把本程序当成独立应用，并用自己的图标。"""
        if os.name == "nt":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
            except Exception:  # noqa: BLE001
                pass
        icon = _icon_file()
        if icon:
            try:
                self.root.iconbitmap(icon)
            except Exception:  # noqa: BLE001
                pass

    def _menu_bar_height(self):
        """菜单栏实际占的高度。

        窗口还没映射时 winfo_height()/reqheight() 都测不准（返回 1），
        所以恢复时用配置里上次关窗时记下的真实值，测不到就按 20 算。
        """
        try:
            menu = self.root.nametowidget(self.root.cget("menu"))
            h = int(menu.winfo_height())
            if h > 1:
                return h
        except Exception:  # noqa: BLE001
            pass
        try:
            h = int(self.cfg.get("menu_h") or 0)
            return h if 1 < h < 60 else 20
        except Exception:  # noqa: BLE001
            return 20

    def _restore_geometry(self):
        """恢复上次的窗口大小和位置；没有记录就居中。越界的话退回居中。"""
        geo = self.cfg.get("geometry")
        if isinstance(geo, str) and "x" in geo:
            try:
                size, x, y = geo.replace("+", " +").split()
                w, h = (int(v) for v in size.split("x"))
                x, y = int(x), int(y)
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                # 防止上次在副屏/拔掉显示器后位置跑到屏幕外
                if w >= 400 and h >= 300 and -50 <= x <= sw - 200 and -20 <= y <= sh - 150:
                    # 菜单栏会吃掉客户区高度，补回去才能让读回的尺寸和存的一致
                    self.root.geometry(f"{w}x{h + self._menu_bar_height()}+{x}+{y}")
                    return
            except Exception:  # noqa: BLE001
                pass
        self._center_window(1180, 700)

    def _center_window(self, w, h):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 24)
        self.root.geometry(f"{w}x{h + self._menu_bar_height()}+{x}+{y}")

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label="刷新数据", accelerator="F5", command=self.refresh)
        fm.add_separator()
        fm.add_command(label="导出当前视图为 CSV…", command=self.export_csv)
        fm.add_command(label="打开数据文件夹", command=self.open_data_dir)
        fm.add_separator()
        fm.add_command(label="更新番剧中文名库（下载约 7MB）", command=self.update_titles)
        fm.add_separator()
        fm.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=fm)

        vm = tk.Menu(menubar, tearoff=0)
        for name, _ in DAYS_CHOICES:
            vm.add_radiobutton(label=f"显示范围：{name}", value=name,
                               variable=self.days_var)
        vm.add_separator()
        vm.add_checkbutton(label="只看已播出", variable=self.aired_only,
                           command=self.render)
        vm.add_separator()
        vm.add_command(label="清空搜索框", accelerator="Esc", command=self._clear_search)
        vm.add_command(label="回到默认排序（按时间）",
                       command=self._reset_sort)
        menubar.add_cascade(label="视图", menu=vm)

        hm = tk.Menu(menubar, tearoff=0)
        hm.add_command(label="快捷键说明", command=self.show_shortcuts)
        hm.add_command(label="打开 AniList 官网", command=self.open_anilist)
        hm.add_separator()
        hm.add_command(label=f"关于（v{APP_VERSION}）", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=hm)

        self.root.config(menu=menubar)

    def _build_header(self):
        """表格上方的仪表盘摘要条。"""
        head = ttk.Frame(self.root, padding=(12, 2, 12, 2))
        head.pack(fill="x")
        self.head_left = tk.Label(head, anchor="w", text="",
                                  font=("Microsoft YaHei UI", 12, "bold"),
                                  fg="#5b4bd6")
        self.head_left.pack(side="left")
        self.head_right = tk.Label(head, anchor="e", text="",
                                   font=("Microsoft YaHei UI", 10),
                                   fg="#5a6070")
        self.head_right.pack(side="right")

    def _reset_sort(self):
        self.sort_col = None
        self.sort_desc = False
        self.render()

    # ---------------- 菜单动作 ----------------

    def export_csv(self):
        """把当前筛选结果导出成 CSV（utf-8-sig，Excel 直接打开不乱码）。"""
        rows = []
        for d in self.days:
            for e in d["eps"]:
                if not self._match(e):
                    continue
                rows.append([
                    d["date"], d["dow"], e["time_local"], e["time_jst"],
                    e["episode"] if e["episode"] else "",
                    e.get("title_zh") or "", e["title"],
                    " / ".join(e["subs"]), e["score"] or "",
                    "已播出" if e["aired"] else "待播出",
                    e["format"] or "", e["studio"] or "",
                    "、".join(e["genres"]), e["url"],
                ])
        if not rows:
            messagebox.showinfo("导出", "当前视图没有可导出的内容。", parent=self.root)
            return
        default = f"日漫更新_{datetime.now():%Y%m%d_%H%M}.csv"
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出为 CSV", defaultextension=".csv",
            initialfile=default, filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["日期", "星期", "本地时间", "JST", "集数",
                            "番剧名（中文）", "原名（日文）",
                            "罗马音/英文名", "评分", "状态", "类型", "制作公司",
                            "标签", "AniList 链接"])
                w.writerows(rows)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self._set_status(f"已导出 {len(rows)} 行到 {path}")

    def open_data_dir(self):
        target = core.CACHE_DIR
        try:
            os.makedirs(target, exist_ok=True)
            if os.name == "nt":
                os.startfile(target)  # noqa: S606
            else:
                webbrowser.open("file://" + target)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打不开", str(exc), parent=self.root)

    def open_anilist(self):
        webbrowser.open("https://anilist.co/search/anime?status=RELEASING&countryOfOrigin=JP")

    def update_titles(self):
        """后台重新下载并生成中文名索引，完成后自动刷新列表。"""
        if getattr(self, "_titles_busy", False):
            return
        self._titles_busy = True
        self._set_status("正在下载番剧中文名库（约 7 MB，请稍候）…")
        threading.Thread(target=self._titles_worker, daemon=True).start()

    def _titles_worker(self):
        try:
            core.build_zh_index(log=lambda *_: None)
            self.queue.put(("titles", None))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("titles_err", str(exc)))

    def show_shortcuts(self):
        messagebox.showinfo(
            "快捷键",
            "F5            刷新数据\n"
            "Ctrl+F        聚焦搜索框\n"
            "Esc           清空搜索\n"
            "双击某一行    打开 AniList 详情\n"
            "右键某一行    详情 / 查中文名 / 复制\n"
            "点表头        按该列排序（再点一次反向）\n\n"
            "范围下拉改变时，会先用现有数据重排，再自动联网补齐。",
            parent=self.root,
        )

    def show_about(self):
        messagebox.showinfo(
            f"关于 日漫每日更新表 v{APP_VERSION}",
            "数据源：AniList（日本动画的全球数据库）\n"
            "anilist.co · 与 B 站无关\n\n"
            "纯 Python 标准库实现：tkinter 界面 + 标准库 HTTP。\n"
            "抓取在后台线程执行，界面不会卡死。\n\n"
            f"缓存目录：{core.CACHE_DIR}\n"
            f"设置文件：{CONFIG_PATH}",
            parent=self.root,
        )

    def on_close(self):
        """关闭窗口：先停掉轮询定时器，再记住大小、位置和筛选条件。"""
        self.closing = True
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:  # noqa: BLE001
                pass
            self._poll_id = None
        try:
            self.cfg.update({
                "days": self.days_var.get(),
                "type": self.type_var.get(),
                "score": int(self.score_var.get()),
                "aired_only": bool(self.aired_only.get()),
            })
            # 窗口还没真正显示过时 geometry 是无效默认值，别拿它覆盖上次记录
            if self.root.winfo_viewable():
                self.cfg["geometry"] = self.root.geometry()
                self.cfg["menu_h"] = self._menu_bar_height()   # 已映射，这时测得准
            save_config(self.cfg)
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()

    # ---------------- 子线程 -> 主线程的传话筒 ----------------

    def _poll_queue(self):
        """主线程定时取子线程的结果。所有界面更新都发生在这里。"""
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "ok":
                    entries, sd, before, after = payload
                    self._on_fetched(entries, sd, before, after)
                elif kind == "titles":
                    self._titles_busy = False
                    self._set_status("中文名库已更新，正在重新匹配…")
                    self.refresh()      # 重新抓一遍，让新的中文名生效
                elif kind == "titles_err":
                    self._titles_busy = False
                    self._set_status(f"中文名库更新失败：{payload}")
                else:
                    self._on_fetch_failed(payload)
        except queue.Empty:
            pass
        if not self.closing:
            self._poll_id = self.root.after(150, self._poll_queue)

    # ---------------- 界面搭建 ----------------

    def _setup_style(self):
        style = ttk.Style()
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                try:
                    style.theme_use(theme)
                    break
                except Exception:  # noqa: BLE001
                    continue
        base = ("Microsoft YaHei UI", 10)
        style.configure("Treeview", font=base, rowheight=27)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabel", font=base)
        style.configure("TButton", font=base)
        style.configure("TCheckbutton", font=base)
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 9),
                        foreground="#5a6070")
        self.root.option_add("*TCombobox*Listbox.font", base)

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(10, 8, 10, 4))
        bar.pack(fill="x")

        self.refresh_btn = ttk.Button(bar, text="⟳ 刷新", command=self.refresh)
        self.refresh_btn.pack(side="left")

        ttk.Label(bar, text="  范围").pack(side="left")
        self.days_var = tk.StringVar(value=self._cfg_choice("days", DAYS_CHOICES, "7 天"))
        ttk.Combobox(bar, textvariable=self.days_var, width=6, state="readonly",
                     values=[n for n, _ in DAYS_CHOICES]).pack(side="left", padx=(4, 10))

        ttk.Label(bar, text="类型").pack(side="left")
        self.type_var = tk.StringVar(value=self._cfg_choice("type", TYPE_CHOICES, "全部类型"))
        ttk.Combobox(bar, textvariable=self.type_var, width=9, state="readonly",
                     values=[n for n, _ in TYPE_CHOICES]).pack(side="left", padx=(4, 10))

        ttk.Label(bar, text="最低评分").pack(side="left")
        try:
            saved_score = int(self.cfg.get("score", 0))
        except Exception:  # noqa: BLE001
            saved_score = 0
        self.score_var = tk.IntVar(value=max(0, min(100, saved_score)))
        sp = ttk.Spinbox(bar, from_=0, to=100, increment=5, width=4,
                         textvariable=self.score_var, command=self.render)
        sp.pack(side="left", padx=(4, 10))
        sp.bind("<KeyRelease>", lambda _e: self.render())

        ttk.Label(bar, text="搜索").pack(side="left")
        self.q_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.q_var, width=22)
        ent.pack(side="left", padx=(4, 10))
        self.search_entry = ent
        self.q_var.trace_add("write", lambda *_: self.render())

        self.aired_only = tk.BooleanVar(value=bool(self.cfg.get("aired_only", False)))
        ttk.Checkbutton(bar, text="只看已播出", variable=self.aired_only,
                        command=self.render).pack(side="left")

        # 类型变化只重新渲染（数据已经在内存里）；范围变化要重排 + 重新抓
        self.type_var.trace_add("write", lambda *_: self.render())
        self.days_var.trace_add("write", lambda *_: self._on_range_changed())

    def _cfg_choice(self, key, choices, default):
        """从配置里取一个下拉框的值，非法就退回默认值。"""
        value = self.cfg.get(key, default)
        return value if value in dict(choices) else default

    def _build_tree(self):
        wrap = ttk.Frame(self.root, padding=(10, 0, 10, 0))
        wrap.pack(fill="both", expand=True)
        inner = ttk.Frame(wrap)
        inner.pack(fill="both", expand=True)

        cols = tuple(c[0] for c in COLUMNS)
        self.tree = ttk.Treeview(inner, columns=cols, show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="日期 / 星期", command=lambda: None)
        self.tree.column("#0", width=170, minwidth=150, anchor="w", stretch=False)
        for key, title, width, anchor, stretch in COLUMNS:
            self.tree.heading(key, text=title,
                              command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, minwidth=52, anchor=anchor,
                             stretch=stretch)

        ysb = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")

        # 窗口拉窄时列会被挤掉，给个横向滚动条兜底
        xsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=xsb.set)
        xsb.pack(side="bottom", fill="x")

        self.tree.tag_configure("today", background="#fff0f6")
        self.tree.tag_configure("odd", background="#f7f8fc")
        self.tree.tag_configure("past", foreground="#7a8090")
        self.tree.tag_configure("next", foreground="#c2410c")

        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Button-3>", self.on_right_click)
        self.tree.bind("<Return>", self.on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="打开 AniList 详情", command=self.open_detail)
        self.menu.add_command(label="在 bgm.tv 搜详情（日文原名）", command=self.open_bgm)
        self.menu.add_separator()
        self.menu.add_command(label="复制中文名", command=self.copy_title_zh)
        self.menu.add_command(label="复制日文原名", command=self.copy_title_ja)
        self.menu.add_command(label="复制 AniList 链接", command=self.copy_url)

    def _build_status(self):
        wrap = ttk.Frame(self.root, padding=(10, 4, 10, 8))
        wrap.pack(fill="x")
        self.status = ttk.Label(wrap, anchor="w", style="Status.TLabel")
        self.status.pack(side="left", fill="x", expand=True)
        self.hint = ttk.Label(
            wrap, anchor="e", style="Status.TLabel",
            text="双击打开详情 · 右键更多操作 · F5 刷新 · Ctrl+F 搜索",
        )
        self.hint.pack(side="right")

    def _bind_keys(self):
        self.root.bind("<F5>", lambda _e: self.refresh())
        self.root.bind("<Control-f>", lambda _e: self._focus_search())
        self.root.bind("<Escape>", lambda _e: self._clear_search())

    def _focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def _clear_search(self):
        self.q_var.set("")

    # ---------------- 数据 ----------------

    def window_args(self):
        """算出抓取窗口：几个 before/after、时区偏移、起止时间戳。"""
        after = dict(DAYS_CHOICES)[self.days_var.get()]
        before = 0
        off = local_offset_hours()
        now_local = datetime.now(timezone.utc) + timedelta(hours=off)
        start_date = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (now_local + timedelta(days=after + 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_ts = int((start_date - timedelta(hours=off)).timestamp())
        end_ts = int((end_date - timedelta(hours=off)).timestamp())
        return before, after, off, start_date, end_date, start_ts, end_ts

    def _make_entries(self, raw, off):
        args = argparse.Namespace(
            all_countries=False, adult=False, type=None, min_score=0
        )
        entries = core.build_entries(raw, off, args)
        now = time.time()
        for e in entries:
            e["countdown"] = core.humanize(e["ts"] - now)
            e["dow_label"] = core.WEEKDAYS[e["dow"]]
        return entries

    def _load_cache_into_view(self):
        raw, name = load_any_cache()
        if not raw:
            return False
        try:
            before, after, off, sd, _ed, _s, _e = self.window_args()
            entries = self._make_entries(raw, off)
            self._apply(entries, sd.date(), before + after + 1)
            self._set_status(f"已载入本地缓存 {name}（正在联网更新…）")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _on_range_changed(self):
        """换了时间范围：先用内存里的数据即时重排，再去联网补齐。"""
        self._regroup()
        self.refresh()

    def _regroup(self):
        if not self.entries:
            return
        before, after, _off, sd, _ed, _s, _e = self.window_args()
        self.days = core.group_days(self.entries, sd.date(), before + after + 1)
        self.render()

    def refresh(self):
        if self.busy:
            self.pending = True      # 正在抓，记下来，完了再抓一次
            return
        self.busy = True
        self.refresh_btn.config(state="disabled", text="抓取中…")
        self._set_status("正在从 AniList 抓取播出日程…")
        # 必须在主线程里读控件，再把算好的参数交给子线程
        params = self.window_args()
        threading.Thread(target=self._fetch_worker, args=params, daemon=True).start()

    def _fetch_worker(self, before, after, off, sd, _ed, start_ts, end_ts):
        """子线程：只做网络和纯计算，一个控件都不碰。"""
        try:
            raw, _total = core.Fetcher(verbose=False).window(start_ts, end_ts)
            core.save_cache(f"gui{after}", start_ts, end_ts, raw)
            entries = self._make_entries(raw, off)
            self.queue.put(("ok", (entries, sd, before, after)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("err", str(exc)))

    def _on_fetched(self, entries, start_date, before, after):
        self.busy = False
        self.refresh_btn.config(state="normal", text="⟳ 刷新")
        self._apply(entries, start_date.date(), before + after + 1)
        self._set_status(
            f"数据源 AniList · 抓取于 {datetime.now():%Y-%m-%d %H:%M:%S}"
        )
        self._run_pending()

    def _on_fetch_failed(self, msg):
        self.busy = False
        self.refresh_btn.config(state="normal", text="⟳ 刷新")
        self._set_status(f"抓取失败：{msg}（表格里显示的是缓存数据）")
        self._run_pending()

    def _run_pending(self):
        if self.pending:
            self.pending = False
            self.refresh()

    def _apply(self, entries, start_date, num_days):
        self.entries = entries
        self.days = core.group_days(entries, start_date, num_days)
        self.fetched_at = datetime.now().strftime("%H:%M:%S")
        self.render()

    # ---------------- 渲染 ----------------

    def _match(self, e):
        want_type = dict(TYPE_CHOICES)[self.type_var.get()]
        if want_type and e["format"] != want_type:
            return False
        try:
            min_score = int(self.score_var.get())
        except Exception:  # noqa: BLE001
            min_score = 0
        if min_score and (e["score"] or 0) < min_score:
            return False
        if self.aired_only.get() and not e["aired"]:
            return False
        q = self.q_var.get().strip().lower()
        if q:
            hay = " ".join([
                e.get("title_zh") or "", e["title"], " ".join(e["subs"]),
                e["studio"] or "", e["format"] or "",
            ]).lower()
            if q not in hay:
                return False
        return True

    def sort_by(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col
            self.sort_desc = False
        self.render()

    def _heading_texts(self):
        for key, title, *_rest in COLUMNS:
            arrow = ""
            if self.sort_col == key:
                arrow = " ▼" if self.sort_desc else " ▲"
            self.tree.heading(key, text=title + arrow)

    def render(self):
        if not hasattr(self, "tree"):
            return
        self._heading_texts()
        self.tree.delete(*self.tree.get_children())
        self.nodes.clear()

        today = datetime.now().strftime("%Y-%m-%d")
        now = time.time()
        shown = 0
        today_count = 0

        for d in self.days:
            rows = [e for e in d["eps"] if self._match(e)]
            if not rows:
                continue
            if self.sort_col:
                rows.sort(key=SORT_KEYS[self.sort_col], reverse=self.sort_desc)
            is_today = d["date"] == today
            if is_today:
                today_count = len(rows)
            shown += len(rows)

            label = f"{d['dow']} {d['label']}"
            if is_today:
                label += "  ← 今天"
            parent = self.tree.insert(
                "", "end", open=True,
                text=label,
                values=("", "", "", f"{len(rows)} 部", "", "", "", ""),
                tags=("today",) if is_today else (),
            )
            self.nodes[parent] = None
            for idx, e in enumerate(rows):
                # 斑马纹只和有背景色的 today 标签冲突，所以今天的行不上条纹
                tags = ["past" if e["aired"] else "next"]
                if idx % 2 == 1 and not is_today:
                    tags.append("odd")
                iid = self.tree.insert(
                    parent, "end", tags=tuple(tags),
                    values=(
                        e["time_local"],
                        e["time_jst"],
                        f"第{e['episode']}话" if e["episode"] else "—",
                        core.display_title(e),
                        e["title"],
                        e["score"] or "—",
                        "已播出" if e["aired"] else "待播出",
                        e["format"] or "",
                        e["studio"] or "",
                    ),
                )
                self.nodes[iid] = e

        # 顶部仪表盘
        self.head_left.config(text=f"今天 {today_count} 部更新")
        nxt = min((e for e in self.entries if e["ts"] > now),
                  key=lambda x: x["ts"], default=None)
        if nxt:
            self.head_right.config(
                text=f"下一部　{nxt['title']}　{nxt['time_local']}（{nxt['countdown']}）")
        else:
            self.head_right.config(text="窗口内已全部播完")

        # 状态栏
        self._set_status(
            f"数据源 AniList · 共 {shown} 部 · 抓取时间 {self.fetched_at} · "
            f"范围 {self.days_var.get()}"
        )

    def _set_status(self, text):
        self.status.config(text="  " + text)

    # ---------------- 交互 ----------------

    def _selected_entry(self):
        iid = self.tree.focus()
        return self.nodes.get(iid)

    def on_select(self, _ev=None):
        """选中一行时，在状态栏显示中文名 / 日文原名 / 制作公司。"""
        e = self._selected_entry()
        if not e:
            return
        names = [x for x in (e.get("title_zh"), e["title"]) if x]
        tail = " · ".join(x for x in (
            e["studio"], e["format"],
            f"{e['score']}分" if e["score"] else "",
        ) if x)
        self._set_status(
            f"{' ／ '.join(names)}    {tail}    "
            f"{e['time_local']} 本地 / {e['time_jst']} JST（{e['countdown']}）"
        )

    def on_double_click(self, event):
        iid = self.tree.identify_row(event.y) or self.tree.focus()
        entry = self.nodes.get(iid)
        if not entry:
            return
        self.tree.selection_set(iid)
        webbrowser.open(entry["url"])

    def on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        entry = self.nodes.get(iid)
        if not entry:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def open_detail(self):
        e = self._selected_entry()
        if e:
            webbrowser.open(e["url"])

    def open_bgm(self):
        e = self._selected_entry()
        if e:
            webbrowser.open(e["bgm"])

    def copy_title_zh(self):
        e = self._selected_entry()
        if not e:
            return
        text = e.get("title_zh") or e["title"]
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status(f"已复制中文名：{text}")

    def copy_title_ja(self):
        e = self._selected_entry()
        if not e:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(e["title"])
        self._set_status(f"已复制日文原名：{e['title']}")

    def copy_url(self):
        e = self._selected_entry()
        if e:
            self.root.clipboard_clear()
            self.root.clipboard_append(e["url"])
            self._set_status(f"已复制链接：{e['url']}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="日漫每日更新表 · 桌面版")
    ap.add_argument("--no-fetch", action="store_true",
                    help="启动时不联网，只显示本地缓存")
    args = ap.parse_args()

    setup_dpi()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法创建窗口：{exc}")
        return 1

    AnimeApp(root, auto_fetch=not args.no_fetch)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
