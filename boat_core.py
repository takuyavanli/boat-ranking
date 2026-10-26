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

# app.py と組み合わせが合っているかの確認用. app.py の APP_VERSION と同じにしておく.
VERSION = "2026-10-27"

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


_RACELIST_HTML: dict[tuple[str, str, int], tuple[float, str]] = {}


def fetch_racelist_html(hd: str, jcd: str, rno: int) -> str:
    """出走表のHTML. 2分以内に取ったものは使い回す (先読みと本番で二重に取らない)."""
    key = (hd, jcd, rno)
    hit = _RACELIST_HTML.get(key)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    r = http_get(f"{BASE}/racelist?rno={rno}&jcd={jcd}&hd={hd}")
    r.encoding = "utf-8"
    if len(_RACELIST_HTML) > 300:
        for k in list(_RACELIST_HTML)[:100]:
            _RACELIST_HTML.pop(k, None)
    _RACELIST_HTML[key] = (time.time(), r.text)
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
    motor_no: Optional[int] = None
    motor_total2: Optional[float] = None     # モーターの期間通算2連対率 (出走表の数字)
    motor_prev: list = field(default_factory=list)   # モーターの前節の着順 (前の使用者)

    @property
    def course_top2(self) -> Optional[float]:
        return self.course.top2 if self.course else None

    @property
    def course_grade(self) -> Optional[str]:
        return course_grade(self.waku, self.course_top3, self.course_top2)

    @staticmethod
    def _top2(places: list) -> tuple[int, int]:
        n = len(places)
        return sum(1 for p in places if p in (1, 2)), n

    @property
    def motor_cur(self) -> tuple[int, int]:
        """モーターの今節 (この選手) の 2連対数・走数."""
        return self._top2([r.place for r in self.current.runs if r.counted])

    @property
    def motor_prev_top2(self) -> tuple[int, int]:
        return self._top2(self.motor_prev)

    @staticmethod
    def _rate(t):
        return t[0] / t[1] * 100 if t[1] else None

    @property
    def motor_cur_rate(self) -> Optional[float]:
        return self._rate(self.motor_cur)

    @property
    def motor_prev_rate(self) -> Optional[float]:
        return self._rate(self.motor_prev_top2)

    @property
    def motor_rate(self) -> Optional[float]:
        """モーターの2連対率 (今節+前節の合算)."""
        a, b = self.motor_cur, self.motor_prev_top2
        return self._rate((a[0] + b[0], a[1] + b[1]))

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
        racer = Racer(waku, toban, name, klass, branch, cur)
        mm = re.match(r"(\d+)\s+([\d.]+)", r0[6].get_text(" ", strip=True)) if len(r0) > 6 else None
        if mm:
            racer.motor_no, racer.motor_total2 = int(mm.group(1)), float(mm.group(2))
        racers.append(racer)

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
    motor: Optional[int] = None


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
        motor = None
        mo = re.search(r"(\d+)\s+(\d+)\s+\S+\s+\d\s", rest)
        if mo:
            motor = int(mo.group(1))
        st = None
        sm = _K_ST.search(rest)
        if sm and counted and not sm.group(1):
            st = float(sm.group(2))
        out.setdefault(toban, []).append(
            KEntry(day, venue, day_no, race, Run(place, mark, st, counted), motor))
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
    top2: Optional[float] = None  # 2連対率 (%) = 1着率 + 2着率


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
            second = _pct(bars[1].get("style", "")) if len(bars) > 1 else None
            top3 = _pct(label.get_text() if label else "")
            top2 = round(win + second, 1) if win is not None and second is not None else (
                win if win is not None and top3 is not None else None)
            out[int(c)] = CourseStat(int(c), top3, win, top2)
        break
    return out


def fetch_course(toban: str) -> Optional[dict[int, CourseStat]]:
    hit = _COURSE_CACHE.get(toban)
    if hit and time.time() - hit[0] < _COURSE_TTL:
        return hit[1]
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    path = _COURSE_DIR / f"v2_{toban}_{today:%Y%m%d}.json"
    data = None
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            data = {int(k): CourseStat(int(k), v[0], v[1], v[2]) for k, v in raw.items()}
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
            path.write_text(json.dumps({k: [v.top3, v.win, v.top2] for k, v in data.items()}), encoding="utf-8")
        except OSError:
            pass
    _COURSE_CACHE[toban] = (time.time(), data)
    return data


