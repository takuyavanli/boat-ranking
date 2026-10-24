"""ボートレースDX — 得点率・平均ST ランキング (Streamlit アプリ)

起動: streamlit run app.py
"""

import datetime as dt
import threading
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import streamlit as st

import boat_core as bc

st.set_page_config(page_title="ボートレースDX", page_icon="🚤", layout="centered")

LANE = {  # 枠の色 (背景, 文字)
    1: ("#FFFFFF", "#111111"), 2: ("#111111", "#FFFFFF"), 3: ("#E60012", "#FFFFFF"),
    4: ("#0068B7", "#FFFFFF"), 5: ("#FFE100", "#111111"), 6: ("#009944", "#FFFFFF"),
}
WEEK = "月火水木金土日"

# ------------------------------------------------------------------ スタイル ----

st.markdown(
    """
<link href="https://fonts.googleapis.com/css2?family=Dela+Gothic+One&family=Noto+Sans+JP:wght@500;700;800&display=swap" rel="stylesheet">
<style>
:root { --navy:#0B2E59; --blue:#0068B7; --bg:#E8F5FC; --sky:#DCEFFA; --line:#CFE3F1; --yellow:#FFD400; --red:#E60012; }
.stApp, .stApp p, .stApp div, .stApp label, .stApp button, .stApp table, .stApp td, .stApp th, .stApp input, .stApp li { font-family: "Noto Sans JP", "Hiragino Sans", sans-serif; }
.stApp, .stApp p, .stApp label, .stApp button, .stApp li, .stApp input { font-weight: 500; }
header[data-testid="stHeader"], #MainMenu, footer { display: none !important; }
.stApp { background: var(--bg); }
.block-container { padding-top: .9rem; padding-bottom: 5rem; max-width: 720px; }
/* ヘッダー */
.hero { position: relative; overflow: hidden; border-radius: 18px; height: 196px; padding: 18px 18px 0;
  background: linear-gradient(165deg, #0058A3 0%, #0A7FD0 55%, #2FB3E6 100%); color: #fff; margin-bottom: 1.2rem; }
.hero .sun { position: absolute; right: -24px; top: -24px; width: 86px; height: 86px; border-radius: 50%; background: var(--yellow); }
.hero h1 { font-family: "Dela Gothic One", "Hiragino Sans", sans-serif; font-size: 30px; line-height: 1.15; font-weight: 400;
  letter-spacing: .02em; transform: skewX(-8deg); transform-origin: left; margin: 6px 0 4px; padding: 0; color: #fff; position: relative; z-index: 2; }
.hero p { font-size: 12px; opacity: .92; margin: 0; position: relative; z-index: 2; color: #fff; }
.hero .boat { position: absolute; right: -8px; bottom: 14px; width: 232px; z-index: 2; }
.hero .sea { position: absolute; left: 0; right: 0; bottom: 0; height: 44px; z-index: 3; }
/* 見出し */
.sec { font-size: 1.12rem; font-weight: 800; color: var(--navy); margin: 1.6rem 0 .6rem; display: flex; align-items: center; gap: .5rem; }
.sec svg { flex: none; }
.lbl { font-size: .82rem; font-weight: 700; color: var(--navy); margin: .2rem 0 -.2rem; }
/* 選択チップ */
div[data-testid="stButtonGroup"] button { border-radius: 999px !important; background: #fff; }
div[data-testid="stDateInput"] > div > div, div[data-testid="stDateInput"] input { background-color: #fff !important; }
div.stButton > button[kind="primary"] { background: var(--red); border-color: var(--red); font-weight: 700; height: 3rem; }
div.stButton > button[kind="secondary"] { border-color: var(--blue); color: var(--blue); background: #fff; font-weight: 700; height: 2.8rem; }
/* レース情報 */
.race { border: 1.5px solid var(--line); border-radius: 14px; padding: .8rem 1rem; margin-top: 1.4rem; background: #fff; }
.race b { font-size: 1.15rem; color: var(--navy); }
.race span { display: block; font-size: .82rem; color: #5b6b7c; margin-top: .1rem; }
/* 表 */
.tblwrap { overflow-x: auto; }
table.rk { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .93rem; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: #fff; }
table.rk th { text-align: left; font-weight: 700; font-size: .76rem; color: var(--navy); background: var(--sky); padding: 7px 6px; }
table.rk td { padding: 9px 6px; border-top: 1px solid var(--line); vertical-align: middle; color: #1b2430; }
table.rk tr.top td { background: #FFFBE0; }
table.rk td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
table.rk td.pos { font-size: 1.1rem; font-weight: 800; width: 2em; text-align: center; color: var(--blue); }
table.rk td.name { white-space: nowrap; }
table.rk .sub { font-size: .74rem; color: #7a8896; }
.lane { display: inline-flex; align-items: center; justify-content: center; width: 1.65em; height: 1.65em; font-weight: 800;
  border-radius: 4px; border: 1px solid rgba(0,0,0,.25); margin-right: .45em; font-size: .9em; }
div[data-testid="stExpander"] details { background: #fff; border-radius: 12px; }
.marks { font-size: .76rem; color: #6b7a89; letter-spacing: .04em; white-space: normal; }
.note { font-size: .78rem; color: #6b7a89; margin-top: .5rem; }
/* 結果 */
.result { border-radius: 14px; background: #fff; border: 1.5px solid var(--line); padding: .9rem 1rem; display: flex; align-items: center; gap: .8rem; margin-top: .4rem; }
.result .order { display: flex; align-items: center; gap: .3rem; font-weight: 800; color: var(--navy); }
.result .order .lane { margin: 0; width: 2em; height: 2em; font-size: 1rem; }
.result .pay { margin-left: auto; text-align: right; color: var(--navy); font-size: .8rem; }
.result .pay b { display: block; font-size: 1.15rem; }
/* 下の波 */
.foot { margin-top: 2.5rem; height: 46px; }
</style>
""",
    unsafe_allow_html=True,
)

