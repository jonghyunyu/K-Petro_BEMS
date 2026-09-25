import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import plotly.graph_objects as go
import plotly.express as px

# -----------------------------------------------------------------------------
# 1. 페이지 기본 설정
# -----------------------------------------------------------------------------
st.set_page_config(page_title="K-PETRO BEMS 통합 모니터링", page_icon="📊", layout="wide")

# -----------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리
# -----------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_data():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    
    sheet_id = "1Ky6Brrh5pWXuDvAXV36SSQ2MBAjtxr__UbY8fU3viBY"
    doc = client.open_by_key(sheet_id)
    
    df_actual = pd.DataFrame(doc.worksheet("월간실적").get_all_records())
    df_2025 = pd.DataFrame(doc.worksheet("2025실적").get_all_records())
    df_target = pd.DataFrame(doc.worksheet("목표치관리").get_all_records())
    
    # 숫자 변환 에러 방지 (문자열 컬럼 자동 탐지)
    for df in [df_actual, df_2025, df_target]:
        for col in df.columns:
            if df[col].dtype == 'object':
                # 숫자로 변환 가능한 것만 변환, 안되면 그대로 두기
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='ignore')
                
    return df_actual, df_2025, df_target

try:
    df_actual, df_2025, df_target = load_data()
except Exception as e:
    st.error(f"데이터 로드 중 에러가 발생했습니다: {e}")
    st.stop()

# -----------------------------------------------------------------------------
# 3. 사이드바 (관리자 제어판 & 시뮬레이터 롤백)
# -----------------------------------------------------------------------------
# (1) 관리자 제어판
with st.sidebar.expander("⚙️ 관리자 제어판 (본부별 실적입력)", expanded=True):
    st.markdown("해당월 및 본부를 선택하여 데이터를 입력합니다.")
    input_month = st.selectbox("입력 대상 월", [f"{i}월" for i in range(1, 13)])
    
    # 엑셀의 지역/본부 컬럼 자동 탐지 (텍스트 형식인 컬럼 찾기)
    cat_cols = [c for c in df_actual.columns if df_actual[c].dtype == 'object' and c not in ['월', '날짜']]
    group_col = cat_cols[0] if cat_cols else None
    
    if group_col:
        hq_list = df_actual[group_col].dropna().unique().tolist()
        input_hq = st.selectbox("본부(지사) 선택", hq_list)
    else:
        st.warning("지역 데이터를 찾을 수 없습니다.")
        
    st.number_input("전력사용량 (kWh)", min_value=0, step=100)
    st.number_input("온실가스 (tCO2eq)", min_value=0, step=10)
    st.number_input("용수사용량 (ton)", min_value=0, step=10)
    
    st.markdown("💡 *데이터 무결성을 위해 실제 DB 반영은 하단 시트에서 직접 연동됩니다.*")
    st.link_button("데이터 입력 구글 시트 이동", "https://docs.google.com/spreadsheets/d/1Ky6Brrh5pWXuDvAXV36SSQ2MBAjtxr__UbY8fU3viBY/edit")

st.sidebar.markdown("---")

# (2) 실적달성 시뮬레이터
st.sidebar.header("📈 실적달성 시뮬레이터")
month_list = [f"{i}월" for i in range(1, 13)]
selected_month = st.sidebar.selectbox("현재 집계 완료(월)", month_list, index=8) # 기본 9월

# 선택 월 누적 필터링
sel_month_num = int(selected_month.replace('월', ''))
if '월' in df_actual.columns:
    df_actual['월_num'] = pd.to_numeric(df_actual['월'].astype(str).str.replace('월', '').str.strip(), errors='coerce').fillna(0).astype(int)
    df_filtered = df_actual[(df_actual['월_num'] > 0) & (df_actual['월_num'] <= sel_month_num)]
else:
    df_filtered = df_actual

