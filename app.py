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

if group_col and not df_actual[group_col].dropna().empty:
    hq_list = [x for x in df_actual[group_col].unique() if str(x).strip() != '']
else:
    hq_list = ['본사', '수도권본부', '강원본부', '충북본부', '대전세종충남본부', '전북본부', '광주전남본부', '대구경북본부', '부산울산경남본부', '제주본부']

# -----------------------------------------------------------------------------
# 3. 사이드바 (실적 직접 입력 제어판 & 시뮬레이터)
# -----------------------------------------------------------------------------
with st.sidebar.expander("⚙️ 관리자 제어판 (본부별 실적입력)", expanded=True):
    st.markdown("웹에서 실적을 입력하면 구글 DB에 실시간 연동됩니다.")
    with st.form("data_input_form", clear_on_submit=True):
        input_month = st.selectbox("입력 대상 월", [f"{i}월" for i in range(1, 13)])
        input_hq = st.selectbox("본부(지사) 선택", hq_list)
        
        input_elec = st.number_input("전력사용량 (kWh)", min_value=0.0, step=100.0)
        input_gas = st.number_input("도시가스 사용량 (m3)", min_value=0.0, step=10.0)
        input_kero = st.number_input("실내등유 사용량 (L)", min_value=0.0, step=10.0)
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
# 4. 연산 및 팝업 (정밀 온실가스 환산계수 반영 및 에러 원천 차단)
# -----------------------------------------------------------------------------
# .get().sum() 사용 시 발생하는 AttributeError 완벽 해결
total_elec = int(df_filtered['전력사용량'].sum()) if '전력사용량' in df_filtered.columns else 0
total_gas = int(df_filtered['도시가스'].sum()) if '도시가스' in df_filtered.columns else 0
total_kero = int(df_filtered['실내등유'].sum()) if '실내등유' in df_filtered.columns else 0
total_water = int(df_filtered['용수사용량'].sum()) if '용수사용량' in df_filtered.columns else 0

# 요청하신 정밀 환산계수 반영
total_ghg = int((total_elec * 0.0004594106) + (total_gas * 0.002187587) + (total_kero * 0.0024652936))

pine_trees = int(total_ghg * 6.6)
st.toast(f"🌲 2026년 {selected_month} 현재 목표 대비 소나무 상쇄 효과: {pine_trees:,.0f} 그루", icon="🌲")

# -----------------------------------------------------------------------------
# 5. 메인 화면 UI
# -----------------------------------------------------------------------------
st.title("📊 K-PETRO BEMS 통합 모니터링")

st.subheader(f"💡 K-PETRO 통합 실적 (누계 - {selected_month} 기준)")
col1, col2, col3, col4 = st.columns(4)
col1.metric("총 온실가스 배출량", f"{total_ghg:,.0f} tCO2eq")
col2.metric("총 전력사용량", f"{total_elec:,.0f} kWh")
col3.metric("총 용수사용량", f"{total_water:,.0f} ton")
col4.metric("소나무 상쇄 효과", f"{pine_trees:,.0f} 그루")

st.markdown("---")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["🎯 전본부 목표 대비 실적현황", "☁️ 온실가스 배출량", "⚡ 전력사용량", "💧 용수사용량", "📈 연간 달성 예측치"])

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
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=70, b=10))
    return fig

target_elec = int(df_target['전력사용량'].sum()) if '전력사용량' in df_target.columns else 100000
target_ghg = int(df_target['온실가스'].sum()) if '온실가스' in df_target.columns else 50000
target_water = int(df_target['용수사용량'].sum()) if '용수사용량' in df_target.columns else 10000

with tab1:
    st.subheader(f"🌐 전사 통합 온실가스·에너지 실적 ({selected_month} 누적 기준)")
    g_col1, g_col2, g_col3 = st.columns(3)
    with g_col1: st.plotly_chart(make_gauge(total_ghg, target_ghg, "총 온실가스 배출량", "tCO2eq"), use_container_width=True)
    with g_col2: st.plotly_chart(make_gauge(total_elec, target_elec, "총 전력사용량", "kWh"), use_container_width=True)
    with g_col3: st.plotly_chart(make_gauge(total_water, target_water, "총 용수사용량", "ton"), use_container_width=True)
    
    st.markdown("---")
    st.subheader("🏢 전본부 상세 실적 현황 한눈에 보기")
    
    if group_col:
        df_grouped = df_filtered.groupby(group_col).sum(numeric_only=True).reset_index()
        df_target_grouped = df_target.groupby(group_col).sum(numeric_only=True).reset_index() if group_col in df_target.columns else pd.DataFrame()
        
        valid_hqs = [hq for hq in df_grouped[group_col].astype(str).tolist() if hq.strip() != '0' and hq.strip() != '']
        
        for hq_name in valid_hqs:
            st.markdown(f"#### 📍 {hq_name}")
            row = df_grouped[df_grouped[group_col].astype(str) == hq_name].iloc[0]
            
            r_elec = int(row['전력사용량']) if '전력사용량' in row else 0
            r_gas = int(row['도시가스']) if '도시가스' in row else 0
            r_kero = int(row['실내등유']) if '실내등유' in row else 0
            r_water = int(row['용수사용량']) if '용수사용량' in row else 0
            
            # 본부별 정밀 온실가스 환산계수 반영
            r_ghg = int((r_elec * 0.0004594106) + (r_gas * 0.002187587) + (r_kero * 0.0024652936))
                
            if not df_target_grouped.empty and hq_name in df_target_grouped[group_col].values:
                t_row = df_target_grouped[df_target_grouped[group_col] == hq_name].iloc[0]
                t_elec = int(t_row.get('전력사용량', r_elec * 1.5))
                t_ghg = int(t_row.get('온실가스', r_ghg * 1.5))
                t_water = int(t_row.get('용수사용량', r_water * 1.5))
            else:
                t_elec, t_ghg, t_water = r_elec * 1.5, r_ghg * 1.5, r_water * 1.5
            
            r_col1, r_col2, r_col3 = st.columns(3)
            with r_col1: st.plotly_chart(make_gauge(r_ghg, t_ghg, f"온실가스 배출량", "tCO2eq"), use_container_width=True)
            with r_col2: st.plotly_chart(make_gauge(r_elec, t_elec, f"전력사용량", "kWh"), use_container_width=True)
            with r_col3: st.plotly_chart(make_gauge(r_water, t_water, f"용수사용량", "ton"), use_container_width=True)
            st.write("") 

