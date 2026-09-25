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
# 2. 데이터 로드 및 전처리 (문자열 -> 숫자 강제 변환)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_data():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    
    # 드라이브 권한 에러 방지를 위한 구글 시트 고유 ID 참조
    sheet_id = "1Ky6Brrh5pWXuDvAXV36SSQ2MBAjtxr__UbY8fU3viBY"
    doc = client.open_by_key(sheet_id)
    
    df_actual = pd.DataFrame(doc.worksheet("월간실적").get_all_records())
    df_2025 = pd.DataFrame(doc.worksheet("2025실적").get_all_records())
    df_target = pd.DataFrame(doc.worksheet("목표치관리").get_all_records())
    
    # 텍스트로 유지해야 할 기준 컬럼들
    exclude_cols = ['월', '구분', '날짜', '지사', '본부', '지역', '사업장'] 
    
    for df in [df_actual, df_2025, df_target]:
        for col in df.columns:
            if col not in exclude_cols:
                # 쉼표 제거 및 강제 숫자 변환 (에러 발생 시 0)
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                
    return df_actual, df_2025, df_target

try:
    df_actual, df_2025, df_target = load_data()
except Exception as e:
    st.error(f"데이터 로드 중 에러가 발생했습니다: {e}")
    st.stop()

# -----------------------------------------------------------------------------
# 3. 사이드바 (실적달성 시뮬레이터) 및 누적 데이터 필터링 
# -----------------------------------------------------------------------------
st.sidebar.header("📈 실적달성 시뮬레이터")
month_list = [f"{i}월" for i in range(1, 13)]
selected_month = st.sidebar.selectbox("현재 집계 완료(월)", month_list, index=8) # 기본값 9월

# 선택된 월 추출
sel_month_num = int(selected_month.replace('월', ''))

# [핵심] '월' 컬럼에서 에러 없이 숫자만 추출 후, 선택한 월까지의 누적 데이터만 필터링
if '월' in df_actual.columns:
    df_actual['월_num'] = pd.to_numeric(df_actual['월'].astype(str).str.replace('월', '').str.strip(), errors='coerce').fillna(0).astype(int)
    # 0보다 크고 선택 월 이하인 누적 데이터만 추출
    df_filtered = df_actual[(df_actual['월_num'] > 0) & (df_actual['월_num'] <= sel_month_num)]
else:
    df_filtered = df_actual

# -----------------------------------------------------------------------------
# 4. 데이터 연산 (종합 실적 - 누적 기준)
# -----------------------------------------------------------------------------
total_elec = int(df_filtered.get('전력사용량', 0).sum())
total_ghg = int(df_filtered['온실가스'].sum()) if '온실가스' in df_filtered.columns else int(total_elec * 0.4781 / 1000)
total_water = int(df_filtered.get('용수사용량', 0).sum())

# 🌲 [팝업 원복] 소나무 상쇄 효과 (동적 누적 계산 반영)
pine_trees = int(total_ghg * 6.6)
st.toast(f"🌲 2026년 {selected_month} 현재 목표 대비 소나무 상쇄 효과: {pine_trees:,.0f} 그루", icon="🌲")

# -----------------------------------------------------------------------------
# 5. 화면 UI 구성
# -----------------------------------------------------------------------------
st.title("📊 K-PETRO BEMS 통합 모니터링")
st.markdown("---")

# ⚙️ 관리자 제어판 (깨진 이미지 삭제, 텍스트 수정)
with st.expander("⚙️ 관리자 제어판"):
    st.subheader("본부별 실적입력")
    st.markdown("하단 링크를 통해 구글 시트에 실적을 입력하면 대시보드에 자동 반영됩니다.")
    st.link_button("데이터 입력 구글 시트 이동", "https://docs.google.com/spreadsheets/d/1Ky6Brrh5pWXuDvAXV36SSQ2MBAjtxr__UbY8fU3viBY/edit")

st.markdown("---")

# 📊 전본부 목표 대비 실적현황 (텍스트 수정 및 동적 월 표기)
st.subheader(f"🎯 전본부 목표 대비 실적현황 ({selected_month} 기준)")

# 목표치 연산
target_elec = int(df_target.get('전력사용량', 0).sum()) if '전력사용량' in df_target.columns else (total_elec * 1.5 if total_elec > 0 else 100000)
target_ghg = int(df_target.get('온실가스', 0).sum()) if '온실가스' in df_target.columns else (total_ghg * 1.5 if total_ghg > 0 else 50000)
target_water = int(df_target.get('용수사용량', 0).sum()) if '용수사용량' in df_target.columns else (total_water * 1.5 if total_water > 0 else 10000)

