import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

# -----------------------------------------------------------------------------
# 1. 페이지 기본 설정 및 구글 API 클라이언트 연결
# -----------------------------------------------------------------------------
st.set_page_config(page_title="K-PETRO BEMS 통합 모니터링", page_icon="📊", layout="wide")

def get_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    return gspread.authorize(creds)

SHEET_ID = "1Ky6Brrh5pWXuDvAXV36SSQ2MBAjtxr__UbY8fU3viBY"

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

try:
    df_actual, df_2025, df_target = load_data()
except Exception as e:
    st.error(f"데이터 로드 중 에러가 발생했습니다: {e}")
    st.stop()

possible_cols = ['본부명', '본부', '지사', '사업장', '구분', '지역']
group_col = next((c for c in possible_cols if c in df_actual.columns), None)

target_order = ['본사·수도권남부', '미래기술연구소', '수도권북부', '대전세종충남', '충북', '전남광주', '전북', '부산울산경남', '대구경북', '강원', '제주']

if group_col and not df_actual[group_col].dropna().empty:
    raw_hqs = [x for x in df_actual[group_col].unique() if str(x).strip() != '']
    hq_list = sorted(raw_hqs, key=lambda x: target_order.index(x) if x in target_order else 999)
else:
    hq_list = target_order

# -----------------------------------------------------------------------------
# 3. 사이드바 (실적 직접 입력 제어판 & 시뮬레이터)
# -----------------------------------------------------------------------------
with st.sidebar.expander("⚙️ 관리자 제어판 (본부별 실적입력)", expanded=True):
    st.markdown("웹에서 실적을 입력하면 구글 DB에 실시간 연동됩니다.")
    with st.form("data_input_form", clear_on_submit=True):
        input_month = st.selectbox("입력 대상 월", [f"{i}월" for i in range(1, 13)])
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
                
                row_data = [2026, input_month, input_hq, input_elec, input_water, input_gas, input_kero, now_str]
                ws.append_row(row_data)
                
                st.success(f"{input_hq} {input_month} 실적이 성공적으로 전송되었습니다!")
                st.cache_data.clear() 
                st.rerun() 
            except Exception as e:
                st.error(f"구글 시트 연동 중 에러 발생: {e}")

st.sidebar.markdown("---")
st.sidebar.header("📈 실적달성 시뮬레이터")
month_list_sim = [f"{i}월" for i in range(1, 13)]
selected_month = st.sidebar.selectbox("현재 집계 완료(월)", month_list_sim, index=8)

sel_month_num = int(selected_month.replace('월', ''))
if '월' in df_actual.columns:
    df_actual['월_num'] = pd.to_numeric(df_actual['월'].astype(str).str.replace('월', '').str.strip(), errors='coerce').fillna(0).astype(int)
    df_filtered = df_actual[(df_actual['월_num'] > 0) & (df_actual['월_num'] <= sel_month_num)]
else:
    df_filtered = df_actual

# -----------------------------------------------------------------------------
# 4. 연산 (정밀 계수 및 목표치 컬럼 완벽 매칭)
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

