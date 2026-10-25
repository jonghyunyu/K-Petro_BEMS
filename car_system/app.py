import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import urllib.parse
import os
import json

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

st.set_page_config(
    page_title="차량 QR 이력·상태 관리 시스템",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded"
)

EXCEL_FILE = "car_data.xlsx"

# 구글 시트 연결 함수
def get_gspread_client():
    if not GSPREAD_AVAILABLE:
        return None
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            client = gspread.authorize(credentials)
            return client
    except Exception as e:
        st.sidebar.error(f"구글 인증 실패: {e}")
    return None

def load_data_from_google_sheets():
    client = get_gspread_client()
    if not client:
        return None, None
    try:
        if "spreadsheet_url" in st.secrets:
            sheet_url = st.secrets["spreadsheet_url"]
            spreadsheet = client.open_by_url(sheet_url)
            
            d_cars = pd.DataFrame(spreadsheet.worksheet("1_차량기본정보").get_all_records())
            d_rules = pd.DataFrame(spreadsheet.worksheet("2_교체주기기준").get_all_records())
            d_cons = pd.DataFrame(spreadsheet.worksheet("3_소모품이력").get_all_records())
            d_reps = pd.DataFrame(spreadsheet.worksheet("4_정비수리이력").get_all_records())
            d_fbs = pd.DataFrame(spreadsheet.worksheet("5_특이사항").get_all_records())
            d_man = pd.DataFrame(spreadsheet.worksheet("6_사고대처매뉴얼").get_all_records())
            
            return (d_cars, d_rules, d_cons, d_reps, d_fbs, d_man), "🟢 구글 스프레드시트 실시간 연동중"
    except Exception as e:
        return None, f"⚠️ 구글 시트 연동 오류 (로컬모드 전환): {e}"
    return None, None

def get_default_dataframes():
    df_cars = pd.DataFrame([
        {"차량명": "본사-1", "차종": "아이오닉 6 (전기차)", "차량정보": "88하 1024", "소속본부": "본사 (지원처)", "용도구분": "일반 업무용", "연료": "전기(EV)", "담당자_연락처": "본사 지원처 (내선 0000)", "최근 주행거리": 24150, "정기검사 만료일": "2027-03-01", "보험만료일": "2027-02-15"},
    ])
    df_rules = pd.DataFrame([
        {"항목": "엔진오일", "권장주기_km": 10000, "권장주기_개월": 12},
    ])
    df_cons = pd.DataFrame([
        {"차량명": "본사-1", "항목": "엔진오일", "교체주기(Km)": 10000, "교체주기(개월)": 12, "마지막 교체일": "2026-01-15", "마지막 교체 주행거리": 14000},
    ])
    df_reps = pd.DataFrame([
        {"차량명": "본사-1", "일자": "2026-07-20", "주행거리(Km)": 14000, "정비구분": "소모품", "내용": "엔진오일 교체", "정비소": "블루핸즈"},
    ])
    df_fbs = pd.DataFrame([
        {"차량명": "본사-1", "날짜": "2026-10-22", "작성자": "김주임", "확인일자": "2026-10-22", "분류": "기타", "내용": "점검 완료", "처리상태": "🟢 조치완료"},
    ])
    df_man = pd.DataFrame([
        {"순서": 1, "단계명": "1단계: 안전 확보", "행동요령": "비상등 켜고 안전지대로 대피합니다."},
    ])
    return df_cars, df_rules, df_cons, df_reps, df_fbs, df_man

def save_data_to_google_sheets(df_cars, df_rules, df_cons, df_reps, df_fbs, df_man):
    client = get_gspread_client()
    if not client or "spreadsheet_url" not in st.secrets:
        return False
    try:
        sheet_url = st.secrets["spreadsheet_url"]
        spreadsheet = client.open_by_url(sheet_url)
        
        def update_ws(ws_name, df):
            ws = spreadsheet.worksheet(ws_name)
            ws.clear()
            df_str = df.astype(str)
            ws.update([df_str.columns.values.tolist()] + df_str.values.tolist())

        update_ws("1_차량기본정보", df_cars)
        update_ws("2_교체주기기준", df_rules)
        update_ws("3_소모품이력", df_cons)
        update_ws("4_정비수리이력", df_reps)
        update_ws("5_특이사항", df_fbs)
        update_ws("6_사고대처매뉴얼", df_man)
        return True
    except Exception as e:
        st.error(f"구글 시트 저장 실패: {e}")
        return False

# 데이터 로드 실행
gs_data, db_status_msg = load_data_options = load_data_from_google_sheets()
if gs_data is not None:
    df_cars, df_rules, df_cons, df_reps, df_fbs, df_man = gs_data