# 💡 [핵심] 3대 반원형 게이지 차트 생성 함수 (우측 목표, 중앙 현재량, 하단 초록색 잔여량)
def make_gauge(val, target, title, unit):
    # 타겟 데이터가 없을 경우를 대비한 자동 보정
    if target <= 0: target = val * 1.2 if val > 0 else 100
    
    remaining = target - val
    # 잔여량은 초록색, 초과 시 빨간색 표기
    remaining_text = f"잔여 목표량: {remaining:,.0f} {unit}" if remaining >= 0 else f"목표 초과: {abs(remaining):,.0f} {unit}"
    color = "#008000" if remaining >= 0 else "#FF0000" 

    fig = go.Figure(go.Indicator(
        mode = "gauge+number",
        value = val,
        number = {'suffix': f" {unit}", 'font': {'size': 26}}, # 중앙 큰 텍스트 및 단위
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': f"<b>{title}</b><br><span style='font-size:14px; color:{color};'>{remaining_text}</span>", 'font': {'size': 18}},
        gauge = {
            'axis': {'range': [0, target], 'tickwidth': 1, 'tickcolor': "darkblue"}, # 우측 끝 목표값 연동
            'bar': {'color': "#1E90FF"},
            'bgcolor': "#E0E0E0",
            'steps': [{'range': [0, target], 'color': '#F5F5F5'}],
            'threshold': {'line': {'color': "red", 'width': 3}, 'thickness': 0.75, 'value': target}
        }
    ))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=70, b=10))
    return fig

# 종합 실적 반원 그래프 (용수 포함 3개 원복)
g_col1, g_col2, g_col3 = st.columns(3)
with g_col1:
    st.plotly_chart(make_gauge(total_elec, target_elec, "전력사용량", "kWh"), use_container_width=True)
with g_col2:
    st.plotly_chart(make_gauge(total_ghg, target_ghg, "온실가스 배출량", "tCO2eq"), use_container_width=True)
with g_col3:
    st.plotly_chart(make_gauge(total_water, target_water, "용수사용량", "ton"), use_container_width=True)

st.markdown("---")

# 📈 본부별 세부 현황 탭 및 개별 반원 그래프 연동
st.subheader("🏢 본부별 세부 현황 및 연간 실적 예측")

group_col = next((c for c in ['본부', '지사', '사업장', '구분', '지역'] if c in df_actual.columns), None)

if group_col:
    # 누적 실적 및 목표 데이터 그룹화
    df_grouped = df_filtered.groupby(group_col).sum(numeric_only=True).reset_index()
    df_target_grouped = df_target.groupby(group_col).sum(numeric_only=True).reset_index() if group_col in df_target.columns else pd.DataFrame()
    
    tabs = st.tabs(df_grouped[group_col].astype(str).tolist())
    
    for idx, row in df_grouped.iterrows():
        hq_name = row[group_col]
        with tabs[idx]:
            # 본부별 누적 실적 산출
            r_elec = int(row.get('전력사용량', 0))
            r_ghg = int(row.get('온실가스', (r_elec * 0.4781 / 1000)))
            r_water = int(row.get('용수사용량', 0))
            
            # 본부별 2026년 목표치 매칭 (없을 경우 실적 비례 생성)
            if not df_target_grouped.empty and hq_name in df_target_grouped[group_col].values:
                t_row = df_target_grouped[df_target_grouped[group_col] == hq_name].iloc[0]
                t_elec = int(t_row.get('전력사용량', r_elec * 1.5))
                t_ghg = int(t_row.get('온실가스', r_ghg * 1.5))
                t_water = int(t_row.get('용수사용량', r_water * 1.5))
            else:
                t_elec, t_ghg, t_water = r_elec * 1.5, r_ghg * 1.5, r_water * 1.5
                
            # 본부별 탭 내부에 3대 반원 그래프 삽입
            r_col1, r_col2, r_col3 = st.columns(3)
            with r_col1: st.plotly_chart(make_gauge(r_elec, t_elec, f"[{hq_name}] 전력사용량", "kWh"), use_container_width=True)
            with r_col2: st.plotly_chart(make_gauge(r_ghg, t_ghg, f"[{hq_name}] 온실가스 배출량", "tCO2eq"), use_container_width=True)
            with r_col3: st.plotly_chart(make_gauge(r_water, t_water, f"[{hq_name}] 용수사용량", "ton"), use_container_width=True)
else:
    st.info("엑셀 파일에 '본부' 또는 '지사' 컬럼이 존재하지 않아 본부별 통계를 생성할 수 없습니다.")