# -----------------------------------------------------------------------------
# 5. [수정] UI 디테일업이 적용된 커스텀 반원 게이지 차트
# -----------------------------------------------------------------------------
def make_gauge(val, target, title, unit, is_forecast=False):
    if target <= 0: target = val * 1.2 if val > 0 else 100
    remaining = target - val
    achievement_rate = (val / target * 100) if target > 0 else 0
    
    format_str = ",.1f" if unit == 'tCO2eq' else ",.0f"
    val_str = format(val, format_str)
    target_str = format(target, format_str)
    rem_str = format(abs(remaining), format_str)
    
    # 예측 탭일 경우 텍스트를 '예상실적'으로 변경
    val_label = "예상실적" if is_forecast else "현재실적"
    
    # 정중앙 텍스트 구성
    if remaining >= 0:
        center_text = f"<span style='font-size:36px; font-weight:900; color:#1E90FF;'>{achievement_rate:.1f}%</span><br><span style='font-size:18px; font-weight:bold; color:#555555;'>△ {rem_str} {unit}</span>"
    else:
        center_text = f"<span style='font-size:36px; font-weight:900; color:#FF0000;'>{achievement_rate:.1f}%</span><br><span style='font-size:18px; font-weight:bold; color:#FF0000;'>▼ 초과 {rem_str} {unit}</span>"

    fig = go.Figure(go.Indicator(
        mode = "gauge",
        value = val,
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': f"<b>{title}</b>", 'font': {'size': 26, 'color': 'black'}},
        gauge = {
            'axis': {'range': [0, target], 'tickwidth': 1, 'tickcolor': "darkblue"},
            'bar': {'color': "#1E90FF"},
            'bgcolor': "#E0E0E0",
            'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.8, 'value': target}
        }
    ))
    
    # [수정] 텍스트가 그래프와 안 겹치게 내림 (y=-0.1) & 폰트 크기 상향 (size=16) & 여백 조정 (b=60)
    fig.update_layout(
        height=340, 
        margin=dict(l=30, r=30, t=70, b=60),
        annotations=[
            dict(x=0.5, y=0.15, xref='paper', yref='paper', text=center_text, showarrow=False, align='center'),
            dict(x=0.15, y=-0.1, xref='paper', yref='paper', text=f"{val_label}: <b>{val_str}</b>", showarrow=False, font=dict(size=16, color='#1E90FF'), xanchor='center'),
            dict(x=0.85, y=-0.1, xref='paper', yref='paper', text=f"목표치: <b>{target_str}</b>", showarrow=False, font=dict(size=16, color='red'), xanchor='center')
        ]
    )
    return fig

# -----------------------------------------------------------------------------
# 6. 메인 화면 UI
# -----------------------------------------------------------------------------
st.title("📊 K-PETRO BEMS 통합 모니터링")
st.subheader(f"💡 K-PETRO 통합 실적 (누계 - {selected_month} 기준)")

# [수정] '총괄 OOOO' 로 텍스트 간소화 적용
g_col1, g_col2, g_col3 = st.columns(3)
with g_col1: st.plotly_chart(make_gauge(total_ghg, target_ghg, "총괄 온실가스 배출량", "tCO2eq"), use_container_width=True, key="top_ghg")
with g_col2: st.plotly_chart(make_gauge(total_elec, target_elec, "총괄 전력사용량", "kWh"), use_container_width=True, key="top_elec")
with g_col3: st.plotly_chart(make_gauge(total_water, target_water, "총괄 용수사용량", "ton"), use_container_width=True, key="top_water")

st.markdown(f"""
<div style='background-color: #E8F5E9; padding: 15px; border-radius: 8px; text-align: center; color: #2E7D32; font-size: 18px; font-weight: bold; margin-bottom: 20px;'>
    🌱 2026년 {selected_month} 기준 목표치 대비 온실가스 감축량 : {ghg_reduction_display:,.1f} tCO2eq (소나무 {pine_trees:,.0f}그루 식재효과)
</div>
""", unsafe_allow_html=True)

st.markdown("---")

tab1, tab2, tab3, tab4 = st.tabs(["☁️ 온실가스 배출량", "⚡ 전력사용량", "💧 용수사용량", "📈 연간 달성 예측치"])

if group_col:
    df_grouped = df_filtered.groupby(group_col).sum(numeric_only=True).reset_index()
    df_target_grouped = df_target.groupby(group_col).sum(numeric_only=True).reset_index() if group_col in df_target.columns else pd.DataFrame()
    raw_valid_hqs = [hq for hq in df_grouped[group_col].astype(str).tolist() if hq.strip() != '0' and hq.strip() != '']
    valid_hqs = sorted(raw_valid_hqs, key=lambda x: target_order.index(x) if x in target_order else 999)
else:
    df_grouped, df_target_grouped, valid_hqs = pd.DataFrame(), pd.DataFrame(), []

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
                        st.plotly_chart(make_gauge(r_ghg, t_ghg, f"{hq_name}", "tCO2eq"), use_container_width=True, key=f"{tab_prefix}_ghg_{hq_name}")
                    elif metric_type == "ELEC":
                        st.plotly_chart(make_gauge(r_elec, t_elec, f"{hq_name}", "kWh"), use_container_width=True, key=f"{tab_prefix}_elec_{hq_name}")
                    elif metric_type == "WATER":
                        st.plotly_chart(make_gauge(r_water, t_water, f"{hq_name}", "ton"), use_container_width=True, key=f"{tab_prefix}_water_{hq_name}")