# -------------------------------------------------------- コースのAI評価 ----
# 同じコースを走る選手全体 (10/17〜10/24 の出走データ) と比べて、
# その選手のコース成績 (3連対率と2連対率の平均) がどの位置にあるかで S〜D を付ける.
# S: 上位10%  A: 上位30%  B: 中間  C: 下位30%  D: 下位10%

COURSE_GRADE_TH = {  # コース: (S, A, B, C) の下限
    1: (92.6, 86.3, 69.5, 53.1),
    2: (68.8, 59.0, 42.4, 30.0),
    3: (65.7, 55.5, 40.0, 28.5),
    4: (59.4, 50.0, 34.7, 23.5),
    5: (54.8, 43.5, 28.2, 18.7),
    6: (45.5, 33.3, 20.0, 9.5),
}
GRADE_TEXT = {"S": "得意", "A": "良い", "B": "普通", "C": "やや苦手", "D": "苦手"}
GRADE_RANGE = {"S": "上位10%", "A": "上位30%", "B": "中間", "C": "下位30%", "D": "下位10%"}


def course_grade(course: int, top3: Optional[float], top2: Optional[float]) -> Optional[str]:
    if top3 is None or top2 is None or course not in COURSE_GRADE_TH:
        return None
    v = (top3 + top2) / 2
    s_, a_, b_, c_ = COURSE_GRADE_TH[course]
    return "S" if v >= s_ else "A" if v >= a_ else "B" if v >= b_ else "C" if v >= c_ else "D"


# ------------------------------------------------------- 合計点からの目安 ----
# 10/17〜10/24 の全場1,248レースで、合計点 (3項目を同じ重み・3〜18点) と実際の着順を集計して決めた目安.
# モーター2連対率を入れた4項目版や、コース2連対率を使う版も試したが、この3項目版がいちばん当たりやすかった.

RULES = {
    "t1a": 11,   # 1号艇: この点数以下なら 1〜2着候補
    "t1b": 16,   # 1号艇: この点数以下なら 2〜3着候補 (それより上は消し候補)
    "t2a": 7,    # 2〜6号艇: この点数以下なら 2〜3着候補
    "t2b": 11,   # 2〜6号艇: この点数以下なら 3着候補 (それより上は消し候補)
    "mk": 7,     # まくり型の点数の上限
    "vbuy": 7,   # 本線を「買い」にする1号艇の点数の上限
}

# 目安の表: (ラベルの種類, 1号艇の条件と成績, 2〜6号艇の条件と成績). 成績は (艇数, 1着率, 2連対率, 3連対率)
GUIDE_ROWS = [
    ("c12", ("11点以下", (1110, 56, 73, 84)), None),
    ("c23", ("12〜16点", (133, 29, 49, 63)), ("7点以下", (1272, 21, 46, 68))),
    ("c3", None, ("8〜11点", (2300, 10, 29, 49))),
    ("cx", ("17点以上（5艇のみ）", (5, 20, 40, 40)), ("12点以上", (2668, 4, 14, 27))),
]

MAKURI_GUIDE = (28, 56, 72)        # まくり型の 1着率・2連対率・3連対率 (378艇)
MAKURI_RACE_1WIN = (57, 43)        # まくり型がいないレース / いるレースの1号艇の1着率


def score_band(score: int) -> str:
    return "low" if score <= RULES["t2a"] else "mid" if score <= RULES["t2b"] else "high"


def candidate_label(waku: int, score: int, makuri: bool = False) -> tuple[str, str]:
    """枠番と合計点 (同じ重み) から、着順の目安ラベルを返す. 戻り値: (ラベル, 種類)."""
    if makuri:
        return "1着候補", "c1"
    if waku == 1:
        if score <= RULES["t1a"]:
            return "1〜2着候補", "c12"
        if score <= RULES["t1b"]:
            return "2〜3着候補", "c23"
        return "消し候補", "cx"
    if score <= RULES["t2a"]:
        return "2〜3着候補", "c23"
    if score <= RULES["t2b"]:
        return "3着候補", "c3"
    return "消し候補", "cx"


