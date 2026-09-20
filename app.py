import streamlit as st
import pandas as pd
import gspread
import plotly.graph_objects as go
from datetime import datetime
import time
import json

st.set_page_config(page_title="K-PETRO 환경경영 통합 모니터링", page_icon="🌿", layout="wide")

HQ_LIST = [
    '본사·수도권남부', '미래기술연구소', '수도권북부', '대전세종충남', 
    '충북', '전남광주', '전북', '부산울산경남', '대구경북', '강원', '제주'
]

# ---------------- 구글 시트(DB) 안전 연결 (로컬/클라우드 통용) ----------------
@st.cache_resource
def init_connection():
    if "gcp_json" in st.secrets:
        creds_dict = json.loads(st.secrets["gcp_json"])
        gc = gspread.service_account_from_dict(creds_dict)
    elif "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
    else:
        gc = gspread.service_account(filename='secrets.json')
    sh = gc.open("K-Petro_BEMS_DB")
    return sh

try:
    sh = init_connection()
    ws_perf = sh.worksheet("월간실적")
    ws_target = sh.worksheet("목표치관리")
    try:
        ws_2025 = sh.worksheet("2025실적")
    except gspread.exceptions.WorksheetNotFound:
        ws_2025 = None
except Exception as e:
    st.error(f"❌ 구글 시트 연결 실패: {e}")
    st.stop()

@st.cache_data(ttl=1)
def load_db_data(_sheet):
    if _sheet is None: return []
    try:
        return _sheet.get_all_records()
    except Exception:
        return []

@st.cache_data(ttl=5)
def load_target_data():
    try:
        records = ws_target.get_all_records()
        return pd.DataFrame(records) if records else pd.DataFrame(columns=['본부명', '전력목표_연간', '용수목표_연간', '온실가스목표_연간'])
    except Exception:
        return pd.DataFrame(columns=['본부명', '전력목표_연간', '용수목표_연간', '온실가스목표_연간'])

# ---------------- 데이터 정제 및 강제 변환 함수 ----------------
def process_dataframe(records, year):
    empty_df = pd.DataFrame(columns=['연도', '월', '본부', '전력사용량(kWh)', '용수사용량(ton)', '도시가스사용량', '실내등유사용량', '온실가스(tCO2eq)'])
    if not records:
        return empty_df
    try:
        df = pd.DataFrame(records)
        if df.empty: return empty_df
        
        df.columns = df.columns.str.strip()
        df = df.rename(columns={'본부명': '본부', '전력사용량': '전력사용량(kWh)', '용수사용량': '용수사용량(ton)'})
        
        df['연도'] = pd.to_numeric(df['연도'].astype(str).str.replace(r'[^0-9]', '', regex=True), errors='coerce').fillna(0).astype(int)
        df['월'] = pd.to_numeric(df['월'].astype(str).str.replace(r'[^0-9]', '', regex=True), errors='coerce').fillna(0).astype(int)
        df['본부'] = df['본부'].astype(str).str.strip()
        
        df['전력사용량(kWh)'] = pd.to_numeric(df['전력사용량(kWh)'], errors='coerce').fillna(0)
        df['용수사용량(ton)'] = pd.to_numeric(df['용수사용량(ton)'], errors='coerce').fillna(0)
        df['도시가스사용량'] = pd.to_numeric(df['도시가스사용량'], errors='coerce').fillna(0)
        df['실내등유사용량'] = pd.to_numeric(df['실내등유사용량'], errors='coerce').fillna(0)
        
        df['온실가스(tCO2eq)'] = (df['전력사용량(kWh)'] * 0.0004594106) + \
                                (df['도시가스사용량'] * 0.002187587) + \
                                (df['실내등유사용량'] * 0.0024652936)
                                
        return df[df['연도'] == int(year)]
    except Exception:
        return empty_df

raw_2026_records = load_db_data(ws_perf)
df_2025 = process_dataframe(load_db_data(ws_2025), 2025)
df_2026 = process_dataframe(raw_2026_records, 2026)

