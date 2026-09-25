"""등급·섹터별 채권 시가평가 수익률 (특수채/은행채/카드채·기타금융채/회사채).

1순위 소스로 금융투자협회 채권정보센터가 집계하는 "민간채권평가회사 4사" 중
하나인 한국자산평가(KAP)의 공개 데이터를 쓴다. 조사 과정(WORK_LOG.md 참고):
- KOFIA 채권정보센터 자체(kofiabond.or.kr)는 legacy frame 페이지로 실제
  접속해보니 빈 화면이었다 (사실상 죽은 사이트).
- KOFIA 오픈API 포털(openapi.kofia.or.kr)도 API 목록 자체가 비어 있었다.
- 그래서 KOFIA가 집계하는 원천 중 하나인 KAP의 공개 웹페이지가 쓰는 AJAX
  엔드포인트를 직접 호출한다 (로그인/유료 아님, 그 페이지가 브라우저에서
  그대로 호출하는 바로 그 요청).

공개 페이지: https://www.koreaap.com/kor/valuation01_01.html
robots.txt 확인함: "Allow: /" (전체 허용, /admin 계열만 제외 - 우리 대상과 무관).
엔드포인트: GET https://www.koreaap.com/vl/valuation01_01/bondRates
    params: ymd=YYYYMMDD, type=Y(YTM), searchType=0(기준수익률 레벨), lang=kor
스프레드(국고 대비 등)는 이 툴이 계산하지 않는다 - 레벨만 가져오고, 국고채
대비 스프레드 계산은 analytics/credit.py가 우리 자체 국고채 커브(ecos.py)를
기준으로 코드로 계산한다 (벤더가 자체 계산한 스프레드 컬럼을 믿지 않고,
직접 통제 가능하게 하기 위함).

순수 함수: 입력은 날짜 문자열, 출력은 JSON 직렬화 가능한 dict.
"""

from __future__ import annotations

import time

import requests

from bond_agent.tools._cache import cached_call

KAP_BOND_RATES_URL = "https://www.koreaap.com/vl/valuation01_01/bondRates"

# AJAX 응답의 M0xx 필드(개월수) -> 우리가 쓸 만기 라벨.
_MATURITY_MONTHS_TO_LABEL: dict[int, str] = {
    3: "3M", 6: "6M", 9: "9M", 12: "1Y", 18: "1.5Y", 24: "2Y", 30: "2.5Y",
    36: "3Y", 48: "4Y", 60: "5Y", 84: "7Y", 120: "10Y", 240: "20Y", 360: "30Y", 600: "50Y",
}

_REQUEST_DELAY_SECONDS = 0.4  # 여러 날짜를 연달아 부를 때(히스토리 백필) 요청 간 딜레이


def _fetch_raw(date: str) -> list[dict]:
    ymd = date.replace("-", "")
    resp = requests.get(
        KAP_BOND_RATES_URL,
        params={"ymd": ymd, "type": "Y", "searchType": "0", "lang": "kor"},
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    return resp.json()


def _row_key(row: dict) -> str | None:
    parts = [row.get("GMRI_TYPE"), row.get("GMRI_SUBTYPE"), row.get("GMRI_BOND")]
    parts = [p.replace("<br/>", "") for p in parts if p and p != "-"]
    if not parts:
        return None
    return "_".join(parts)


def get_credit_curve(date: str) -> dict:
    """지정일의 섹터×등급×만기 수익률(%)을 가져온다.

    Args:
        date: "YYYY-MM-DD"

    Returns:
        {
          "source": "KAP(한국자산평가)",
          "date": date,
          "series": {
            "국채_국고채_국고/양곡/외평/재정": {"3Y": 3.997, "10Y": 4.370, ...},
            "특수채_공사채_AAA": {...},
            "특수채_공사채_AA-": {...},
            "금융채I_은행채_AAA": {...},
            "금융채II_카드채_AA-": {...},
            "금융채II_기타금융채_BBB-": {...},
            "회사채(공모)_무보증_AA-": {...},
            "회사채(공모)_무보증_BBB-": {...},
            "회사채(사모)_무보증_BBB-": {...},
            ...
          }
        }

    같은 날짜로 같은 날 다시 호출하면 로컬 캐시를 쓴다 (bond_agent.tools._cache와
    동일한 당일 무효화 방식).
    """

    def _fetch() -> dict:
        rows = _fetch_raw(date)
        series: dict[str, dict[str, float]] = {}
        for row in rows:
            key = _row_key(row)
            if key is None:
                continue
            maturities: dict[str, float] = {}
            for months, label in _MATURITY_MONTHS_TO_LABEL.items():
                raw_val = row.get(f"M{months:03d}")
                if isinstance(raw_val, str):
                    raw_val = raw_val.strip()
                if raw_val in (None, "", "-"):
                    continue
                try:
                    maturities[label] = float(raw_val)
                except (TypeError, ValueError):
                    continue
            if maturities:
                series[key] = maturities
        return {"source": "KAP(한국자산평가)", "date": date, "series": series}

    cache_key = f"credit_curve:{date}"
    return cached_call("credit", cache_key, _fetch)


def get_credit_curve_history(dates: list[str]) -> dict[str, dict]:
    """여러 날짜의 get_credit_curve 결과를 모아서 반환한다 (분위수/이동평균 계산용).

    호출마다(캐시 적중 여부와 무관하게) _REQUEST_DELAY_SECONDS만큼 쉰다 -
    캐시가 없는 첫 백필(수십~수백 건 요청)에서 서버에 부담을 주지 않기 위한
    스크래핑 예의다. 캐시가 이미 있으면 요청 자체가 안 나가니 딜레이가
    사실상 손해는 아니고, 캐시 상태를 매번 들여다보는 복잡한 로직보다
    이렇게 항상 쉬는 게 더 단순하고 안전하다.

    Args:
        dates: "YYYY-MM-DD" 문자열 리스트.

    Returns:
        {date: get_credit_curve(date)의 반환값, ...} (가져오지 못한 날짜는 제외)
    """
    results: dict[str, dict] = {}
    for date in dates:
        try:
            results[date] = get_credit_curve(date)
        except requests.exceptions.RequestException:
            continue
        time.sleep(_REQUEST_DELAY_SECONDS)
    return results
