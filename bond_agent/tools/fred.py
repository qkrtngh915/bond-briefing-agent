"""FRED (세인트루이스 연은) API 툴.

get_us_yields(start_date, end_date) -> DGS2, DGS10, SOFR 일별 시계열.
순수 함수: 입력은 날짜 문자열, 출력은 JSON 직렬화 가능한 dict.
"""

from __future__ import annotations

import requests

from bond_agent.config import FRED_API_KEY, FRED_BASE_URL
from bond_agent.tools._cache import cached_call

# FRED series id: 내부 키 매핑
FRED_SERIES_IDS = {
    "dgs2": "DGS2",
    "dgs10": "DGS10",
    "sofr": "SOFR",
}


def _fetch_series(series_id: str, start_date: str, end_date: str) -> dict[str, float]:
    """단일 FRED 시리즈를 가져와 {"YYYY-MM-DD": value} 로 반환한다.

    결측값은 FRED에서 "." 문자열로 내려오므로 제거한다.
    """
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date,
        "observation_end": end_date,
    }
    resp = requests.get(FRED_BASE_URL, params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    if "observations" not in payload:
        raise RuntimeError(f"FRED API 오류 (series_id={series_id}): {payload}")

    series: dict[str, float] = {}
    for obs in payload["observations"]:
        value = obs.get("value")
        if value in (None, ".", ""):
            continue
        try:
            series[obs["date"]] = float(value)
        except ValueError:
            continue
    return series


def get_us_yields(start_date: str, end_date: str) -> dict:
    """미국 국채 수익률(및 SOFR) 일별 시계열.

    Args:
        start_date: "YYYY-MM-DD"
        end_date: "YYYY-MM-DD"

    Returns:
        {
          "source": "FRED",
          "start_date": start_date,
          "end_date": end_date,
          "series": {
            "dgs2": {"YYYY-MM-DD": float, ...},
            "dgs10": {...},
            "sofr": {...},
          }
        }

    같은 (start_date, end_date) 조합으로 같은 날 다시 호출하면 로컬 캐시를 쓴다.
    """
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    cache_key = f"us_yields:{start_date}:{end_date}"

    def _fetch() -> dict:
        series = {
            name: _fetch_series(series_id, start_date, end_date)
            for name, series_id in FRED_SERIES_IDS.items()
        }
        return {
            "source": "FRED",
            "start_date": start_date,
            "end_date": end_date,
            "series": series,
        }

    return cached_call("fred", cache_key, _fetch)
