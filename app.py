import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta, timezone
import os
import base64
import hq_detail

# -----------------------------------------------------------------------------
# 1. 페이지 기본 설정 및 구글 API 클라이언트 연결
# -----------------------------------------------------------------------------
st.set_page_config(page_title="K-REMS 통합 모니터링", page_icon="🏢", layout="wide")

# K-PETRO CI Official Colors
KPETRO_GREEN = "#8CC63F"
KPETRO_GRAY = "#54585A"

def get_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    return gspread.authorize(creds)

SHEET_ID = "1Ky6Brrh5pWXuDvAXV36SSQ2MBAjtxr__UbY8fU3viBY"

def get_image_base64(filenames):
    for fname in filenames:
        if os.path.exists(fname):
            with open(fname, "rb") as f:
                return base64.b64encode(f.read()).decode()
    return None

# 본부명 뒤에 자동으로 '본부'를 붙여주는 스마트 포맷팅 함수
def format_hq_name(name):
    if name in ['미래기술연구소', '본사·수도권남부']:
        return name
    if not name.endswith('본부'):
        return name + '본부'
    return name

# -----------------------------------------------------------------------------
# 세션 상태 초기화 (탭 전환용)
# -----------------------------------------------------------------------------
if 'view' not in st.session_state:
    st.session_state.view = "main"   # main / forecast / ranking

# -----------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리
# -----------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_data():
    client = get_client()
    doc = client.open_by_key(SHEET_ID)
    
    df_actual = pd.DataFrame(doc.worksheet("월간실적").get_all_records())
    df_2025 = pd.DataFrame(doc.worksheet("2025실적").get_all_records())
    df_target = pd.DataFrame(doc.worksheet("목표치관리").get_all_records())
    
    exclude_cols = ['연도', '월', '구분', '날짜', '지사', '본부', '지역', '사업장', '본부명', '입력시간'] 
    for df in [df_actual, df_2025, df_target]:
        for col in df.columns:
            if col not in exclude_cols:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                
    return df_actual, df_2025, df_target

# 한전 자동 수집기(kepco_sync.py)가 쌓는 일별·시간대별 전력 (본부별 상세분석 탭용)
@st.cache_data(ttl=600)
def load_power_data():
    doc = get_client().open_by_key(SHEET_ID)
    out = []
    for title in ["일간전력", "시간별전력"]:
        try:
            out.append(pd.DataFrame(doc.worksheet(title).get_all_records()))
        except Exception:
            out.append(pd.DataFrame())  # 탭이 아직 없으면 빈 표
    return hq_detail.prepare_daily(out[0]), hq_detail.prepare_hourly(out[1])

try:
    df_actual, df_2025, df_target = load_data()
except Exception as e:
    st.error(f"데이터 로드 중 에러가 발생했습니다: {e}")
    st.stop()

# 대시보드 기준 연도 (해가 바뀌면 이 숫자와 '2025실적' 탭 등을 함께 바꿔주세요)
REPORT_YEAR = 2026
# 한전 자동 수집이 다음 해 실적 행(예: 2027년 1월)을 미리 만들기 때문에 올해 행만 사용
# (연도 칸이 비어 있는 기존 행은 올해 행으로 간주)
if '연도' in df_actual.columns:
    _years = pd.to_numeric(df_actual['연도'].astype(str).str.strip(), errors='coerce')
    df_actual = df_actual[_years.isna() | (_years == REPORT_YEAR)].copy()

possible_cols = ['본부명', '본부', '지사', '사업장', '구분', '지역']
group_col = next((c for c in possible_cols if c in df_actual.columns), None)

target_order = ['본사·수도권남부', '미래기술연구소', '수도권북부', '대전세종충남', '충북', '전남광주', '전북', '부산울산경남', '대구경북', '강원', '제주']

if group_col and not df_actual[group_col].dropna().empty:
    raw_hqs = [x for x in df_actual[group_col].unique() if str(x).strip() != '']
    hq_list = sorted(raw_hqs, key=lambda x: target_order.index(x) if x in target_order else 999)
else:
    hq_list = target_order

# 오늘(한국시간)이 속한 달을 기본 선택값으로 사용 (기준 연도가 지난 뒤에는 12월)
_now_kst = datetime.now(timezone(timedelta(hours=9)))
CURRENT_MONTH_INDEX = _now_kst.month - 1 if _now_kst.year == REPORT_YEAR else (11 if _now_kst.year > REPORT_YEAR else 0)