# [수정] 예측 탭 전용 3열 그리드 출력 함수 (비례 산출 로직 적용)
def render_forecast_grid(metric_type, tab_prefix):
    if not valid_hqs:
        st.info("표시할 본부별 데이터가 없습니다.")
        return
    
    # 💡 [핵심] 현재 집계된 월수를 기반으로 연말(12월) 예상치 산출 계수 생성
    forecast_multiplier = (12 / sel_month_num) if sel_month_num > 0 else 1
        
    for i in range(0, len(valid_hqs), 3):
        cols = st.columns(3)
        for j in range(3):
            idx = i + j
            if idx < len(valid_hqs):
                hq_name = valid_hqs[idx]
                row = df_grouped[df_grouped[group_col].astype(str) == hq_name].iloc[0]
                
                # 누적 실적에 예측 계수를 곱하여 연말 예상치(Forecast) 산출
                r_elec = float(row.get('전력사용량', 0)) * forecast_multiplier
                r_gas = float(row.get('도시가스사용량', 0)) * forecast_multiplier
                r_kero = float(row.get('실내등유사용량', 0)) * forecast_multiplier
                r_water = float(row.get('용수사용량', 0)) * forecast_multiplier
                r_ghg = (r_elec * 0.0004594106) + (r_gas * 0.002187587) + (r_kero * 0.0024652936)
                
                if not df_target_grouped.empty and hq_name in df_target_grouped[group_col].values:
                    t_row = df_target_grouped[df_target_grouped[group_col] == hq_name].iloc[0]
                    t_elec = float(t_row.get('전력목표_연간', 0))
                    t_ghg = float(t_row.get('온실가스목표_연간', 0))
                    t_water = float(t_row.get('용수목표_연간', 0))
                    
                    if t_elec == 0: t_elec = r_elec * 1.2
                    if t_ghg == 0: t_ghg = r_ghg * 1.2
                    if t_water == 0: t_water = r_water * 1.2
                else:
                    t_elec, t_ghg, t_water = r_elec * 1.2, r_ghg * 1.2, r_water * 1.2

                with cols[j]:
                    # 예측 탭에서는 is_forecast=True 파라미터를 넘겨 텍스트를 "예상실적"으로 자동 변경
                    if metric_type == "GHG":
                        st.plotly_chart(make_gauge(r_ghg, t_ghg, f"{hq_name}", "tCO2eq", is_forecast=True), use_container_width=True, key=f"{tab_prefix}_ghg_{hq_name}")
                    elif metric_type == "ELEC":
                        st.plotly_chart(make_gauge(r_elec, t_elec, f"{hq_name}", "kWh", is_forecast=True), use_container_width=True, key=f"{tab_prefix}_elec_{hq_name}")
                    elif metric_type == "WATER":
                        st.plotly_chart(make_gauge(r_water, t_water, f"{hq_name}", "ton", is_forecast=True), use_container_width=True, key=f"{tab_prefix}_water_{hq_name}")

with tab1:
    st.subheader(f"☁️ 본부별 온실가스 배출량 상세 실적 (누계 - {selected_month})")
    render_hq_grid("GHG", "tab1")

with tab2:
    st.subheader(f"⚡ 본부별 전력사용량 상세 실적 (누계 - {selected_month})")
    render_hq_grid("ELEC", "tab2")

with tab3:
    st.subheader(f"💧 본부별 용수사용량 상세 실적 (누계 - {selected_month})")
    render_hq_grid("WATER", "tab3")

# [수정] 예측 탭 하위 메뉴 구성 및 각 항목별 3열 반원 그래프 출력
with tab4:
    st.subheader("📈 항목별 종합 연간 달성 예측 시뮬레이션")
    f_tab1, f_tab2, f_tab3 = st.tabs(["☁️ 온실가스 예측치", "⚡ 전력사용량 예측치", "💧 용수사용량 예측치"])
    
    with f_tab1:
        render_forecast_grid("GHG", "forecast_ghg")
    with f_tab2:
        render_forecast_grid("ELEC", "forecast_elec")
    with f_tab3:
        render_forecast_grid("WATER", "forecast_water")