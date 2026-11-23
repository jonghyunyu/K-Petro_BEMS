"""
K-REMS 한전 전력사용량 일일 자동 수집기
------------------------------------------------------------
app.py 는 건드리지 않습니다. GitHub Actions(kepco_daily.yml)가 매일 따로 실행합니다.

하는 일
  1) 한전 OPM Open-API(getDayLpData)로 11개 본부 고객번호의 하루 사용량(15분 x 96개)을 받아 합산
  2) 구글시트 '일간전력' 탭에 날짜·본부별로 한 줄씩 기록 (같은 날짜·본부는 덮어씀)
     - 하루 최대수요(kW, 15분 평균 전력 최댓값)도 함께 기록
     - '시간별전력' 탭에는 0시~23시 시간대별 사용량(kWh)을 하루 한 줄로 기록
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
  python kepco_sync.py --from 2026-10-30             # 10/30부터 어제까지 모두 다시 수집 (지난 기간 채우기)
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
DAILY_HEADER = ["날짜", "본부명", "전력사용량", "온실가스(tCO2eq)", "15분값개수", "수집시간", "최대수요(kW)"]
HOURLY_TAB = "시간별전력"   # 하루 1줄: 날짜, 본부명, 0시~23시 사용량(kWh), 수집시간
HOURLY_HEADER = ["날짜", "본부명"] + [f"{h}시" for h in range(24)] + ["수집시간"]

ELEC_FACTOR = 0.0004594106  # app.py 와 같은 전력 배출계수 (tCO2eq/kWh)
KST = timezone(timedelta(hours=9))

OPM_URL = "https://opm.kepco.co.kr:11080/OpenAPI/getDayLpData.do"
# pwr_qty0015, 0030, 0045, 0100, ... , 2345, 2400 (96개, 단위 kWh)
LP_KEYS = [f"pwr_qty{(i*15)//60:02d}{(i*15)%60:02d}" for i in range(1, 97)]
OPM_TIMEOUT = (10, 60)  # (접속, 응답) 초. 접속이 안 되면 10초 만에 포기
FAIL_FAST_N = 3         # 실행 첫 요청 N개가 모두 접속 실패면 서버 장애로 보고 전체 중단


class KepcoUnreachable(Exception):
    """한전 서버에 접속 자체가 안 됨 -> 남은 본부/날짜도 모두 실패할 것이므로 실행 중단"""


# 이번 실행의 첫 요청들 결과 (True = 접속 실패). FAIL_FAST_N 개까지만 기록
_first_results = []


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
    """고객번호 1개의 하루 사용량.
    반환: (합계 kWh, 받은 15분 값 개수, 15분 값 96개 목록[kWh, 값 없으면 None])"""
    if os.environ.get("KEPCO_MOCK") == "1":
        base = random.uniform(15, 60)
        lp = [round(base * (1.8 if 36 <= i < 72 else 0.6) * random.uniform(0.8, 1.2), 2) for i in range(96)]
        return round(sum(lp), 1), 96, lp

    import requests
    try:
        resp = requests.get(OPM_URL, params={
            "custNo": customer_no,
            "date": day.strftime("%Y%m%d"),
            "serviceKey": os.environ["KEPCO_API_KEY"].strip(),
            "returnType": "02",  # JSON
        }, timeout=OPM_TIMEOUT)
    except requests.exceptions.ConnectionError:  # ConnectTimeout 포함
        _record_first(True)
        raise
    _record_first(False)
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

    # 다계기(계량기 여러 개)면 여러 줄 -> 같은 15분 구간끼리 합산
    lp = [None] * 96
    for r in rows:
        for i, k in enumerate(LP_KEYS):
            v = _num(r.get(k))
            if v is not None:
                lp[i] = (lp[i] or 0.0) + v
    count = sum(v is not None for v in lp)
    total = sum(v for v in lp if v is not None)
    return round(total, 2), count, lp


def _record_first(conn_failed: bool):
    """첫 FAIL_FAST_N 개 요청이 모두 접속 실패면 KepcoUnreachable 발생"""
    if len(_first_results) >= FAIL_FAST_N:
        return
    _first_results.append(conn_failed)
    if len(_first_results) == FAIL_FAST_N and all(_first_results):
        raise KepcoUnreachable(f"첫 요청 {FAIL_FAST_N}건이 모두 접속 실패/시간초과 ({OPM_URL})")


def summarize_lp(lp_list):
    """여러 고객번호의 15분 값 목록 -> (시간별 24개 kWh, 최대수요 kW)
    최대수요 = 15분 사용량(kWh) x 4 의 하루 최댓값 (한전 최대수요와 같은 15분 평균 전력)"""
    merged = [None] * 96
    for lp in lp_list:
        for i, v in enumerate(lp):
            if v is not None:
                merged[i] = (merged[i] or 0.0) + v
    hourly = []
    for h in range(24):
        part = [v for v in merged[h * 4:(h + 1) * 4] if v is not None]
        hourly.append(round(sum(part), 2) if part else "")
    vals = [v for v in merged if v is not None]
    peak_kw = round(max(vals) * 4, 1) if vals else ""
    return hourly, peak_kw


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


def get_or_create_ws(doc, title: str, header: list, rows: int = 5000):
    """탭이 없으면 만들고, 머리글에 새 칸이 추가됐으면 맞춰 늘림 (기존 데이터는 그대로)"""
    try:
        ws = doc.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = doc.add_worksheet(title=title, rows=rows, cols=len(header))
        ws.append_row(header)
        return ws
    if ws.col_count < len(header):
        ws.add_cols(len(header) - ws.col_count)
    current = ws.row_values(1)
    if len(current) < len(header) and current == header[:len(current)]:
        # 예전 머리글 뒤에 새 칸 이름만 이어 붙임 (사람이 고친 머리글은 건드리지 않음)
        start = gspread.utils.rowcol_to_a1(1, len(current) + 1)
        end = gspread.utils.rowcol_to_a1(1, len(header))
        ws.update(range_name=f"{start}:{end}", values=[header[len(current):]])
    return ws


def get_or_create_daily_ws(doc):
    return get_or_create_ws(doc, DAILY_TAB, DAILY_HEADER)


def _upsert(ws, day: date, rows_by_hq: dict, width: int):
    """(날짜, 본부명) 이 같은 줄은 덮어쓰고, 없으면 아래에 추가"""
    rows = ws.get_all_values()
    index = {(r[0], r[1]): i + 1 for i, r in enumerate(rows) if len(r) >= 2}
    last_col = gspread.utils.rowcol_to_a1(1, width).rstrip("1")
    updates, appends = [], []
    for hq, row in rows_by_hq.items():
        r = index.get((day.isoformat(), hq))
        if r:
            updates.append({"range": f"A{r}:{last_col}{r}", "values": [row]})
        else:
            appends.append(row)
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    if appends:
        ws.append_rows(appends, value_input_option="USER_ENTERED")


def upsert_daily(ws, day: date, values: dict, now_str: str):
    rows = {hq: [day.isoformat(), hq, v["kwh"], round(v["kwh"] * ELEC_FACTOR, 4), v["count"], now_str, v["peak_kw"]]
            for hq, v in values.items()}
    _upsert(ws, day, rows, len(DAILY_HEADER))


def upsert_hourly(ws, day: date, values: dict, now_str: str):
    rows = {hq: [day.isoformat(), hq] + v["hourly"] + [now_str] for hq, v in values.items()}
    _upsert(ws, day, rows, len(HOURLY_HEADER))


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


def month_totals(ws, year: int, month: int, meter_days: dict):
    """일간전력 탭에서 본부별로 고지서 방식 (year, month) 기간의 kWh 합계와,
    15분 값이 하나도 없는 날(데이터 미제공일) 목록"""
    totals, empty = {}, {}
    for r in ws.get_all_values()[1:]:
        if len(r) < 3:
            continue
        try:
            d = date.fromisoformat(r[0])
        except ValueError:
            continue
        if bill_month(meter_days, r[1], d) == (year, month):
            totals[r[1]] = totals.get(r[1], 0.0) + (_num(r[2]) or 0.0)
            if len(r) > 4 and (_num(r[4]) or 0) == 0:
                empty.setdefault(r[1], []).append(d)
    return totals, empty


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
            hourly, peak_kw = summarize_lp([p[2] for p in parts])
            result[hq] = {"kwh": kwh, "count": n, "hourly": hourly, "peak_kw": peak_kw}
            warn = "" if n == 96 else f"  (15분 값 {n}/96개: 아직 덜 집계됨)"
            print(f"  - {hq}: {kwh:,.1f} kWh · 최대수요 {peak_kw} kW{warn}")
        except KepcoUnreachable:
            raise  # 서버 장애: 다음 본부로 넘어가지 않고 실행 전체 중단
        except Exception as e:  # 한 본부 실패가 전체를 멈추지 않도록
            failed += 1
            print(f"  ! {hq}: 실패 - {e}")
    return result, failed


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="수집할 날짜 YYYY-MM-DD")
    p.add_argument("--days", type=int, default=3, help="어제부터 거슬러 며칠을 다시 수집할지 (기본 3)")
    p.add_argument("--from", dest="from_date", help="이 날짜부터 어제까지 모두 수집 (지난 기간 채우기용) YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="시트에 쓰지 않고 결과만 출력")
    args = p.parse_args()

    start = date.fromisoformat(os.environ.get("KEPCO_START") or "2026-10-30")
    monthly_from = os.environ.get("MONTHLY_FROM") or "2026-01"
    mf_y, mf_m = map(int, monthly_from.split("-"))

    today = datetime.now(KST).date()
    if args.date:
        days = [date.fromisoformat(args.date)]
    elif args.from_date:
        f = date.fromisoformat(args.from_date)
        days = [f + timedelta(days=i) for i in range((today - f).days)]
    else:
        days = [today - timedelta(days=i) for i in range(args.days, 0, -1)]
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
    hourly_ws = None if args.dry_run else get_or_create_ws(doc, HOURLY_TAB, HOURLY_HEADER)
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    touched, ok_total, fail_total = set(), 0, 0
    for d in days:
        print(f"[{d}]")
        try:
            values, failed = collect(d, sites)
        except KepcoUnreachable as e:
            print(f"  ! {e}")
            print(f"한전 서버 접속 불가로 수집 중단 (지금까지 성공 {ok_total}건). 오후 재실행 때 다시 시도합니다.")
            sys.exit(1)
        ok_total += len(values)
        fail_total += failed
        if values and not args.dry_run:
            upsert_daily(daily_ws, d, values, now_str)
            upsert_hourly(hourly_ws, d, values, now_str)
            for hq in values:
                touched.add(bill_month(meter_days, hq, d))

    if not args.dry_run:
        monthly_ws = doc.worksheet(MONTHLY_TAB)
        for y, m in sorted(touched):
            if (y, m) < (mf_y, mf_m):
                continue
            totals, empty = month_totals(daily_ws, y, m, meter_days)
            # 사용 기간 전체를 한전 데이터로 받을 수 있는 본부만 자동 기록 (나머지는 고지서 수기 값 유지)
            auto = {hq: v for hq, v in totals.items() if period(meter_days, hq, y, m)[0] >= start}
            for hq in sorted(set(totals) - set(auto)):
                ps, pe = period(meter_days, hq, y, m)
                print(f"[{y}년 {m}월] {hq}: 기간 {ps}~{pe} 중 동의일 이전이 있어 고지서 수기 입력 유지")
            # 한전 데이터가 오지 않은 날이 있는 본부는 월 합계가 틀리므로 자동 기록하지 않음
            for hq in sorted(set(auto) & set(empty)):
                days_txt = ", ".join(d.strftime("%m/%d") for d in sorted(empty[hq]))
                print(f"[{y}년 {m}월] ⚠ {hq}: 한전 데이터 미제공일({days_txt})이 있어 자동 기록 보류 → 고지서 수기 입력 필요")
                auto.pop(hq)
            if auto:
                write_monthly(monthly_ws, y, m, auto, now_str)
                print(f"[{y}년 {m}월] 월간실적 전력사용량 자동 갱신: {', '.join(auto)}")

    print(f"완료: 성공 {ok_total}건, 실패 {fail_total}건")
    if ok_total == 0:
        sys.exit(1)  # 하나도 못 받으면 GitHub Actions에 빨간 실패로 표시


if __name__ == "__main__":
    main()