def makuri_wakus(racers: list[Racer], eq_score: dict[int, int]) -> set[int]:
    """1着候補 (まくり型) の枠番を返す.
    2〜4号艇で、合計点 (同じ重み) が RULES["mk"] 以下、かつ平均STが内側の艇全員より0.02秒以上早い艇."""
    by_waku = {r.waku: r for r in racers}
    out = set()
    for r in racers:
        if not (2 <= r.waku <= 4) or eq_score.get(r.waku, 99) > RULES["mk"] or r.total_st is None:
            continue
        inner = [by_waku.get(i) for i in range(1, r.waku)]
        if all(x is None or x.total_st is None or round(x.total_st - r.total_st, 3) >= 0.02 for x in inner):
            out.add(r.waku)
    return out


# ------------------------------------------------------------ 買い目の目安 ----

LABEL_STRENGTH = {"c1": 0, "c12": 1, "c23": 2, "c3": 3, "cx": 4}


def _formation(head: int, second: list[int], third: list[int]) -> list[tuple[int, int, int]]:
    """1着固定・2着2艇・3着3艇のフォーメーション (重複を除いて4点)."""
    out = []
    for a in second:
        for b in third:
            if len({head, a, b}) == 3:
                out.append((head, a, b))
    return out[:4]


def _main8(head: int, rest: list[int]) -> list[tuple[int, int, int]]:
    """本線8点: 1着固定・2着3艇・3着4艇 (いちばん下の組み合わせ 頭-C-D を1点削る)."""
    A, B, C, D = rest[:4]
    out = [(head, a, c) for a in (A, B, C) for c in (A, B, C, D) if c != a]
    out.remove((head, C, D))
    return out


def _ana8(head: int, rest: list[int]) -> list[tuple[int, int, int]]:
    """穴目8点: 頭と次の上位艇の1・2着裏表、3着は残り全艇."""
    A = rest[0]
    others = [w for w in rest if w != A]
    return [(head, A, c) for c in others] + [(A, head, c) for c in others]


def suggest_bets(order: list[int], labels: dict[int, str], points: int = 4) -> dict:
    """総合順位の並び (枠番の順) とラベルから、本線・穴目 (3連単) を作る.
    本線の頭: 1号艇が「1〜2着候補」なら1号艇。そうでなければ★1着候補、いなければ総合1位。
    穴目の頭: 本線の頭と1号艇以外で、★1着候補 (いなければ総合順位が一番上の艇)。
    相手は頭を除いた総合順位の上位から選ぶ。points=4 か 8。"""
    makuri = [w for w in order if labels.get(w) == "c1"]
    if labels.get(1) == "c12":
        head = 1
    elif makuri:
        head = makuri[0]
    else:
        head = order[0]
    rest = [w for w in order if w != head]

    cands = [w for w in order if w not in (head, 1)]
    cands.sort(key=lambda w: (LABEL_STRENGTH.get(labels.get(w), 9) != 0, order.index(w)))
    ana_head = cands[0]
    rest2 = [w for w in order if w != ana_head]

    j = lambda xs: "・".join(str(x) for x in xs)
    if points == 12:
        flow = lambda h, r: [(h, a, c) for a in r[:4] for c in r[:4] if a != c]
        main = flow(head, rest)
        main_desc = f"1着 <b>{head}</b>　2着 <b>{j(rest[:4])}</b>　3着 <b>{j(rest[:4])}</b>"
        ana = flow(ana_head, rest2)
        ana_desc = f"1着 <b>{ana_head}</b>　2着 <b>{j(rest2[:4])}</b>　3着 <b>{j(rest2[:4])}</b>"
    elif points == 8:
        main = _main8(head, rest)
        main_desc = (f"1着 <b>{head}</b>　2着 <b>{j(rest[:3])}</b>　3着 <b>{j(rest[:4])}</b>"
                     f"（{head}-{rest[2]}-{rest[3]} を除く）")
        ana = _ana8(ana_head, rest2)
        ana_desc = (f"1・2着 <b>{ana_head}</b>と<b>{rest2[0]}</b>の裏表　"
                    f"3着 <b>{j([w for w in rest2 if w != rest2[0]])}</b>")
    else:
        main = _formation(head, rest[:2], rest[:3])
        main_desc = f"1着 <b>{head}</b>　2着 <b>{j(rest[:2])}</b>　3着 <b>{j(rest[:3])}</b>"
        ana = _formation(ana_head, rest2[:2], rest2[:3])
        ana_desc = f"1着 <b>{ana_head}</b>　2着 <b>{j(rest2[:2])}</b>　3着 <b>{j(rest2[:3])}</b>"
    return {
        "main": main, "main_head": head, "main_desc": main_desc,
        "ana": ana, "ana_head": ana_head, "ana_desc": ana_desc,
        # 以前の形 (集計スクリプト用)
        "main_form": (head, rest[:2], rest[:3]), "ana_form": (ana_head, rest2[:2], rest2[:3]),
    }


