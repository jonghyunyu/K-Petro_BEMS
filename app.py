import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import plotly.graph_objects as go

# ==========================================
# 1. 페이지 기본 설정
# ==========================================
st.set_page_config(page_title="K-PETRO BEMS", layout="wide")

# ==========================================
# 2. 구글 시트 연동 및 데이터 불러오기
# ==========================================
@st.cache_resource
def init_connection():
    scope = ['https://www.googleapis.com/auth/spreadsheets']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    return client

client = init_connection()
sheet_url = "K-Petro_BEMS_DB" # 구글 시트 파일 이름

@st.cache_data(ttl=60)
def load_data():
    doc = client.open(sheet_url)
    
    # 목표치 데이터 로드
    ws_target = doc.worksheet("목표치관리")
    df_target = pd.DataFrame(ws_target.get_all_records())
    
    # 2025년 과거 실적 로드
    ws_2025 = doc.worksheet("2025실적")
    df_2025 = pd.DataFrame(ws_2025.get_all_records())
    
    # 2026년 실적 데이터 로드
    ws_actual = doc.worksheet("월간실적")
    df_actual = pd.DataFrame(ws_actual.get_all_records())
    
    # 온실가스 배출량 환산 (전력: 0.4781, 도시가스: 2.176, 실내등유: 2.493)
    if not df_actual.empty:
        df_actual['온실가스배출량'] = (
            (df_actual.get('전력사용량', 0) * 0.4781 / 1000) + 
            (df_actual.get('도시가스사용량', 0) * 2.176 / 1000) + 
            (df_actual.get('실내등유사용량', 0) * 2.493 / 1000)
        )
    return doc, df_target, df_2025, df_actual

doc, df_target, df_2025, df_actual = load_data()

# 11개 본부 리스트
hq_list = [
    "본사·수도권남부", "미래기술연구소", "수도권북부", "대전세종충남", "충북",
    "전남광주", "전북", "부산울산경남", "대구경북", "강원", "제주"
]

# ==========================================
# 3. 좌측 사이드바 (관리자 제어판 및 시뮬레이터)
# ==========================================
st.sidebar.header("관리자 제어판")

# 데이터 입력 폼
with st.sidebar.expander("본부별 실적입력"):
    with st.form("data_input_form"):
        input_month = st.selectbox("입력 월", list(range(1, 13)))
        input_hq = st.selectbox("본부명", hq_list)
        input_power = st.number_input("전력사용량 (kWh)", min_value=0)
        input_water = st.number_input("용수사용량 (ton)", min_value=0)
        input_gas = st.number_input("도시가스사용량 (Nm3)", min_value=0)
        input_oil = st.number_input("실내등유사용량 (L)", min_value=0)
        
        submitted = st.form_submit_button("구글 시트로 전송")
        if submitted:
            ws_actual = doc.worksheet("월간실적")
            # 입력 년도는 2026 고정
            ws_actual.append_row([2026, input_month, input_hq, input_power, input_water, input_gas, input_oil])
            st.sidebar.success(f"{input_month}월 {input_hq} 데이터 전송 완료! (새로고침을 눌러주세요)")
            st.cache_data.clear()

st.sidebar.markdown("---")

# 시뮬레이터 기준 월 설정
st.sidebar.subheader("실적달성 시뮬레이터")
selected_month = st.sidebar.selectbox("현재 집계 완료(월)", list(range(1, 13)), index=8) # 기본값 9월

# ==========================================
# 4. 메인 화면 상단 (누적 실적 및 소나무 효과)
# ==========================================
st.title("🌱 K-PETRO 환경경영 통합 모니터링")

# 선택된 월까지의 누적 실적 데이터 필터링
if not df_actual.empty:
    df_cumulative = df_actual[df_actual['월'] <= selected_month]
else:
    df_cumulative = pd.DataFrame(columns=['전력사용량', '용수사용량', '온실가스배출량'])

# 전사 누적 실적 합산
total_power_current = df_cumulative['전력사용량'].sum() if not df_cumulative.empty else 0
total_water_current = df_cumulative['용수사용량'].sum() if not df_cumulative.empty else 0
total_ghg_current = df_cumulative['온실가스배출량'].sum() if not df_cumulative.empty else 0

# 전사 누적 목표 합산 (연간 목표치 / 12 * 선택된 월)
total_power_target = df_target['전력목표_연간'].sum() * (selected_month / 12)
total_water_target = df_target['용수목표_연간'].sum() * (selected_month / 12)
total_ghg_target = df_target['온실가스목표_연간'].sum() * (selected_month / 12)

# 소나무 효과 산정 (온실가스 기준)
saved_ghg = total_ghg_target - total_ghg_current
pine_trees = saved_ghg * 151

