#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日漫每日更新表 · 核心模块（数据源：AniList）
==========================================

这是数据层 + 命令行版。窗口版 jp_anime_daily_gui.py 直接 import 本模块，
复用这里的抓取、缓存、筛选、分组逻辑，所以两个版本的数据完全一致。

用 AniList 的公开 GraphQL API 抓「日本动画」的播出日程，
在终端打印本周每天更新什么。

和 B 站没有任何关系：AniList 是日本动画的全球数据库（英文站 anilist.co），
数据来自各动画官方公布的在播日程，带精确到分钟的播出时间戳。

零依赖：只用 Python 标准库。

用法：
    python jp_anime_daily.py                    # 前后各 3 天
    python jp_anime_daily.py --days 7           # 从今天起往后 7 天
    python jp_anime_daily.py --today            # 只看今天
    python jp_anime_daily.py --min-score 75     # 只看评分 ≥75 的
    python jp_anime_daily.py --type TV          # 只看 TV 动画（排除 OVA/剧场版等）
    python jp_anime_daily.py --offline          # 不联网，用本地缓存渲染

想要窗口界面，跑 jp_anime_daily_gui.py（或双击启动器）。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

API = "https://graphql.anilist.co"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# AniList 的媒体格式 -> 中文
FORMAT_NAMES = {
    "TV": "TV",
    "TV_SHORT": "短篇",
    "MOVIE": "剧场版",
    "SPECIAL": "特别篇",
    "OVA": "OVA",
    "ONA": "网络动画",
    "MUSIC": "音乐",
}

QUERY = """
query ($from: Int, $to: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage total }
    airingSchedules(airingAt_greater: $from, airingAt_lesser: $to, sort: TIME) {
      episode
      airingAt
      media {
        id
        idMal
        countryOfOrigin
        isAdult
        format
        status
        episodes
        duration
        genres
        averageScore
        popularity
        title { romaji native english }
        coverImage { large }
        siteUrl
        studios(isMain: true) { nodes { name } }
        externalLinks { site url type }
      }
    }
  }
}
"""