BOAT_SVG = """
<svg class="boat" viewBox="0 0 260 110" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
 <g transform="rotate(-7 150 55)">
  <path d="M60 40 L0 14 L0 24 L58 46 Z" fill="#fff" opacity=".5"/>
  <path d="M60 70 L0 96 L0 86 L58 64 Z" fill="#fff" opacity=".5"/>
  <path d="M56 50 L10 44 L10 66 L56 60 Z" fill="#fff" opacity=".28"/>
  <circle cx="230" cy="36" r="2.6" fill="#fff"/><circle cx="219" cy="30" r="1.8" fill="#fff" opacity=".85"/>
  <circle cx="230" cy="74" r="2.6" fill="#fff"/><circle cx="219" cy="80" r="1.8" fill="#fff" opacity=".85"/>
  <path d="M68 31 L166 29 Q224 35 250 55 Q224 75 166 81 L68 79 Q60 79 60 71 L60 39 Q60 31 68 31 Z" fill="#fff"/>
  <path d="M188 35 Q226 41 246 55 Q226 69 188 75 L200 55 Z" fill="#E60012"/>
  <path d="M170 33 L184 34 L196 55 L184 76 L170 77 L182 55 Z" fill="#FFD400"/>
  <rect x="66" y="52" width="100" height="6" fill="#E60012"/>
  <rect x="98" y="41" width="46" height="28" rx="9" fill="#0B2E59"/>
  <rect x="105" y="47" width="32" height="16" rx="5" fill="#16477F"/>
  <rect x="38" y="43" width="24" height="24" rx="6" fill="#1B2430"/>
  <rect x="43" y="48" width="14" height="14" rx="3.5" fill="#3A4A5C"/>
  </g>
</svg>"""

SEA_SVG = """
<svg class="sea" viewBox="0 0 400 44" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M0 18 Q50 6 100 18 T200 18 T300 18 T400 18 V44 H0 Z" fill="#E8F5FC" opacity=".4"/>
  <path d="M0 28 Q50 16 100 28 T200 28 T300 28 T400 28 V44 H0 Z" fill="#E8F5FC"/>
</svg>"""

WAVE_ICON = """<svg width="22" height="12" viewBox="0 0 22 12" aria-hidden="true">
<path d="M1 7 Q4.5 2 8 7 T15 7 T21 7" fill="none" stroke="#0068B7" stroke-width="2.4" stroke-linecap="round"/></svg>"""

FOOT_SVG = """<div class="foot"><svg viewBox="0 0 400 46" preserveAspectRatio="none" width="100%" height="46" aria-hidden="true">
<path d="M0 20 Q50 8 100 20 T200 20 T300 20 T400 20 V46 H0 Z" fill="#2FB3E6" opacity=".25"/>
<path d="M0 30 Q50 18 100 30 T200 30 T300 30 T400 30 V46 H0 Z" fill="#0A7FD0" opacity=".35"/></svg></div>"""

