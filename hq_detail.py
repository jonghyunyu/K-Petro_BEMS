"""
K-REMS '본부별 상세분석' 탭
------------------------------------------------------------
본부를 고르면
  ① 온실가스·전력·용수 누계 실적(목표 대비 게이지 + 전년 동기비·전월비·전사 비중)
  ② 일별 전력 그래프 (월별 / 기간별 조회, 전월·직전 기간 비교선, 급증일 표시)
  ③ 시간대별 사용 패턴 (평일·주말 24시간, 야간 대기전력 비율)
  ④ 자동 분석 코멘트 (전년 대비 · 전월 대비 · 전사 대비 · 일별 패턴)
  ⑤ 'OO본부 에너지·자원 사용현황' PDF 출력
을 보여준다. 데이터는 app.py 가 읽은 월간 실적과 한전 자동 수집기(kepco_sync.py)가
쌓는 '일간전력' / '시간별전력' 탭을 쓴다. 외부 AI API 는 쓰지 않는다 (비용 0원).
"""

import calendar
import io
import os
import textwrap
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

KPETRO_GREEN = "#8CC63F"
KPETRO_GRAY = "#54585A"
WEEKEND_COLOR = "#B7C4CC"
SPIKE_COLOR = "#FF3B30"
COMPARE_COLOR = "#F28C28"

EF = {"전력사용량": 0.0004594106, "도시가스사용량": 0.002187587, "실내등유사용량": 0.0024652936}
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
SPIKE_RATIO = 1.3          # 같은 요일 유형(평일/주말) 중앙값의 1.3배를 넘으면 '급증'
WEEKLY_THRESHOLD = 62      # 조회 기간이 이 일수를 넘으면 주 단위 합계로 표시
NIGHT_HOURS = [22, 23, 0, 1, 2, 3, 4, 5]
DAY_HOURS = list(range(9, 18))

FONT_CANDIDATES = [
    "fonts/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/nanum/NanumGothic.ttf",
]
FONT_BOLD_CANDIDATES = [
    "fonts/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/nanum/NanumGothicBold.ttf",
]


# =============================================================================
# 데이터 정리
# =============================================================================
def _to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.strip(), errors="coerce")


def prepare_daily(df):
    """'일간전력' 탭 -> 날짜(date), 본부명, 전력사용량, 최대수요(kW)"""
    if df is None or df.empty or "날짜" not in df.columns:
        return pd.DataFrame(columns=["날짜", "본부명", "전력사용량", "최대수요(kW)"])
    d = df.copy()
    d["날짜"] = pd.to_datetime(d["날짜"], errors="coerce").dt.date
    d = d.dropna(subset=["날짜"])
    d["본부명"] = d["본부명"].astype(str).str.strip()
    d["전력사용량"] = _to_num(d["전력사용량"])
    d["최대수요(kW)"] = _to_num(d["최대수요(kW)"]) if "최대수요(kW)" in d.columns else float("nan")
    return d[["날짜", "본부명", "전력사용량", "최대수요(kW)"]]


def prepare_hourly(df):
    """'시간별전력' 탭 -> 날짜(date), 본부명, 0시 ~ 23시"""
    hours = [f"{h}시" for h in range(24)]
    if df is None or df.empty or "날짜" not in df.columns or not set(hours) <= set(df.columns):
        return pd.DataFrame(columns=["날짜", "본부명"] + hours)
    d = df.copy()
    d["날짜"] = pd.to_datetime(d["날짜"], errors="coerce").dt.date
    d = d.dropna(subset=["날짜"])
    d["본부명"] = d["본부명"].astype(str).str.strip()
    for h in hours:
        d[h] = _to_num(d[h])
    return d[["날짜", "본부명"] + hours]


def _monthly(df, group_col, hq, col):
    if df is None or df.empty or col not in df.columns or group_col not in df.columns or "월_num" not in df.columns:
        return {}
    d = df[df[group_col].astype(str) == hq]
    return {int(k): float(v) for k, v in d.groupby("월_num")[col].sum().items() if int(k) > 0}


def _ghg(e, g, k):
    return e * EF["전력사용량"] + g * EF["도시가스사용량"] + k * EF["실내등유사용량"]


def _pct(cur, prev):
    return (cur / prev - 1) * 100 if prev and prev > 0 else None