# -----------------------------------------------------------------------------
# 3. 사이드바 (실적 직접 입력 제어판 & 시뮬레이터 & 탭 전환 버튼)
# -----------------------------------------------------------------------------
with st.sidebar.expander("⚙️ 관리자 제어판 (본부별 실적입력)", expanded=True):
    st.markdown("웹에서 실적을 입력하면 구글 DB에 실시간 연동됩니다.")
    with st.form("data_input_form", clear_on_submit=True):
        input_month = st.selectbox("입력 대상 월", [f"{i}월" for i in range(1, 13)], index=CURRENT_MONTH_INDEX)
        input_hq = st.selectbox("본부(지사) 선택", hq_list)
        
        input_elec = st.number_input("전력사용량 (kWh)", min_value=0.0, step=100.0)
        input_gas = st.number_input("도시가스사용량 (m3)", min_value=0.0, step=10.0)
        input_kero = st.number_input("실내등유사용량 (L)", min_value=0.0, step=10.0)
        input_water = st.number_input("용수사용량 (ton)", min_value=0.0, step=10.0)
        
        submitted = st.form_submit_button("실적 DB 등록 (구글시트 연동)")
        
        if submitted:
            try:
                client = get_client()
                doc = client.open_by_key(SHEET_ID)
                ws = doc.worksheet("월간실적")
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                row_data = [REPORT_YEAR, input_month, input_hq, input_elec, input_water, input_gas, input_kero, now_str]
                ws.append_row(row_data)
                
                st.success(f"{input_hq} {input_month} 실적이 성공적으로 전송되었습니다!")
                st.cache_data.clear() 
                st.rerun() 
            except Exception as e:
                st.error(f"구글 시트 연동 중 에러 발생: {e}")

for _df in (df_actual, df_2025):
    if not _df.empty and '월' in _df.columns:
        _df['월_num'] = pd.to_numeric(_df['월'].astype(str).str.replace('월', '').str.strip(), errors='coerce').fillna(0).astype(int)

# -----------------------------------------------------------------------------
# 본부별 실적 입력 여부 판정 (입력 현황 배너와 '최신 입력월' 계산에 함께 사용)
#  - 필수: 전력·용수, 그리고 작년 같은 달에 가스를 쓴 본부는 가스까지
#  - 등유는 구매 시점이 불규칙해 참고로만 본다
# -----------------------------------------------------------------------------
INPUT_ITEMS = [("전력", "전력사용량"), ("용수", "용수사용량"), ("가스", "도시가스사용량"), ("등유", "실내등유사용량")]

def input_status(hq, month_num):
    has_cols = bool(group_col) and '월_num' in df_actual.columns
    sub = df_actual[(df_actual[group_col].astype(str) == hq) & (df_actual['월_num'] == month_num)] if has_cols else pd.DataFrame()
    got = {name: (float(sub[col].sum()) > 0 if col in sub.columns else False) for name, col in INPUT_ITEMS}
    prev = df_2025[(df_2025[group_col].astype(str) == hq) & (df_2025['월_num'] == month_num)] \
        if (group_col and not df_2025.empty and group_col in df_2025.columns and '월_num' in df_2025.columns) else pd.DataFrame()
    gas_required = '도시가스사용량' in prev.columns and float(prev['도시가스사용량'].sum()) > 0
    required = ["전력", "용수"] + (["가스"] if gas_required else [])
    if all(got[k] for k in required):
        state = "완료"
    elif any(got.values()):
        state = "일부"
    else:
        state = "미입력"
    times = [str(t) for t in sub.get('입력시간', pd.Series(dtype=str)).tolist() if str(t).strip() not in ("", "0", "nan")]
    return {"hq": hq, "got": got, "required": required, "state": state, "last": max(times) if times else ""}

# 본부별 최신 입력월 = 전력 실적이 들어온 가장 최근 달 (누계·예측의 기준)
#  - 용수·가스는 고지서가 늦거나 격월로 오는 경우가 많아 기준에서 제외
#  - 이번 달(진행 중)은 한전 자동 수집으로 전력이 일부만 채워져 있으므로 제외
_month_cap = CURRENT_MONTH_INDEX if _now_kst.year == REPORT_YEAR else 12
hq_last = {}
for _hq in hq_list:
    elec_months = [m for m in range(1, _month_cap + 1) if input_status(_hq, m)["got"]["전력"]]
    hq_last[_hq] = max(elec_months) if elec_months else 0

if '월_num' in df_actual.columns and group_col:
    _limit = df_actual[group_col].astype(str).map(hq_last).fillna(0)
    df_filtered = df_actual[(df_actual['월_num'] > 0) & (df_actual['월_num'] <= _limit)]
else:
    df_filtered = df_actual

# 사이드바: 화면 전환 버튼 (연간 달성 예측 / 온실가스 절감률 순위)
if 'view' not in st.session_state:
    st.session_state.view = "main"

def _view_button(label, view, key):
    active = st.session_state.view == view
    if st.sidebar.button("🔙 메인 대시보드로 돌아가기" if active else label, use_container_width=True, key=key,
                         type="primary" if active else "secondary"):
        st.session_state.view = "main" if active else view
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("<div style='font-size: 19px; font-weight: bold; letter-spacing: -1px; margin-bottom: 10px;'>📈 연간 달성 예측</div>", unsafe_allow_html=True)
_view_button("📈 전사 연간 달성 예측 확인", "forecast", "forecast_toggle_button")