# ---------------- 목표치 딕셔너리 매칭 ----------------
df_target = load_target_data()
target_dict = {}
for hq in HQ_LIST:
    tg_row = df_target[df_target['본부명'].astype(str).str.strip() == hq] if not df_target.empty and '본부명' in df_target.columns else pd.DataFrame()
    if not tg_row.empty:
        target_dict[hq] = {
            'elec': float(tg_row['전력목표_연간'].values[0]) if str(tg_row['전력목표_연간'].values[0]).strip() != '' else 130000,
            'water': float(tg_row['용수목표_연간'].values[0]) if str(tg_row['용수목표_연간'].values[0]).strip() != '' else 2500,
            'ghg': float(tg_row['온실가스목표_연간'].values[0]) if str(tg_row['온실가스목표_연간'].values[0]).strip() != '' else 700
        }
    else:
        target_dict[hq] = {'elec': 130000, 'water': 2500, 'ghg': 700}

# ---------------- 왼쪽 사이드바 (입력 및 다운로드) ----------------
with st.sidebar:
    st.image("https://www.kpetro.or.kr/images/kr/common/logo.png", use_container_width=True)
    st.header("⚙️ 관리자 제어판")
    
    with st.expander("📝 본부별 실적 직접 입력 (비동기 자동합산)", expanded=True):
        with st.form("data_input_form"):
            input_hq = st.selectbox("본부 선택", HQ_LIST)
            input_month_str = st.selectbox("입력 월", [f"{i}월" for i in range(1, 13)], index=9)
            input_month = int(input_month_str.replace("월", ""))
            
            input_elec = st.number_input("⚡ 전력 사용량 (입력안함=0)", min_value=0, step=100)
            input_water = st.number_input("💧 용수 사용량 (입력안함=0)", min_value=0, step=10)
            input_city_gas = st.number_input("🔥 도시가스 사용량 (입력안함=0)", min_value=0, step=10)
            input_kerosene = st.number_input("🛢️ 실내등유 사용량 (입력안함=0)", min_value=0, step=10)
            
            submitted = st.form_submit_button("구글 시트로 전송 🚀")
            
            if submitted:
                with st.spinner('DB 저장 중...'):
                    records = ws_perf.get_all_records()
                    row_idx = None
                    for i, r in enumerate(records):
                        try:
                            if int(str(r.get('연도', 0)).strip() or 0) == 2026 and int(str(r.get('월', 0)).strip() or 0) == input_month and str(r.get('본부명', '')).strip() == input_hq:
                                row_idx = i + 2
                                break
                        except:
                            continue
                    
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    if row_idx:
                        if input_elec > 0: ws_perf.update_cell(row_idx, 4, input_elec)
                        if input_water > 0: ws_perf.update_cell(row_idx, 5, input_water)
                        if input_city_gas > 0: ws_perf.update_cell(row_idx, 6, input_city_gas)
                        if input_kerosene > 0: ws_perf.update_cell(row_idx, 7, input_kerosene)
                        ws_perf.update_cell(row_idx, 8, now_str)
                    else:
                        ws_perf.append_row([2026, input_month, input_hq, input_elec, input_water, input_city_gas, input_kerosene, now_str])
                    
                    st.cache_data.clear()
                    st.success("✅ DB 저장 완료! 화면을 최신화합니다...")
                    time.sleep(1)
                    st.rerun()

    st.divider()
    st.markdown("### ⏳ 현행화 시뮬레이터")
    current_month = st.slider("현재 집계 완료 월:", 1, 12, 10)
    
    st.divider()
    st.markdown("### 📥 데이터 추출")
    if not df_2026.empty:
        csv_data = df_2026.to_csv(index=False).encode('utf-8-sig')
        st.download_button(label="📊 2026년 전체 실적 다운로드", data=csv_data, file_name="KPETRO_2026_실적.csv", mime="text/csv")