def hq_metrics(ctx, hq):
    """본부 1곳의 누계·전년 동기·전월·목표 수치"""
    gc, n = ctx["group_col"], ctx["hq_last"].get(hq, 0)
    cur = {c: _monthly(ctx["df_actual"], gc, hq, c) for c in ["전력사용량", "도시가스사용량", "실내등유사용량", "용수사용량"]}
    prev = {c: _monthly(ctx["df_2025"], gc, hq, c) for c in cur}
    months = range(1, n + 1)

    def ytd(src, c, ms=months):
        return sum(src[c].get(m, 0.0) for m in ms)

    def month_val(src, c, m):
        return src[c].get(m, 0.0)

    out = {}
    e, g, k = ytd(cur, "전력사용량"), ytd(cur, "도시가스사용량"), ytd(cur, "실내등유사용량")
    pe, pg, pk = ytd(prev, "전력사용량"), ytd(prev, "도시가스사용량"), ytd(prev, "실내등유사용량")
    # 용수: 고지서가 늦게 오는 달(올해 0)은 전년에서도 빼고 비교
    water_ms = [m for m in months if cur["용수사용량"].get(m, 0) > 0]
    w, pw = ytd(cur, "용수사용량"), ytd(prev, "용수사용량", water_ms)

    out["온실가스"] = {"ytd": _ghg(e, g, k), "prev_ytd": _ghg(pe, pg, pk), "unit": "tCO2eq"}
    out["전력"] = {"ytd": e, "prev_ytd": pe, "unit": "kWh"}
    out["용수"] = {"ytd": w, "prev_ytd": pw, "unit": "ton"}

    # 당월 vs 전월 (1월이면 작년 12월과 비교)
    def m_ghg(src, m):
        return _ghg(month_val(src, "전력사용량", m), month_val(src, "도시가스사용량", m), month_val(src, "실내등유사용량", m))
    pm_src, pm = (prev, 12) if n == 1 else (cur, n - 1)
    out["온실가스"].update(month=m_ghg(cur, n), prev_month=m_ghg(pm_src, pm))
    out["전력"].update(month=month_val(cur, "전력사용량", n), prev_month=month_val(pm_src, "전력사용량", pm))
    out["용수"].update(month=month_val(cur, "용수사용량", n), prev_month=month_val(pm_src, "용수사용량", pm))

    # 연간 목표 (없으면 기존 화면과 같은 방식으로 누계 x 1.5)
    tg = ctx["df_target"]
    t_row = None
    if tg is not None and not tg.empty and gc in tg.columns:
        rows = tg[tg[gc].astype(str) == hq]
        if not rows.empty:
            t_row = rows.sum(numeric_only=True)
    for key, col in [("온실가스", "온실가스목표_연간"), ("전력", "전력목표_연간"), ("용수", "용수목표_연간")]:
        t = float(t_row.get(col, 0)) if t_row is not None else 0.0
        out[key]["target"] = t if t > 0 else out[key]["ytd"] * 1.5
        out[key]["target_given"] = t > 0
        out[key]["yoy"] = _pct(out[key]["ytd"], out[key]["prev_ytd"])
        out[key]["mom"] = _pct(out[key]["month"], out[key]["prev_month"])
    return out


def company_metrics(ctx):
    """전사 합계와 본부별 온실가스 전년 대비 증감률(순위용)"""
    per_hq = {hq: hq_metrics(ctx, hq) for hq in ctx["hqs"]}
    total = {}
    for key in ["온실가스", "전력", "용수"]:
        ytd = sum(m[key]["ytd"] for m in per_hq.values())
        prev = sum(m[key]["prev_ytd"] for m in per_hq.values())
        total[key] = {"ytd": ytd, "prev_ytd": prev, "yoy": _pct(ytd, prev)}
    ghg_yoy = {hq: m["온실가스"]["yoy"] for hq, m in per_hq.items() if m["온실가스"]["yoy"] is not None}
    ranking = sorted(ghg_yoy, key=lambda h: ghg_yoy[h])  # 감소율이 큰 순
    return per_hq, total, ranking


# =============================================================================
# 일별 / 시간대별 분석
# =============================================================================
def month_range(y, m):
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def prev_month(y, m):
    return (y - 1, 12) if m == 1 else (y, m - 1)


def daily_series(daily, hq, start, end):
    d = daily[(daily["본부명"] == hq) & (daily["날짜"] >= start) & (daily["날짜"] <= end)]
    return d.groupby("날짜")[["전력사용량", "최대수요(kW)"]].sum(min_count=1)


def build_daily_frame(daily, hq, start, end, cmp_start, cmp_end, metric):
    """조회 기간의 날짜별 값 + 같은 위치(n번째 날)의 비교 기간 값"""
    cur = daily_series(daily, hq, start, end)
    cmp_ = daily_series(daily, hq, cmp_start, cmp_end) if cmp_start else pd.DataFrame()
    n_days = (end - start).days + 1
    rows = []
    for i in range(n_days):
        dt = start + timedelta(days=i)
        cdt = cmp_start + timedelta(days=i) if cmp_start else None
        val = cur[metric].get(dt) if metric in cur.columns else None
        cval = cmp_[metric].get(cdt) if (cdt and cdt <= cmp_end and metric in cmp_.columns) else None
        rows.append({
            "날짜": dt, "값": None if pd.isna(val) else val,
            "비교날짜": cdt, "비교값": None if (cval is None or pd.isna(cval)) else cval,
            "주말": dt.weekday() >= 5,
        })
    f = pd.DataFrame(rows)
    f["급증"] = False
    for weekend in (False, True):
        part = f[(f["주말"] == weekend) & f["값"].notna()]
        if len(part) >= 3:
            med = part["값"].median()
            if med > 0:
                f.loc[part.index, "급증"] = part["값"] > med * SPIKE_RATIO
    return f