st.markdown(
    f'<div class="hero"><div class="sun"></div><h1>ボートレースDX</h1>'
    f'<p>得点率と平均STで、出走6艇をランキング</p>{BOAT_SVG}{SEA_SVG}</div>',
    unsafe_allow_html=True,
)


# ------------------------------------------------------------ 表示用の関数 ----

def fmt(v, digits=2):
    if v is None:
        return "-"
    return str(Decimal(str(v)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def lane_chip(w):
    bg, fg = LANE[w]
    return f'<span class="lane" style="background:{bg};color:{fg}">{w}</span>'


def period(s: bc.Series):
    return f"{s.start:%m/%d}〜{s.end:%m/%d}" if s.start else ""


def section(title):
    st.markdown(f'<div class="sec">{WAVE_ICON}{title}</div>', unsafe_allow_html=True)


def table(headers, rows, top_first=False):
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        (f'<tr class="top">' if top_first and i == 0 else "<tr>") + "".join(r) + "</tr>"
        for i, r in enumerate(rows))
    st.markdown(f'<div class="tblwrap"><table class="rk"><tr>{th}</tr>{body}</table></div>',
                unsafe_allow_html=True)


# ------------------------------------------------------------- データ取得 ----

@st.cache_data(ttl=3 * 3600, show_spinner=False)
def open_venues(hd):
    try:
        return bc.parse_open_venues(bc.fetch_index_html(hd))
    except Exception:
        return []


@st.cache_data(ttl=90, show_spinner=False)
def get_race(hd, jcd, rno):
    return bc.build_race(hd, jcd, rno, result_loader=None)


@st.cache_data(ttl=20, show_spinner=False)
def get_result(hd, jcd, rno):
    try:
        return bc.parse_result_detail(bc.fetch_result_html(hd, jcd, rno))
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def warm_up(day_key):
    """アプリを開いた時点で、直近4週間の成績データを裏で集め始める (1日1回)."""
    threading.Thread(target=bc.warm_up, daemon=True).start()
    return True


# ------------------------------------------------------------------- 入力 ----

today = dt.datetime.now(ZoneInfo("Asia/Tokyo")).date()
warm_up(today.isoformat())


st.markdown('<div class="lbl">日付</div>', unsafe_allow_html=True)
day = st.date_input("日付", value=today, format="YYYY/MM/DD", label_visibility="collapsed")
hd = day.strftime("%Y%m%d")

codes = open_venues(hd) or list(bc.VENUES)
st.markdown('<div class="lbl">会場</div>', unsafe_allow_html=True)
jcd = st.pills("会場", codes, default=codes[0], label_visibility="collapsed",
               format_func=lambda c: bc.VENUES[c], key="venue") or codes[0]

st.markdown('<div class="lbl">レース</div>', unsafe_allow_html=True)
rno = st.pills("レース", list(range(1, 13)), default=1, label_visibility="collapsed",
               format_func=lambda n: f"{n}R") or 1

st.markdown('<div class="lbl">総合順位の重み</div>', unsafe_allow_html=True)
weight_label = st.radio("総合順位の重み", ["得点率とSTを同じ重みで", "得点率を2倍重視"], horizontal=True,
                        label_visibility="collapsed")
rate_weight = 2 if weight_label.startswith("得点率を2倍") else 1

if st.button("ランキングを出す", type="primary", width="stretch"):
    st.session_state["query"] = (hd, jcd, rno)
    st.session_state.pop("show_result", None)

query = st.session_state.get("query")
if not query:
    st.markdown(FOOT_SVG, unsafe_allow_html=True)
    st.stop()

# ------------------------------------------------------------------- 計算 ----

q_hd, q_jcd, q_rno = query
try:
    with st.spinner("公式サイトから成績を取得しています（20秒ほどかかることがあります）", show_time=True):
        info = get_race(q_hd, q_jcd, q_rno)
except ValueError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"データを取得できませんでした。時間をおいて再度お試しください。（{type(e).__name__}）")
    st.stop()