if saved_ghg > 0:
    st.success(f"🌲 2026년 {selected_month}월 현재 목표 대비 {pine_trees:,.0f}그루의 소나무 식재 효과를 달성했습니다!")
else:
    st.error(f"⚠️ 2026년 {selected_month}월 현재 목표 대비 온실가스 배출량이 {-saved_ghg:,.1f} tCO2eq 초과되었습니다.")

st.markdown("---")

# ==========================================
# 5. 전본부 목표 대비 실적현황 (전체 게이지)
# ==========================================
st.subheader(f"전본부 목표 대비 실적현황 ({selected_month}월 기준 누적)")

col1, col2, col3 = st.columns(3)

def create_overall_gauge(title, current_val, target_val, color):
    fig = go.Figure(go.Indicator(
        mode = "gauge+number+delta",
        value = current_val,
        title = {'text': title, 'font': {'size': 18}},
        delta = {'reference': target_val, 'position': "bottom", 'increasing': {'color': "red"}, 'decreasing': {'color': "green"}},
        gauge = {
            'axis': {'range': [0, max(target_val * 1.2, current_val)], 'tickwidth': 1},
            'bar': {'color': color},
            'steps': [{'range': [0, target_val], 'color': "lightgray"}]
        }
    ))
    return fig

with col1:
    st.plotly_chart(create_overall_gauge("전사 누적 온실가스 (tCO2eq)", total_ghg_current, total_ghg_target, "green"), use_container_width=True)
with col2:
    st.plotly_chart(create_overall_gauge("전사 누적 전력사용 (kWh)", total_power_current, total_power_target, "royalblue"), use_container_width=True)
with col3:
    st.plotly_chart(create_overall_gauge("전사 누적 용수사용 (ton)", total_water_current, total_water_target, "deepskyblue"), use_container_width=True)

st.markdown("---")

# ==========================================
# 6. 본부별 세부 현황 (단위 추가, 목표치 고정, 잔여량 표기)
# ==========================================
st.subheader("본부별 세부 실적 (연간 목표 대비 현황)")

def create_hq_gauge(title, current_val, annual_target, color):
    fig = go.Figure(go.Indicator(
        mode = "gauge+number+delta",
        value = current_val,
        title = {'text': title, 'font': {'size': 14}},
        delta = {
            'reference': annual_target, 
            'position': "bottom", 
            'relative': False, 
            'increasing': {'color': "red"}, 
            'decreasing': {'color': "green"}
        },
        gauge = {
            'axis': {'range': [0, annual_target], 'tickwidth': 1},
            'bar': {'color': color},
            'steps': [
                {'range': [0, annual_target * 0.8], 'color': "lightgray"},
                {'range': [annual_target * 0.8, annual_target], 'color': "gray"}
            ]
        }
    ))
    # 레이아웃 여백 최소화로 깔끔한 배치
    fig.update_layout(margin=dict(l=20, r=20, t=50, b=20), height=250)
    return fig

# 3열씩 배치하여 11개 본부 출력
cols = st.columns(3)
for idx, hq in enumerate(hq_list):
    col = cols[idx % 3]
    with col:
        st.markdown(f"#### {hq}")
        
        # 현재 누적값 추출
        if not df_cumulative.empty and hq in df_cumulative['본부명'].values:
            hq_data = df_cumulative[df_cumulative['본부명'] == hq]
            hq_power_actual = hq_data['전력사용량'].sum()
            hq_water_actual = hq_data['용수사용량'].sum()
            hq_ghg_actual = hq_data['온실가스배출량'].sum()
        else:
            hq_power_actual, hq_water_actual, hq_ghg_actual = 0, 0, 0
            
        # 연간 목표치 추출
        target_row = df_target[df_target['본부명'] == hq]
        if not target_row.empty:
            hq_power_target = target_row['전력목표_연간'].values[0]
            hq_water_target = target_row['용수목표_연간'].values[0]
            hq_ghg_target = target_row['온실가스목표_연간'].values[0]
        else:
            hq_power_target, hq_water_target, hq_ghg_target = 1, 1, 1 # 0으로 나누기 방지용 임시값
            
        # 탭을 활용해 차트를 깔끔하게 분리
        tab1, tab2, tab3 = st.tabs(["온실가스", "전력", "용수"])
        with tab1:
            st.plotly_chart(create_hq_gauge("온실가스 (tCO2eq)", hq_ghg_actual, hq_ghg_target, "green"), use_container_width=True)
        with tab2:
            st.plotly_chart(create_hq_gauge("전력사용 (kWh)", hq_power_actual, hq_power_target, "royalblue"), use_container_width=True)
        with tab3:
            st.plotly_chart(create_hq_gauge("용수사용 (ton)", hq_water_actual, hq_water_target, "deepskyblue"), use_container_width=True)
        st.markdown("<br>", unsafe_allow_html=True)