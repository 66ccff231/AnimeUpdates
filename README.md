# 日漫每日更新表

看**日本动画**每天更新什么：终端里打印一周播出表，同时生成一个可离线打开的网页
`jp_anime_daily.html`（海报、搜索、筛选、点进详情）。

**数据源是 [AniList](https://anilist.co/)（日本动画的全球数据库），不是 B 站。**
AniList 的播出时间是各动画官方公布的在播时刻，精确到分钟，还带第几话、评分、制作公司、
可看的流媒体平台。

零依赖：只用 Python 标准库，不需要 `pip install`。

## 快速开始

双击 `双击我-查看日漫更新.bat`（抓「昨天 + 未来 6 天」，生成网页并自动打开）。

命令行方式：

```bat
cd C:\Users\柒柒\Desktop\jp-anime-daily
python jp_anime_daily.py
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--today` | 只看今天更新 |
| `--days 7` | 从今天起往后 7 天 |
| `--before 3` / `--after 3` | 今天往前 / 往后几天（默认都是 3） |
| `--min-score 75` | 只看 AniList 评分 ≥75 的（0–100） |
| `--type TV` | 只看某种格式：`TV` / `MOVIE` / `OVA` / `ONA` / `SPECIAL` |
| `--tz 8` | 显示用哪个时区，默认本机时区（`--tz 8` = 北京时间） |
| `--all-countries` | 不限日本，把中韩美动画也算进来 |
| `--adult` | 包含成人向条目（默认过滤掉） |
| `--offline` | 不联网，用 `data/` 里的缓存渲染 |
| `--no-open` | 生成网页但不自动打开浏览器 |
| `--html-only` | 只生成网页，不在终端打印表格 |

例子：

```bat
:: 今天有什么好看的（评分 75 以上）
python jp_anime_daily.py --today --min-score 75

:: 未来一周的 TV 动画
python jp_anime_daily.py --days 7 --type TV

:: 没网也能看（用上次抓的缓存）
python jp_anime_daily.py --offline --no-open
```

## 关于时间

页面和终端都同时给出**本地时间**和**日本时间（JST）**。
动画的播出时刻永远以 JST 为准，比如「23:00 JST」在北京时间就是 22:00。

## 关于中文名

AniList 只有日文原名、罗马音、英文名。每张卡片底部有 **「查中文名 bgm.tv ↗」**
链接，点开就是 bgm.tv（中文动画数据库）用日文原名搜出来的结果。

## 文件说明

- `jp_anime_daily.py` — 主程序（抓取 + 终端表格 + 生成网页）
- `jp_anime_daily.html` — 生成的网页，双击就能看
- `data/anilist_*.json` — 每次抓取的原始数据缓存
- `双击我-查看日漫更新.bat` — 一键启动

## 备注

- AniList 的公共 API 有限速（正常 90 次/分钟，紧张时会降到 30）。程序翻页之间
  会停 0.7 秒，遇到 429/400 会自动退避重试最多 4 次。
- 抓一周大约 3 页、110 条记录，很快。
- 接口自报的 `pageInfo.total` 数不准（会说 5000），所以程序只显示实际抓到的条数。
