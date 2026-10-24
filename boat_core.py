"""ボートレース 得点率・平均ST ランキングの計算ロジック.

データ元
- 今節: BOAT RACE公式サイトの出走表 (racelist) の「今節成績」
- 前節: 公式の競走成績ダウンロードデータ (日別 Kファイル, LZH形式)

計算ルール
- 着順点: 1着10 / 2着8 / 3着6 / 4着4 / 5着2 / 6着1
- 転覆・落水・失格・F・L など完走できなかったレースは 0点 (走数に含む)
- 欠場は走数に含めない
- 平均STは F・L・欠場 を除いて計算
- 今節は対象レースより前に終わったレースのみ
"""

from __future__ import annotations

import datetime as dt
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

import lzh

UA = {"User-Agent": "Mozilla/5.0 (personal boat race stats tool)"}
BASE = "https://www.boatrace.jp/owpc/pc/race"

VENUES = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川",
    "06": "浜名湖", "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国",
    "11": "びわこ", "12": "住之江", "13": "尼崎", "14": "鳴門", "15": "丸亀",
    "16": "児島", "17": "宮島", "18": "徳山", "19": "下関", "20": "若松",
    "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村",
}

POINTS = {1: 10, 2: 8, 3: 6, 4: 4, 5: 2, 6: 1}
Z2H = str.maketrans("０１２３４５６７８９", "0123456789")
PREV_SEARCH_DAYS = 60  # 前節を探しにいく最大日数


# ---------------------------------------------------------------- 取得 ----

def http_get(url: str) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r


def fetch_racelist_html(hd: str, jcd: str, rno: int) -> str:
    r = http_get(f"{BASE}/racelist?rno={rno}&jcd={jcd}&hd={hd}")
    r.encoding = "utf-8"
    return r.text


def fetch_result_html(hd: str, jcd: str, rno: int) -> str:
    r = http_get(f"{BASE}/raceresult?rno={rno}&jcd={jcd}&hd={hd}")
    r.encoding = "utf-8"
    return r.text


def fetch_index_html(hd: str) -> str:
    r = http_get(f"{BASE}/index?hd={hd}")
    r.encoding = "utf-8"
    return r.text