d = dt.datetime.strptime(q_hd, "%Y%m%d").date()
st.markdown(
    f'<div class="race"><b>{bc.VENUES[q_jcd]} {q_rno}R　{info.race_name}</b>'
    f'<span>{d.year}/{d.month}/{d.day}（{WEEK[d.weekday()]}）　{unicodedata.normalize("NFKC", info.title)}　{info.day_label}</span></div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- 総合順位 ----

rows = bc.overall(info.racers, rate_weight=rate_weight)
section("総合順位")
table(
    ["", "選手", "得点率", "平均ST", "合計"],
    [[
        f'<td class="pos">{r["rank"]}</td>',
        f'<td class="name">{lane_chip(r["racer"].waku)}{r["racer"].name}'
        f'<span class="sub"> {r["racer"].klass}</span></td>',
        f'<td class="num">{fmt(r["racer"].total_rate)}<span class="sub">（{r["rate_rank"]}位）</span></td>',
        f'<td class="num">{fmt(r["racer"].total_st)}<span class="sub">（{r["st_rank"]}位）</span></td>',
        f'<td class="num">{r["score"]}</td>',
    ] for r in rows],
    top_first=True,
)
st.markdown(
    '<div class="note">得点率・平均STは今節と前節を合わせた全走で計算。'
    + ("得点率の順位×2＋STの順位" if rate_weight == 2 else "得点率の順位＋STの順位")
    + "が小さい順に並べています。</div>",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ 得点率 ----

section("得点率")
rr = bc.rank_map({r.waku: r.total_rate for r in info.racers}, higher_is_better=True)
by_rate = sorted(info.racers, key=lambda r: (rr[r.waku], r.waku))
table(
    ["", "選手", "合算", "今節", "前節"],
    [[
        f'<td class="pos">{rr[r.waku]}</td>',
        f'<td class="name">{lane_chip(r.waku)}{r.name}</td>',
        f'<td class="num"><b>{fmt(r.total_rate)}</b></td>',
        f'<td class="num">{fmt(r.current.rate)}<div class="marks">{r.current.marks or "出走なし"}</div></td>',
        f'<td class="num">{fmt(r.prev.rate)}<div class="marks">{r.prev.venue} {period(r.prev)}</div>'
        f'<div class="marks">{r.prev.marks}</div></td>',
    ] for r in by_rate],
)

# ------------------------------------------------------------------ 平均ST ----

section("平均ST")
tab_cur, tab_prev = st.tabs(["今節", "前節"])
for tab, pick in ((tab_cur, lambda r: r.current), (tab_prev, lambda r: r.prev)):
    with tab:
        sr = bc.rank_map({r.waku: pick(r).avg_st for r in info.racers}, higher_is_better=False)
        ordered = sorted(info.racers, key=lambda r: (sr[r.waku], r.waku))
        table(
            ["", "選手", "平均ST", "走数"],
            [[
                f'<td class="pos">{sr[r.waku]}</td>',
                f'<td class="name">{lane_chip(r.waku)}{r.name}</td>',
                f'<td class="num"><b>{fmt(pick(r).avg_st)}</b></td>',
                f'<td class="num">{len(pick(r).sts)}</td>',
            ] for r in ordered],
        )

# ------------------------------------------------------------------ 結果 ----

section("レース結果")
if st.session_state.get("show_result") == query:
    res = get_result(q_hd, q_jcd, q_rno)
    if res:
        lanes, pay = res
        order = '<span style="color:#9fb3c6">–</span>'.join(lane_chip(w) for w in lanes)
        st.markdown(
            f'<div class="result"><div class="order">{order}</div>'
            f'<div class="pay">3連単<b>{pay}円</b></div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("まだ結果が出ていません。レース終了後にもう一度押してください。")
        st.session_state.pop("show_result", None)
else:
    if st.button("結果を見る", width="stretch"):
        st.session_state["show_result"] = query
        st.rerun()

with st.expander("計算のルール"):
    st.markdown(
        """
- 着順点は 1着10・2着8・3着6・4着4・5着2・6着1。転覆・落水・失格・F・Lは0点で走数に含み、欠場は走数から除きます。
- 今節は、選んだレースより前に終わったレースだけを使います。
- 前節は、今節の初日より前で最後に走った節です（公式の日別競走成績データから検索）。
- 平均STは F・L・欠場を除いて計算し、小数第2位で四捨五入して表示します。
- データ元：BOAT RACE オフィシャルウェブサイト。サイトの仕様が変わると動かなくなることがあります。
"""
    )

st.markdown(FOOT_SVG, unsafe_allow_html=True)