# ---------------- 롤링 연간 예측 계산 ----------------
annual_data = []
for hq in HQ_LIST:
    hq_25 = df_2025[df_2025['본부'] == hq] if not df_2025.empty else pd.DataFrame(columns=df_2026.columns)
    hq_26 = df_2026[df_2026['본부'] == hq] if not df_2026.empty else pd.DataFrame(columns=df_2026.columns)
    
    for metric, unit, target_yr in [('온실가스(tCO2eq)', 'ton', target_dict[hq]['ghg']), 
                                    ('전력사용량(kWh)', 'kWh', target_dict[hq]['elec']), 
                                    ('용수사용량(ton)', 'ton', target_dict[hq]['water'])]:
        last_ytd = hq_25[hq_25['월'] <= current_month][metric].sum() if not hq_25.empty and metric in hq_25.columns else 0
        last_ytg = hq_25[hq_25['월'] > current_month][metric].sum() if not hq_25.empty and metric in hq_25.columns else 0
        current_ytd = hq_26[hq_26['월'] <= current_month][metric].sum() if not hq_26.empty and metric in hq_26.columns else 0
        
        trend_rate = current_ytd / last_ytd if last_ytd > 0 else 1
        expected_ytg = last_ytg * trend_rate
        predicted_total = current_ytd + expected_ytg
        
        annual_data.append({
            '본부': hq, '지표': metric.split('(')[0], '단위': unit, '연간목표': target_yr,
            f'올해현재(1~{current_month}월)': current_ytd, f'작년동기(1~{current_month}월)': last_ytd,
            f'작년잔여({current_month+1}~12월)': last_ytg, '증감률(%)': (trend_rate - 1) * 100 if current_ytd > 0 else 0,
            '연말예상치': predicted_total
        })
annual_data_df = pd.DataFrame(annual_data)


# ---------------- 대시보드 메인 화면 ----------------
st.title("🌿 한국석유관리원 BEMS")

monthly_df = df_2026[df_2026['월'] == current_month] if not df_2026.empty else pd.DataFrame()

total_monthly_target_ghg = sum([target_dict[hq]['ghg'] for hq in HQ_LIST]) / 12
total_monthly_actual_ghg = monthly_df['온실가스(tCO2eq)'].sum() if not monthly_df.empty and '온실가스(tCO2eq)' in monthly_df.columns else 0
ghg_savings_ton = total_monthly_target_ghg - total_monthly_actual_ghg

if not monthly_df.empty:
    if ghg_savings_ton >= 0:
        pine_trees = int((ghg_savings_ton * 1000) / 6.6)
        st.success(f"🎉 **{current_month}월 전사 친환경 성과:** 목표 대비 온실가스를 **{ghg_savings_ton:,.1f} ton** 절감했습니다. 이는 **30년생 소나무 {pine_trees:,}그루**를 심은 것과 같은 훌륭한 성과입니다! 🌲")
    else:
        st.warning(f"⚠️ **{current_month}월 전사 친환경 알림:** 목표 대비 온실가스 배출량이 **{abs(ghg_savings_ton):,.1f} ton** 초과되었습니다.")

def create_gauge(current, target, title, height=280):
    fig = go.Figure(go.Indicator(
        mode = "gauge+number+delta", 
        value = current, 
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': title, 'font': {'size': 18}},
        delta = {'reference': target, 'increasing': {'color': "red"}, 'decreasing': {'color': "green"}},
        gauge = {
            'axis': {'range': [None, target * 1.2] if target > 0 else [0, 100]},
            'bar': {'color': "#1f77b4" if target == 0 or (current / target) < 0.9 else "#d62728"},
            'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': target}
        }
    ))
    fig.update_layout(height=height, margin=dict(l=20, r=20, t=40, b=20))
    return fig

st.subheader(f"🏢 전사 통합 목표 대비 사용률 ({current_month}월 단일)")
top_col1, top_col2, top_col3 = st.columns(3)

total_tg_elec = sum([target_dict[hq]['elec'] for hq in HQ_LIST]) / 12
total_tg_water = sum([target_dict[hq]['water'] for hq in HQ_LIST]) / 12