else:
    # 구글 연동 실패 또는 로컬 모드
    if not os.path.exists(EXCEL_FILE):
        d_cars, d_rules, d_cons, d_reps, d_fbs, d_man = get_default_dataframes()
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
            for s_name, df in {"1_차량기본정보": d_cars, "2_교체주기기준": d_rules, "3_소모품이력": d_cons, "4_정비수리이력": d_reps, "5_특이사항": d_fbs, "6_사고대처매뉴얼": d_man}.items():
                df.to_excel(writer, sheet_name=s_name, index=False)
    xls = pd.ExcelFile(EXCEL_FILE)
    df_cars = pd.read_excel(xls, "1_차량기본정보")
    df_rules = pd.read_excel(xls, "2_교체주기기준")
    df_cons = pd.read_excel(xls, "3_소모품이력")
    df_reps = pd.read_excel(xls, "4_정비수리이력")
    df_fbs = pd.read_excel(xls, "5_특이사항")
    df_man = pd.read_excel(xls, "6_사고대처매뉴얼")
    db_status_msg = "💻 로컬 연동모드 (엑셀)"

def save_all_data(df_cars, df_rules, df_cons, df_reps, df_fbs, df_man):
    if "gcp_service_account" in st.secrets:
        success = save_data_to_google_sheets(df_cars, df_rules, df_cons, df_reps, df_fbs, df_man)
        if success:
            return
    sheets_dict = {
        "1_차량기본정보": df_cars, "2_교체주기기준": df_rules, "3_소모품이력": df_cons,
        "4_정비수리이력": df_reps, "5_특이사항": df_fbs, "6_사고대처매뉴얼": df_man,
    }
    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
        for s_name, df in sheets_dict.items():
            df.to_excel(writer, sheet_name=s_name, index=False)

# UI 디자인 및 스타일
st.markdown("""
<style>
    .car-header {
        background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
        color: white; padding: 22px; border-radius: 12px; margin-bottom: 16px;
    }
    .popup-alert-box {
        background-color: #fff3cd; border: 1px solid #ffeeba; border-left: 5px solid #dc3545; padding: 12px; border-radius: 6px; margin-bottom: 10px;
    }
</style>
""", unsafe_allow_html=True)

if "last_popup_car" not in st.session_state:
    st.session_state.last_popup_car = None

query_car = st.query_params.get("car", None)

@st.dialog("🚨 운행 전 차량 주의사항 알림")
def show_vehicle_alert_popup(car_name, car_row, overdue_df, open_fb_df, insp_alert, ins_alert):
    st.markdown(f"### 🚘 {car_name} — {car_row['차종']}")
    st.caption(f"차량정보: {car_row['차량정보']} | 소속: {car_row['소속본부']}")
    if insp_alert:
        st.markdown(f"""<div class="popup-alert-box"><b>🔴 법정 정기검사 만료 임박!</b><br>• 만료일: <b>{str(car_row['정기검사 만료일'])[:10]}</b> ({insp_alert})</div>""", unsafe_allow_html=True)
    if ins_alert:
        st.markdown(f"""<div class="popup-alert-box"><b>🔴 차량 보험 갱신 만료 임박!</b><br>• 만료일: <b>{str(car_row['보험만료일'])[:10]}</b> ({ins_alert})</div>""", unsafe_allow_html=True)
    if not overdue_df.empty:
        st.markdown("#### 🔧 소모품 교체주기 도래/경과 항목")
        for _, item in overdue_df.iterrows():
            st.markdown(f"""<div class="popup-alert-box"><b>{item['상태']} - {item['항목']}</b></div>""", unsafe_allow_html=True)
    if not open_fb_df.empty:
        st.markdown("#### 💬 이전 탑승자 접수 특이사항 (미조치)")
        for _, fb in open_fb_df.iterrows():
            st.warning(f"**[{fb['분류']}]** {fb['내용']}\n\n*(작성: {str(fb['날짜'])[:10]} {fb['작성자']} / 확인일자: {str(fb['확인일자'])[:10]})*")
    if st.button("✅ 내용 확인 완료", type="primary", use_container_width=True):
        st.rerun()