# --------------------------------------------------------------- 買い判定 ----
# 10/17〜10/24 の全場1,248レースで、本線・穴目の的中率と回収率 (1点100円) を集計した結果から決めた判定.
# 前半 (10/21〜24) と後半 (10/17〜20) の両方で同じ傾向になったものだけを使っている.

def bet_verdicts(score1: int, main_head: int, points: int = 4) -> dict:
    """1号艇の合計点 (同じ重み)・本線の頭・点数から、本線と穴目の判定を返す.
    戻り値: {"main": (判定, 種類, 説明), "ana": (...)}"""
    buy = score1 <= RULES["vbuy"]
    if points == 12:
        if main_head == 1 and buy:
            main = ("◎ 買い", "v-buy", "このタイプの本線12点：的中53%・回収81%（653レース）")
        elif main_head == 1:
            main = ("見送り推奨", "v-skip", "このタイプの本線12点：的中34%・回収66%（457レース）")
        else:
            main = ("○ 狙い", "v-ref", "このタイプの本線12点：的中28%・回収136%（138レース）。前半124%・後半148%")
        if buy:
            ana = ("見送り推奨", "v-skip", "1号艇7点以下のときの穴目12点：的中12%・回収57%（653レース）")
        else:
            ana = ("遊び枠", "v-play", "このタイプの穴目12点：的中17%・回収112%（595レース）。前半81%・後半143%とぶれが大きいので少額で")
        return {"main": main, "ana": ana}
    if points == 8:
        if main_head == 1 and buy:
            main = ("◎ 買い", "v-buy", "このタイプの本線8点：的中44%・回収84%（653レース）")
        elif main_head == 1:
            main = ("見送り推奨", "v-skip", "このタイプの本線8点：的中26%・回収56%（457レース）")
        else:
            main = ("○ 狙い", "v-ref", "このタイプの本線8点：的中20%・回収152%（138レース）。前半・後半とも100%超えだが、レース数は少なめ")
        if buy:
            ana = ("見送り推奨", "v-skip", "1号艇7点以下のときの穴目8点：的中26%・回収62%（653レース）")
        else:
            ana = ("遊び枠", "v-play", "このタイプの穴目8点：的中16%・回収98%（595レース）。期間で回収がぶれるので少額で")
        return {"main": main, "ana": ana}
    if main_head == 1 and buy:
        main = ("◎ 買い", "v-buy", "このタイプの本線4点：的中28%・回収92%（653レース）")
    elif main_head == 1:
        main = ("見送り推奨", "v-skip", "このタイプの本線4点：的中17%・回収60%（457レース）")
    else:
        main = ("△ 参考", "v-ref", "このタイプの本線4点：的中12%・回収196%（138レース）。高配当頼みで判断保留")
    if buy:
        ana = ("見送り推奨", "v-skip", "1号艇7点以下のときの穴目4点：的中6%・回収64%（653レース）")
    else:
        ana = ("遊び枠", "v-play", "このタイプの穴目4点：的中8%・回収73%（595レース）。少額での狙いに")
    return {"main": main, "ana": ana}


# --------------------------------------------------------- モーターの前節 ----

def motor_prev_runs(venue: str, before: dt.date, store: KStore, max_days: int = 28) -> dict[int, list]:
    """その場の前の節で、各モーターが走った着順を返す. {モーター番号: [着順, ...]}
    取得済みの成績データだけで探す (待ち時間を増やさない)."""
    def venue_entries(day):
        return [e for es in store.cache.get(day, {}).values() for e in es if e.venue == venue]
    day = before - dt.timedelta(days=1)
    last = None
    for _ in range(max_days):
        if venue_entries(day):
            last = day
            break
        day -= dt.timedelta(days=1)
    if last is None:
        return {}
    out: dict[int, list] = {}
    d = last
    for _ in range(10):
        es = venue_entries(d)
        if not es:
            break
        for e in es:
            if e.motor is not None and e.run.counted:
                out.setdefault(e.motor, []).append(e.run.place)
        if min(e.day_no for e in es) <= 1:
            break
        d -= dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------- オッズ ----