# -----------------------------------------------------------------------------
# 4. 연산 및 팝업 (소나무 상쇄 효과)
# -----------------------------------------------------------------------------
total_elec = int(pd.to_numeric(df_filtered.get('전력사용량', 0), errors='coerce').sum())
total_ghg = int(pd.to_numeric(df_filtered.get('온실가스', 0), errors='coerce').sum()) if '온실가스' in df_filtered.columns else int(total_elec * 0.4781 / 1000)
total_water = int(pd.to_numeric(df_filtered.get('용수사용량', 0), errors='coerce').sum())

pine_trees = int(total_ghg * 6.6)
st.toast(f"🌲 2026년 {selected_month} 현재 목표 대비 소나무 상쇄 효과: {pine_trees:,.0f} 그루", icon="🌲")

# -----------------------------------------------------------------------------
# 5. 메인 화면 UI (탭 구조 원복)
# -----------------------------------------------------------------------------
st.title("📊 K-PETRO BEMS 통합 모니터링")

# 상단 요약 지표
st.subheader(f"💡 K-PETRO 통합 실적 (누계 - {selected_month} 기준)")
col1, col2, col3, col4 = st.columns(4)
col1.metric("총 전력사용량", f"{total_elec:,.0f} kWh")
col2.metric("총 온실가스 배출량", f"{total_ghg:,.0f} tCO2eq")
col3.metric("총 용수사용량", f"{total_water:,.0f} ton")
col4.metric("소나무 상쇄 효과", f"{pine_trees:,.0f} 그루")

st.markdown("---")

# 탭 메뉴 구성
tab1, tab2, tab3, tab4, tab5 = st.tabs(["🎯 전본부 목표 대비 실적현황", "⚡ 전력사용량", "☁️ 온실가스 배출량", "💧 용수사용량", "📈 연간 달성 예측치"])

# 반원형 게이지 차트 생성 함수
def make_gauge(val, target, title, unit):
    if target <= 0: target = val * 1.2 if val > 0 else 100
    remaining = target - val
    remaining_text = f"잔여 목표량: {remaining:,.0f} {unit}" if remaining >= 0 else f"목표 초과: {abs(remaining):,.0f} {unit}"
    color = "#008000" if remaining >= 0 else "#FF0000" 

    fig = go.Figure(go.Indicator(
        mode = "gauge+number",
        value = val,
        number = {'suffix': f" {unit}", 'font': {'size': 26}},
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': f"<b>{title}</b><br><span style='font-size:14px; color:{color};'>{remaining_text}</span>"},
        gauge = {
            'axis': {'range': [0, target]},
            'bar': {'color': "#1E90FF"},
            'bgcolor': "#E0E0E0",
            'threshold': {'line': {'color': "red", 'width': 3}, 'thickness': 0.75, 'value': target}
        }
    ))
    fig.update_layout(height=350, margin=dict(l=10, r=10, t=70, b=10))
    return fig

# 목표치 합산 연산
target_elec = int(pd.to_numeric(df_target.get('전력사용량', 0), errors='coerce').sum()) if '전력사용량' in df_target.columns else 100000
target_ghg = int(pd.to_numeric(df_target.get('온실가스', 0), errors='coerce').sum()) if '온실가스' in df_target.columns else 50000
target_water = int(pd.to_numeric(df_target.get('용수사용량', 0), errors='coerce').sum()) if '용수사용량' in df_target.columns else 10000

# [탭 1] 전본부 목표 대비 실적현황 (반원형 3대 그래프)
with tab1:
    st.subheader(f"전본부 목표 대비 실적현황 ({selected_month} 누적 기준)")
    g_col1, g_col2, g_col3 = st.columns(3)
    with g_col1: st.plotly_chart(make_gauge(total_elec, target_elec, "전력사용량", "kWh"), use_container_width=True)
    with g_col2: st.plotly_chart(make_gauge(total_ghg, target_ghg, "온실가스 배출량", "tCO2eq"), use_container_width=True)
    with g_col3: st.plotly_chart(make_gauge(total_water, target_water, "용수사용량", "ton"), use_container_width=True)