st.sidebar.markdown("---")
st.sidebar.markdown("<div style='font-size: 19px; font-weight: bold; letter-spacing: -1px; margin-bottom: 10px;'>🏆 온실가스 절감률 순위 표시</div>", unsafe_allow_html=True)
_view_button("🏆 온실가스 절감률 순위 확인", "ranking", "rank_toggle_button")

st.sidebar.markdown("---")
with st.sidebar.expander("📅 본부별 집계 기준월"):
    st.markdown("누계·예측은 본부마다 **전력 실적이 입력된 가장 최근 달**까지를 기준으로 계산합니다. (진행 중인 이번 달 제외)")
    st.markdown("\n".join(f"- {format_hq_name(h)}: **{hq_last[h]}월**" if hq_last[h] else f"- {format_hq_name(h)}: 입력 완료 월 없음" for h in hq_list))

# -----------------------------------------------------------------------------
# 4. 연산 (총괄 및 본부별 실적/랭킹 계산)
# -----------------------------------------------------------------------------
total_elec = float(df_filtered['전력사용량'].sum()) if '전력사용량' in df_filtered.columns else 0.0
total_gas = float(df_filtered['도시가스사용량'].sum()) if '도시가스사용량' in df_filtered.columns else 0.0
total_kero = float(df_filtered['실내등유사용량'].sum()) if '실내등유사용량' in df_filtered.columns else 0.0
total_water = float(df_filtered['용수사용량'].sum()) if '용수사용량' in df_filtered.columns else 0.0

total_ghg = (total_elec * 0.0004594106) + (total_gas * 0.002187587) + (total_kero * 0.0024652936)

target_elec = float(df_target['전력목표_연간'].sum()) if '전력목표_연간' in df_target.columns else 0.0
target_ghg = float(df_target['온실가스목표_연간'].sum()) if '온실가스목표_연간' in df_target.columns else 0.0
target_water = float(df_target['용수목표_연간'].sum()) if '용수목표_연간' in df_target.columns else 0.0

if target_elec == 0: target_elec = 100000.0
if target_ghg == 0: target_ghg = 5000.0
if target_water == 0: target_water = 10000.0

ghg_reduction = target_ghg - total_ghg
ghg_reduction_display = ghg_reduction if ghg_reduction > 0 else 0
pine_trees = int(ghg_reduction_display * 6.6)

if group_col:
    df_grouped = df_filtered.groupby(group_col).sum(numeric_only=True)
    df_grouped = df_grouped.reindex(df_grouped.index.union(pd.Index(hq_list)), fill_value=0).rename_axis(group_col).reset_index()
    df_target_grouped = df_target.groupby(group_col).sum(numeric_only=True).reset_index() if group_col in df_target.columns else pd.DataFrame()
    raw_valid_hqs = [hq for hq in df_grouped[group_col].astype(str).tolist() if hq.strip() != '0' and hq.strip() != '']
    valid_hqs = sorted(raw_valid_hqs, key=lambda x: target_order.index(x) if x in target_order else 999)
else:
    df_grouped, df_target_grouped, valid_hqs = pd.DataFrame(), pd.DataFrame(), []

rank_data = []
if valid_hqs:
    for hq_name in valid_hqs:
        row = df_grouped[df_grouped[group_col].astype(str) == hq_name].iloc[0]
        r_elec = float(row.get('전력사용량', 0))
        r_gas = float(row.get('도시가스사용량', 0))
        r_kero = float(row.get('실내등유사용량', 0))
        r_ghg = (r_elec * 0.0004594106) + (r_gas * 0.002187587) + (r_kero * 0.0024652936)
        
        if not df_target_grouped.empty and hq_name in df_target_grouped[group_col].values:
            t_row = df_target_grouped[df_target_grouped[group_col] == hq_name].iloc[0]
            t_ghg = float(t_row.get('온실가스목표_연간', 0))
            if t_ghg == 0: t_ghg = r_ghg * 1.5
        else:
            t_ghg = r_ghg * 1.5
            
        achievement_rate = (r_ghg / t_ghg * 100) if t_ghg > 0 else 0
        
        disp_name = format_hq_name(hq_name)
        rank_data.append({
            "본부명": disp_name, 
            "현재배출량(tCO2eq)": round(r_ghg, 1),
            "목표량(tCO2eq)": round(t_ghg, 1),
            "사용률(%)": round(achievement_rate, 1)
        })
    rank_data = sorted(rank_data, key=lambda x: x["사용률(%)"])

