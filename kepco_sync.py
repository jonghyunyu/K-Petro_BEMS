"""
K-REMS 한전 전력사용량 일일 자동 수집기
------------------------------------------------------------
app.py 는 건드리지 않습니다. GitHub Actions(kepco_daily.yml)가 매일 따로 실행합니다.

하는 일
  1) 한전 OPM Open-API(getDayLpData)로 11개 본부 고객번호의 하루 사용량(15분 x 96개)을 받아 합산
  2) 구글시트 '일간전력' 탭에 날짜·본부별로 한 줄씩 기록 (같은 날짜·본부는 덮어씀)
  3) 기관의 고지서 입력 방식과 같은 기간(본부별 검침일 기준)으로 합산해 '월간실적' 탭의
     (연도, 월, 본부명) 행 '전력사용량' 칸에 기록.
     사용 기간이 정보제공 동의일(KEPCO_START) 이후에 시작하는 달만 자동 기록하고,
     그 이전 달은 고지서 수기 값을 건드리지 않음
     -> app.py 가 읽는 값이 바뀌므로 반원 그래프와 전력 온실가스가 자동 갱신

필요한 GitHub Secrets
  KEPCO_API_KEY        OPM 'Open API 인증키 목록'의 인증키(서비스키)
  KEPCO_SITES          {"본사·수도권남부": ["0227437841"], ...}  본부명 -> 고객번호 목록
  KEPCO_METER_DAYS     {"본사·수도권남부": [1, 0], "수도권북부": [5, 1], ...}
                       본부별 [검침 시작일, 월보정]. 월보정 1 = 시작한 달의 다음 달 실적으로 부름
                       예) 수도권북부 [5, 1]: 8/5~9/4 사용량 = '9월' 실적
  GCP_SERVICE_ACCOUNT  구글 서비스계정 키 (JSON 그대로, 또는 Streamlit secrets.toml 내용 그대로)

선택 설정 (GitHub Variables 또는 환경변수)
  KEPCO_START          이 날짜 이전은 조회하지 않음 (정보제공 동의일)       기본 2026-10-30
  MONTHLY_FROM         (선택) 이 '월' 이전은 자동 기록하지 않음                         기본 2026-01
  KEPCO_MOCK=1         한전 대신 가짜 숫자로 시험

실행 예
  python kepco_sync.py                  # 최근 3일 다시 수집 (한전 정정분 반영)
  python kepco_sync.py --date 2026-10-30
  python kepco_sync.py --date 2026-10-30 --dry-run   # 시트에 쓰지 않고 결과만 출력
"""

import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1Ky6Brrh5pWXuDvAXV36SSQ2MBAjtxr__UbY8fU3viBY"
MONTHLY_TAB = "월간실적"
DAILY_TAB = "일간전력"
DAILY_HEADER = ["날짜", "본부명", "전력사용량", "온실가스(tCO2eq)", "15분값개수", "수집시간"]

ELEC_FACTOR = 0.0004594106  # app.py 와 같은 전력 배출계수 (tCO2eq/kWh)
KST = timezone(timedelta(hours=9))

OPM_URL = "https://opm.kepco.co.kr:11080/OpenAPI/getDayLpData.do"
# pwr_qty0015, 0030, 0045, 0100, ... , 2345, 2400 (96개, 단위 kWh)
LP_KEYS = [f"pwr_qty{(i*15)//60:02d}{(i*15)%60:02d}" for i in range(1, 97)]


def _num(v):
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s not in ("", "None", "null", "-") else None
    except ValueError:
        return None


def _month_num(v) -> int:
    try:
        return int(str(v).replace("월", "").strip())
    except ValueError:
        return 0


def _year_num(v) -> int:
    try:
        return int(str(v).strip())
    except ValueError:
        return 0


# -----------------------------------------------------------------------------
# 한전 OPM
# -----------------------------------------------------------------------------
def fetch_day(customer_no: str, day: date):
    """고객번호 1개의 하루 사용량. (합계 kWh, 받은 15분 값 개수)"""
    if os.environ.get("KEPCO_MOCK") == "1":
        return round(random.uniform(1500, 6000), 1), 96

    import requests
    resp = requests.get(OPM_URL, params={
        "custNo": customer_no,
        "date": day.strftime("%Y%m%d"),
        "serviceKey": os.environ["KEPCO_API_KEY"].strip(),
        "returnType": "02",  # JSON
    }, timeout=60)
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"JSON 아님: {resp.text[:200]}")

    header = body.get("cmmMsgHeader") or {}
    code = str(header.get("returnCode", "00"))
    if header and code not in ("00", "0"):
        raise RuntimeError(f"한전 응답 {code}: {header.get('errMsg')}")

    rows = body.get("dayLpDataInfoList") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        raise RuntimeError("데이터 없음 (아직 미집계이거나 정보제공 동의 전 날짜)")

    total, count = 0.0, 0
    for r in rows:  # 다계기(계량기 여러 개)면 여러 줄 -> 모두 합산
        vals = [_num(r.get(k)) for k in LP_KEYS]
        vals = [v for v in vals if v is not None]
        total += sum(vals)
        count = max(count, len(vals))
    return round(total, 2), count


