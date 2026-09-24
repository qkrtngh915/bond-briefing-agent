"""한국은행 ECOS API 툴.

StatisticSearch API로 국고채 3/5/10/30년, 회사채(AA-, BBB-) 3년은 "시장금리(일별)"
(817Y002) 통계표에서, 기준금리는 "한국은행 기준금리 및 여수신금리"(722Y001)
통계표에서 가져온다. 통계표/항목 코드는 config.py의 ECOS_SERIES_DEFS에
scripts/lookup_ecos_codes.py 로 확인한 값만 채운다 (하드코딩 추측 금지).

순수 함수: 입력은 날짜 문자열, 출력은 JSON 직렬화 가능한 dict.
"""

from __future__ import annotations

import time

import requests

from bond_agent.config import ECOS_API_KEY, ECOS_BASE_URL, ECOS_SERIES_DEFS
from bond_agent.tools._cache import cached_call

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 1.5


def _check_configured() -> None:
    if not ECOS_API_KEY:
        raise RuntimeError("ECOS_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    if not ECOS_SERIES_DEFS:
        raise RuntimeError(
            "ECOS 통계표/항목 코드가 config.py에 채워지지 않았습니다. "
            "먼저 scripts/lookup_ecos_codes.py 로 코드를 조회해서 채워야 합니다."
        )


def _request_rows(table_code: str, item_code: str, start: str, end: str) -> list[dict]:
    """StatisticSearch 호출 + 응답 검증. ECOS가 가끔 일시적으로 빈 응답을
    내려주는 경우가 있어 (실제로 확인됨) 몇 차례 재시도한다.
    """
    url = (
        f"{ECOS_BASE_URL}/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/"
        f"{table_code}/D/{start}/{end}/{item_code}"
    )
    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            if "StatisticSearch" not in payload:
                result = payload.get("RESULT", {})
                raise RuntimeError(
                    f"ECOS API 오류 (item_code={item_code}): "
                    f"{result.get('CODE', '?')} {result.get('MESSAGE', payload)}"
                )
            return payload["StatisticSearch"].get("row", [])
        except (requests.exceptions.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"ECOS API 호출이 {_MAX_RETRIES}번 모두 실패했습니다: {last_error}") from last_error


def _fetch_item(table_code: str, item_code: str, start_date: str, end_date: str) -> dict[str, float]:
    """단일 (통계표, 항목) 조합의 일별 시계열을 가져와 {"YYYY-MM-DD": value} 로 반환한다.

    결측("-", "") 및 파싱 불가한 값은 제거한다 (ECOS는 휴일 데이터를 보통 아예
    내려주지 않지만, 방어적으로 한 번 더 걸러낸다).
    """
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")
    rows = _request_rows(table_code, item_code, start, end)
    series: dict[str, float] = {}
    for row in rows:
        raw_date = row.get("TIME", "")
        value = row.get("DATA_VALUE")
        if not raw_date or len(raw_date) != 8 or value in (None, "", "-"):
            continue
        try:
            numeric_value = float(value)
        except ValueError:
            continue
        iso_date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        series[iso_date] = numeric_value
    return series


def get_kr_yields(start_date: str, end_date: str) -> dict:
    """한국 시장금리 일별 시계열.

    Args:
        start_date: "YYYY-MM-DD"
        end_date: "YYYY-MM-DD"

    Returns:
        {
          "source": "ECOS",
          "table_codes": {series_key: "<통계표코드>", ...},  # 기준금리와 시장금리는 통계표가 다름
          "start_date": start_date,
          "end_date": end_date,
          "series": {
            "base_rate": {"YYYY-MM-DD": float, ...},
            "ktb_3y": {...},
            "ktb_5y": {...},
            "ktb_10y": {...},
            "ktb_30y": {...},
            "corp_aa_minus_3y": {...},
            "corp_bbb_minus_3y": {...},
          }
        }

    같은 (start_date, end_date) 조합으로 같은 날 다시 호출하면 로컬 캐시를 쓴다.
    """
    _check_configured()
    cache_key = f"kr_yields:{start_date}:{end_date}"

    def _fetch() -> dict:
        series = {
            series_key: _fetch_item(table_code, item_code, start_date, end_date)
            for series_key, (table_code, item_code, _label) in ECOS_SERIES_DEFS.items()
        }
        return {
            "source": "ECOS",
            "table_codes": {k: v[0] for k, v in ECOS_SERIES_DEFS.items()},
            "start_date": start_date,
            "end_date": end_date,
            "series": series,
        }

    return cached_call("ecos", cache_key, _fetch)