# -----------------------------------------------------------------------------
# 5. 커스텀 반원 게이지 차트 
# -----------------------------------------------------------------------------
def make_gauge(val, target, title, unit, is_forecast=False):
    if target <= 0: target = val * 1.2 if val > 0 else 100
    remaining = target - val
    achievement_rate = (val / target * 100) if target > 0 else 0
    
    format_str = ",.1f" if unit == 'tCO2eq' else ",.0f"
    val_str = format(val, format_str)
    target_str = format(target, format_str)
    rem_str = format(abs(remaining), format_str)
    
    val_label = "예상실적" if is_forecast else "현재실적"
    
    if remaining >= 0:
        center_text = f"<span style='font-size:36px; font-weight:900; color:{KPETRO_GREEN};'>{achievement_rate:.1f}%</span><br><span style='font-size:18px; font-weight:bold; color:{KPETRO_GRAY};'>△ {rem_str} {unit}</span>"
    else:
        center_text = f"<span style='font-size:36px; font-weight:900; color:#FF0000;'>{achievement_rate:.1f}%</span><br><span style='font-size:18px; font-weight:bold; color:#FF0000;'>▼ 초과 {rem_str} {unit}</span>"

    fig = go.Figure(go.Indicator(
        mode = "gauge",
        value = val,
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': f"<b>{title}</b>", 'font': {'size': 26, 'color': KPETRO_GRAY}},
        gauge = {
            'axis': {'range': [0, target], 'tickwidth': 1, 'tickcolor': "darkblue"},
            'bar': {'color': "#1E90FF"},
            'bgcolor': "#E0E0E0",
            'steps': [
                {'range': [target * 0.495, target * 0.505], 'color': "#FFC107"},
                {'range': [target * 0.895, target * 0.905], 'color': "#FF3B30"} 
            ]
        }
    ))
    
    fig.update_layout(
        height=340, 
        margin=dict(l=30, r=30, t=70, b=60),
        annotations=[
            dict(x=0.5, y=0.15, xref='paper', yref='paper', text=center_text, showarrow=False, align='center'),
            dict(x=0.15, y=-0.1, xref='paper', yref='paper', text=f"{val_label}: <b>{val_str}</b>", showarrow=False, font=dict(size=16, color=KPETRO_GRAY), xanchor='center'),
            dict(x=0.85, y=-0.1, xref='paper', yref='paper', text=f"목표치: <b>{target_str}</b>", showarrow=False, font=dict(size=16, color='red'), xanchor='center')
        ]
    )
    return fig

# -----------------------------------------------------------------------------
# 6. 메인 화면 UI (타이틀)
# -----------------------------------------------------------------------------
left_logo_b64 = get_image_base64(["한글상하조합_2.jpg", "한글상하조합_2.png", "한글상하조합.jpg", "한글상하조합.png"])
yubanjang_b64 = get_image_base64(["유반장.jpg", "유반장.png"])

if left_logo_b64:
    left_html = f'<img src="data:image/jpeg;base64,{left_logo_b64}" style="height: 96px; object-fit: contain;">'
else:
    left_html = f"<span style='color: #aaa; font-size: 14px;'>[로고 이미지 깃허브 업로드 필요]</span>"

if yubanjang_b64:
    yubanjang_html = f'<img src="data:image/jpeg;base64,{yubanjang_b64}" style="height: 96px; object-fit: contain;">'
else:
    yubanjang_html = ""

title_html = f"<div style='display: flex; justify-content: space-between; align-items: center; margin-top: 10px; margin-bottom: 20px;'><div style='flex: 1; display: flex; justify-content: flex-start; align-items: center; padding-left: 10px;'>{left_html}</div><div style='flex: 2; display: flex; justify-content: center; align-items: center;'><span style='font-size: 48px; font-weight: 900; color: {KPETRO_GRAY}; letter-spacing: -1.5px;'>K-REMS 통합 모니터링</span></div><div style='flex: 1; display: flex; justify-content: flex-end; align-items: center; padding-right: 10px;'>{yubanjang_html}</div></div>"
st.markdown(title_html, unsafe_allow_html=True)

def _upto(hq):
    """게이지 제목 옆에 붙는 집계 기준월 표시"""
    n = hq_last.get(hq, 0)
    return f" (~{n}월)" if n else " (입력 없음)"

# -------------------------------------------------------------------------
# 연간 달성 예측 (2026-10 개선: 2025·2026 실적 백테스트로 선정)
#  - 전력·도시가스: 올해 누계 + 작년 남은 달 x 증감률
#      증감률 = (누계 증감률 + 최근 3개월 증감률) / 2, 0.7~1.3배로 제한
#  - 실내등유: 구매 시점이 불규칙 -> 작년 남은 달 사용량 그대로
#  - 온실가스: 전력·가스·등유 예측값을 각각 배출계수로 환산해 합산
#  - 용수: (작년 기반 증감률 예측 + 올해 월평균 x 남은 달) / 2
#          고지서가 늦게 오는 달(올해 0, 작년 값 있음)은 미입력으로 보고 예측 대상에 포함
# -------------------------------------------------------------------------
EF = {'전력사용량': 0.0004594106, '도시가스사용량': 0.002187587, '실내등유사용량': 0.0024652936}