# -----------------------------------------------------------------------------
# 구글 시트
# -----------------------------------------------------------------------------
def load_service_account():
    raw = os.environ["GCP_SERVICE_ACCOUNT"].strip()
    if raw.startswith("{"):
        return json.loads(raw)
    import tomllib  # Streamlit secrets.toml 내용을 그대로 붙여넣은 경우
    data = tomllib.loads(raw)
    return data.get("gcp_service_account", data)


def get_doc():
    creds = Credentials.from_service_account_info(load_service_account(), scopes=[
        "https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds).open_by_key(SHEET_ID)


def get_or_create_daily_ws(doc):
    try:
        return doc.worksheet(DAILY_TAB)
    except gspread.WorksheetNotFound:
        ws = doc.add_worksheet(title=DAILY_TAB, rows=5000, cols=len(DAILY_HEADER))
        ws.append_row(DAILY_HEADER)
        return ws


def upsert_daily(ws, day: date, values: dict, now_str: str):
    rows = ws.get_all_values()
    index = {(r[0], r[1]): i + 1 for i, r in enumerate(rows) if len(r) >= 2}
    updates, appends = [], []
    for hq, (kwh, n) in values.items():
        row = [day.isoformat(), hq, kwh, round(kwh * ELEC_FACTOR, 4), n, now_str]
        r = index.get((day.isoformat(), hq))
        if r:
            updates.append({"range": f"A{r}:F{r}", "values": [row]})
        else:
            appends.append(row)
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    if appends:
        ws.append_rows(appends, value_input_option="USER_ENTERED")


def _meter(meter_days: dict, hq: str):
    """본부 설정 -> (검침 시작일, 월보정). 예) [5, 1] = 5일부터, 시작 월 +1 을 실적 월로 부름"""
    v = meter_days.get(hq, 1)
    if isinstance(v, (list, tuple)):
        return int(v[0]), int(v[1]) if len(v) > 1 else 0
    return int(v), 0


def _clamp(year: int, month: int, day: int) -> date:
    import calendar
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift(year: int, month: int, n: int):
    k = year * 12 + (month - 1) + n
    return k // 12, k % 12 + 1


def bill_month(meter_days: dict, hq: str, d: date):
    """그 날짜가 속하는 실적 '월' (기관 고지서 입력 방식과 동일)
    예) 수도권북부 [5, 1]: 8/5~9/4 -> 9월 실적 / 본사 [1, 0]: 9/1~9/30 -> 9월 실적"""
    day, off = _meter(meter_days, hq)
    sy, sm = (d.year, d.month) if d >= _clamp(d.year, d.month, day) else _shift(d.year, d.month, -1)
    return _shift(sy, sm, off)


def period(meter_days: dict, hq: str, year: int, month: int):
    """실적 (year, month) 의 사용 기간 (시작일, 종료일)"""
    day, off = _meter(meter_days, hq)
    sy, sm = _shift(year, month, -off)
    ny, nm = _shift(sy, sm, 1)
    return _clamp(sy, sm, day), _clamp(ny, nm, day) - timedelta(days=1)


def month_totals(ws, year: int, month: int, meter_days: dict) -> dict:
    """일간전력 탭에서 본부별로 고지서 방식 (year, month) 기간의 kWh 합계"""
    totals = {}
    for r in ws.get_all_values()[1:]:
        if len(r) < 3:
            continue
        try:
            d = date.fromisoformat(r[0])
        except ValueError:
            continue
        if bill_month(meter_days, r[1], d) == (year, month):
            totals[r[1]] = totals.get(r[1], 0.0) + (_num(r[2]) or 0.0)
    return totals


def write_monthly(ws, year: int, month: int, totals: dict, now_str: str):
    """월간실적의 (연도, 월, 본부명) 첫 행 '전력사용량'을 월 누계로 덮어씀. 행이 없으면 추가."""
    rows = ws.get_all_values()
    header = rows[0]
    c_year, c_month, c_hq = header.index("연도"), header.index("월"), header.index("본부명")
    c_elec = header.index("전력사용량")
    c_time = header.index("입력시간") if "입력시간" in header else None
    first_year = next((_year_num(r[c_year]) for r in rows[1:] if _year_num(r[c_year])), year)

    targets = {}
    for i, r in enumerate(rows[1:], start=2):
        y = _year_num(r[c_year]) or first_year
        if y == year and _month_num(r[c_month]) == month and r[c_hq].strip():
            targets.setdefault(r[c_hq].strip(), i)

    col = lambda c: gspread.utils.rowcol_to_a1(1, c + 1).rstrip("1")
    updates, appends = [], []
    for hq, kwh in totals.items():
        r = targets.get(hq)
        if r:
            updates.append({"range": f"{col(c_elec)}{r}", "values": [[round(kwh, 1)]]})
            if c_time is not None:
                updates.append({"range": f"{col(c_time)}{r}", "values": [[now_str]]})
        else:
            new = [""] * len(header)
            new[c_year], new[c_month], new[c_hq], new[c_elec] = year, month, hq, round(kwh, 1)
            if c_time is not None:
                new[c_time] = now_str
            appends.append(new)
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    if appends:
        ws.append_rows(appends, value_input_option="USER_ENTERED")


# -----------------------------------------------------------------------------
# 실행
# -----------------------------------------------------------------------------
def collect(day: date, sites: dict):
    result, failed = {}, 0
    for hq, customers in sites.items():
        try:
            parts = [fetch_day(c, day) for c in customers]
            kwh = round(sum(p[0] for p in parts), 1)
            n = min(p[1] for p in parts)
            result[hq] = (kwh, n)
            warn = "" if n == 96 else f"  (15분 값 {n}/96개: 아직 덜 집계됨)"
            print(f"  - {hq}: {kwh:,.1f} kWh{warn}")
        except Exception as e:  # 한 본부 실패가 전체를 멈추지 않도록
            failed += 1
            print(f"  ! {hq}: 실패 - {e}")
    return result, failed


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="수집할 날짜 YYYY-MM-DD")
    p.add_argument("--days", type=int, default=3, help="어제부터 거슬러 며칠을 다시 수집할지 (기본 3)")
    p.add_argument("--dry-run", action="store_true", help="시트에 쓰지 않고 결과만 출력")
    args = p.parse_args()

    start = date.fromisoformat(os.environ.get("KEPCO_START") or "2026-10-30")
    monthly_from = os.environ.get("MONTHLY_FROM") or "2026-01"
    mf_y, mf_m = map(int, monthly_from.split("-"))

    today = datetime.now(KST).date()
    days = [date.fromisoformat(args.date)] if args.date else \
           [today - timedelta(days=i) for i in range(args.days, 0, -1)]
    days = [d for d in days if d >= start]
    if not days:
        print(f"조회할 날짜가 없습니다 (정보제공 동의일 {start} 이후만 조회 가능)")
        return

    sites = json.loads(os.environ["KEPCO_SITES"])
    meter_days = json.loads(os.environ.get("KEPCO_METER_DAYS") or "{}")
    missing = [hq for hq in sites if hq not in meter_days]
    if missing:
        print(f"※ 검침일 미설정 본부(1일로 간주): {', '.join(missing)}")
    print(f"본부 {len(sites)}곳 · 날짜 {', '.join(map(str, days))}")

    doc = None if args.dry_run else get_doc()
    daily_ws = None if args.dry_run else get_or_create_daily_ws(doc)
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    touched, ok_total, fail_total = set(), 0, 0
    for d in days:
        print(f"[{d}]")
        values, failed = collect(d, sites)
        ok_total += len(values)
        fail_total += failed
        if values and not args.dry_run:
            upsert_daily(daily_ws, d, values, now_str)
            for hq in values:
                touched.add(bill_month(meter_days, hq, d))

    if not args.dry_run:
        monthly_ws = doc.worksheet(MONTHLY_TAB)
        for y, m in sorted(touched):
            if (y, m) < (mf_y, mf_m):
                continue
            totals = month_totals(daily_ws, y, m, meter_days)
            # 사용 기간 전체를 한전 데이터로 받을 수 있는 본부만 자동 기록 (나머지는 고지서 수기 값 유지)
            auto = {hq: v for hq, v in totals.items() if period(meter_days, hq, y, m)[0] >= start}
            for hq in sorted(set(totals) - set(auto)):
                ps, pe = period(meter_days, hq, y, m)
                print(f"[{y}년 {m}월] {hq}: 기간 {ps}~{pe} 중 동의일 이전이 있어 고지서 수기 입력 유지")
            if auto:
                write_monthly(monthly_ws, y, m, auto, now_str)
                print(f"[{y}년 {m}월] 월간실적 전력사용량 자동 갱신: {', '.join(auto)}")

    print(f"완료: 성공 {ok_total}건, 실패 {fail_total}건")
    if ok_total == 0:
        sys.exit(1)  # 하나도 못 받으면 GitHub Actions에 빨간 실패로 표시


if __name__ == "__main__":
    main()