with tab2:
    st.subheader(f"☁️ 온실가스 배출량 월별 추이 (누계 - {selected_month})")
    # 월별 온실가스 추이를 그리기 위해 각 월의 누적 배출량을 직접 계산
    if '전력사용량' in df_actual.columns:
        df_monthly_ghg = df_actual[df_actual['월_num'] > 0].copy()
        df_monthly_ghg['온실가스_계산'] = (
            df_monthly_ghg.get('전력사용량', 0) * 0.0004594106 +
            df_monthly_ghg.get('도시가스', 0) * 0.002187587 +
            df_monthly_ghg.get('실내등유', 0) * 0.0024652936
        )
        df_monthly_trend = df_monthly_ghg.groupby('월_num')[['온실가스_계산']].sum().reset_index()
        fig_ghg = px.bar(df_monthly_trend, x='월_num', y='온실가스_계산', text_auto='.0f', labels={'온실가스_계산': '온실가스'})
        fig_ghg.update_layout(xaxis=dict(tickmode='linear', dtick=1))
        st.plotly_chart(fig_ghg, use_container_width=True)
    else:
        st.info("데이터베이스에 실적이 입력되지 않았습니다.")

with tab3:
    st.subheader(f"⚡ 전력사용량 월별 추이 (누계 - {selected_month})")
    if '전력사용량' in df_actual.columns:
        df_monthly = df_actual[df_actual['월_num'] > 0].groupby('월_num')[['전력사용량']].sum(numeric_only=True).reset_index()
        fig_elec = px.bar(df_monthly, x='월_num', y='전력사용량', text_auto='.0f')
        fig_elec.update_layout(xaxis=dict(tickmode='linear', dtick=1))
        st.plotly_chart(fig_elec, use_container_width=True)

with tab4:
    st.subheader(f"💧 용수사용량 월별 추이 (누계 - {selected_month})")
    if '용수사용량' in df_actual.columns:
        df_monthly = df_actual[df_actual['월_num'] > 0].groupby('월_num')[['용수사용량']].sum(numeric_only=True).reset_index()
        fig_water = px.bar(df_monthly, x='월_num', y='용수사용량', text_auto='.0f')
        fig_water.update_layout(xaxis=dict(tickmode='linear', dtick=1))
        st.plotly_chart(fig_water, use_container_width=True)

with tab5:
    st.subheader("본부별 세부 현황 및 연간 실적 예측 (개별 탭 보기)")
    if group_col and valid_hqs:
        inner_tabs = st.tabs(valid_hqs)
        for idx, hq_name in enumerate(valid_hqs):
            with inner_tabs[idx]:
                row = df_grouped[df_grouped[group_col].astype(str) == hq_name].iloc[0]
                
                r_elec = int(row['전력사용량']) if '전력사용량' in row else 0
                r_gas = int(row['도시가스']) if '도시가스' in row else 0
                r_kero = int(row['실내등유']) if '실내등유' in row else 0
                r_water = int(row['용수사용량']) if '용수사용량' in row else 0
                
                # 본부별 예측 탭에도 정밀 계수 적용
                r_ghg = int((r_elec * 0.0004594106) + (r_gas * 0.002187587) + (r_kero * 0.0024652936))
                
                if not df_target_grouped.empty and hq_name in df_target_grouped[group_col].values:
                    t_row = df_target_grouped[df_target_grouped[group_col] == hq_name].iloc[0]
                    t_elec = int(t_row.get('전력사용량', r_elec * 1.5))
                    t_ghg = int(t_row.get('온실가스', r_ghg * 1.5))
                    t_water = int(t_row.get('용수사용량', r_water * 1.5))
                else:
                    t_elec, t_ghg, t_water = r_elec * 1.5, r_ghg * 1.5, r_water * 1.5
                
                r_col1, r_col2, r_col3 = st.columns(3)
                with r_col1: st.plotly_chart(make_gauge(r_ghg, t_ghg, f"[{hq_name}] 온실가스 배출량", "tCO2eq"), use_container_width=True)
                with r_col2: st.plotly_chart(make_gauge(r_elec, t_elec, f"[{hq_name}] 전력사용량", "kWh"), use_container_width=True)
                with r_col3: st.plotly_chart(make_gauge(r_water, t_water, f"[{hq_name}] 용수사용량", "ton"), use_container_width=True)
    else:
        st.info("표시할 본부별 데이터가 없습니다.")