def _monthly(df, hq, col):
    if df is None or df.empty or col not in df.columns or group_col not in df.columns or '월_num' not in df.columns:
        return {}
    d = df[df[group_col].astype(str) == hq]
    return {int(k): float(v) for k, v in d.groupby('월_num')[col].sum().items()}

def _ratio(cur, prev, months):
    def r(ms):
        p = sum(prev.get(m, 0) for m in ms)
        return (sum(cur.get(m, 0) for m in ms) / p) if p > 0 else None
    a, b = r(months), r(months[-3:])
    if a is None and b is None:
        return 1.0
    v = a if b is None else b if a is None else 0.5 * a + 0.5 * b
    return min(max(v, 0.7), 1.3)

def _forecast_item(hq, col):
    cur, prev = _monthly(df_actual, hq, col), _monthly(df_2025, hq, col)
    n = hq_last.get(hq, 0)   # 본부별 최신 입력월
    upto = list(range(1, n + 1))
    if col == '용수사용량':
        done = [m for m in upto if cur.get(m, 0) > 0 or prev.get(m, 0) == 0]
    else:
        done = upto
    rest = [m for m in range(1, 13) if m not in done]
    ytd = sum(cur.get(m, 0) for m in done)
    if not done:
        return sum(prev.values())   # 입력 완료된 달이 없으면 작년 실적 수준으로 가정
    has_prev = sum(prev.values()) > 0
    if col == '실내등유사용량':
        return ytd + sum(prev.get(m, 0) for m in rest)
    by_avg = ytd + ytd / len(done) * len(rest)
    if not has_prev:
        return by_avg
    by_ratio = ytd + sum(prev.get(m, 0) for m in rest) * _ratio(cur, prev, done)
    if col == '용수사용량':
        return 0.5 * by_ratio + 0.5 * by_avg
    return by_ratio

def forecast_hq(hq):
    e = _forecast_item(hq, '전력사용량')
    g = _forecast_item(hq, '도시가스사용량')
    k = _forecast_item(hq, '실내등유사용량')
    w = _forecast_item(hq, '용수사용량')
    ghg = e * EF['전력사용량'] + g * EF['도시가스사용량'] + k * EF['실내등유사용량']
    return e, ghg, w

def render_forecast_grid(metric_type, tab_prefix):
    if not valid_hqs:
        st.info("표시할 본부별 데이터가 없습니다.")
        return

    for i in range(0, len(valid_hqs), 3):
        cols = st.columns(3)
        for j in range(3):
            idx = i + j
            if idx < len(valid_hqs):
                hq_name = valid_hqs[idx]
                disp_name = format_hq_name(hq_name)
                row = df_grouped[df_grouped[group_col].astype(str) == hq_name].iloc[0]

                curr_elec = float(row.get('전력사용량', 0))
                curr_gas = float(row.get('도시가스사용량', 0))
                curr_kero = float(row.get('실내등유사용량', 0))
                curr_water = float(row.get('용수사용량', 0))
                curr_ghg = (curr_elec * 0.0004594106) + (curr_gas * 0.002187587) + (curr_kero * 0.0024652936)

                pred_elec, pred_ghg, pred_water = forecast_hq(hq_name)

                if not df_target_grouped.empty and hq_name in df_target_grouped[group_col].values:
                    t_row = df_target_grouped[df_target_grouped[group_col] == hq_name].iloc[0]
                    t_elec = float(t_row.get('전력목표_연간', 0))
                    t_ghg = float(t_row.get('온실가스목표_연간', 0))
                    t_water = float(t_row.get('용수목표_연간', 0))

                    if t_elec == 0: t_elec = curr_elec * 1.2
                    if t_ghg == 0: t_ghg = curr_ghg * 1.2
                    if t_water == 0: t_water = curr_water * 1.2
                else:
                    t_elec, t_ghg, t_water = curr_elec * 1.2, curr_ghg * 1.2, curr_water * 1.2

                with cols[j]:
                    if metric_type == "GHG":
                        st.plotly_chart(make_gauge(pred_ghg, t_ghg, f"{disp_name}{_upto(hq_name)}", "tCO2eq", is_forecast=True), use_container_width=True, key=f"{tab_prefix}_ghg_{hq_name}")
                    elif metric_type == "ELEC":
                        st.plotly_chart(make_gauge(pred_elec, t_elec, f"{disp_name}{_upto(hq_name)}", "kWh", is_forecast=True), use_container_width=True, key=f"{tab_prefix}_elec_{hq_name}")
                    elif metric_type == "WATER":
                        st.plotly_chart(make_gauge(pred_water, t_water, f"{disp_name}{_upto(hq_name)}", "ton", is_forecast=True), use_container_width=True, key=f"{tab_prefix}_water_{hq_name}")