def daily_stats(f):
    v = f[f["값"].notna()]
    wd, we = v[~v["주말"]]["값"], v[v["주말"]]["값"]
    cmp_v = f["비교값"].dropna()
    return {
        "days": len(v), "sum": v["값"].sum() if len(v) else 0.0,
        "avg": v["값"].mean() if len(v) else None,
        "weekday_avg": wd.mean() if len(wd) else None,
        "weekend_avg": we.mean() if len(we) else None,
        "max_row": v.loc[v["값"].idxmax()] if len(v) else None,
        "spikes": v[v["급증"]],
        # 비교는 양쪽 모두 값이 있는 날끼리만 (데이터가 아직 쌓이는 중인 날은 제외)
        "cmp_cur_sum": f[f["값"].notna() & f["비교값"].notna()]["값"].sum(),
        "cmp_sum": f[f["값"].notna() & f["비교값"].notna()]["비교값"].sum(),
        "has_cmp": len(cmp_v) > 0,
    }


def hourly_profile(hourly, hq, start, end):
    """평일·주말 시간대별 평균 사용량(kWh)과 야간 대기전력 비율(밤 ÷ 낮)"""
    hours = [f"{h}시" for h in range(24)]
    d = hourly[(hourly["본부명"] == hq) & (hourly["날짜"] >= start) & (hourly["날짜"] <= end)]
    d = d.dropna(subset=hours, how="all")
    if d.empty:
        return None
    is_we = d["날짜"].map(lambda x: x.weekday() >= 5)
    wd, we = d[~is_we][hours].mean(), d[is_we][hours].mean()
    night = wd[[f"{h}시" for h in NIGHT_HOURS]].mean()
    day = wd[[f"{h}시" for h in DAY_HOURS]].mean()
    return {
        "weekday": wd if (~is_we).any() else None,
        "weekend": we if is_we.any() else None,
        "base_ratio": (night / day * 100) if day and day > 0 and pd.notna(night) else None,
        "days": len(d),
    }


# =============================================================================
# 자동 분석 코멘트 (규칙 기반)
# =============================================================================
def _fmt_pct(v):
    return f"{v:+.1f}%"


def build_comments(hq_name, m, total, ranking, hq, n_month, stats, prof, period_label, metric_label):
    """(아이콘, 문장) 목록. 계산은 모두 코드가 하고 정해진 문장 틀에 채워 넣는다."""
    c = []
    n_hq = len(ranking)

    # 1) 전년 대비 (누계) + 전사 대비
    g = m["온실가스"]
    if g["yoy"] is not None:
        comp = total["온실가스"]["yoy"]
        if comp is not None:
            diff = g["yoy"] - comp
            if diff <= -1:
                judge = "전사 평균보다 감축 성과가 우수합니다"
                icon = "✅"
            elif diff >= 1:
                judge = "전사 평균보다 증가 폭이 커 원인 점검이 필요합니다"
                icon = "⚠️"
            else:
                judge = "전사 평균과 비슷한 수준입니다"
                icon = "ℹ️"
            rank_txt = f" (온실가스 감축률 {n_hq}개 본부 중 {ranking.index(hq) + 1}위)" if hq in ranking else ""
            c.append((icon, f"1~{n_month}월 온실가스 배출량은 전년 동기 대비 {_fmt_pct(g['yoy'])}로, "
                            f"전사 평균({_fmt_pct(comp)})과 비교해 {judge}{rank_txt}."))
        else:
            c.append(("ℹ️", f"1~{n_month}월 온실가스 배출량은 전년 동기 대비 {_fmt_pct(g['yoy'])}입니다."))

    # 2) 전사 대비 비중
    if total["전력"]["ytd"] > 0:
        share = m["전력"]["ytd"] / total["전력"]["ytd"] * 100
        c.append(("ℹ️", f"전력사용량 누계는 {m['전력']['ytd']:,.0f} kWh로 전사 사용량의 {share:.1f}%를 차지합니다."))

    # 3) 연간 목표 진행률
    if g["target_given"] and g["target"] > 0:
        use = g["ytd"] / g["target"] * 100
        elapsed = n_month / 12 * 100
        if use > elapsed + 3:
            c.append(("⚠️", f"연간 온실가스 목표의 {use:.1f}%를 사용해 경과율({elapsed:.0f}%)보다 빠릅니다. "
                            f"남은 기간 월 평균 {max(g['target'] - g['ytd'], 0) / max(12 - n_month, 1):,.1f} tCO2eq 이내 관리가 필요합니다."))
        else:
            c.append(("✅", f"연간 온실가스 목표의 {use:.1f}%를 사용해 경과율({elapsed:.0f}%) 이내로 관리되고 있습니다."))

    # 4) 전월 대비 (당월)
    e = m["전력"]
    if e["mom"] is not None and e["month"] > 0:
        icon = "⚠️" if e["mom"] >= 10 else "✅" if e["mom"] <= -5 else "ℹ️"
        c.append((icon, f"{n_month}월 전력사용량은 {e['month']:,.0f} kWh로 전월 대비 {_fmt_pct(e['mom'])}입니다."))

    # 5) 일별 패턴 (조회 기간)
    if stats and stats["days"] > 0:
        if stats["has_cmp"] and stats["cmp_sum"] > 0:
            ch = _pct(stats["cmp_cur_sum"], stats["cmp_sum"])
            icon = "⚠️" if ch >= 10 else "✅" if ch <= -5 else "ℹ️"
            c.append((icon, f"{period_label} 일별 {metric_label}은(는) 비교 기간의 같은 날짜 대비 {_fmt_pct(ch)}입니다."))
        if stats["weekday_avg"] and stats["weekend_avg"] is not None and metric_label == "전력사용량":
            r = stats["weekend_avg"] / stats["weekday_avg"] * 100
            if r >= 60:
                c.append(("⚠️", f"주말 하루 평균 사용량이 평일의 {r:.0f}%로 높습니다. 주말 냉난방·공조 설비의 운전 일정을 점검해 보세요."))
            else:
                c.append(("✅", f"주말 하루 평균 사용량은 평일의 {r:.0f}% 수준으로 적정합니다."))
        sp = stats["spikes"]
        if len(sp):
            days = ", ".join(f"{d.month}/{d.day}({WEEKDAY_KO[d.weekday()]})" for d in sp["날짜"].head(4))
            more = f" 외 {len(sp) - 4}일" if len(sp) > 4 else ""
            c.append(("⚠️", f"같은 요일 유형 평균보다 {int((SPIKE_RATIO - 1) * 100)}% 이상 급증한 날: {days}{more}. 행사·설비 고장·데이터 오류 여부를 확인하세요."))

    # 6) 야간 대기전력 비율 (밤 ÷ 낮)
    if prof and prof["base_ratio"] is not None:
        r = prof["base_ratio"]
        if r >= 50:
            c.append(("⚠️", f"사람이 없는 밤(22~06시)에도 낮(09~18시) 전력의 {r:.0f}%를 쓰고 있습니다. 퇴근 후 꺼지지 않는 조명·냉난방·대기전력을 점검하면 절감 여지가 있습니다."))
        else:
            c.append(("✅", f"밤(22~06시) 전력 사용은 낮(09~18시)의 {r:.0f}% 수준으로, 퇴근 후 전력 관리가 잘 되고 있습니다."))

    if not c:
        c.append(("ℹ️", "분석할 데이터가 아직 충분하지 않습니다."))
    return c


