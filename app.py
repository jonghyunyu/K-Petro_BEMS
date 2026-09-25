import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# -----------------------------------------------------------------------------
# 1. 페이지 및 기본 설정
# -----------------------------------------------------------------------------
st.set_page_config(page_title="K-PETRO BEMS 통합 모니터링", page_icon="📊", layout="wide")

# -----------------------------------------------------------------------------
# 2. 구글 시트 API 연결 세팅
# -----------------------------------------------------------------------------
@st.cache_resource
def init_connection():
    scope = ['https://www.googleapis.com/auth/spreadsheets']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    return client

client = init_connection()
sheet_id = "1Ky6Brrh5pWXuDvAXV36SSQ2MBAjtxr__UbY8fU3viBY" # 구글 시트 고유 ID (권한 에러 방지용)

# -----------------------------------------------------------------------------
# 3. 데이터 클렌징 함수 (TypeError 원천 차단)
# -----------------------------------------------------------------------------
def clean_numeric_data(df):
    """엑셀에서 가져온 문자열 데이터(쉼표 포함 등)를 계산 가능한 숫자로 강제 변환합니다."""
    for col in df.columns:
        # 텍스트로 유지해야 하는 컬럼(월, 구분, 지사, 본부 등)은 제외
        if col not in ['월', '구분', '날짜', '지사', '본부', '항목']: 
            # 쉼표 제거 후 숫자로 강제 변환, 변환 불가 시 NaN 처리 후 0으로 채움
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    return df

# -----------------------------------------------------------------------------
# 4. 데이터 로드 및 전처리 적용
# -----------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_data():
    doc = client.open_by_key(sheet_id)
    
    # 탭별 데이터 로드 및 숫자형 강제 변환 적용
    try:
        df_target = clean_numeric_data(pd.DataFrame(doc.worksheet("목표치관리").get_all_records()))
    except:
        df_target = pd.DataFrame()
        
    try:
        df_2025 = clean_numeric_data(pd.DataFrame(doc.worksheet("2025실적").get_all_records()))
    except:
        df_2025 = pd.DataFrame()
        
    try:
        df_actual = clean_numeric_data(pd.DataFrame(doc.worksheet("월간실적").get_all_records()))
    except:
        df_actual = pd.DataFrame()
        
    return doc, df_target, df_2025, df_actual

doc, df_target, df_2025, df_actual = load_data()

# 온실가스 배출량 연산 (전력사용량 * 0.4781 / 1000) - 에러 발생했던 구간 수정 완료
if not df_actual.empty and '전력사용량' in df_actual.columns:
    df_actual['온실가스'] = df_actual['전력사용량'] * 0.4781 / 1000

# -----------------------------------------------------------------------------
# 5. UI 대시보드 구성 (자연수 표기 적용)
# -----------------------------------------------------------------------------
st.title("📊 K-PETRO BEMS 통합 모니터링")
st.markdown("---")

# 시뮬레이션용 월 선택 필터
selected_month = st.sidebar.selectbox("데이터 조회 월 선택", [f"{i}월" for i in range(1, 13)], index=8) # 9월 기본값

if not df_actual.empty:
    # 1️⃣ 종합 실적 요약 (자연수 표기 처리)
    st.subheader("💡 K-PETRO 종합 실적 (누계)")
    
    # 전체 합계 계산 (데이터가 없을 경우 0 처리)
    total_elec = df_actual['전력사용량'].sum() if '전력사용량' in df_actual.columns else 0
    total_ghg = df_actual['온실가스'].sum() if '온실가스' in df_actual.columns else 0
    total_water = df_actual['용수'].sum() if '용수' in df_actual.columns else 0
    
    # 소나무 상쇄 효과 연산 (온실가스 1톤당 약 139.6그루 가정)
    pine_tree_offset = total_ghg * 139.6 
    
    col1, col2, col3, col4 = st.columns(4)
    # int()를 씌워 소수점을 모두 버리고 자연수로 포맷팅
    col1.metric("총 전력사용량", f"{int(total_elec):,} kWh")
    col2.metric("총 온실가스 배출량", f"{int(total_ghg):,} tCO2eq")
    col3.metric("총 용수 사용량", f"{int(total_water):,} ton")
    col4.metric("🌲 소나무 상쇄 효과", f"{int(pine_tree_offset):,} 그루")

    st.markdown("---")

    # 2️⃣ 각 본부별 세부 실적 (자연수 표기 처리)
    st.subheader("🏢 각 본부별 세부 실적")
    
    # 본부별 그룹화 (본부 컬럼이 '본부' 또는 '지사'로 되어있는지 엑셀 명칭에 맞게 수정 필요)
    group_col = '본부' if '본부' in df_actual.columns else '지사' if '지사' in df_actual.columns else None
    
    if group_col:
        df_grouped = df_actual.groupby(group_col).sum(numeric_only=True).reset_index()
        
        # 화면 출력을 위한 자연수 변환 및 단위 추가
        df_display = df_grouped.copy()
        
        if '전력사용량' in df_display.columns:
            df_display['전력사용량 (kWh)'] = df_display['전력사용량'].apply(lambda x: f"{int(x):,}")
        if '온실가스' in df_display.columns:
            df_display['온실가스 (tCO2eq)'] = df_display['온실가스'].apply(lambda x: f"{int(x):,}")
        if '용수' in df_display.columns:
            df_display['용수 (ton)'] = df_display['용수'].apply(lambda x: f"{int(x):,}")
            
        # 기존 숫자형 컬럼 숨기고 포맷팅된 컬럼만 출력
        cols_to_show = [group_col] + [c for c in df_display.columns if '(' in c]
        st.dataframe(df_display[cols_to_show], use_container_width=True, hide_index=True)
    else:
        st.info("엑셀 파일에 '본부' 또는 '지사' 컬럼이 존재하지 않아 본부별 통계를 출력할 수 없습니다.")
else:
    st.warning("월간실적 탭에 데이터가 없습니다. 구글 시트를 확인해 주세요.")