# =============================================================================
# 7. 화면 분기 (순위표 탭 vs 메인 대시보드 탭)
# =============================================================================
if st.session_state.view == "ranking":
    # ------------------ [순위표 탭 화면] ------------------
    st.markdown(f"### 🥇 2026년 온실가스 절감 우수 본부 TOP 3 (누계 · 본부별 최신 입력월 기준)")
    if len(rank_data) >= 3:
        medals = [("🥇 1위", "#FFD700", rank_data[0]), ("🥈 2위", "#C0C0C0", rank_data[1]), ("🥉 3위", "#CD7F32", rank_data[2])]
        r_cols = st.columns(3)
        for idx, (medal, color, data) in enumerate(medals):
            with r_cols[idx]:
                st.markdown(f"""
                <div style='background-color: {color}20; border: 2px solid {color}; border-radius: 10px; padding: 20px; text-align: center; margin-bottom: 15px;'>
                    <h2 style='margin: 0; color: {color}; text-shadow: 1px 1px 2px rgba(0,0,0,0.2);'>{medal}</h2>
                    <h3 style='margin: 10px 0; color: {KPETRO_GRAY};'>{data['본부명']}</h3>
                    <p style='margin: 0; font-size: 18px; font-weight: bold;'>목표 대비 사용률: <span style='color: #1E90FF;'>{data['사용률(%)']}%</span></p>
                </div>
                """, unsafe_allow_html=True)
                
                st.plotly_chart(make_gauge(data['현재배출량(tCO2eq)'], data['목표량(tCO2eq)'], f"{data['본부명']} 온실가스", "tCO2eq"), use_container_width=True, key=f"rank_gauge_{idx}")
                
    st.markdown("<br>", unsafe_allow_html=True)
    
    with st.expander("📊 전 본부 절감률 순위확인 (클릭하여 펼치기)"):
        df_rank = pd.DataFrame(rank_data)
        df_rank.index = df_rank.index + 1 
        st.dataframe(df_rank, use_container_width=True)

elif st.session_state.view == "forecast":
    # ------------------ [연간 달성 예측 화면] ------------------
    st.subheader("📈 K-PETRO 전사 연간 달성 예측 (2026년 연말 예상치)")
    preds = {hq: forecast_hq(hq) for hq in valid_hqs}
    pred_elec_all = sum(p[0] for p in preds.values())
    pred_ghg_all = sum(p[1] for p in preds.values())
    pred_water_all = sum(p[2] for p in preds.values())

    fc1, fc2, fc3 = st.columns(3)
    with fc1: st.plotly_chart(make_gauge(pred_ghg_all, target_ghg, "전사 온실가스 배출량", "tCO2eq", is_forecast=True), use_container_width=True, key="fc_ghg")
    with fc2: st.plotly_chart(make_gauge(pred_elec_all, target_elec, "전사 전력사용량", "kWh", is_forecast=True), use_container_width=True, key="fc_elec")
    with fc3: st.plotly_chart(make_gauge(pred_water_all, target_water, "전사 용수사용량", "ton", is_forecast=True), use_container_width=True, key="fc_water")

    over = [format_hq_name(hq) for hq, p in preds.items()
            if not df_target_grouped.empty and hq in df_target_grouped[group_col].values
            and float(df_target_grouped[df_target_grouped[group_col] == hq].iloc[0].get('온실가스목표_연간', 0)) > 0
            and p[1] > float(df_target_grouped[df_target_grouped[group_col] == hq].iloc[0].get('온실가스목표_연간', 0))]
    gap = target_ghg - pred_ghg_all
    if gap >= 0:
        msg, bg, fg = f"✅ 현재 추세라면 연말 온실가스 배출량은 목표보다 <b>{gap:,.1f} tCO2eq 적은</b> {pred_ghg_all:,.1f} tCO2eq로 <b>목표 달성이 예상</b>됩니다.", "#E8F5E9", "#2E7D32"
    else:
        msg, bg, fg = f"⚠️ 현재 추세라면 연말 온실가스 배출량이 목표를 <b>{-gap:,.1f} tCO2eq 초과</b>할 것으로 예상됩니다.", "#FFF4E5", "#B45309"
    over_txt = ("<br><span style='font-size:15px'>목표 초과 예상 본부: " + ", ".join(over) + "</span>") if over else ""
    st.markdown(f"<div style='background:{bg}; padding:15px; border-radius:8px; text-align:center; color:{fg}; font-size:18px; margin-bottom:10px;'>{msg}{over_txt}</div>", unsafe_allow_html=True)
    st.caption("※ 전사 목표 = 전 본부 연간 목표 합계, 전사 예측 = 전 본부 연말 예측치 합계. 예측은 본부마다 최신 입력월까지의 실적에 "
               "작년 남은 달 사용량 × 올해 증감 추세를 더해 계산합니다.")

    st.markdown("---")
    st.subheader("🏢 본부별 · 자원별 연간 달성 예측")
    f_tab1, f_tab2, f_tab3 = st.tabs(["☁️ 온실가스 예측치", "⚡ 전력사용량 예측치", "💧 용수사용량 예측치"])
    with f_tab1:
        render_forecast_grid("GHG", "forecast_ghg")
    with f_tab2:
        render_forecast_grid("ELEC", "forecast_elec")
    with f_tab3:
        render_forecast_grid("WATER", "forecast_water")

