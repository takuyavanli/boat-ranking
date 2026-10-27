"""ボートレースDX — 得点率・平均ST ランキング (Streamlit アプリ)

起動: streamlit run app.py
"""

import datetime as dt
import importlib
import threading
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import streamlit as st

import boat_core as bc

st.set_page_config(page_title="ボートレースDX", page_icon="🚤", layout="centered")

# boat_core.py と組み合わせが合っているかの確認用 (boat_core.VERSION と同じにしておく)
APP_VERSION = "2026-10-27c"

# 消し候補 (2〜6号艇・12点以上) で平均STが1〜3位の艇に、平均STの欄で目印を出すか
SHOW_ST_MARK = False

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
.st-key-odds_refresh button { height: 2.2rem !important; min-height: 2.2rem; font-size: .82rem; padding: 0 .9rem; border-radius: 999px; margin-top: -.3rem; }
div.stButton > button[kind="secondary"] { border-color: var(--blue); color: var(--blue); background: #fff; font-weight: 700; height: 2.8rem; }
/* レース情報 */
.race { border: 1.5px solid var(--line); border-radius: 14px; padding: .8rem 1rem; margin-top: 1.4rem; background: #fff; }
.race b { font-size: 1.15rem; color: var(--navy); }
.race span { display: block; font-size: .82rem; color: #5b6b7c; margin-top: .1rem; }
/* 表 */
.tblwrap { overflow-x: auto; }
table.rk { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .93rem; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: #fff; }
table.rk th { text-align: left; font-weight: 700; font-size: .72rem; white-space: nowrap; color: var(--navy); background: var(--sky); padding: 7px 6px; }
table.rk td { padding: 9px 6px; border-top: 1px solid var(--line); vertical-align: middle; color: #1b2430; }
table.rk tr.top td { background: #FFFBE0; }
table.rk td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
table.rk td.pos { font-size: 1.1rem; font-weight: 800; width: 2em; text-align: center; color: var(--blue); }
table.rk td.name { white-space: nowrap; }
table.rk .sub { font-size: .72rem; color: #7a8896; font-weight: 500; }
table.rk td.name .sub { display: block; margin-left: 2.1em; line-height: 1.1; }
.lane { display: inline-flex; align-items: center; justify-content: center; width: 1.65em; height: 1.65em; font-weight: 800;
  border-radius: 4px; border: 1px solid rgba(0,0,0,.25); margin-right: .45em; font-size: .9em; }
div[data-testid="stExpander"] details { background: #fff; border-radius: 12px; }
.marks { font-size: .76rem; color: #6b7a89; letter-spacing: .04em; white-space: normal; }
.note { font-size: .78rem; color: #6b7a89; margin-top: .5rem; }
/* 目安ラベル */
.badge { display: inline-block; font-size: .68rem; font-weight: 800; line-height: 1; padding: .28em .55em; border-radius: 999px; white-space: nowrap; }
.badge.c1 { background: var(--navy); color: var(--yellow); }
.badge.c12 { background: var(--red); color: #fff; }
.badge.c23 { background: var(--yellow); color: var(--navy); }
.badge.c3 { background: #CFE8F7; color: var(--navy); }
.badge.cx { background: #E4E8EC; color: #7a8896; }
.rk { display: inline-block; min-width: 2.4em; text-align: center; font-size: .66rem; font-weight: 800; border-radius: 999px; padding: .12em .35em; line-height: 1.35; }
.rk.r1 { background: var(--red); color: #fff; }
.rk.r2 { background: var(--yellow); color: var(--navy); }
.rk.r3 { background: #009944; color: #fff; }
.rk.rn { color: #7a8896; font-weight: 600; }
table.rk td.pos .pn { display: inline-flex; align-items: center; justify-content: center; width: 1.7em; height: 1.7em; border-radius: 50%; }
table.rk td.pos .pn.r1 { background: var(--red); color: #fff; }
table.rk td.pos .pn.r2 { background: var(--yellow); color: var(--navy); }
table.rk td.pos .pn.r3 { background: #009944; color: #fff; }
table.rk.tight td, table.rk.tight th { padding-left: 3px; padding-right: 3px; }
table.rk.tight td.num { font-size: .84rem; }
table.rk.tight td.pos { width: 1.6em; font-size: 1rem; }
table.rk.tight td.name { font-size: .88rem; }
table.rk.tight td.name .meta { margin-left: 0; flex-wrap: wrap; }
table.rk.tight .rk { min-width: 2.2em; font-size: .62rem; }
.grade { display: inline-flex; align-items: center; gap: .25rem; font-size: .74rem; font-weight: 700; color: var(--navy); white-space: nowrap; }
.grade b { display: inline-flex; align-items: center; justify-content: center; width: 1.45em; height: 1.45em; border-radius: 5px; font-size: .82rem; font-weight: 800; }
.grade.gS b { background: #0058A3; color: #fff; }
.grade.gA b { background: #2FB3E6; color: #fff; }
.grade.gB b { background: #DCEFFA; color: var(--navy); }
.grade.gC b { background: #EEF1F4; color: #7a8896; }
.grade.gD b { background: #E4E8EC; color: #9aa6b2; }
.grade.gC, .grade.gD { color: #7a8896; }
.stmark { display: inline-block; margin-top: .2rem; font-size: .62rem; font-weight: 800; color: var(--navy); background: #FFF4B8; border: 1px solid var(--yellow); border-radius: 999px; padding: .12em .45em; white-space: nowrap; }
table.rk td.name .meta { display: flex; align-items: center; gap: .35rem; margin: .25rem 0 0 2.1em; }
table.rk td.name .k { font-size: .7rem; color: #7a8896; font-weight: 500; }
/* 合計点の見方 */
.guide { background: #fff; border: 1.5px solid var(--line); border-radius: 12px; padding: .7rem .7rem .6rem; margin-top: .9rem; }
.guide .gt { font-size: .86rem; font-weight: 800; color: var(--navy); margin: 0 0 .5rem .1rem; }
.guide table { width: 100%; border-collapse: collapse; font-size: .78rem; }
.guide th { font-size: .74rem; color: var(--navy); font-weight: 700; text-align: left; padding: .2rem .3rem .4rem; }
.guide td { padding: .45rem .3rem; border-top: 1px solid var(--line); vertical-align: top; color: #1b2430; line-height: 1.45; }
.guide td.rowh { font-weight: 800; color: var(--navy); white-space: nowrap; vertical-align: middle; }
.guide td.muted { color: #b5c0cb; text-align: center; vertical-align: middle; }
.guide td .badge { margin-bottom: .25rem; }
.guide .mk1 { display: flex; gap: .5rem; align-items: flex-start; border-top: 1px solid var(--line); padding: .55rem .3rem .1rem; font-size: .76rem; line-height: 1.55; color: #1b2430; }
.guide .mk1 .badge { flex: none; margin-top: .1rem; }
.guide .gn { font-size: .72rem; color: #6b7a89; margin-top: .45rem; line-height: 1.5; }
/* 買い目 */
.bets { display: grid; grid-template-columns: 1fr; gap: .7rem; }
.bet { background: #fff; border: 1.5px solid var(--line); border-radius: 14px; padding: .75rem .85rem .8rem; }
.bet.main { border-color: #F3B3B8; }
.bet.ana { border-color: #C9D6E6; }
.flag { display: inline-block; font-size: .66rem; font-weight: 800; padding: .1em .45em; border-radius: 5px; margin: .15rem .2rem 0 0; white-space: nowrap; }
.flag.f-makuri { background: #FFE3E6; color: #C4001A; }
.flag.f-sashi { background: #E2F1FF; color: #0058A3; }
.flag.f-mz { background: #F1E6FF; color: #6B2FB3; }
.flag.f-in { background: #FFF1D6; color: #9A5B00; }
.bet.best { border-color: #8FC8EC; border-width: 2px; }
.bet.best .tag { background: var(--blue); color: #fff; }
.bet.my { border-color: #FFE27A; }
.bet.my .tag { background: var(--yellow); color: var(--navy); }
.myrow { margin-top: .55rem; }
.myrow .mh { font-size: .82rem; font-weight: 800; color: var(--navy); }
.myrow .mh span { font-weight: 500; color: #7a8896; font-size: .7rem; margin-left: .3rem; }
.myrow .g3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: .35rem; margin-top: .25rem; }
.mc { background: #F4F9FD; border-radius: 10px; padding: .35rem .15rem .3rem; text-align: center; }
.mc .lane { width: 1.45em; height: 1.45em; font-size: .8rem; margin: 0; }
.mc .dash { color: #9fb3c6; font-weight: 700; margin: 0 .05rem; }
.mc .od { display: block; font-size: .7rem; color: #5b6b7c; font-weight: 700; margin-top: .15rem; }
.mc.gami { background: #FDECEE; }
.mc.gami .od { color: var(--red); }
.bet .bh { display: flex; align-items: center; gap: .5rem; margin-bottom: .35rem; }
.bet .bt { font-weight: 800; font-size: 1rem; color: var(--navy); }
.bet .bh { flex-wrap: wrap; row-gap: .3rem; }
.bet .bt, .bet .tag, .bet .oddstime { white-space: nowrap; }
.bet .tag { font-size: .7rem; font-weight: 800; padding: .25em .6em; border-radius: 999px; }
.bet.main .tag { background: var(--red); color: #fff; }
.bet.ana .tag { background: var(--navy); color: var(--yellow); }
.bet .verdict { margin-left: auto; font-size: .74rem; font-weight: 800; padding: .3em .7em; border-radius: 999px; white-space: nowrap; }
.verdict.v-buy { background: var(--blue); color: #fff; }
.verdict.v-skip { background: #E4E8EC; color: #6b7a89; }
.verdict.v-ref { background: #FFF4B8; color: var(--navy); border: 1px solid var(--yellow); }
.verdict.v-play { background: #CFE8F7; color: var(--navy); }
.bet .vnote { font-size: .72rem; color: #6b7a89; background: #F4F9FD; border-radius: 8px; padding: .35rem .55rem; margin: .1rem 0 .55rem; line-height: 1.5; }
.bet.skip .grid { opacity: .45; }
.bet .form { font-size: .76rem; color: #5b6b7c; margin-bottom: .55rem; line-height: 1.5; }
.bet .grid { display: grid; grid-template-columns: 1fr 1fr; gap: .45rem; }
.combo { display: flex; align-items: center; justify-content: center; gap: .2rem; background: #F4F9FD; border-radius: 10px; padding: .45rem .3rem; }
.combo .lane { margin: 0; width: 1.75em; height: 1.75em; font-size: .95rem; }
.combo .dash { color: #9fb3c6; font-weight: 700; }
.combo .od { margin-left: .35rem; font-size: .74rem; font-weight: 700; color: #5b6b7c; font-variant-numeric: tabular-nums; min-width: 3.2em; text-align: right; }
.combo.gami { background: #FDECEE; }
.combo.gami .od { color: var(--red); }
.bet .oddsnote { font-size: .7rem; color: var(--red); margin-top: .45rem; }
.bet .oddstime { font-size: .68rem; color: #7a8896; margin-left: .3rem; font-weight: 500; }
/* 1日まとめ */
.day { background: #fff; border: 1.5px solid var(--line); border-radius: 14px; padding: .8rem .9rem; margin-top: 1.2rem; }
.day b.t { font-size: 1.1rem; color: var(--navy); }
.day .sum { display: grid; grid-template-columns: 1fr; gap: .4rem; margin-top: .6rem; }
.day .sum div { background: #F4F9FD; border-radius: 10px; padding: .5rem .65rem; font-size: .82rem; color: #1b2430; line-height: 1.5; }
.day .sum div b { color: var(--navy); }
.day .sum .roi { float: right; font-size: 1.05rem; font-weight: 800; color: var(--blue); }
.day .sum .roi.low { color: #7a8896; }
table.rk tr.dim td { opacity: .5; }
.hit { font-weight: 800; color: var(--red); }
.miss { color: #9aa6b2; font-weight: 700; }
.mini .lane { width: 1.45em; height: 1.45em; font-size: .78em; margin-right: .1em; }
.combo.dup { background: #fff; outline: 1.5px dashed #9fb3c6; outline-offset: -1.5px; }
.bet .dupnote { font-size: .7rem; color: #6b7a89; margin-top: .45rem; }
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
    f'<p>得点率・平均ST・コース成績で、出走6艇をランキング</p>{BOAT_SVG}{SEA_SVG}</div>',
    unsafe_allow_html=True,
)


if getattr(bc, "VERSION", None) != APP_VERSION:
    # GitHub を更新しても、サーバーが古い boat_core.py を覚えたままのことがあるので、読み込み直してみる
    try:
        import lzh
        importlib.reload(lzh)
        bc = importlib.reload(bc)
    except Exception:
        pass
if getattr(bc, "VERSION", None) != APP_VERSION:
    st.error(
        "app.py と boat_core.py の版が合っていません。"
        f"（app.py: {APP_VERSION} ／ boat_core.py: {getattr(bc, 'VERSION', '古い版')}）\n\n"
        "1. GitHub で boat_core.py を開き、上の方の VERSION の行が app.py と同じ日付か確認してください。"
        "違っていたら、zip の中の boat_core.py をアップロードし直してください。\n\n"
        "2. 同じ日付になっているのにこの表示が出る場合は、share.streamlit.io の管理画面で「Reboot app」を押してください。"
    )
    st.stop()


# ------------------------------------------------------------ 表示用の関数 ----

def fmt(v, digits=2):
    if v is None:
        return "-"
    return str(Decimal(str(v)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def lane_chip(w):
    bg, fg = LANE[w]
    return f'<span class="lane" style="background:{bg};color:{fg}">{w}</span>'


def pct0(v):
    return "-" if v is None else f"{fmt(v, 0)}%"


def pct(v):
    return "-" if v is None else f"{fmt(v, 1)}%"


def combo_html(c, dup=False, odds=None, points=8):
    sep = '<span class="dash">-</span>'
    cls = "combo"
    if dup:
        cls += " dup"
    od = ""
    if odds is not None:
        if odds < points:
            cls += " gami"
        od = f'<span class="od">{odds:g}倍</span>'
    return f'<div class="{cls}"><span>{sep.join(lane_chip(w) for w in c)}</span>{od}</div>'


def my_card(rows, verdict, odds, otime):
    vtext, vkind, vnote = verdict
    odds = odds or {}
    sep = '<span class="dash">-</span>'
    n = sum(len(r["combos"]) for r in rows)
    gami = 0
    body = ""
    for r in rows:
        cells = ""
        for c in r["combos"]:
            o = odds.get(c)
            g = o is not None and o < n
            gami += g
            od = f'<span class="od">{o:g}倍</span>' if o is not None else ""
            cells += f'<div class="mc{" gami" if g else ""}">{sep.join(lane_chip(w) for w in c)}{od}</div>'
        body += (f'<div class="myrow"><div class="mh">{r["label"]}<span>（{r["rank_label"]}）</span></div>'
                 f'<div class="g3">{cells}</div></div>')
    gnote = (f'<div class="oddsnote">赤い{gami}点はオッズが{n}倍未満です（当たっても買った金額を下回ります）。</div>'
             if gami else "")
    otime_html = f'<span class="oddstime">オッズ {otime}時点</span>' if odds and otime else ""
    return (f'<div class="bet my{" skip" if vkind == "v-skip" else ""}"><div class="bh"><span class="bt">マイ買い目</span>'
            f'<span class="tag">{n}点</span>{otime_html}<span class="verdict {vkind}">{vtext}</span></div>'
            f'<div class="form">コース別成績の表の順位で組む型。1位と3位、2位と4位を組にして、6位は消し。</div>'
            f'<div class="vnote">{vnote}</div>{body}{gnote}</div>')


def bet_card(kind, title, tag, combos, form, note, verdict, dups=(), odds=None, points=8, otime=""):
    vtext, vkind, vnote = verdict
    odds = odds or {}
    gami = [c for c in combos if odds.get(c) is not None and odds[c] < points]
    oddsnote = (f'<div class="oddsnote">赤い{len(gami)}点はオッズが{points}倍未満です（当たっても買った金額を下回ります）。</div>'
                if gami else "")
    otime_html = f'<span class="oddstime">オッズ {otime}時点</span>' if odds and otime else ""
    dupnote = (f'<div class="dupnote">点線の{len(dups)}点は本線と同じ組み合わせです（重ねて買う必要はありません）。</div>'
               if dups else "")
    skip = " skip" if vkind == "v-skip" else ""
    return (f'<div class="bet {kind}{skip}"><div class="bh"><span class="bt">{title}</span>'
            f'<span class="tag">{tag}</span>{otime_html}<span class="verdict {vkind}">{vtext}</span></div>'
            f'<div class="form">{form}<br>{note}</div>'
            f'<div class="vnote">{vnote}</div>'
            f'<div class="grid">{"".join(combo_html(c, c in dups, odds.get(c), points) for c in combos)}</div>'
            f'{oddsnote}{dupnote}</div>')


def badge(waku, score, makuri=False):
    text, kind = bc.candidate_label(waku, score, makuri)
    if kind == "c1":
        text = "★" + text
    return f'<span class="badge {kind}">{text}</span>'


def grade_html(g):
    if not g:
        return '<span class="grade">-</span>'
    return f'<span class="grade g{g}"><b>{g}</b>{bc.GRADE_TEXT[g]}</span>'


def grade_cell(g, rank):
    """コースのAI評価と、その項目の順位."""
    return f'<td class="num">{grade_html(g)}<div class="sub">{rank_pill(rank)}</div></td>'


def rank_pill(rank):
    cls = f"r{rank}" if rank in (1, 2, 3) else "rn"
    return f'<span class="rk {cls}">{rank}位</span>'


def pos_cell(rank):
    """各項目の表の順位欄 (1〜3位は 赤・黄・緑)."""
    cls = f" r{rank}" if rank in (1, 2, 3) else ""
    return f'<td class="pos"><span class="pn{cls}">{rank}</span></td>'


def stacked(value, rank, mark=False):
    """値の下に小さく順位を出す (1〜3位は 赤・黄・緑)."""
    m = '<br><span class="stmark">ST上位</span>' if mark else ""
    return f'<td class="num">{value}<div class="sub">{rank_pill(rank)}{m}</div></td>'


def period(s: bc.Series):
    return f"{s.start:%m/%d}〜{s.end:%m/%d}" if s.start else ""


def section(title):
    st.markdown(f'<div class="sec">{WAVE_ICON}{title}</div>', unsafe_allow_html=True)


def table(headers, rows, top_first=False, cls=""):
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        (f'<tr class="top">' if top_first and i == 0 else "<tr>") + "".join(r) + "</tr>"
        for i, r in enumerate(rows))
    st.markdown(f'<div class="tblwrap"><table class="rk {cls}"><tr>{th}</tr>{body}</table></div>',
                unsafe_allow_html=True)


# ------------------------------------------------------------- データ取得 ----

def get_race(hd, jcd, rno):
    # 会場を選んだ時点で裏で先に計算している結果があれば、それをすぐ使う
    return bc.get_race_cached(hd, jcd, rno)


@st.cache_data(ttl=60, show_spinner=False)
def get_odds(hd, jcd, rno):
    """戻り値: (オッズ, 取得時刻, 取れなかったときの理由)."""
    now = dt.datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%H:%M")
    try:
        odds = bc.parse_odds3t(bc.fetch_odds_html(hd, jcd, rno))
    except Exception as e:
        return {}, now, f"{type(e).__name__}"
    if not odds:
        return {}, now, "まだ発売前か、オッズが出ていません"
    return odds, now, ""


@st.cache_data(ttl=60, show_spinner=False)
def get_before(hd, jcd, rno):
    """直前情報 (展示タイム・スタート展示). まだ出ていなければ空."""
    try:
        return bc.parse_beforeinfo(bc.fetch_beforeinfo_html(hd, jcd, rno))
    except Exception:
        return {}


FLAG_CLASS = {"まくり注意": "f-makuri", "差し注意": "f-sashi", "まくり差し注意": "f-mz", "1号艇注意": "f-in", "イン注意": "f-in"}


def flag_html(text):
    return f'<span class="flag {FLAG_CLASS.get(text, "f-in")}">{text}</span>' if text else ""


@st.cache_data(ttl=120, show_spinner=False)
def get_day(hd, jcd, points):
    return bc.day_summary(hd, jcd, points)


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

# 開催場の一覧は裏で取りに行き、まだ届いていないときは全場を出しておく (画面を待たせない)
open_codes = bc.open_venues_cached(hd)
codes = open_codes or list(bc.VENUES)
st.markdown('<div class="lbl">会場</div>', unsafe_allow_html=True)
if open_codes is None:
    @st.fragment(run_every=2)
    def wait_venues():
        # 開催場の一覧が届いたら画面を描き直す
        if bc.open_venues_cached(hd) is not None:
            st.rerun()
        st.caption("開催場を確認中です。少しすると開催している場だけになります。")
    wait_venues()


def on_venue_pick():
    st.session_state["venue_picked"] = True


jcd = st.pills("会場", codes, default=codes[0], label_visibility="collapsed",
               format_func=lambda c: bc.VENUES[c], key="venue", on_change=on_venue_pick) or codes[0]
if st.session_state.get("venue_picked") and (open_codes is None or jcd in open_codes):
    bc.prefetch_venue(hd, jcd)   # 選んだ会場の全レースを裏で先に計算しておく

st.markdown('<div class="lbl">レース</div>', unsafe_allow_html=True)
def on_race_pick():
    st.session_state["race_picked"] = True


rno = st.pills("レース", list(range(1, 13)), default=1, label_visibility="collapsed",
               format_func=lambda n: f"{n}R", key="race", on_change=on_race_pick) or 1
if st.session_state.get("race_picked") and (open_codes is None or jcd in open_codes):
    bc.prefetch_race(hd, jcd, rno)   # 選んだレースを最優先で裏で計算しておく

rate_weight = 1   # 総合順位は3項目を同じ重みで足す
points = 8        # 1日まとめの内部計算用 (画面には出さない)

if st.button("ランキングを出す", type="primary", width="stretch"):
    st.session_state["query"] = (hd, jcd, rno)
    st.session_state.pop("show_result", None)
    st.session_state.pop("day_query", None)
if st.button("この日・この会場をまとめて見る", width="stretch"):
    st.session_state["day_query"] = (hd, jcd)
    st.session_state.pop("query", None)

day_query = st.session_state.get("day_query")
if day_query:
    d_hd, d_jcd = day_query
    try:
        with st.spinner("全レースの判定と結果を集めています（30秒ほどかかります）", show_time=True):
            day = get_day(d_hd, d_jcd, points)
    except Exception as e:
        st.error(f"データを取得できませんでした。（{type(e).__name__}: {str(e)[:160]}）")
        st.stop()
    dd = dt.datetime.strptime(d_hd, "%Y%m%d").date()

    def tally(rows, which, pick):
        sel = [x for x in rows if pick(x) and x["result"]]
        n = len(sel)
        cost = sum(len(x[which]) * 100 for x in sel)
        hits = [x for x in sel if x["hit_" + which]]
        ret = sum(x["pay"] for x in hits)
        return n, len(hits), cost, ret

    def sum_line(title, t):
        n, h, cost, ret = t
        if not n:
            return f'<div><b>{title}</b>：対象レースなし</div>'
        roi = ret / cost * 100
        low = " low" if roi < 100 else ""
        return (f'<div><span class="roi{low}">回収 {roi:.0f}%</span><b>{title}</b><br>'
                f'{n}レース・的中 {h}・購入 {cost:,}円・払戻 {ret:,}円</div>')

    st.markdown(
        f'<div class="day"><b class="t">{bc.VENUES[d_jcd]}　{dd.month}/{dd.day}（{WEEK[dd.weekday()]}）1日まとめ</b>'
        f'<span class="racesub">　1点100円</span><div class="sum">'
        + sum_line("ベスト6点を全レース買った場合", tally(day, "best6", lambda x: True))
        + sum_line("マイ買い目15点を全レース買った場合", tally(day, "my", lambda x: True))
        + '</div></div>',
        unsafe_allow_html=True,
    )

    def mark(v):
        if v is None:
            return '<span class="miss">-</span>'
        return '<span class="hit">○</span>' if v else '<span class="miss">×</span>'

    def mini_combo(c):
        return '<span class="mini">' + "".join(lane_chip(w) for w in c) + "</span>" if c else "未確定"

    section("レースごとの結果")
    rows_html = []
    for x in day:
        strong = x["best6_type"].startswith("1号艇が強い")
        rows_html.append(
            "<tr>"
            + f'<td class="pos">{x["rno"]}R</td>'
            + f'<td><span class="verdict {"v-buy" if strong else "v-ref"}" style="font-size:.64rem;padding:.2em .5em">'
            + ("1号艇強" if strong else "弱め") + '</span></td>'
            + f'<td class="num">{mark(x["hit_best6"])}</td>'
            + f'<td class="num">{mark(x["hit_my"])}</td>'
            + f'<td class="num">{mini_combo(x["result"])}</td>'
            + f'<td class="num">{(format(x["pay"], ",") + "円") if x["pay"] else "-"}</td></tr>'
        )
    st.markdown(
        '<div class="tblwrap"><table class="rk tight"><tr><th></th><th>型</th><th>6点</th><th>マイ</th>'
        '<th>結果</th><th>払戻</th></tr>' + "".join(rows_html) + '</table></div>'
        '<div class="note">6点・マイの○は、ベスト6点・マイ買い目15点にその日の3連単の結果が入っていたレースです。'
        '型は、1号艇が7点以下なら「1号艇強」、8点以上なら「弱め」です。'
        'まだ終わっていないレースは「-」になります。</div>',
        unsafe_allow_html=True,
    )
    st.markdown(FOOT_SVG, unsafe_allow_html=True)
    st.stop()

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
    st.error(f"データを取得できませんでした。時間をおいて再度お試しください。（{type(e).__name__}: {str(e)[:160]}）")
    st.stop()

d = dt.datetime.strptime(q_hd, "%Y%m%d").date()
st.markdown(
    f'<div class="race"><b>{bc.VENUES[q_jcd]} {q_rno}R　{info.race_name}</b>'
    f'<span>{d.year}/{d.month}/{d.day}（{WEEK[d.weekday()]}）　{unicodedata.normalize("NFKC", info.title)}　{info.day_label}</span></div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- 総合順位 ----

before = get_before(q_hd, q_jcd, q_rno)
exr = bc.exh_ranks(before) if before else None
flags = bc.attack_flags(before)
order3 = [x["racer"].waku for x in bc.overall(info.racers, rate_weight=1)]   # 3項目だけの並び (ベスト6点用)
rows = bc.overall(info.racers, rate_weight=rate_weight, exh_rank=exr)
eq_score = {x["racer"].waku: x["score"] for x in bc.overall(info.racers, rate_weight=1)}
makuri = bc.makuri_wakus(info.racers, eq_score)
labels = {w: bc.candidate_label(w, eq_score[w], w in makuri)[1] for w in eq_score}

# --------------------------------------------------------------- 買い目 ----

section("買い目（3連単）")
if st.button("🔄 オッズ・直前情報を更新", key="odds_refresh"):
    get_odds.clear()
    get_before.clear()
    st.rerun()
odds, otime, odds_err = get_odds(q_hd, q_jcd, q_rno)
corder = bc.course_order(info.racers)
my_rows = bc.my_bets(corder)
b6 = bc.best6(order3, corder, eq_score[1])
st.markdown(
    '<div class="bets">'
    + bet_card("best", "ベスト6点", "6点", b6["combos"], b6["desc"],
               "過去8日分（全場1,248レース）でよく出た並びから、レースの種類ごとに選んだ6点。",
               (b6["type"], b6["kind"], b6["note"]), odds=odds, points=6, otime=otime)
    + '</div>'
    + (f'<div class="note" style="color:#E60012">オッズを取得できませんでした（{odds_err}）。「オッズを更新」で取り直せます。</div>' if odds_err else '')
    + '<div class="note">ベスト6点は全体で的中31%・回収90%（前半93%・後半86%）。3連単は高配当で回収率が大きく動くので、目安として使ってください。</div>',
    unsafe_allow_html=True,
)
with st.expander("マイ買い目（15点）も見る"):
    st.markdown(
        '<div class="bets">' + my_card(my_rows, bc.my_verdict(corder), odds, otime) + '</div>'
        '<div class="note">マイ買い目は全体で的中26%・回収75%（前半57%・後半94%）。</div>',
        unsafe_allow_html=True,
    )

section("総合順位")
table(
    ["", "選手", "得点率", "平均ST", "コースAI評価"] + (["展示"] if exr else []) + ["合計"],
    [[
        f'<td class="pos">{r["rank"]}</td>',
        f'<td class="name">{lane_chip(r["racer"].waku)}{r["racer"].name}'
        f'<div class="meta">{badge(r["racer"].waku, eq_score[r["racer"].waku], r["racer"].waku in makuri)}'
        f'<span class="k">{r["racer"].klass}</span></div>{flag_html(flags.get(r["racer"].waku))}</td>',
        stacked(fmt(r["racer"].total_rate), r["rate_rank"]),
        stacked(fmt(r["racer"].total_st), r["st_rank"],
                SHOW_ST_MARK and r["racer"].waku != 1 and labels[r["racer"].waku] == "cx" and r["st_rank"] <= 3),
        grade_cell(r["racer"].course_grade, r["course_rank"]),
    ] + ([stacked(f'{before[r["racer"].waku]["exh"]:.2f}', r["exh_rank"])] if exr else []) + [
        f'<td class="num"><b>{r["score"]:g}</b></td>',
    ] for r in rows],
    top_first=True, cls="tight",
)
st.markdown(
    '<div class="note">得点率・平均STは今節と前節を合わせた全走です。コースAI評価は、今回の枠と同じコースの3連対率・2連対率を、'
    'そのコースを走る選手全体と比べて自動で判定したもの（S 上位10%・A 上位30%・B 中間・C 下位30%・D 下位10%）。'
    'その下の順位はコース3連対率の順位で、合計点にはこの順位を使っています。'
    + ('直前情報が出ているので、3つの順位に「展示タイムの順位×0.5」を足した合計が小さい順に並べています。'
       '目安ラベル・★1着候補・ベスト6点は、展示を入れない3項目の合計で判定しています。</div>'
       if exr else
       '3つの順位の合計が小さい順に並べています。直前情報（締切30分前ごろ）が出ると、展示タイムの順位も合計に入ります。</div>'),
    unsafe_allow_html=True,
)


LABEL_TEXT = {"c12": "1〜2着候補", "c23": "2〜3着候補", "c3": "3着候補", "cx": "消し候補"}


def guide_cell(item):
    if item is None:
        return '<td class="muted">—</td>'
    cond, (n, win, top2, top3) = item
    return f'<td><b>{cond}</b><br>1着 {win}%<br>2連対 {top2}%<br>3連対 {top3}%</td>'


guide_rows = "".join(
    f'<tr><td class="rowh"><span class="badge {kind}">{LABEL_TEXT[kind]}</span></td>'
    f'{guide_cell(one)}{guide_cell(other)}</tr>'
    for kind, one, other in bc.GUIDE_ROWS
)
st.markdown(
    '<div class="guide"><div class="gt">合計点の見方（3項目・3〜18点）</div><table>'
    '<tr><th></th><th>1号艇</th><th>2〜6号艇</th></tr>'
    + guide_rows + '</table>'
    f'<div class="mk1">{badge(2, 7, True)}'
    f'<div><b>まくり型</b>：2〜4号艇で、平均STが内側の艇全員より0.02秒以上早く、合計点が{bc.RULES["mk"]}点以下。'
    f'1着 {bc.MAKURI_GUIDE[0]}%・2連対 {bc.MAKURI_GUIDE[1]}%・3連対 {bc.MAKURI_GUIDE[2]}%。'
    f'この艇がいるレースは、1号艇の1着率が{bc.MAKURI_RACE_1WIN[0]}%から{bc.MAKURI_RACE_1WIN[1]}%に下がります。</div></div>'
    + ('<div class="mk1"><span class="stmark" style="margin-top:.1rem">ST上位</span>'
       '<div>消し候補の2〜6号艇のうち、平均STが1〜3位の艇の目印です。</div></div>' if SHOW_ST_MARK else '')
    + '<div class="gn">10/17〜10/24の全場1,248レースで、3項目を同じ重みにした合計点と着順を集計した目安です。'
    'ラベルも同じ重みの合計点で判定しています。</div></div>',
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ 得点率 ----

section("直前情報")
if before:
    by_course = sorted(before, key=lambda w: before[w]["course"])
    name_of = {r.waku: r.name for r in info.racers}
    table(
        ["進入", "選手", "展示タイム", "展示ST", "目印"],
        [[
            f'<td class="pos">{before[w]["course"]}</td>',
            f'<td class="name">{lane_chip(w)}{name_of.get(w, "")}</td>',
            stacked(f'{before[w]["exh"]:.2f}', exr[w]),
            f'<td class="num">{("F" if before[w].get("stF") else "")}{abs(before[w]["st"]):.2f}</td>',
            f'<td>{flag_html(flags.get(w))}</td>',
        ] for w in by_course],
        cls="tight",
    )
    st.markdown(
        '<div class="note">進入はスタート展示のコースです。目印は、10/17〜10/24の1,231レースで調べた条件です。'
        '<br>・まくり注意：3・4コースで、展示STが内側の全艇より0.05秒以上速く、展示タイム3番目以内（1着19%、通常12%）'
        '<br>・差し注意：2コースで、展示タイム1〜2番目、3コースの展示STが1コースより0.03秒以上速い（1着22%、通常15%）'
        '<br>・まくり差し注意：3・4コースで、すぐ内より0.03秒以上速く、展示タイム1〜2番目（1着16%、通常12%）'
        '<br>・1号艇注意：1コースの展示タイムが5〜6番目（1着40%、通常53%）</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="note">直前情報（展示タイム・スタート展示）はまだ出ていません。締切の30分前ごろに出るので、'
        '出たら「🔄 オッズ・直前情報を更新」を押してください。</div>',
        unsafe_allow_html=True,
    )


section("得点率")
rr = bc.rank_map({r.waku: r.total_rate for r in info.racers}, higher_is_better=True)
by_rate = sorted(info.racers, key=lambda r: (rr[r.waku], r.waku))
table(
    ["", "選手", "合算", "今節", "前節"],
    [[
        pos_cell(rr[r.waku]),
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
                pos_cell(sr[r.waku]),
                f'<td class="name">{lane_chip(r.waku)}{r.name}</td>',
                f'<td class="num"><b>{fmt(pick(r).avg_st)}</b></td>',
                f'<td class="num">{len(pick(r).sts)}</td>',
            ] for r in ordered],
        )

# ------------------------------------------------------------ コース別成績 ----

section("コース別成績")
cr = bc.rank_map({r.waku: r.course_top3 for r in info.racers}, higher_is_better=True, digits=1)
by_course = sorted(info.racers, key=lambda r: (cr[r.waku], r.waku))
table(
    ["", "選手", "AI評価", "3連対", "2連対", "1着"],
    [[
        pos_cell(cr[r.waku]),
        f'<td class="name">{lane_chip(r.waku)}{r.name}</td>',
        f'<td class="num">{grade_html(r.course_grade)}<div class="sub">{r.waku}コース{bc.GRADE_RANGE.get(r.course_grade or "", "")}</div></td>',
        f'<td class="num"><b>{pct0(r.course_top3)}</b></td>',
        f'<td class="num">{pct0(r.course_top2)}</td>',
        f'<td class="num">{pct0(r.course.win if r.course else None)}</td>',
    ] for r in by_course],
    cls="tight",
)
st.markdown(
    '<div class="note">公式サイトの選手データ（コース別成績）の数字です。順位と総合順位には3連対率を使っています。'
    '前付けなどで枠とコースが変わった場合は反映されません。</div>',
    unsafe_allow_html=True,
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
- コース別成績は、今回の枠番と同じコースでの成績です（公式サイトの選手データ）。総合順位には3連対率を使い、2連対率と1着率は参考として表示します。
- コースAI評価は、そのコースの3連対率と2連対率の平均を、同じコースを走る選手全体（10/17〜10/24の出走データ）と比べて S〜D を付けたものです。同じ評価でも1コースと6コースでは基準の数字が違います。
- 総合順位は、得点率・平均ST・コース別3連対率の順位の合計が小さい順です。同点のときは得点率が上の選手を上にします。
- 「★1着候補」は、2〜4号艇で平均STが内側の艇全員より0.02秒以上早く、合計点（同じ重み）が7点以下の艇です。
- データ元：BOAT RACE オフィシャルウェブサイト。サイトの仕様が変わると動かなくなることがあります。
"""
    )

st.markdown(FOOT_SVG, unsafe_allow_html=True)