def fetch_odds_html(hd: str, jcd: str, rno: int) -> str:
    r = http_get(f"{BASE}/odds3t?rno={rno}&jcd={jcd}&hd={hd}")
    r.encoding = "utf-8"
    return r.text


def parse_odds3t(html: str) -> dict[tuple[int, int, int], Optional[float]]:
    """3連単オッズの表を {(1着, 2着, 3着): オッズ} にする. 発売前・欠場などは None."""
    s = BeautifulSoup(html, "html.parser")
    tables = s.select("div.table1 table")
    out: dict[tuple[int, int, int], Optional[float]] = {}
    target = None
    for t in tables:
        if t.select_one("td.oddsPoint"):
            target = t
            break
    if target is None:
        return out
    second = [None] * 6
    for tr in target.select("tbody tr"):
        tds = tr.find_all("td", recursive=False)
        i, col = 0, 0
        while i < len(tds) and col < 6:
            td = tds[i]
            if td.get("rowspan"):
                second[col] = int(_norm(td.get_text()).translate(Z2H) or 0)
                i += 1
                if i >= len(tds):
                    break
                td = tds[i]
            third_txt = _norm(td.get_text()).translate(Z2H)
            odds_td = tds[i + 1] if i + 1 < len(tds) else None
            i += 2
            if third_txt.isdigit() and second[col]:
                txt = _norm(odds_td.get_text()) if odds_td else ""
                try:
                    val = float(txt.replace(",", ""))
                except ValueError:
                    val = None
                out[(col + 1, second[col], int(third_txt))] = val
            col += 1
    return out


# ------------------------------------------------------------ マイ買い目 ----
# コース別成績の表の順位 (3連対率の順・同じ数字は枠番の若い順) で組む15点.
#   1位-3位-2・4・5位 / 3位-1位-2・4・5位 / 2・4・5位-3位-1位
#   4位-2位-1・3・5位 / 1・3・5位-2位-4位
# 10/17〜10/24 の全場1,248レースで 的中26%・回収75%。1号艇が表の1〜2位のレースで 的中26%・回収79%、
# 3位以下のレースで 的中22%・回収38%。

MY_ROWS = [  # (1着, 2着, 3着) を「表の何位か」で書いたもの
    ([1], [3], [2, 4, 5]),
    ([3], [1], [2, 4, 5]),
    ([2, 4, 5], [3], [1]),
    ([4], [2], [1, 3, 5]),
    ([1, 3, 5], [2], [4]),
]


def course_order(racers: list[Racer]) -> list[int]:
    """コース別成績の表の並び (枠番の順). 3連対率の高い順、同じなら枠番の若い順."""
    cr = rank_map({r.waku: r.course_top3 for r in racers}, higher_is_better=True, digits=1)
    return sorted((r.waku for r in racers), key=lambda w: (cr[w], w))


def my_bets(order: list[int]) -> list[dict]:
    """マイ買い目の5行. 各行: {"label": "2-3-145", "rank_label": "1位-3位-2・4・5位", "combos": [...]}"""
    pos = lambda k: order[k - 1]
    rows = []
    for firsts, seconds, thirds in MY_ROWS:
        combos = [(pos(a), pos(b), pos(c)) for a in firsts for b in seconds for c in thirds]
        lab = lambda ks: "".join(str(pos(k)) for k in ks)
        rlab = lambda ks: "・".join(str(k) for k in ks) + "位"
        rows.append({
            "label": f"{lab(firsts)}-{lab(seconds)}-{lab(thirds)}",
            "rank_label": f"{rlab(firsts)}-{rlab(seconds)}-{rlab(thirds)}",
            "combos": combos,
        })
    return rows