with st.sidebar:
    st.title("🚘 차량 QR 관리 시스템")
    st.caption(db_status_msg)
    if st.button("🔄 데이터 새로고침", use_container_width=True):
        st.session_state.last_popup_car = None
        st.rerun()
    st.divider()
    view_mode = st.radio("🖥️ 화면 모드 선택", ["📱 모바일 QR 화면 (현장)", "📊 관리자 대시보드 (보고용)"], index=0)
    st.divider()
    if view_mode == "📱 모바일 QR 화면 (현장)":
        st.subheader("🔍 QR 스캔 차량 선택 (2단계)")
        branch_options = sorted(df_cars["소속본부"].dropna().unique().tolist())
        selected_branch = st.selectbox("1단계: 소속 본부 선택", branch_options)
        filtered_df = df_cars[df_cars["소속본부"] == selected_branch]
        car_name_options = filtered_df["차량명"].astype(str).tolist()
        if car_name_options:
            default_idx = 0
            if query_car in car_name_options:
                default_idx = car_name_options.index(query_car)
            selected_car_name = st.selectbox("2단계: 차량 선택", car_name_options, index=default_idx, format_func=lambda x: f"{x} | {filtered_df[filtered_df['차량명']==x]['차종'].values[0]}")
        else:
            st.warning("해당 본부에 등록된 차량이 없습니다.")
            selected_car_name = None
    else:
        st.subheader("🏢 전사 본부 필터")
        branch_list = ["전체 보기"] + sorted(df_cars["소속본부"].dropna().unique().tolist())
        selected_branch = st.selectbox("조회 대상 본부", branch_list)

if view_mode == "📱 모바일 QR 화면 (현장)":
    if selected_car_name:
        car_row = df_cars[df_cars["차량명"] == selected_car_name].iloc[0]
        current_km = int(float(car_row["최근 주행거리"])) if str(car_row["최근 주행거리"]).strip() != "" else 0
        st.markdown(f"""<div class="car-header"><h2>🚘 {selected_car_name} &nbsp;<span style="font-size:0.75em; font-weight:400;">({car_row['차종']})</span></h2></div>""", unsafe_allow_html=True)
        
        tab1, tab2, tab3 = st.tabs(["🛠 정비·소모품 이력", f"💬 특이사항", "🚨 사고절차"])
        with tab1:
            st.markdown("#### 📜 주요 정비 및 수리 이력")
            df_car_reps = df_reps[df_reps["차량명"] == selected_car_name]
            if df_car_reps.empty:
                st.info("등록된 수리 이력이 없습니다.")
            else:
                for _, rep in df_car_reps.iterrows():
                    st.markdown(f"**[{rep['정비구분']}] {rep['내용']}** (일자: {rep['일자']})")
        with tab2:
            st.markdown("#### ✍️ 운행 중 특이사항 10초 등록")
            with st.form("feedback_form", clear_on_submit=True):
                writer = st.text_input("작성자")
                memo = st.text_input("특이사항 메모")
                if st.form_submit_button("📢 등록하기", use_container_width=True):
                    if writer and memo:
                        new_fb = pd.DataFrame([{"차량명": selected_car_name, "날짜": datetime.now().strftime("%Y-%m-%d"), "작성자": writer, "확인일자": datetime.now().strftime("%Y-%m-%d"), "분류": "기타", "내용": memo, "처리상태": "🔴 확인중"}])
                        df_fbs = pd.concat([new_fb, df_fbs], ignore_index=True)
                        save_all_data(df_cars, df_rules, df_cons, df_reps, df_fbs, df_man)
                        st.success("등록 완료!")
                        st.rerun()
        with tab3:
            st.error(f"🚨 사고 발생 시 행동 매뉴얼 (담당: {car_row['담당자_연락처']})")
            for _, step in df_man.sort_values("순서").iterrows():
                st.markdown(f"**{step['단계명']}**: {step['행동요령']}")
else:
    st.title("📊 전사 차량 QR 이력·상태 통합 관제 대시보드")
    admin_tab1, admin_tab2, admin_tab5 = st.tabs(["🚘 차량별 총괄표", "📥 수리이력 등록", "🖨️ QR 스티커 발급"])
    with admin_tab1:
        st.dataframe(df_cars, use_container_width=True, hide_index=True)
    with admin_tab2:
        st.subheader("수리 이력 등록")
        with st.form("admin_rep"):
            t_car = st.selectbox("차량 선택", df_cars["차량명"].tolist())
            t_desc = st.text_input("수리 내용")
            if st.form_submit_button("저장"):
                new_r = pd.DataFrame([{"차량명": t_car, "일자": datetime.now().strftime("%Y-%m-%d"), "주행거리(Km)": 0, "정비구분": "일반", "내용": t_desc, "정비소": "공업사"}])
                df_reps = pd.concat([new_r, df_reps], ignore_index=True)
                save_all_data(df_cars, df_rules, df_cons, df_reps, df_fbs, df_man)
                st.success("저장 완료!")
                st.rerun()
    with admin_tab5:
        st.subheader("🖨️ 차량 부착용 QR 스티커 시안")
        for _, c_row in df_cars.iterrows():
            st.write(f"**{c_row['차량명']}** ({c_row['차종']})")
            target_url = f"https://share.streamlit.io/?car={urllib.parse.quote(str(c_row['차량명']))}"
            st.image(f"https://api.qrserver.com/v1/create-qr-code/?size=120x120&data={urllib.parse.quote(target_url)}", width=120)