# =============================================================================
# 차트 (화면용 plotly)
# =============================================================================
def daily_chart(f, metric_label, unit, cmp_label, weekly=False):
    fig = go.Figure()
    if weekly:
        w = f.dropna(subset=["값"]).copy()
        w["주"] = w["날짜"].map(lambda x: x - timedelta(days=x.weekday()))
        agg = "max" if unit == "kW" else "sum"
        g = w.groupby("주")["값"].agg(agg).reset_index()
        labels = [f"{d.month}/{d.day}주" for d in g["주"]]
        fig.add_bar(x=labels, y=g["값"], marker_color=KPETRO_GREEN, name=f"주간 {metric_label}",
                    hovertemplate="%{x}<br>%{y:,.0f} " + unit + "<extra></extra>")
    else:
        labels = [f"{d.month}.{d.day}" if (i == 0 or d.day == 1) else f"{d.day}" for i, d in enumerate(f["날짜"])]
        colors = [SPIKE_COLOR if s else WEEKEND_COLOR if we else KPETRO_GREEN for s, we in zip(f["급증"], f["주말"])]
        hover = [f"{d.month}/{d.day}({WEEKDAY_KO[d.weekday()]})" for d in f["날짜"]]
        fig.add_bar(x=labels, y=f["값"], marker_color=colors, name=metric_label, customdata=hover, showlegend=False,
                    hovertemplate="%{customdata}<br>%{y:,.0f} " + unit + "<extra></extra>")
        for nm, colr in [("평일", KPETRO_GREEN), ("주말", WEEKEND_COLOR), ("급증일", SPIKE_COLOR)]:
            fig.add_scatter(x=[None], y=[None], mode="markers", name=nm, hoverinfo="skip",
                            marker=dict(symbol="square", size=12, color=colr))
        if f["비교값"].notna().any():
            ch = [f"{d.month}/{d.day}" if d else "" for d in f["비교날짜"]]
            fig.add_scatter(x=labels, y=f["비교값"], mode="lines+markers", name=cmp_label,
                            line=dict(color=COMPARE_COLOR, width=2.5, shape="spline"), marker=dict(size=7),
                            customdata=ch, hovertemplate="%{customdata}<br>%{y:,.0f} " + unit + "<extra></extra>")
        v = f[f["값"].notna() & ~f["주말"]]["값"]
        if len(v):
            fig.add_hline(y=v.mean(), line_dash="dot", line_color=KPETRO_GRAY,
                          annotation_text=f"평일 평균 {v.mean():,.0f}", annotation_position="top right",
                          annotation_bgcolor="rgba(255,255,255,0.8)")
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), bargap=0.35,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                      yaxis_title=f"{metric_label}({unit})", plot_bgcolor="white",
                      xaxis=dict(type="category", rangeslider=dict(visible=weekly or len(f) > 31)))
    fig.update_yaxes(gridcolor="#E5E5E5", rangemode="tozero")
    return fig