with top_col1: st.plotly_chart(create_gauge(total_monthly_actual_ghg, total_monthly_target_ghg, "전사 온실가스(tCO2eq) 월간목표", height=320), use_container_width=True, key="top_gauge_ghg")
with top_col2: st.plotly_chart(create_gauge(monthly_df['전력사용량(kWh)'].sum() if not monthly_df.empty and '전력사용량(kWh)' in monthly_df.columns else 0, total_tg_elec, "전사 전력(kWh) 월간목표", height=320), use_container_width=True, key="top_gauge_elec")
with top_col3: st.plotly_chart(create_gauge(monthly_df['용수사용량(ton)'].sum() if not monthly_df.empty and '용수사용량(ton)' in monthly_df.columns else 0, total_tg_water, "전사 용수(ton) 월간목표", height=320), use_container_width=True, key="top_gauge_water")

st.markdown("---")
st.subheader("📍 본부별 세부 현황 및 연간 실적 예측")
tab1, tab2, tab3, tab4 = st.tabs(["🌍 온실가스", "⚡ 전력", "💧 용수", "🎯 연간 실적 예측 (현행화 반영)"])

for tab, metric_col, tg_key in zip([tab1, tab2, tab3], ['온실가스(tCO2eq)', '전력사용량(kWh)', '용수사용량(ton)'], ['ghg', 'elec', 'water']):
    with tab:
        cols = st.columns(3)
        for i, hq in enumerate(HQ_LIST):
            val = monthly_df[monthly_df['본부'] == hq][metric_col].sum() if not monthly_df.empty and metric_col in monthly_df.columns else 0
            tg_val = target_dict[hq][tg_key] / 12 
            with cols[i % 3]: st.plotly_chart(create_gauge(val, tg_val, f"[{hq}]"), use_container_width=True, key=f"gauge_{metric_col}_{hq}")

with tab4:
    st.markdown("##### 📈 2026년 연말 예상 실적 (과거 데이터 추세 반영)")
    selected_metric = st.selectbox("분석할 지표를 선택하세요:", ["온실가스", "전력사용량", "용수사용량"], key="forecast_selectbox")
    
    chart_df = annual_data_df[annual_data_df['지표'] == selected_metric] if not annual_data_df.empty and '지표' in annual_data_df.columns else pd.DataFrame()
    
    if not chart_df.empty:
        fig_bar = go.Figure()
        fig_bar.add_tabs = [...] # placeholder
        fig_bar.add_trace(go.Bar(x=chart_df['본부'], y=chart_df['연간목표'], name='연간 목표량', marker_color='#1f77b4'))
        colors = ['#d62728' if y > t else '#ff7f0e' for y, t in zip(chart_df['연말예상치'], chart_df['연간목표'])]
        fig_bar.add_trace(go.Bar(x=chart_df['본부'], y=chart_df['연말예상치'], name='현행화된 연말 예상치', marker_color=colors))
        fig_bar.update_layout(barmode='group', title=f"본부별 연간 예상 실적 비교 (기준: {current_month}월)", height=400)
        st.plotly_chart(fig_bar, use_container_width=True, key="annual_forecast_barchart")

# ---------------- 🔧 실시간 데이터 연동 진단 패널 ----------------
with st.expander("🔍 [디버그] 구글 시트 연동 상태 실시간 확인", expanded=True):
    st.write(f"**1. 구글 시트(`월간실적`)에서 불러온 원본 레코드 개수:** {len(raw_2026_records)}개")
    st.write(f"**2. 2026년 연도 필터링 후 데이터 개수 (`df_2026`):** {len(df_2026)}개")
    if not df_2026.empty:
        st.dataframe(df_2026)
    else:
        st.warning("⚠️ 2026년 데이터가 필터링되지 않았거나 비어 있습니다. 구글 시트의 '연도' 컬럼 값(2026)이나 시트 이름을 확인해 주세요.")