def fetch_k_text(day: dt.date) -> Optional[str]:
    """日別の競走成績ファイルを取得して展開する. 無ければ None."""
    url = f"https://www1.mbrace.or.jp/od2/K/{day:%Y%m}/k{day:%y%m%d}.lzh"
    try:
        r = requests.get(url, headers=UA, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code != 200 or b"-lh" not in r.content[:10]:
        return None
    try:
        return lzh.extract_first(r.content).decode("cp932", errors="replace")
    except Exception:
        return None


_K_CACHE: dict[dt.date, str] = {}
_K_LOCK = threading.Lock()
_K_CACHE_MAX = 150


def load_k_cached(day: dt.date) -> Optional[str]:
    """Kファイルをプロセス内でキャッシュして返す (取得できなかった日はキャッシュしない)."""
    if day in _K_CACHE:
        return _K_CACHE[day]
    text = fetch_k_text(day)
    if text is not None:
        with _K_LOCK:
            if len(_K_CACHE) >= _K_CACHE_MAX:
                _K_CACHE.pop(next(iter(_K_CACHE)))
            _K_CACHE[day] = text
    return text


# --------------------------------------------------------------- データ型 ----

@dataclass
class Run:
    place: Optional[int]      # 1-6 の着順. 完走できなかった場合は None
    code: str                 # 表示用の着順表記 (1, 転, F, 失 など)
    st: Optional[float]       # ST. F・L・欠場は None
    counted: bool = True      # 走数に含めるか (欠場は False)

    @property
    def points(self) -> int:
        return POINTS.get(self.place, 0) if self.counted else 0


@dataclass
class Series:
    venue: str = ""
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    runs: list[Run] = field(default_factory=list)

    @property
    def counted_runs(self) -> list[Run]:
        return [r for r in self.runs if r.counted]

    @property
    def total(self) -> int:
        return sum(r.points for r in self.counted_runs)

    @property
    def n(self) -> int:
        return len(self.counted_runs)

    @property
    def rate(self) -> Optional[float]:
        return self.total / self.n if self.n else None

    @property
    def sts(self) -> list[float]:
        return [r.st for r in self.runs if r.st is not None]

    @property
    def avg_st(self) -> Optional[float]:
        s = self.sts
        return sum(s) / len(s) if s else None

    @property
    def marks(self) -> str:
        return " ".join(r.code for r in self.runs)


@dataclass
class Racer:
    waku: int
    toban: str
    name: str
    klass: str
    branch: str
    current: Series
    prev: Series = field(default_factory=Series)

    @property
    def total_rate(self) -> Optional[float]:
        n = self.current.n + self.prev.n
        return (self.current.total + self.prev.total) / n if n else None

    @property
    def total_st(self) -> Optional[float]:
        s = self.current.sts + self.prev.sts
        return sum(s) / len(s) if s else None


@dataclass
class RaceInfo:
    hd: str
    jcd: str
    rno: int
    title: str
    race_name: str
    day_label: str
    series_start: dt.date
    racers: list[Racer]
    result: Optional[str] = None  # 例: "1-2-5（3連単 700円）"


# ------------------------------------------------------------ 出走表の解析 ----

def _norm(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", s or "")


def _tab_date(label: str, target: dt.date) -> Optional[dt.date]:
    m = re.search(r"(\d+)月(\d+)日", label)
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    year = target.year
    if mo > target.month + 6:      # 年をまたぐ節 (例: 12月開始→1月のレース)
        year -= 1
    elif mo < target.month - 6:
        year += 1
    return dt.date(year, mo, d)


def _parse_mark(text: str) -> tuple[Optional[int], str, bool]:
    t = _norm(text).translate(Z2H)
    if t in ("1", "2", "3", "4", "5", "6"):
        return int(t), t, True
    if t == "欠":
        return None, "欠", False
    return None, t, True


def _parse_st(text: str) -> Optional[float]:
    t = _norm(text)
    m = re.fullmatch(r"0?\.(\d{2})", t)
    return float("0." + m.group(1)) if m else None


def parse_racelist(html: str, hd: str, jcd: str, rno: int) -> RaceInfo:
    s = BeautifulSoup(html, "html.parser")
    target = dt.datetime.strptime(hd, "%Y%m%d").date()

    title_el = s.select_one("h2.heading2_titleName")
    race_el = s.select_one("h3.title16_titleDetail__add2020")
    tabs = s.select("div.tab2 ul li")
    if not tabs or not s.select("div.table1 tbody.is-fs12"):
        raise ValueError("出走表が見つかりませんでした。日付・会場・レース番号を確認してください。")

    labels = [li.get_text(" ", strip=True) for li in tabs]
    active = next((i for i, li in enumerate(tabs) if "is-active2" in (li.get("class") or [])), None)
    if active is None:
        raise ValueError("指定した日付はこの会場の開催日ではありません。")
    series_start = _tab_date(labels[0], target) or target
    day_label = labels[active].split()[-1]

    racers: list[Racer] = []
    for tb in s.select("div.table1 tbody.is-fs12"):
        rows = tb.find_all("tr", recursive=False)
        r0 = rows[0].find_all("td", recursive=False)
        waku = int(_norm(r0[0].get_text()).translate(Z2H))
        info = r0[2].get_text(" ", strip=True)
        m = re.search(r"(\d{4})\s*/\s*([AB][12])\s+(.+?)\s+(\S+?)/\S+\s+\d+歳", info)
        toban, klass, name, branch = (m.group(1), m.group(2), _norm(m.group(3)), m.group(4)) if m else (
            re.search(r"\d{4}", info).group(0), "", info, "")

        slot_race = r0[-15:-1]   # 14枠 (7日×2走). 末尾は「早見」
        slot_st = rows[2].find_all("td", recursive=False)
        slot_res = rows[3].find_all("td", recursive=False)

        runs: list[Run] = []
        for j in range(min(len(slot_race), len(slot_res))):
            race_txt = _norm(slot_race[j].get_text())
            res_txt = _norm(slot_res[j].get_text())
            if not race_txt.isdigit() or not res_txt:
                continue
            day_idx = j // 2
            if day_idx > active or (day_idx == active and int(race_txt) >= rno):
                continue
            place, code, counted = _parse_mark(res_txt)
            st = _parse_st(slot_st[j].get_text()) if counted else None
            runs.append(Run(place=place, code=code, st=st, counted=counted))

        cur = Series(venue=VENUES.get(jcd, ""), start=series_start, end=target, runs=runs)
        racers.append(Racer(waku, toban, name, klass, branch, cur))

    racers.sort(key=lambda r: r.waku)
    return RaceInfo(
        hd=hd, jcd=jcd, rno=rno,
        title=_norm(title_el.get_text()) if title_el else "",
        race_name=(race_el.get_text(" ", strip=True).split()[0] if race_el else ""),
        day_label=day_label, series_start=series_start, racers=racers,
    )


def parse_result(html: str) -> Optional[str]:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = re.search(r"3連単\s*(\d)\s*-\s*(\d)\s*-\s*(\d)\s*[¥￥]\s*([\d,]+)", text)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}（3連単 {m.group(4)}円）"


def parse_open_venues(html: str) -> list[str]:
    codes = re.findall(r"racelist\?[^\"']*jcd=(\d{2})", html)
    seen: list[str] = []
    for c in codes:
        if c not in seen:
            seen.append(c)
    return sorted(seen)


# ---------------------------------------------------- 競走成績(Kファイル) ----

@dataclass
class KEntry:
    day: dt.date
    venue: str
    day_no: int
    race: int
    run: Run


_K_LINE = re.compile(r"^\s{1,3}(\S+)\s+(\d)\s+(\d{4})\s+(.*)$")
_K_ST = re.compile(r"\d+\s+\d+\s+\S+\s+\d\s+(F|L)?(\d?\.\d+)")


def parse_k(text: str, day: dt.date) -> dict[str, list[KEntry]]:
    out: dict[str, list[KEntry]] = {}
    venue, day_no, race = "", 0, 0
    for line in text.splitlines():
        if "［成績］" in line:
            venue = _norm(line.split("［成績］")[0])
            m = re.search(r"第\s*(\d+)日", line)
            day_no = int(m.group(1)) if m else 0
            continue
        m = re.match(r"^\s+(\d+)R\s+\S", line)
        if m and "-" not in line[:20]:
            race = int(m.group(1))
            continue
        m = _K_LINE.match(line)
        if not m or not venue:
            continue
        code, toban, rest = m.group(1), m.group(3), m.group(4)
        if code.isdigit() and 1 <= int(code) <= 6:
            place, mark, counted = int(code), str(int(code)), True
        elif code.startswith("K"):
            place, mark, counted = None, "欠", False
        elif code.startswith("S"):
            place, mark, counted = None, "失", True
        else:  # F, L など
            place, mark, counted = None, code[0], True
        st = None
        sm = _K_ST.search(rest)
        if sm and counted and not sm.group(1):
            st = float(sm.group(2))
        out.setdefault(toban, []).append(
            KEntry(day, venue, day_no, race, Run(place, mark, st, counted)))
    return out


class KStore:
    """日別Kファイルの取得・解析結果をまとめて持つ (同じ日は1回だけ取得)."""

    def __init__(self, loader: Callable[[dt.date], Optional[str]] = load_k_cached):
        self.loader = loader
        self.cache: dict[dt.date, dict[str, list[KEntry]]] = {}

    def prefetch(self, days: list[dt.date], workers: int = 8) -> None:
        """まとめて並列取得しておく (前節検索を速くするため)."""
        todo = [d for d in days if d not in self.cache]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            texts = list(ex.map(self.loader, todo))
        for d, text in zip(todo, texts):
            self.cache[d] = parse_k(text, d) if text else {}

    def get(self, day: dt.date) -> dict[str, list[KEntry]]:
        if day not in self.cache:
            text = self.loader(day)
            self.cache[day] = parse_k(text, day) if text else {}
        return self.cache[day]


def find_prev_series(toban: str, before: dt.date, store: KStore) -> Series:
    """before より前で、その選手が最後に走った節を返す."""
    day = before - dt.timedelta(days=1)
    for _ in range(PREV_SEARCH_DAYS):
        entries = store.get(day).get(toban)
        if entries:
            break
        day -= dt.timedelta(days=1)
    else:
        return Series()

    venue = entries[0].venue
    collected: list[KEntry] = []
    d, misses = day, 0
    while misses <= 1 and (day - d).days < 10:
        es = [e for e in store.get(d).get(toban, []) if e.venue == venue]
        if es:
            collected = es + collected
            misses = 0
            if min(e.day_no for e in es) <= 1:
                break
        else:
            misses += 1
        d -= dt.timedelta(days=1)

    collected.sort(key=lambda e: (e.day, e.race))
    return Series(
        venue=venue,
        start=collected[0].day if collected else None,
        end=collected[-1].day if collected else None,
        runs=[e.run for e in collected],
    )


# ------------------------------------------------------------- ランキング ----

def round_half_up(v: Optional[float], digits: int = 2) -> Optional[float]:
    if v is None:
        return None
    from decimal import ROUND_HALF_UP, Decimal
    return float(Decimal(str(v)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def rank_map(values: dict[int, Optional[float]], higher_is_better: bool,
             digits: Optional[int] = 2) -> dict[int, int]:
    """値で順位付け. 表示と同じ桁(既定は小数第2位)で同じ値なら同順位. None は最下位扱い."""
    if digits is not None:
        values = {k: round_half_up(v, digits) for k, v in values.items()}

    def key(k):
        v = values[k]
        if v is None:
            return float("inf")
        return -v if higher_is_better else v
    order = sorted(values, key=key)
    ranks, prev_key, prev_rank = {}, None, 0
    for i, k in enumerate(order, 1):
        kk = round(key(k), 6)
        if kk != prev_key:
            prev_rank, prev_key = i, kk
        ranks[k] = prev_rank
    return ranks


def overall(racers: list[Racer], rate_weight: int = 1) -> list[dict]:
    rates = {r.waku: r.total_rate for r in racers}
    sts = {r.waku: r.total_st for r in racers}
    rr = rank_map(rates, higher_is_better=True)
    sr = rank_map(sts, higher_is_better=False)
    rows = []
    for r in racers:
        rows.append({
            "racer": r,
            "rate_rank": rr[r.waku],
            "st_rank": sr[r.waku],
            "score": rr[r.waku] * rate_weight + sr[r.waku],
        })
    rows.sort(key=lambda x: (x["score"], -(x["racer"].total_rate or -1), x["racer"].waku))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def build_race(hd: str, jcd: str, rno: int,
               racelist_loader=fetch_racelist_html,
               result_loader=fetch_result_html,
               store: Optional[KStore] = None) -> RaceInfo:
    info = parse_racelist(racelist_loader(hd, jcd, rno), hd, jcd, rno)
    store = store or KStore()
    store.prefetch([info.series_start - dt.timedelta(days=i) for i in range(1, 22)])
    for r in info.racers:
        r.prev = find_prev_series(r.toban, info.series_start, store)
    try:
        info.result = parse_result(result_loader(hd, jcd, rno))
    except Exception:
        info.result = None
    return info