# [탭 2,3,4] 각 항목별 월별 추이 차트
with tab2:
    st.subheader(f"⚡ 전력사용량 월별 추이 (누계 - {selected_month})")
    df_monthly = df_actual[df_actual['월_num'] > 0].groupby('월_num')[['전력사용량']].sum(numeric_only=True).reset_index()
    fig_elec = px.bar(df_monthly, x='월_num', y='전력사용량', text_auto='.0f')
    fig_elec.update_layout(xaxis=dict(tickmode='linear', dtick=1))
    st.plotly_chart(fig_elec, use_container_width=True)

with tab3:
    st.subheader(f"☁️ 온실가스 배출량 월별 추이 (누계 - {selected_month})")
    df_monthly = df_actual[df_actual['월_num'] > 0].groupby('월_num')[['온실가스']].sum(numeric_only=True).reset_index()
    fig_ghg = px.bar(df_monthly, x='월_num', y='온실가스', text_auto='.0f')
    fig_ghg.update_layout(xaxis=dict(tickmode='linear', dtick=1))
    st.plotly_chart(fig_ghg, use_container_width=True)

with tab4:
    st.subheader(f"💧 용수사용량 월별 추이 (누계 - {selected_month})")
    df_monthly = df_actual[df_actual['월_num'] > 0].groupby('월_num')[['용수사용량']].sum(numeric_only=True).reset_index()
    fig_water = px.bar(df_monthly, x='월_num', y='용수사용량', text_auto='.0f')
    fig_water.update_layout(xaxis=dict(tickmode='linear', dtick=1))
    st.plotly_chart(fig_water, use_container_width=True)

# [탭 5] 연간 달성 예측치 (본부별 세부 데이터 및 게이지 연동)
with tab5:
    st.subheader("본부별 세부 현황 및 연간 실적 예측")
    if group_col:
        df_grouped = df_filtered.groupby(group_col).sum(numeric_only=True).reset_index()
        df_target_grouped = df_target.groupby(group_col).sum(numeric_only=True).reset_index() if group_col in df_target.columns else pd.DataFrame()
        
        inner_tabs = st.tabs(df_grouped[group_col].astype(str).tolist())
        
        for idx, row in df_grouped.iterrows():
            hq_name = row[group_col]
            with inner_tabs[idx]:
                r_elec = int(row.get('전력사용량', 0))
                r_ghg = int(row.get('온실가스', (r_elec * 0.4781 / 1000)))
                r_water = int(row.get('용수사용량', 0))
                
                # 목표치 매칭
                if not df_target_grouped.empty and hq_name in df_target_grouped[group_col].values:
                    t_row = df_target_grouped[df_target_grouped[group_col] == hq_name].iloc[0]
                    t_elec = int(t_row.get('전력사용량', r_elec * 1.5))
                    t_ghg = int(t_row.get('온실가스', r_ghg * 1.5))
                    t_water = int(t_row.get('용수사용량', r_water * 1.5))
                else:
                    t_elec, t_ghg, t_water = r_elec * 1.5, r_ghg * 1.5, r_water * 1.5
                
                # 본부별 반원 그래프
                r_col1, r_col2, r_col3 = st.columns(3)
                with r_col1: st.plotly_chart(make_gauge(r_elec, t_elec, f"[{hq_name}] 전력사용량", "kWh"), use_container_width=True)
                with r_col2: st.plotly_chart(make_gauge(r_ghg, t_ghg, f"[{hq_name}] 온실가스 배출량", "tCO2eq"), use_container_width=True)
                with r_col3: st.plotly_chart(make_gauge(r_water, t_water, f"[{hq_name}] 용수사용량", "ton"), use_container_width=True)
    else:
        st.warning("데이터 내 지역 구분 기준(본부/지사)을 찾을 수 없습니다.")