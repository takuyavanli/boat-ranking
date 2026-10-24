"""ボートレース 得点率・平均ST ランキング (Streamlit アプリ)

起動: streamlit run app.py
"""

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import streamlit as st

import boat_core as bc

st.set_page_config(page_title="得点率・STランキング", page_icon="🚤", layout="centered")

LANE = {  # 枠の色 (背景, 文字)
    1: ("#FFFFFF", "#111111"), 2: ("#111111", "#FFFFFF"), 3: ("#E60012", "#FFFFFF"),
    4: ("#0068B7", "#FFFFFF"), 5: ("#FFE100", "#111111"), 6: ("#009944", "#FFFFFF"),
}

st.markdown(
    """
<style>
.block-container {padding-top: 3.6rem; max-width: 760px;}
h1.apptitle {font-size: 1.6rem; font-weight: 800; margin: 0 0 .1rem; padding: 0; letter-spacing: .01em;}
table.rk {width: 100%; border-collapse: collapse; font-size: 0.95rem;}
table.rk th {text-align: left; font-weight: 600; font-size: 0.8rem; opacity: .65;
  padding: 6px 6px; border-bottom: 1px solid rgba(128,128,128,.35);}
table.rk td {padding: 9px 6px; border-bottom: 1px solid rgba(128,128,128,.18); vertical-align: middle;}
table.rk td.num {font-variant-numeric: tabular-nums; white-space: nowrap;}
table.rk td.pos {font-size: 1.15rem; font-weight: 700; width: 2.2em;}
table.rk td.name {white-space: nowrap;}
table.rk .sub {font-size: 0.78rem; opacity: .6;}
.lane {display: inline-flex; align-items: center; justify-content: center; width: 1.7em; height: 1.7em;
  font-weight: 700; border-radius: 3px; border: 1px solid rgba(128,128,128,.6); margin-right: .45em;}
.racehead {font-size: 1.05rem; font-weight: 700; margin: .2rem 0 0;}
.racesub {opacity: .65; font-size: .85rem; margin-bottom: .6rem;}
.marks {font-size: 0.8rem; opacity: .7; letter-spacing: .04em; white-space: normal;}
.tblwrap {overflow-x: auto;}
</style>
""",
    unsafe_allow_html=True,
)


# ------------------------------------------------------------ 表示用の関数 ----

def fmt(v, digits=2):
    if v is None:
        return "-"
    q = Decimal(1).scaleb(-digits)
    return str(Decimal(str(v)).quantize(q, rounding=ROUND_HALF_UP))


def lane_chip(w):
    bg, fg = LANE[w]
    return f'<span class="lane" style="background:{bg};color:{fg}">{w}</span>'


def period(s: bc.Series):
    if not s.start:
        return ""
    return f"{s.start:%m/%d}〜{s.end:%m/%d}"


def table(headers, rows):
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(rows_i) + "</tr>" for rows_i in rows)
    st.markdown(f'<div class="tblwrap"><table class="rk"><tr>{th}</tr>{body}</table></div>',
                unsafe_allow_html=True)


# ------------------------------------------------------------- データ取得 ----

@st.cache_data(ttl=600, show_spinner=False)
def open_venues(hd):
    try:
        return bc.parse_open_venues(bc.fetch_index_html(hd))
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def racelist_html(hd, jcd, rno):
    return bc.fetch_racelist_html(hd, jcd, rno)


@st.cache_data(ttl=60, show_spinner=False)
def result_html(hd, jcd, rno):
    return bc.fetch_result_html(hd, jcd, rno)


# ------------------------------------------------------------------- 入力 ----

st.markdown('<h1 class="apptitle">得点率・STランキング</h1>', unsafe_allow_html=True)
st.caption("今節と前節の成績・平均STから、出走6艇をいい順に並べます。")

today = dt.datetime.now(ZoneInfo("Asia/Tokyo")).date()
c1, c2, c3 = st.columns([1.2, 1.2, 0.8])
with c1:
    day = st.date_input("日付", value=today, format="YYYY/MM/DD")
hd = day.strftime("%Y%m%d")
codes = open_venues(hd) or list(bc.VENUES)
with c2:
    jcd = st.selectbox("会場", codes, format_func=lambda c: bc.VENUES[c])
with c3:
    rno = st.selectbox("レース", list(range(1, 13)), format_func=lambda n: f"{n}R")

weight_label = st.radio(
    "総合順位の重み", ["得点率とSTを同じ重みで", "得点率を2倍重視"], horizontal=True,
)
rate_weight = 2 if weight_label.startswith("得点率を2倍") else 1

go = st.button("ランキングを出す", type="primary", width="stretch")

if go:
    st.session_state["query"] = (hd, jcd, rno)

query = st.session_state.get("query")
if not query:
    st.info("日付・会場・レースを選んで「ランキングを出す」を押してください。")
    st.stop()

# ------------------------------------------------------------------- 計算 ----

hd, jcd, rno = query
try:
    with st.spinner("公式サイトから成績を取得しています（前節の検索に少しかかります）"):
        info = bc.build_race(
            hd, jcd, rno,
            racelist_loader=racelist_html,
            result_loader=result_html,
            store=bc.KStore(),
        )
except ValueError as e:
    st.error(str(e))
    st.stop()
except Exception as e:  # 通信エラーなど
    st.error(f"データを取得できませんでした。時間をおいて再度お試しください。（{type(e).__name__}）")
    st.stop()

d = dt.datetime.strptime(hd, "%Y%m%d").date()
st.markdown(
    f'<div class="racehead">{bc.VENUES[jcd]} {rno}R　{info.race_name}</div>'
    f'<div class="racesub">{d:%Y/%m/%d}　{info.title}　{info.day_label}</div>',
    unsafe_allow_html=True,
)
if info.result:
    st.success(f"このレースの結果：{info.result}")

# ---------------------------------------------------------------- 総合順位 ----

rows = bc.overall(info.racers, rate_weight=rate_weight)
st.subheader("総合順位")
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
)
st.caption(
    "得点率・平均STは今節と前節を合わせた全走で計算。"
    + ("得点率の順位×2＋STの順位" if rate_weight == 2 else "得点率の順位＋STの順位")
    + "が小さい順で、同点は得点率が上の選手を上にしています。"
)

# ------------------------------------------------------------------ 得点率 ----

st.subheader("得点率")
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

st.subheader("平均ST")
tab_cur, tab_prev = st.tabs(["今節", "前節"])
for tab, pick in ((tab_cur, lambda r: r.current), (tab_prev, lambda r: r.prev)):
    with tab:
        vals = {r.waku: pick(r).avg_st for r in info.racers}
        sr = bc.rank_map(vals, higher_is_better=False)
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
