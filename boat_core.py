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
import json
import re
import tempfile
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

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

_SESSION = requests.Session()
_SESSION.headers.update(UA)
_SESSION.mount("https://", HTTPAdapter(pool_connections=8, pool_maxsize=32, max_retries=1))
TIMEOUT = (6, 25)  # (接続, 読み込み) 秒


def http_get(url: str) -> requests.Response:
    r = _SESSION.get(url, timeout=TIMEOUT)
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
        r = _SESSION.get(url, timeout=TIMEOUT)
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


_K_DIR = Path(tempfile.gettempdir()) / "boatdx_k"


def load_k_cached(day: dt.date) -> Optional[str]:
    """Kファイルをメモリとディスクにキャッシュして返す (取得できなかった日はキャッシュしない)."""
    if day in _K_CACHE:
        return _K_CACHE[day]
    path = _K_DIR / f"K{day:%y%m%d}.txt"
    text = None
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = None
    if text is None:
        text = fetch_k_text(day)
        if text is not None:
            try:
                _K_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except OSError:
                pass
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
    course: Optional["CourseStat"] = None   # 今回の枠と同じコースでの成績

    @property
    def course_top3(self) -> Optional[float]:
        return self.course.top3 if self.course else None

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


def parse_result_detail(html: str) -> Optional[tuple[list[int], str]]:
    """結果ページから 3連単の着順 (艇番3つ) と払戻金を取り出す. 未確定なら None."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = re.search(r"3連単\s*(\d)\s*-\s*(\d)\s*-\s*(\d)\s*[¥￥]\s*([\d,]+)", text)
    if not m:
        return None
    return [int(m.group(i)) for i in (1, 2, 3)], m.group(4)


def parse_result(html: str) -> Optional[str]:
    d = parse_result_detail(html)
    if not d:
        return None
    (a, b, c), pay = d
    return f"{a}-{b}-{c}（3連単 {pay}円）"


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
        self.missing: set[dt.date] = set()  # データを取れなかった日

    def prefetch(self, days: list[dt.date], workers: int = 16) -> None:
        """まとめて並列取得しておく (前節検索を速くするため)."""
        todo = [d for d in dict.fromkeys(days) if d not in self.cache]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=workers) as ex:
            texts = list(ex.map(self.loader, todo))
        for d, text in zip(todo, texts):
            self.cache[d] = parse_k(text, d) if text else {}
            if not text:
                self.missing.add(d)

    def get(self, day: dt.date) -> dict[str, list[KEntry]]:
        if day not in self.cache:
            text = self.loader(day)
            self.cache[day] = parse_k(text, day) if text else {}
            if not text:
                self.missing.add(day)
        return self.cache[day]


def find_prev_series(toban: str, before: dt.date, store: KStore,
                     max_days: int = PREV_SEARCH_DAYS, fetch: bool = True) -> Series:
    """before より前で、その選手が最後に走った節を返す.
    fetch=False のときは取得済みのデータだけで探す (待ち時間を増やさない)."""
    get = store.get if fetch else (lambda d: store.cache.get(d, {}))
    day = before - dt.timedelta(days=1)
    for _ in range(max_days):
        entries = get(day).get(toban)
        if entries:
            break
        day -= dt.timedelta(days=1)
    else:
        return Series()

    venue = entries[0].venue
    collected: list[KEntry] = []
    d, misses = day, 0
    while misses <= 1 and (day - d).days < 10:
        es = [e for e in get(d).get(toban, []) if e.venue == venue]
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


# ------------------------------------------------- 過去3節ページ (前節の特定) ----

@dataclass
class B3Race:
    day: Optional[dt.date]
    rno: Optional[int]
    mark: str


@dataclass
class B3Series:
    jcd: str
    venue: str
    start: dt.date
    end: dt.date
    races: list[B3Race]


_B3_CACHE: dict[str, tuple[float, list[B3Series]]] = {}
_B3_TTL = 3 * 3600


def parse_back3(html: str) -> list[B3Series]:
    s = BeautifulSoup(html, "html.parser")
    out: list[B3Series] = []
    for tb in s.select("table tbody"):
        tds = tb.find_all("td", recursive=True)
        if len(tds) < 6:
            continue
        dates = re.findall(r"(\d{4})/(\d{2})/(\d{2})", tds[0].get_text())
        if len(dates) < 2:
            continue
        start, end = (dt.date(int(y), int(m), int(d)) for y, m, d in dates[:2])
        img = tds[1].find("img")
        venue = _norm(img.get("alt", "")) if img else ""
        races: list[B3Race] = []
        jcd = ""
        for node in tds[-1].children:
            if getattr(node, "name", None) == "a":
                href = node.get("href", "")
                mm = re.search(r"rno=(\d+)&(?:amp;)?jcd=(\d{2})&(?:amp;)?hd=(\d{8})", href)
                mark = _norm(node.get_text()).strip("[]［］")
                if mm:
                    jcd = mm.group(2)
                    races.append(B3Race(dt.datetime.strptime(mm.group(3), "%Y%m%d").date(), int(mm.group(1)), mark))
                else:
                    races.append(B3Race(None, None, mark))
            else:
                for ch in _norm(str(node)):
                    races.append(B3Race(None, None, ch))
        out.append(B3Series(jcd, venue or VENUES.get(jcd, ""), start, end, races))
    return out


def fetch_back3(toban: str) -> Optional[list[B3Series]]:
    hit = _B3_CACHE.get(toban)
    if hit and time.time() - hit[0] < _B3_TTL:
        return hit[1]
    try:
        r = http_get(f"https://www.boatrace.jp/owpc/pc/data/racersearch/back3?toban={toban}")
        r.encoding = "utf-8"
        data = parse_back3(r.text)
    except Exception:
        return None
    _B3_CACHE[toban] = (time.time(), data)
    return data


def pick_prev(series_list: Optional[list[B3Series]], before: dt.date) -> Optional[B3Series]:
    """今節の初日より前に終わった節のうち、いちばん新しいものを選ぶ."""
    if not series_list:
        return None
    cands = [x for x in series_list if x.end < before]
    return max(cands, key=lambda x: x.end) if cands else None


def prev_from_back3(toban: str, b3: B3Series, store: KStore) -> Series:
    """過去3節ページの着順に、Kファイルから取ったSTを合わせる."""
    runs: list[Run] = []
    for r in b3.races:
        k = None
        if r.day is not None:
            k = next((e for e in store.get(r.day).get(toban, []) if e.race == r.rno), None)
        if k is not None:
            runs.append(k.run)
        else:
            place, code, counted = _parse_mark(r.mark)
            runs.append(Run(place, code, None, counted))
    return Series(venue=b3.venue, start=b3.start, end=b3.end, runs=runs)


# --------------------------------------------------------- コース別成績 ----

@dataclass
class CourseStat:
    course: int
    top3: Optional[float]   # 3連対率 (%)
    win: Optional[float]    # 1着率 (%)


_COURSE_CACHE: dict[str, tuple[float, dict[int, CourseStat]]] = {}
_COURSE_TTL = 12 * 3600
_COURSE_DIR = Path(tempfile.gettempdir()) / "boatdx_course"


def _pct(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text or "")
    return float(m.group(1)) if m else None


def parse_course(html: str) -> dict[int, CourseStat]:
    """選手ページ「コース別成績」から、コースごとの3連対率と1着率を取り出す."""
    s = BeautifulSoup(html, "html.parser")
    out: dict[int, CourseStat] = {}
    for table in s.find_all("table"):
        head = table.find("thead")
        if not head or "コース別3連対率" not in head.get_text():
            continue
        for tr in table.select("tbody tr"):
            th = tr.find("th")
            c = _norm(th.get_text()).translate(Z2H) if th else ""
            if not c.isdigit():
                continue
            label = tr.select_one(".table1_progress2Label")
            bars = tr.select(".table1_progress2Bar > span.is-progress")
            win = _pct(bars[0].get("style", "")) if bars else None
            out[int(c)] = CourseStat(int(c), _pct(label.get_text() if label else ""), win)
        break
    return out


def fetch_course(toban: str) -> Optional[dict[int, CourseStat]]:
    hit = _COURSE_CACHE.get(toban)
    if hit and time.time() - hit[0] < _COURSE_TTL:
        return hit[1]
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    path = _COURSE_DIR / f"{toban}_{today:%Y%m%d}.json"
    data = None
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            data = {int(k): CourseStat(int(k), v[0], v[1]) for k, v in raw.items()}
        except (OSError, ValueError):
            data = None
    if data is None:
        try:
            r = http_get(f"https://www.boatrace.jp/owpc/pc/data/racersearch/course?toban={toban}")
            r.encoding = "utf-8"
            data = parse_course(r.text)
        except Exception:
            return None
        try:
            _COURSE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({k: [v.top3, v.win] for k, v in data.items()}), encoding="utf-8")
        except OSError:
            pass
    _COURSE_CACHE[toban] = (time.time(), data)
    return data


# ------------------------------------------------------- 合計点からの目安 ----
# 10/23・10/24 の全会場336レースで、合計点 (3つを同じ重み) と実際の着順を集計した結果から決めた目安.

GUIDE = {  # (枠の区分, 点数帯) -> (1着率, 3連対率)
    ("1", "3〜7"): (67, 89), ("1", "8〜11"): (45, 82), ("1", "12〜16"): (25, 63),
    ("2-6", "3〜7"): (21, 68), ("2-6", "8〜11"): (9, 50), ("2-6", "12〜16"): (4, 31),
}


def score_band(score: int) -> str:
    return "3〜7" if score <= 7 else "8〜11" if score <= 11 else "12〜16" if score <= 16 else "17〜18"


def candidate_label(waku: int, score: int) -> tuple[str, str]:
    """枠番と合計点 (同じ重み) から、着順の目安ラベルを返す. 戻り値: (ラベル, 種類)."""
    if waku == 1:
        if score <= 11:
            return "1〜2着候補", "c12"
        if score <= 16:
            return "2〜3着候補", "c23"
        return "消し候補", "cx"
    if score <= 7:
        return "2〜3着候補", "c23"
    if score <= 11:
        return "3着候補", "c3"
    return "消し候補", "cx"


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
    """得点率・平均ST・コース別3連対率の順位を合計して並べる."""
    rr = rank_map({r.waku: r.total_rate for r in racers}, higher_is_better=True)
    sr = rank_map({r.waku: r.total_st for r in racers}, higher_is_better=False)
    cr = rank_map({r.waku: r.course_top3 for r in racers}, higher_is_better=True, digits=1)
    rows = []
    for r in racers:
        rows.append({
            "racer": r,
            "rate_rank": rr[r.waku],
            "st_rank": sr[r.waku],
            "course_rank": cr[r.waku],
            "score": rr[r.waku] * rate_weight + sr[r.waku] + cr[r.waku],
        })
    rows.sort(key=lambda x: (x["score"], -(x["racer"].total_rate or -1), x["racer"].waku))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def build_race(hd: str, jcd: str, rno: int,
               racelist_loader=fetch_racelist_html,
               result_loader=fetch_result_html,
               store: Optional[KStore] = None) -> RaceInfo:
    """出走表・前節・STをまとめて取得する.
    公式サイトは1回の応答に時間がかかるので、なるべく同時に取りに行き、
    取得済みのデータはディスクに残して使い回す."""
    store = store or KStore()
    target = dt.datetime.strptime(hd, "%Y%m%d").date()
    window = [target - dt.timedelta(days=i) for i in range(1, 29)]

    # 1) 出走表と、直近4週間の成績データを同時に取りに行く (取得済みの日は通信なし)
    with ThreadPoolExecutor(max_workers=2) as ex:
        k_fut = ex.submit(store.prefetch, window, 24)
        info = parse_racelist(racelist_loader(hd, jcd, rno), hd, jcd, rno)
        k_fut.result()
    start = info.series_start
    span = (start - window[-1]).days

    # 2) まずは手元の成績データだけで前節を探す
    lost = []
    for r in info.racers:
        r.prev = find_prev_series(r.toban, start, store, max_days=span, fetch=False)
        gap_missing = r.prev.end and any(
            (r.prev.end + dt.timedelta(days=i)) in store.missing
            for i in range(1, (start - r.prev.end).days))
        if not r.prev.runs or gap_missing:
            lost.append(r)

    # 3) 6人のコース別成績と、前節が見つからない選手の過去3節ページを同時に取る
    with ThreadPoolExecutor(max_workers=12) as ex:
        cs = {r.toban: ex.submit(fetch_course, r.toban) for r in info.racers}
        b3 = {r.toban: ex.submit(fetch_back3, r.toban) for r in lost}
        cs = {t: f.result() for t, f in cs.items()}
        b3 = {t: pick_prev(f.result(), start) for t, f in b3.items()}
    for r in info.racers:
        r.course = (cs.get(r.toban) or {}).get(r.waku)
    if lost:
        store.prefetch([x.day for p in b3.values() if p for x in p.races if x.day])
        for r in lost:
            if b3.get(r.toban):
                r.prev = prev_from_back3(r.toban, b3[r.toban], store)

    if result_loader is not None:
        try:
            info.result = parse_result(result_loader(hd, jcd, rno))
        except Exception:
            info.result = None
    return info


def warm_up(days: int = 28) -> None:
    """アプリを開いた時点で、直近の成績データを先に集めておく."""
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    KStore().prefetch([today - dt.timedelta(days=i) for i in range(1, days + 1)], 24)