def hourly_chart(prof):
    fig = go.Figure()
    x = [f"{h}시" for h in range(24)]
    if prof["weekday"] is not None:
        fig.add_scatter(x=x, y=prof["weekday"].values, mode="lines+markers", name="평일 평균",
                        line=dict(color=KPETRO_GREEN, width=3))
    if prof["weekend"] is not None:
        fig.add_scatter(x=x, y=prof["weekend"].values, mode="lines+markers", name="주말 평균",
                        line=dict(color=WEEKEND_COLOR, width=3, dash="dash"))
    fig.add_vrect(x0="22시", x1="23시", fillcolor="#455A64", opacity=0.08, line_width=0)
    fig.add_vrect(x0="0시", x1="5시", fillcolor="#455A64", opacity=0.08, line_width=0,
                  annotation_text="야간", annotation_position="top left")
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                      yaxis_title="시간당 사용량(kWh)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_yaxes(gridcolor="#E5E5E5", rangemode="tozero")
    return fig


# =============================================================================
# PDF 보고서 (matplotlib, A4 1장)
# =============================================================================
def _font_path(cands):
    for p in cands:
        if os.path.exists(p):
            return p
    return None


@st.cache_data(show_spinner=False, max_entries=64)
def make_pdf(hq_disp, n_month, m, f, metric_label, unit, period_label, cmp_label, weekly, prof, comments, logo_path=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.backends.backend_pdf import PdfPages

    reg, bold = _font_path(FONT_CANDIDATES), _font_path(FONT_BOLD_CANDIDATES)
    if reg:
        font_manager.fontManager.addfont(reg)
        fam = font_manager.FontProperties(fname=reg).get_name()
        if bold:
            font_manager.fontManager.addfont(bold)
        plt.rcParams["font.family"] = fam
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(8.27, 11.69))  # A4
    W = fig.text

    # --- 머리글
    fig.patches.append(plt.Rectangle((0, 0.945), 1, 0.055, transform=fig.transFigure, color=KPETRO_GRAY, zorder=0))
    fig.patches.append(plt.Rectangle((0, 0.941), 1, 0.004, transform=fig.transFigure, color=KPETRO_GREEN, zorder=0))
    W(0.05, 0.965, f"{hq_disp} 에너지·자원 사용현황", fontsize=17, fontweight="bold", color="white", va="center")
    W(0.95, 0.965, "K-REMS", fontsize=12, fontweight="bold", color=KPETRO_GREEN, va="center", ha="right")
    W(0.05, 0.922, f"누계 기준: 2026년 1~{n_month}월   |   일별 조회: {period_label}   |   출력: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
      fontsize=8.5, color=KPETRO_GRAY)

    # --- ① 실적 요약 표
    W(0.05, 0.895, "1. 누계 실적 요약", fontsize=11.5, fontweight="bold", color=KPETRO_GRAY)
    ax = fig.add_axes([0.05, 0.775, 0.90, 0.11]); ax.axis("off")
    head = ["항목", "누계 실적", "연간 목표", "목표 대비", "전년 동기 대비", f"{n_month}월 전월 대비"]
    body = []
    for key in ["온실가스", "전력", "용수"]:
        x = m[key]; fmt = ",.1f" if key == "온실가스" else ",.0f"
        body.append([
            f"{key} ({x['unit']})", format(x["ytd"], fmt),
            format(x["target"], fmt) if x["target_given"] else "-",
            f"{x['ytd'] / x['target'] * 100:.1f}%" if x["target_given"] and x["target"] else "-",
            _fmt_pct(x["yoy"]) if x["yoy"] is not None else "-",
            _fmt_pct(x["mom"]) if x["mom"] is not None else "-",
        ])
    tb = ax.table(cellText=body, colLabels=head, loc="center", cellLoc="center", colWidths=[.2, .17, .17, .13, .17, .16])
    tb.auto_set_font_size(False); tb.set_fontsize(9); tb.scale(1, 1.55)
    for (r, ccol), cell in tb.get_celld().items():
        cell.set_edgecolor("#D0D0D0")
        if r == 0:
            cell.set_facecolor(KPETRO_GRAY); cell.get_text().set_color("white"); cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor("#F7F9F5" if r % 2 else "white")
            t = cell.get_text().get_text()
            if ccol in (4, 5) and t.startswith("+"):
                cell.get_text().set_color("#D32F2F")
            elif ccol in (4, 5) and t.startswith("-") and t != "-":
                cell.get_text().set_color("#2E7D32")

    # --- ② 일별 그래프
    W(0.05, 0.748, f"2. 일별 {metric_label} ({period_label})", fontsize=11.5, fontweight="bold", color=KPETRO_GRAY)
    ax = fig.add_axes([0.08, 0.548, 0.87, 0.185])
    if weekly:
        w = f.dropna(subset=["값"]).copy()
        w["주"] = w["날짜"].map(lambda x: x - timedelta(days=x.weekday()))
        g = w.groupby("주")["값"].agg("max" if unit == "kW" else "sum").reset_index()
        xs = range(len(g))
        ax.bar(xs, g["값"], color=KPETRO_GREEN, width=0.6)
        ax.set_xticks(list(xs)); ax.set_xticklabels([f"{d.month}/{d.day}주" for d in g["주"]], fontsize=7, rotation=45)
    else:
        xs = list(range(len(f)))
        colors = [SPIKE_COLOR if s else WEEKEND_COLOR if we else KPETRO_GREEN for s, we in zip(f["급증"], f["주말"])]
        ax.bar(xs, f["값"].fillna(0), color=colors, width=0.6)
        if f["비교값"].notna().any():
            ax.plot(xs, f["비교값"], color=COMPARE_COLOR, marker="o", ms=3, lw=1.5, label=cmp_label)
        step = 1 if len(f) <= 31 else max(len(f) // 20, 1)
        ax.set_xticks(xs[::step])
        ax.set_xticklabels([f"{d.month}/{d.day}" if (i == 0 or d.day == 1) else str(d.day)
                            for i, d in list(enumerate(f["날짜"]))[::step]], fontsize=7)
        ax.legend(fontsize=7.5, loc="upper right", frameon=False, ncol=2)
    ax.set_ylabel(unit, fontsize=8); ax.tick_params(axis="y", labelsize=7.5)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(axis="y", color="#E5E5E5"); ax.set_axisbelow(True)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    W(0.08, 0.502, "■ 평일", fontsize=7.5, color=KPETRO_GREEN)
    W(0.13, 0.502, "■ 주말", fontsize=7.5, color="#8FA3AE")
    W(0.18, 0.502, f"■ 급증일(같은 요일 유형 중앙값의 {SPIKE_RATIO:.1f}배 초과)", fontsize=7.5, color=SPIKE_COLOR)

    # --- ③ 시간대별 패턴
    W(0.05, 0.472, "3. 시간대별 사용 패턴 (평일·주말 평균)", fontsize=11.5, fontweight="bold", color=KPETRO_GRAY)
    ax = fig.add_axes([0.08, 0.335, 0.87, 0.125])
    if prof:
        hx = list(range(24))
        if prof["weekday"] is not None:
            ax.plot(hx, prof["weekday"].values, color=KPETRO_GREEN, lw=2, marker="o", ms=2.5, label="평일 평균")
        if prof["weekend"] is not None:
            ax.plot(hx, prof["weekend"].values, color="#8FA3AE", lw=2, ls="--", label="주말 평균")
        ax.axvspan(-0.5, 5.5, color="#455A64", alpha=0.07); ax.axvspan(21.5, 23.5, color="#455A64", alpha=0.07)
        ax.set_xticks(hx); ax.set_xticklabels([f"{h}" for h in hx], fontsize=7); ax.set_xlim(-0.5, 23.5)
        ax.set_ylabel("kWh/시간", fontsize=8); ax.tick_params(axis="y", labelsize=7.5)
        ax.legend(fontsize=7.5, loc="upper left", frameon=False, ncol=2)
        ax.grid(axis="y", color="#E5E5E5")
        for s in ["top", "right"]:
            ax.spines[s].set_visible(False)
    else:
        ax.axis("off")
        ax.text(0.5, 0.5, "시간대별 데이터가 아직 없습니다.", ha="center", va="center", fontsize=9, color="#888")

    # --- ④ 분석 코멘트
    W(0.05, 0.288, "4. 자동 분석 코멘트", fontsize=11.5, fontweight="bold", color=KPETRO_GRAY)
    y = 0.266
    marks = {"✅": ("●", "#2E7D32"), "⚠️": ("▲", "#E65100"), "ℹ️": ("■", "#546E7A")}
    for icon, text in comments:
        mk, col = marks.get(icon, ("■", KPETRO_GRAY))
        lines = textwrap.wrap(text, 58)
        W(0.06, y, mk, fontsize=8, color=col, va="top")
        for ln in lines:
            W(0.085, y, ln, fontsize=8.8, color="#333333", va="top")
            y -= 0.018
        y -= 0.006
        if y < 0.05:
            break

    W(0.5, 0.022, "한국석유관리원 운영지원팀 · K-REMS 통합 모니터링 (자동 생성 보고서)", fontsize=7.5, color="#888888", ha="center")

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        pdf.savefig(fig)
    plt.close(fig)
    return buf.getvalue()


# =============================================================================
# 화면
# =============================================================================
def _delta_html(label, v, good_down=True):
    if v is None:
        return f"<span style='color:#999'>{label} -</span>"
    up = v > 0
    color = ("#D32F2F" if up else "#2E7D32") if good_down else ("#2E7D32" if up else "#D32F2F")
    arrow = "▲" if up else "▼"
    return f"<span style='color:{color}; font-weight:bold'>{label} {arrow} {abs(v):.1f}%</span>"


def render(ctx):
    daily, hourly = ctx["daily"], ctx["hourly"]
    hqs = ctx["hqs"]
    if not hqs:
        st.info("표시할 본부 데이터가 없습니다.")
        return
    fmt = ctx["format_hq_name"]

    # ---------------- 조회 조건
    c1, c2 = st.columns([1, 2])
    with c1:
        hq = st.selectbox("🏢 본부 선택", hqs, format_func=fmt, key="hqd_hq")
    with c2:
        mode = st.radio("조회 방식", ["월별 조회", "기간별 조회"], horizontal=True, key="hqd_mode")
    hq_disp = fmt(hq)
    n_month = ctx["hq_last"].get(hq, 0)

    today = date.today()
    hq_days = daily[daily["본부명"] == hq]["날짜"]
    last_day = hq_days.max() if len(hq_days) else today
    first_day = hq_days.min() if len(hq_days) else today

    if mode == "월별 조회":
        months = sorted({(d.year, d.month) for d in hq_days}, reverse=True) or [(today.year, today.month)]
        ym = st.selectbox("조회 월", months, format_func=lambda t: f"{t[0]}년 {t[1]}월", key="hqd_month")
        start, end = month_range(*ym)
        py, pm = prev_month(*ym)
        cmp_start, cmp_end = month_range(py, pm)
        cmp_label = f"전월동일({pm}월)"
        period_label = f"{ym[0]}년 {ym[1]}월"
    else:
        default = (max(first_day, last_day - timedelta(days=29)), last_day)
        rng = st.date_input("조회 기간 (시작일 ~ 종료일)", value=default, key="hqd_range")
        if not isinstance(rng, (list, tuple)) or len(rng) != 2:
            st.info("종료일까지 선택해 주세요.")
            return
        start, end = rng
        span = (end - start).days + 1
        cmp_end = start - timedelta(days=1)
        cmp_start = cmp_end - timedelta(days=span - 1)
        cmp_label = f"직전 동일기간({cmp_start.month}/{cmp_start.day}~{cmp_end.month}/{cmp_end.day})"
        period_label = f"{start.year}.{start.month}.{start.day} ~ {end.year}.{end.month}.{end.day} ({span}일)"
    weekly = (end - start).days + 1 > WEEKLY_THRESHOLD

    # ---------------- ① 본부 요약
    per_hq, total, ranking = company_metrics(ctx)
    m = per_hq[hq]
    period_txt = f"2026년 1~{n_month}월" if n_month else "입력 완료된 달 없음"
    st.markdown(f"#### ① {hq_disp} 누계 실적 ({period_txt}, 목표 대비)")
    gcols = st.columns(3)
    specs = [("온실가스", "온실가스 배출량", "tCO2eq"), ("전력", "전력사용량", "kWh"), ("용수", "용수사용량", "ton")]
    for col, (key, title, unit) in zip(gcols, specs):
        x = m[key]
        with col:
            st.plotly_chart(ctx["make_gauge"](x["ytd"], x["target"], title, unit), use_container_width=True, key=f"hqd_g_{key}")
            share = x["ytd"] / total[key]["ytd"] * 100 if total[key]["ytd"] else 0
            st.markdown(
                f"<div style='text-align:center; font-size:15px; margin-top:-10px'>"
                f"{_delta_html('전년 동기', x['yoy'])} &nbsp;|&nbsp; {_delta_html(f'{n_month}월 전월비', x['mom'])}"
                f"<br><span style='color:{KPETRO_GRAY}'>전사 대비 비중 <b>{share:.1f}%</b></span></div>",
                unsafe_allow_html=True)
    st.caption(f"※ 누계 기준 월 = 이 본부의 필수 항목 입력이 끝난 가장 최근 달({n_month}월). 전년 동기 = 2025년 같은 기간.")

    st.markdown("---")

    # ---------------- ② 일별 전력
    st.markdown(f"#### ② 일별 전력 현황 · {period_label}")
    has_peak = daily["최대수요(kW)"].notna().any()
    metric_opts = ["사용량(kWh)", "최대수요(kW)"] if has_peak else ["사용량(kWh)"]
    metric_sel = st.radio("표시 항목", metric_opts, horizontal=True, key="hqd_metric")
    metric, metric_label, unit = ("최대수요(kW)", "최대수요", "kW") if metric_sel.startswith("최대") else ("전력사용량", "전력사용량", "kWh")

    f = build_daily_frame(daily, hq, start, end, cmp_start, cmp_end, metric)
    stats = daily_stats(f)
    if stats["days"] == 0:
        st.info(f"{period_label}에 한전 자동 수집 데이터가 없습니다. (수집 시작: 2026-10-30)")
    else:
        k1, k2, k3, k4 = st.columns(4)
        if unit == "kWh":
            k1.metric("기간 합계", f"{stats['sum']:,.0f} kWh", f"{stats['days']}일 집계", delta_color="off")
        else:
            mx = stats["max_row"]
            k1.metric("기간 최대수요", f"{mx['값']:,.0f} kW", f"{mx['날짜'].month}/{mx['날짜'].day}", delta_color="off")
        k2.metric("평일 평균", f"{stats['weekday_avg']:,.0f} {unit}" if stats["weekday_avg"] else "-")
        k3.metric("주말 평균", f"{stats['weekend_avg']:,.0f} {unit}" if stats["weekend_avg"] else "-")
        if stats["has_cmp"] and stats["cmp_sum"] > 0:
            ch = _pct(stats["cmp_cur_sum"], stats["cmp_sum"])
            k4.metric(f"비교 기간 대비", f"{ch:+.1f}%", "같은 날짜끼리 비교", delta_color="off")
        else:
            k4.metric("비교 기간 대비", "-", "비교 데이터 없음", delta_color="off")
        st.plotly_chart(daily_chart(f, metric_label, unit, cmp_label, weekly), use_container_width=True, key="hqd_daily")
        legend = (f"🟩 평일 &nbsp; ⬜ 주말 &nbsp; 🟥 급증일(같은 요일 유형 중앙값의 {SPIKE_RATIO:.1f}배 초과) &nbsp; 🟧 {cmp_label}"
                  if not weekly else f"조회 기간이 {WEEKLY_THRESHOLD}일을 넘어 주 단위 합계로 표시합니다.")
        st.caption(legend)

    # ---------------- ③ 시간대별
    st.markdown("#### ③ 시간대별 사용 패턴")
    prof = hourly_profile(hourly, hq, start, end)
    if prof is None:
        st.info("이 기간의 시간대별 데이터가 아직 없습니다. (한전 수집기가 시간대별 값을 쌓기 시작하면 자동으로 표시됩니다)")
    else:
        h1, h2 = st.columns([3, 1])
        with h1:
            st.plotly_chart(hourly_chart(prof), use_container_width=True, key="hqd_hourly")
        with h2:
            r = prof["base_ratio"]
            st.metric("야간 대기전력 비율", f"{r:.0f}%" if r is not None else "-",
                      help="평일 밤(22~06시) 시간당 평균 사용량 ÷ 평일 낮(09~18시) 시간당 평균 사용량")
            if r is not None:
                judge = ("🟢 양호" if r < 30 else "🟡 보통" if r < 50 else "🔴 점검 필요")
                st.markdown(
                    f"<div style='font-size:13px; color:#555; background:#F5F5F5; padding:8px 10px; border-radius:6px; margin:-6px 0 12px'>"
                    f"사람이 없는 <b>밤</b>에도 <b>낮</b> 전력의 <b>{r:.0f}%</b>를 쓰고 있다는 뜻입니다. "
                    f"낮을수록 퇴근 후 전력 관리가 잘 되는 것입니다.<br>"
                    f"<b>판정: {judge}</b> <span style='color:#888'>(30% 미만 양호 · 50% 이상 점검)</span></div>",
                    unsafe_allow_html=True)
            if prof["weekday"] is not None:
                pk = prof["weekday"].idxmax()
                st.metric("평일 최대 사용 시간대", pk, f"{prof['weekday'].max():,.0f} kWh/시간", delta_color="off")
            st.caption(f"{prof['days']}일 평균")

    # ---------------- ④ 자동 분석 코멘트
    st.markdown("#### ④ 자동 분석 코멘트")
    comments = build_comments(hq_disp, m, total, ranking, hq, n_month,
                              stats if stats["days"] else None, prof, period_label, metric_label)
    box = "".join(f"<div style='margin:6px 0; font-size:16px'>{i} {t}</div>" for i, t in comments)
    st.markdown(f"<div style='background:#F4F8EE; border-left:5px solid {KPETRO_GREEN}; padding:14px 18px; border-radius:6px'>{box}</div>",
                unsafe_allow_html=True)
    st.caption("※ 시트 데이터를 코드가 직접 계산해 정해진 기준으로 작성한 분석입니다.")

    # ---------------- ⑤ PDF
    st.markdown("<br>", unsafe_allow_html=True)
    try:
        pdf = make_pdf(hq_disp, n_month, m, f, metric_label, unit, period_label, cmp_label, weekly, prof, comments)
        fname = f"{hq_disp}_에너지자원_사용현황_{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}.pdf"
        st.download_button(f"📄 {hq_disp} 에너지·자원 사용현황 조회 (PDF 출력)", data=pdf, file_name=fname,
                           mime="application/pdf", use_container_width=True, type="primary", key="hqd_pdf")
    except Exception as e:
        st.warning(f"PDF를 만드는 중 문제가 생겼습니다: {e}")