else:
    # ------------------ [메인 대시보드 화면] ------------------
    st.subheader(f"💡 K-PETRO 통합 실적 (2026년 누계 · 본부별 최신 입력월 기준)")

    g_col1, g_col2, g_col3 = st.columns(3)
    with g_col1: st.plotly_chart(make_gauge(total_ghg, target_ghg, "총괄 온실가스 배출량", "tCO2eq"), use_container_width=True, key="top_ghg")
    with g_col2: st.plotly_chart(make_gauge(total_elec, target_elec, "총괄 전력사용량", "kWh"), use_container_width=True, key="top_elec")
    with g_col3: st.plotly_chart(make_gauge(total_water, target_water, "총괄 용수사용량", "ton"), use_container_width=True, key="top_water")

    st.markdown(f"""
    <div style='background-color: #E8F5E9; padding: 15px; border-radius: 8px; text-align: center; color: #2E7D32; font-size: 18px; font-weight: bold; margin-bottom: 20px;'>
        🌱 2026년 누계 기준 목표치 대비 온실가스 감축량 : {ghg_reduction_display:,.1f} tCO2eq (소나무 {pine_trees:,.0f}그루 식재효과)
    </div>
    """, unsafe_allow_html=True)

    # ------------------ 본부별 실적 입력 현황 배너 ------------------
    def render_input_status(month_num):
        rows = [input_status(hq, month_num) for hq in hq_list]

        n_done = sum(r["state"] == "완료" for r in rows)
        n_part = sum(r["state"] == "일부" for r in rows)
        n_none = sum(r["state"] == "미입력" for r in rows)
        todo = [format_hq_name(r["hq"]) for r in rows if r["state"] != "완료"]
        all_done = not todo
        bg, fg, bd = ("#E8F5E9", "#2E7D32", "#8CC63F") if all_done else ("#FFF4E5", "#B45309", "#F5A623")
        todo_txt = "모든 본부 입력 완료 🎉" if all_done else "미완료: " + ", ".join(todo)
        st.markdown(f"""
        <div style='background:{bg}; border-left:6px solid {bd}; padding:12px 18px; border-radius:8px; margin-bottom:8px;'>
            <span style='font-size:18px; font-weight:bold; color:{fg};'>📋 {month_num}월 실적 입력 현황</span>
            <span style='font-size:16px; margin-left:14px; color:{KPETRO_GRAY};'>
                입력 완료 <b style='color:#2E7D32'>{n_done}</b> · 일부 입력 <b style='color:#B45309'>{n_part}</b> · 미입력 <b style='color:#D32F2F'>{n_none}</b>
                &nbsp;/&nbsp; 전체 {len(rows)}개 본부</span>
            <div style='font-size:15px; color:{fg}; margin-top:4px;'>{todo_txt}</div>
        </div>""", unsafe_allow_html=True)

        with st.expander(f"🔎 {month_num}월 본부별 입력 상세 보기"):
            style = {"완료": ("#E8F5E9", "#2E7D32", "✅ 입력 완료"), "일부": ("#FFF4E5", "#B45309", "🟠 일부 입력"), "미입력": ("#FDECEA", "#D32F2F", "⛔ 미입력")}
            cards = ""
            for r in rows:
                b, c, label = style[r["state"]]
                def _mark(name, ok, req):
                    if ok:
                        return f"<span style='color:#2E7D32'>● {name}</span>"
                    if req:
                        return f"<span style='color:#D32F2F; font-weight:bold'>○ {name}</span>"
                    return f"<span style='color:#BBBBBB'>○ {name}</span>"
                marks = " ".join(_mark(name, ok, name in r["required"]) for name, ok in r["got"].items())
                last = f"<div style='font-size:12px; color:#888; margin-top:4px'>최근 입력 {r['last']}</div>" if r["last"] else ""
                cards += (f"<div style='background:{b}; border:1px solid {c}40; border-radius:8px; padding:10px 12px;'>"
                          f"<div style='font-weight:bold; color:{KPETRO_GRAY}; font-size:15px'>{format_hq_name(r['hq'])}</div>"
                          f"<div style='color:{c}; font-weight:bold; font-size:14px; margin:2px 0 4px'>{label}</div>"
                          f"<div style='font-size:13px'>{marks}</div>{last}</div>")
            st.markdown(f"<div style='display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px;'>{cards}</div>", unsafe_allow_html=True)
            st.caption("※ 입력 완료 기준: 전력·용수 + 작년 같은 달에 가스를 쓴 본부는 가스까지. 등유는 구매 시점이 불규칙해 참고용입니다. "
                       "빨간 ○ = 입력이 필요한 항목, 회색 ○ = 참고 항목. 한전 자동 수집으로 채워진 전력도 입력으로 봅니다.")

    _last_month = CURRENT_MONTH_INDEX if CURRENT_MONTH_INDEX > 0 else 1   # 기본: 지난달 (고지서가 들어와 입력해야 하는 달)
    _sel = st.selectbox("📋 입력 현황 확인 월", [f"{i}월" for i in range(1, 13)], index=_last_month - 1, key="input_status_month")
    render_input_status(int(_sel.replace("월", "")))

    st.markdown("---")

    tab1, tab2, tab3, tab5 = st.tabs(["☁️ 온실가스 배출량", "⚡ 전력사용량", "💧 용수사용량", "🔍 본부별 상세분석"])

    def render_hq_grid(metric_type, tab_prefix):
        if not valid_hqs:
            st.info("표시할 본부별 데이터가 없습니다.")
            return
            
        for i in range(0, len(valid_hqs), 3):
            cols = st.columns(3)
            for j in range(3):
                idx = i + j
                if idx < len(valid_hqs):
                    hq_name = valid_hqs[idx]
                    disp_name = format_hq_name(hq_name) 
                    row = df_grouped[df_grouped[group_col].astype(str) == hq_name].iloc[0]
                    
                    r_elec = float(row.get('전력사용량', 0))
                    r_gas = float(row.get('도시가스사용량', 0))
                    r_kero = float(row.get('실내등유사용량', 0))
                    r_water = float(row.get('용수사용량', 0))
                    r_ghg = (r_elec * 0.0004594106) + (r_gas * 0.002187587) + (r_kero * 0.0024652936)
                    
                    if not df_target_grouped.empty and hq_name in df_target_grouped[group_col].values:
                        t_row = df_target_grouped[df_target_grouped[group_col] == hq_name].iloc[0]
                        t_elec = float(t_row.get('전력목표_연간', 0))
                        t_ghg = float(t_row.get('온실가스목표_연간', 0))
                        t_water = float(t_row.get('용수목표_연간', 0))
                        
                        if t_elec == 0: t_elec = r_elec * 1.5
                        if t_ghg == 0: t_ghg = r_ghg * 1.5
                        if t_water == 0: t_water = r_water * 1.5
                    else:
                        t_elec, t_ghg, t_water = r_elec * 1.5, r_ghg * 1.5, r_water * 1.5

                    with cols[j]:
                        if metric_type == "GHG":
                            st.plotly_chart(make_gauge(r_ghg, t_ghg, f"{disp_name}{_upto(hq_name)}", "tCO2eq"), use_container_width=True, key=f"{tab_prefix}_ghg_{hq_name}")
                        elif metric_type == "ELEC":
                            st.plotly_chart(make_gauge(r_elec, t_elec, f"{disp_name}{_upto(hq_name)}", "kWh"), use_container_width=True, key=f"{tab_prefix}_elec_{hq_name}")
                        elif metric_type == "WATER":
                            st.plotly_chart(make_gauge(r_water, t_water, f"{disp_name}{_upto(hq_name)}", "ton"), use_container_width=True, key=f"{tab_prefix}_water_{hq_name}")

    with tab1:
        st.subheader(f"☁ 본부별 온실가스 배출량 상세 실적 (누계 · 본부별 최신 입력월 기준)")
        render_hq_grid("GHG", "tab1")

    with tab2:
        st.subheader(f"⚡ 본부별 전력사용량 상세 실적 (누계 · 본부별 최신 입력월 기준)")
        render_hq_grid("ELEC", "tab2")

    with tab3:
        st.subheader(f"💧 본부별 용수사용량 상세 실적 (누계 · 본부별 최신 입력월 기준)")
        render_hq_grid("WATER", "tab3")

    with tab5:
        try:
            df_daily, df_hourly = load_power_data()
        except Exception as e:
            st.warning(f"한전 일별 데이터를 불러오지 못했습니다: {e}")
            df_daily, df_hourly = hq_detail.prepare_daily(None), hq_detail.prepare_hourly(None)
        hq_detail.render({
            "df_actual": df_actual, "df_2025": df_2025, "df_target": df_target,
            "group_col": group_col, "hqs": valid_hqs, "hq_last": hq_last,
            "daily": df_daily, "hourly": df_hourly,
            "make_gauge": make_gauge, "format_hq_name": format_hq_name,
        })

st.markdown(f"""
<hr style='border: 1px solid #E0E0E0; margin-top: 50px;'>
<div style='text-align: center; color: #888888; font-size: 14px; padding-bottom: 20px;'>
    &copy; 2026 한국석유관리원 운영지원팀. All rights reserved.
</div>
""", unsafe_allow_html=True)