def _app_dir():
    """程序所在目录。

    打包成 exe（PyInstaller --onefile）之后，__file__ 指向的是每次启动时
    临时解包出来的目录，退出就没了；必须改用 exe 自己所在的目录，
    否则缓存会写进临时目录、下次启动读不到。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


HERE = _app_dir()
CACHE_DIR = os.path.join(HERE, "data")

JST = timezone(timedelta(hours=9))  # 日本时间，动画播出时间都以它为准

# ----------------------------------------------------------------------------
# 终端输出辅助
# ----------------------------------------------------------------------------


def setup_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes

            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass


class C:
    ON = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False

    @classmethod
    def w(cls, code, t):
        return f"\033[{code}m{t}\033[0m" if cls.ON else t

    @classmethod
    def pink(cls, t):
        return cls.w("38;5;211", t)

    @classmethod
    def bold(cls, t):
        return cls.w("1", t)

    @classmethod
    def dim(cls, t):
        return cls.w("2", t)

    @classmethod
    def green(cls, t):
        return cls.w("38;5;78", t)

    @classmethod
    def yellow(cls, t):
        return cls.w("38;5;221", t)

    @classmethod
    def blue(cls, t):
        return cls.w("38;5;75", t)


def dwidth(s):
    """粗略显示宽度：CJK / 全角 / emoji 算 2 列。"""
    w = 0
    for ch in str(s):
        o = ord(ch)
        if o > 0x1100 and (
            0x2E80 <= o <= 0xA4CF
            or 0xAC00 <= o <= 0xD7A3
            or 0xF900 <= o <= 0xFAFF
            or 0xFE30 <= o <= 0xFE6F
            or 0xFF00 <= o <= 0xFF60
            or 0xFFE0 <= o <= 0xFFE6
            or 0x1F300 <= o <= 0x1FAFF
        ):
            w += 2
        else:
            w += 1
    return w


def pad(s, width):
    s = str(s)
    return s + " " * max(0, width - dwidth(s))


def cut(s, width):
    s = str(s)
    if dwidth(s) <= width:
        return s
    out, w = "", 0
    for ch in s:
        cw = 2 if dwidth(ch) == 2 else 1
        if w + cw > width - 1:
            break
        out += ch
        w += cw
    return out + "…"


# ----------------------------------------------------------------------------
# 抓取
# ----------------------------------------------------------------------------


class Fetcher:
    def __init__(self, verbose=True):
        self.verbose = verbose

    def log(self, msg):
        if self.verbose:
            print(C.dim(msg))

    def gql(self, variables, retries=4):
        body = json.dumps({"query": QUERY, "variables": variables}).encode("utf-8")
        last = None
        for attempt in range(1, retries + 1):
            req = urllib.request.Request(
                API,
                data=body,
                headers={
                    "User-Agent": UA,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("errors"):
                    raise RuntimeError(str(payload["errors"])[:200])
                return payload["data"]["Page"]
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:160]
                except Exception:
                    pass
                last = f"HTTP {e.code} {detail}"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            if attempt < retries:
                wait = 2.0 * attempt
                # AniList 有速率限制，429/400 时多等一会
                if last.startswith(("HTTP 429", "HTTP 400")):
                    wait = 3.0 * attempt
                self.log(f"    第 {attempt} 次失败（{last}），{wait:.0f}s 后重试…")
                time.sleep(wait)
        raise RuntimeError(f"AniList 请求失败：{last}")

    def window(self, start_ts, end_ts, max_pages=20):
        """翻页抓完窗口内的所有播出记录。"""
        out, page = [], 1
        total = None
        while page <= max_pages:
            p = self.gql({"from": start_ts, "to": end_ts, "page": page})
            if total is None:
                total = p["pageInfo"].get("total")
            out.extend(p.get("airingSchedules") or [])
            self.log(f"    第 {page} 页：累计 {len(out)} 条")
            if not p["pageInfo"].get("hasNextPage"):
                break
            page += 1
            time.sleep(0.7)  # 对公共 API 客气一点
        return out, total


def cache_path(mode):
    return os.path.join(CACHE_DIR, f"anilist_{mode}_{datetime.now():%Y-%m-%d}.json")


def save_cache(mode, start_ts, end_ts, entries):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(mode), "w", encoding="utf-8") as f:
        json.dump(
            {
                "mode": mode,
                "window": [start_ts, end_ts],
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "entries": entries,
            },
            f,
            ensure_ascii=False,
            indent=1,
        )


def load_cache(mode):
    if not os.path.isdir(CACHE_DIR):
        return None
    files = sorted(
        [
            f
            for f in os.listdir(CACHE_DIR)
            if f.startswith(f"anilist_{mode}_") and f.endswith(".json")
        ],
        reverse=True,
    )
    for name in files:
        try:
            with open(os.path.join(CACHE_DIR, name), "r", encoding="utf-8") as f:
                d = json.load(f)
            d["_from"] = name
            return d
        except Exception:
            continue
    return None


# ----------------------------------------------------------------------------
# 数据整理
# ----------------------------------------------------------------------------


def pick_title(m):
    t = m.get("title") or {}
    native = (t.get("native") or "").strip()
    romaji = (t.get("romaji") or "").strip()
    english = (t.get("english") or "").strip()
    main = native or romaji or english or f"#{m.get('id')}"
    subs = [x for x in (romaji, english) if x and x != main]
    return main, subs


def build_entries(raw, tz_offset_hours, args):
    """把接口原始记录整理成条目列表。"""
    now = time.time()
    out = []
    for item in raw:
        m = item.get("media") or {}
        if not m:
            continue
        if not args.all_countries and m.get("countryOfOrigin") != "JP":
            continue
        if m.get("isAdult") and not args.adult:
            continue
        if args.type and (m.get("format") or "").upper() != args.type.upper():
            continue
        score = m.get("averageScore") or 0
        if args.min_score and score < args.min_score:
            continue

        ts = item.get("airingAt")
        if not ts:
            continue
        # 按固定偏移换算显示时间，不依赖系统时区数据库（Windows 的 Python 没有 tzdata）
        local_dt = datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(hours=tz_offset_hours)
        jst_dt = datetime.fromtimestamp(ts, tz=JST)

        main, subs = pick_title(m)
        studios = ((m.get("studios") or {}).get("nodes") or [])
        stream = [
            {"site": e.get("site"), "url": e.get("url")}
            for e in (m.get("externalLinks") or [])
            if (e.get("type") or "").upper() == "STREAMING" and e.get("site")
        ]
        out.append(
            {
                "ts": ts,
                "aired": ts <= now,
                "episode": item.get("episode"),
                "title": main,
                "subs": subs,
                "cover": ((m.get("coverImage") or {}).get("large") or ""),
                "url": m.get("siteUrl") or f"https://anilist.co/anime/{m.get('id')}",
                "score": score,
                "format": FORMAT_NAMES.get((m.get("format") or "").upper(), m.get("format") or ""),
                "total_eps": m.get("episodes"),
                "genres": (m.get("genres") or [])[:3],
                "studio": (studios[0].get("name") if studios else "") or "",
                "stream": stream[:3],
                # 中文名（bgm.tv 是中文动画数据库，给个搜索入口，不依赖 B 站）
                "bgm": "https://bgm.tv/subject_search/" + quote(main) + "?cat=2",
                "time_local": local_dt.strftime("%H:%M"),
                "time_jst": jst_dt.strftime("%H:%M"),
                "date_local": local_dt.strftime("%Y-%m-%d"),
                "dow": local_dt.weekday(),
                "date_jst": jst_dt.strftime("%Y-%m-%d"),
            }
        )
    out.sort(key=lambda x: x["ts"])
    return out


def group_days(entries, start_date, num_days):
    """按本地日期分组成连续的天列表。"""
    buckets = {}
    for e in entries:
        buckets.setdefault(e["date_local"], []).append(e)
    days = []
    for i in range(num_days):
        d = start_date + timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        days.append(
            {
                "date": key,
                "label": f"{d.month}-{d.day}",
                "dow": WEEKDAYS[d.weekday()],
                "weekday_idx": d.weekday(),
                "eps": buckets.get(key, []),
            }
        )
    return days


# ----------------------------------------------------------------------------
# 终端渲染
# ----------------------------------------------------------------------------


def print_console(days, entries, now, fetched_at, today_key, from_cache=None, tz_label=""):
    print()
    print(C.pink("━" * 88))
    print(
        C.bold(" 🎌  日漫每日更新表 ")
        + C.dim(f" 数据源 AniList · 抓取于 {fetched_at} · 时间 {tz_label}")
        + (C.yellow(f"  [缓存 {from_cache}]") if from_cache else "")
    )
    print(C.pink("━" * 88))

    # 接下来要播的
    upcoming = [e for e in entries if not e["aired"]][:6]
    if upcoming:
        print()
        print(C.bold("⏭  接下来播出"))
        for e in upcoming:
            ep = f"第{e['episode']}话" if e["episode"] else ""
            print(
                f"   {pad(e['countdown'], 12)} {C.dim(e['time_local'])} "
                f"{pad(cut(e['title'], 40), 42)}{pad(ep, 8)}"
            )

    today_eps = [e for d in days if d["date"] == today_key for e in d["eps"]]
    print()
    print(
        C.bold("📅 今天 ") + C.pink(C.bold(f"{len(today_eps)} 部"))
        + C.dim(f"   本周窗口共 {len(entries)} 集")
    )

    for d in days:
        head = f"{d['dow']} {d['label']}"
        if d["date"] == today_key:
            head = C.pink(C.bold(f"▶ {head}  今天"))
        else:
            head = C.bold(head)
        print()
        print(f" {head}   {C.dim(str(len(d['eps'])) + ' 集')}")
        if not d["eps"]:
            print(C.dim("    —— 没有更新 ——"))
            continue
        for e in d["eps"]:
            ep = f"第{e['episode']}话" if e["episode"] else "--"
            score = f"{e['score']}" if e["score"] else "-"
            state = C.green("● 已播") if e["aired"] else C.dim("○ 待播")
            print(
                f"   {C.dim(e['time_local'])} "
                f"{pad(cut(e['title'], 42), 44)}"
                f"{pad(ep, 9)}{pad('[' + score + ']', 7)}{state}"
            )

    print()
    print(C.dim(" 数据来源：AniList  https://anilist.co/"))
    print(C.pink("━" * 88))
    print()


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------


def humanize(delta):
    if delta <= 0:
        return "已播出"
    d, rem = divmod(int(delta), 86400)
    h, m = divmod(rem // 60, 60)
    if d:
        return f"{d}天{h}小时后"
    if h:
        return f"{h}小时{m}分后"
    return f"{m}分钟后"


def main():
    setup_console()
    ap = argparse.ArgumentParser(
        description="日漫每日更新表（数据源 AniList，与 B 站无关）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--before", type=int, default=3, help="今天往前几天（默认 3）")
    ap.add_argument("--after", type=int, default=3, help="今天往后几天（默认 3）")
    ap.add_argument("--days", type=int, default=None, help="从今天起往后 N 天（等价于 --before 0 --after N-1）")
    ap.add_argument("--today", action="store_true", help="只看今天（等价于 --before 0 --after 0）")
    ap.add_argument("--min-score", type=float, default=0, help="只看 AniList 评分 ≥ 这个值的（0-100）")
    ap.add_argument("--type", default=None, help="只看某种格式：TV / MOVIE / OVA / ONA / SPECIAL")
    ap.add_argument("--all-countries", action="store_true", help="不限日本，包含中韩美等所有动画")
    ap.add_argument("--adult", action="store_true", help="包含成人向条目（默认过滤掉）")
    ap.add_argument("--tz", type=float, default=None, help="显示用的时区偏移小时，默认本机时区（如 --tz 8）")
    ap.add_argument("--offline", action="store_true", help="只用本地缓存，不联网")
    args = ap.parse_args()

    if args.today:
        args.before, args.after = 0, 0
    if args.days is not None:
        args.before, args.after = 0, max(0, args.days - 1)

    before, after = max(0, args.before), max(0, args.after)
    num_days = before + after + 1

    # 时区
    if args.tz is None:
        off_hours = -time.timezone / 3600.0
        if time.daylight and time.localtime().tm_isdst:
            off_hours = -time.altzone / 3600.0
        tz_label = f"本地时区 UTC{off_hours:+g}"
        if abs(off_hours - 8) < 0.01:
            tz_label = "北京时间 UTC+8"
    else:
        off_hours = args.tz
        tz_label = f"UTC{off_hours:+g}"
        if abs(off_hours - 8) < 0.01:
            tz_label = "北京时间 UTC+8"

    now = time.time()
    # 用「本地日期」的 00:00 作为窗口边界
    local_now = datetime.fromtimestamp(now, tz=timezone.utc) + timedelta(hours=off_hours)
    start_date = (local_now - timedelta(days=before)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_date = (local_now + timedelta(days=after + 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_ts = int((start_date - timedelta(hours=off_hours)).timestamp())
    end_ts = int((end_date - timedelta(hours=off_hours)).timestamp())

    mode = f"b{before}a{after}"
    raw = None
    from_cache = None
    if not args.offline:
        print(C.dim(f"→ 正在从 AniList 抓取 {start_date:%Y-%m-%d} ~ {(end_date - timedelta(days=1)):%Y-%m-%d} 的播出日程…"))
        try:
            raw, _api_total = Fetcher().window(start_ts, end_ts)
            print(C.dim(f"  抓到 {len(raw)} 条播出记录（全球各地区，稍后只保留日本动画）"))
            save_cache(mode, start_ts, end_ts, raw)
        except Exception as e:  # noqa: BLE001
            print(C.yellow(f"  ✗ {e}"))
    if raw is None:
        cached = load_cache(mode) or load_cache("b3a3")
        if cached:
            raw = cached["entries"]
            from_cache = cached["_from"]
            print(C.yellow(f"  改用本地缓存：{from_cache}"))
        else:
            print()
            print("没有拿到任何数据（既没网也没缓存）。请检查网络后重试。")
            return 1

    entries = build_entries(raw, off_hours, args)
    for e in entries:
        e["countdown"] = humanize(e["ts"] - now)
        e["dow_label"] = WEEKDAYS[e["dow"]]

    days = group_days(entries, start_date.date(), num_days)
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_key = local_now.strftime("%Y-%m-%d")

    print_console(days, entries, now, fetched_at, today_key, from_cache, tz_label)
    print(C.dim(" 想要窗口界面：python jp_anime_daily_gui.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