def my_verdict(order: list[int]) -> tuple[str, str, str]:
    """1号艇が表の何位かで、マイ買い目が向いているレースかを返す."""
    k = order.index(1) + 1
    if k <= 2:
        return ("向いている", "v-ref", f"1号艇がコース表の{k}位。このタイプは的中26%・回収79%（1,137レース）")
    return ("不向き", "v-skip", f"1号艇がコース表の{k}位。このタイプは的中22%・回収38%（111レース）")


# ------------------------------------------------------- 速くするための先読み ----
# 公式サイトは海外のサーバーからだと1回の応答に数秒〜10秒かかるので、
# 画面を待たせずに裏で先に取っておき、結果をサーバーの中に覚えておく.

_VENUE_CACHE: dict[str, tuple[float, list[str]]] = {}
_VENUE_DIR = Path(tempfile.gettempdir()) / "boatdx_venue"
_RACE_CACHE: dict[tuple[str, str, int], tuple[float, "RaceInfo"]] = {}
_STORE_CACHE: dict[str, "KStore"] = {}
_BUSY: set = set()
_PRE_LOCK = threading.Lock()


def open_venues_cached(hd: str, max_age: float = 3 * 3600) -> Optional[list[str]]:
    """開催場の一覧. 取得済みならすぐ返す. まだなら None (裏で取りに行く)."""
    hit = _VENUE_CACHE.get(hd)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    path = _VENUE_DIR / f"{hd}.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        try:
            codes = json.loads(path.read_text())
            _VENUE_CACHE[hd] = (time.time(), codes)
            return codes
        except (OSError, ValueError):
            pass
    _run_once(("venue", hd), lambda: _load_venues(hd))
    return None


def _load_venues(hd: str) -> None:
    try:
        codes = parse_open_venues(fetch_index_html(hd))
    except Exception:
        return
    if codes:
        _VENUE_CACHE[hd] = (time.time(), codes)
        try:
            _VENUE_DIR.mkdir(parents=True, exist_ok=True)
            (_VENUE_DIR / f"{hd}.json").write_text(json.dumps(codes))
        except OSError:
            pass


def _run_once(key, fn) -> None:
    """同じ仕事が裏で動いていなければ、裏で1回だけ動かす."""
    with _PRE_LOCK:
        if key in _BUSY:
            return
        _BUSY.add(key)

    def job():
        try:
            fn()
        finally:
            with _PRE_LOCK:
                _BUSY.discard(key)
    threading.Thread(target=job, daemon=True).start()


def _store_for(hd: str) -> "KStore":
    """日付ごとに成績データの読み込み結果を使い回す (同じ日のレースは解析し直さない)."""
    with _PRE_LOCK:
        st = _STORE_CACHE.get(hd)
        if st is None:
            if len(_STORE_CACHE) > 6:
                _STORE_CACHE.pop(next(iter(_STORE_CACHE)))
            st = _STORE_CACHE[hd] = KStore()
        return st


def race_max_age(hd: str) -> float:
    """今日のレースは今節の成績が増えていくので短め、過去のレースは長めに覚えておく."""
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d")
    return 300 if hd >= today else 6 * 3600


_INFLIGHT: dict = {}


def get_race_cached(hd: str, jcd: str, rno: int) -> "RaceInfo":
    """レースの計算結果. 覚えていればすぐ返し、裏で計算中ならそれを待つ (二重に計算しない)."""
    from concurrent.futures import Future
    key = (hd, jcd, rno)
    hit = _RACE_CACHE.get(key)
    if hit and time.time() - hit[0] < race_max_age(hd):
        return hit[1]
    with _PRE_LOCK:
        fut = _INFLIGHT.get(key)
        owner = fut is None
        if owner:
            fut = _INFLIGHT[key] = Future()
    if not owner:
        return fut.result(timeout=180)
    try:
        info = build_race(hd, jcd, rno, result_loader=None, store=_store_for(hd))
        with _PRE_LOCK:
            if len(_RACE_CACHE) > 400:
                for k in list(_RACE_CACHE)[:100]:
                    _RACE_CACHE.pop(k, None)
            _RACE_CACHE[key] = (time.time(), info)
        fut.set_result(info)
        return info
    except Exception as e:
        fut.set_exception(e)
        raise
    finally:
        with _PRE_LOCK:
            _INFLIGHT.pop(key, None)


