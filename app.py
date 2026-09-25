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
# 3. 사이드바 (월 선택) 및 누적 데이터 필터링 (에러 원천 차단)
# -----------------------------------------------------------------------------
st.sidebar.header("데이터 조회 월 선택")
month_list = [f"{i}월" for i in range(1, 13)]
selected_month = st.sidebar.selectbox("월 선택", month_list, index=8) # 기본값 9월

# [핵심 수정] '월' 컬럼에 섞인 빈칸, '합계' 등 문자열 에러를 무시하고 안전하게 숫자로 변환
df_actual['월_num'] = pd.to_numeric(df_actual['월'].astype(str).str.replace('월', '').str.strip(), errors='coerce').fillna(0).astype(int)

sel_month_num = int(selected_month.replace('월', ''))

# 0이 아닌 정상적인 월 데이터만 필터링 (엑셀의 빈 줄이나 합계 행 배제)
df_filtered = df_actual[(df_actual['월_num'] > 0) & (df_actual['월_num'] <= sel_month_num)]

# -----------------------------------------------------------------------------
# 4. 데이터 연산 (종합 실적 - 필터링된 누적 데이터 기준)
# -----------------------------------------------------------------------------
total_elec = int(df_filtered.get('전력사용량', 0).sum())

if '온실가스' in df_filtered.columns and df_filtered['온실가스'].sum() > 0:
    total_ghg = int(df_filtered['온실가스'].sum())
else:
    total_ghg = int(total_elec * 0.4781 / 1000)

total_water = int(df_filtered.get('용수사용량', 0).sum())
pine_trees = int(total_ghg * 6.6)

# -----------------------------------------------------------------------------
# 5. 화면 UI 구성
# -----------------------------------------------------------------------------
st.title("📊 K-PETRO BEMS 통합 모니터링")
st.markdown("---")

# [UI 파트 1] 상단 요약 지표 (Metric)
st.subheader(f"💡 K-PETRO 종합 실적 (누계 - {selected_month} 기준)")
col1, col2, col3, col4 = st.columns(4)

col1.metric(label="총 전력사용량", value=f"{total_elec:,.0f} kWh")
col2.metric(label="총 온실가스 배출량", value=f"{total_ghg:,.0f} tCO2eq")
col3.metric(label="총 용수사용량", value=f"{total_water:,.0f} ton")
col4.metric(label="소나무 상쇄 효과", value=f"{pine_trees:,.0f} 그루")

st.markdown("---")

# [UI 파트 2] 목표 달성률 게이지 차트
st.subheader("🎯 2026년 연간 목표 대비 달성률")

target_elec = int(df_target.get('전력사용량', 0).sum()) if '전력사용량' in df_target.columns else (total_elec * 1.5 if total_elec > 0 else 100000)
target_ghg = int(df_target.get('온실가스', 0).sum()) if '온실가스' in df_target.columns else (total_ghg * 1.5 if total_ghg > 0 else 50)

def make_gauge(val, target, title, unit):
    fig = go.Figure(go.Indicator(
        mode = "gauge+number",
        value = val,
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': f"{title} ({unit})"},
        gauge = {
            'axis': {'range': [None, target]},
            'bar': {'color': "#0047AB"}, # K-PETRO 블루
            'steps' : [
                {'range': [0, target*0.5], 'color': "#E0E0E0"},
                {'range': [target*0.5, target*0.8], 'color': "#BDBDBD"}],
            'threshold' : {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': target}
        }
    ))
    fig.update_layout(height=350, margin=dict(l=20, r=20, t=50, b=20))
    return fig

g_col1, g_col2 = st.columns(2)
with g_col1:
    st.plotly_chart(make_gauge(total_elec, target_elec, "전력사용량 달성률", "kWh"), use_container_width=True)
with g_col2:
    st.plotly_chart(make_gauge(total_ghg, target_ghg, "온실가스 배출량 달성률", "tCO2eq"), use_container_width=True)

st.markdown("---")

# [UI 파트 3] 월별 실적 추이 바 차트
st.subheader("📈 월별 실적 추이")
# 그래프 그릴 때 0으로 처리된 빈칸/합계 행 제외
df_monthly = df_actual[df_actual['월_num'] > 0].groupby('월_num')[['전력사용량']].sum().reset_index()
fig_bar = px.bar(df_monthly, x='월_num', y='전력사용량', title="월별 누적 전력사용량 (kWh)", text_auto='.0f')
# X축 간격을 무조건 1단위 정수로 강제 고정 (소수점 표시 방지)
fig_bar.update_layout(xaxis=dict(tickmode='linear', dtick=1))
fig_bar.update_traces(marker_color='#1E90FF')
st.plotly_chart(fig_bar, use_container_width=True)

st.markdown("---")

# [UI 파트 4] 지역(본부/지사)별 세부 탭
st.subheader("🏢 각 본부별 세부 실적")

possible_cols = ['본부', '지사', '사업장', '구분', '지역']
group_col = next((c for c in possible_cols if c in df_actual.columns), None)

if group_col:
    df_grouped = df_filtered.groupby(group_col).sum(numeric_only=True).reset_index()
    tabs = st.tabs(df_grouped[group_col].astype(str).tolist())
    
    for idx, row in df_grouped.iterrows():
        with tabs[idx]:
            r_elec = int(row.get('전력사용량', 0))
            r_ghg = int(row.get('온실가스', (r_elec * 0.4781 / 1000)))
            r_water = int(row.get('용수사용량', 0))
            
            r_col1, r_col2, r_col3 = st.columns(3)
            r_col1.metric(label="전력사용량", value=f"{r_elec:,.0f} kWh")
            r_col2.metric(label="온실가스 배출량", value=f"{r_ghg:,.0f} tCO2eq")
            r_col3.metric(label="용수사용량", value=f"{r_water:,.0f} ton")
else:
    st.info(f"엑셀 파일에 {', '.join(possible_cols)} 등의 컬럼이 존재하지 않아 본부별 탭을 생성할 수 없습니다.")