def prefetch_race(hd: str, jcd: str, rno: int) -> None:
    """レースを選んだ時点で、そのレースを最優先で裏で計算しておく."""
    _run_once(("race", hd, jcd, rno), lambda: _safe(lambda: get_race_cached(hd, jcd, rno)))


def _safe(fn):
    try:
        fn()
    except Exception:
        pass


def prefetch_venue(hd: str, jcd: str) -> None:
    """会場を選んだ時点で、その日の全レースを裏で先に計算しておく.
    1) 12レース分の出走表をまとめて取り、2) 出てくる選手全員のコース別成績をまとめて取り、
    3) そのあと各レースを計算する (通信をまとめて同時に行うので、1レースずつより速い)."""
    def work():
        def racelist(rno):
            try:
                return parse_racelist(fetch_racelist_html(hd, jcd, rno), hd, jcd, rno)
            except Exception:
                return None
        with ThreadPoolExecutor(max_workers=12) as ex:
            infos = [x for x in ex.map(racelist, range(1, 13)) if x]
        tobans = sorted({r.toban for info in infos for r in info.racers})
        with ThreadPoolExecutor(max_workers=16) as ex:
            list(ex.map(fetch_course, tobans))
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(lambda rno: _safe(lambda: get_race_cached(hd, jcd, rno)), range(1, 13)))
    _run_once(("venue_races", hd, jcd), work)


# ---------------------------------------------------------------- 1日まとめ ----

def analyze_race(info: RaceInfo, points: int = 8) -> dict:
    """1レース分の総合順位・ラベル・買い目・判定をまとめて返す (画面と1日まとめで共通)."""
    rows = overall(info.racers, 1)
    eq = {x["racer"].waku: x["score"] for x in rows}
    mk = makuri_wakus(info.racers, eq)
    labels = {w: candidate_label(w, eq[w], w in mk)[1] for w in eq}
    bets = suggest_bets([x["racer"].waku for x in rows], labels, points)
    verdicts = bet_verdicts(eq[1], bets["main_head"], points)
    corder = course_order(info.racers)
    my = [c for row in my_bets(corder) for c in row["combos"]]
    return {"rows": rows, "eq": eq, "makuri": mk, "labels": labels, "bets": bets, "verdicts": verdicts,
            "my": my, "my_verdict": my_verdict(corder)}


def day_summary(hd: str, jcd: str, points: int = 8, races: range = range(1, 13)) -> list[dict]:
    """その日・その場の全レースについて、判定と的中・払戻をまとめる."""
    store = KStore()
    target = dt.datetime.strptime(hd, "%Y%m%d").date()
    store.prefetch([target - dt.timedelta(days=i) for i in range(1, 29)], 24)

    def one(rno):
        try:
            info = get_race_cached(hd, jcd, rno)
        except Exception:
            return None
        a = analyze_race(info, points)
        try:
            res = parse_result_detail(fetch_result_html(hd, jcd, rno))
        except Exception:
            res = None
        combo = tuple(res[0]) if res else None
        pay = int(res[1].replace(",", "")) if res else None
        b = a["bets"]
        return {
            "rno": rno, "race_name": info.race_name,
            "main_verdict": a["verdicts"]["main"], "ana_verdict": a["verdicts"]["ana"],
            "main_head": b["main_head"], "ana_head": b["ana_head"],
            "main": b["main"], "ana": b["ana"],
            "result": combo, "pay": pay,
            "hit_main": combo in b["main"] if combo else None,
            "hit_ana": combo in b["ana"] if combo else None,
            "my": a["my"], "my_verdict": a["my_verdict"],
            "hit_my": combo in a["my"] if combo else None,
        }

    with ThreadPoolExecutor(max_workers=6) as ex:
        out = [x for x in ex.map(one, races) if x]
    return sorted(out, key=lambda x: x["rno"])


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
    mp = motor_prev_runs(VENUES.get(jcd, ""), start, store)
    for r in info.racers:
        r.motor_prev = mp.get(r.motor_no, []) if r.motor_no is not None else []
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
    """アプリを開いた時点で、開催場の一覧と直近の成績データを先に集めておく."""
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    for d in (today, today + dt.timedelta(days=1)):
        open_venues_cached(d.strftime("%Y%m%d"))
    KStore().prefetch([today - dt.timedelta(days=i) for i in range(1, days + 1)], 24)
