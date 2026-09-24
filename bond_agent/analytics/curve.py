"""국채/크레딧 스프레드, 전일 대비 변동, 이상치 계산.

숫자는 전부 여기서 계산한다. LLM 레이어는 이 결과를 그대로 서술만 하고
새로운 숫자를 만들지 않는다.

"영업일" 정의: ECOS는 휴일에는 데이터를 내려주지 않으므로, 한국 국고채(ktb_3y)
시리즈에 실제로 값이 있는 날짜만을 한국 영업일로 취급한다. 별도의 공휴일
캘린더 라이브러리는 쓰지 않는다.

미국 데이터는 시차 때문에, 한국 기준일 d 에는 "d의 전날(캘린더 기준) 이전에
발표된 가장 최근 FRED 값"을 사용한다. 실제로 어느 날짜의 값을 썼는지는
반환값의 *_asof_date 필드에 항상 명시한다.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Optional

from bond_agent.tools import ecos, fred

KR_YIELD_KEYS = [
    "base_rate",
    "ktb_3y",
    "ktb_5y",
    "ktb_10y",
    "ktb_30y",
    "corp_aa_minus_3y",
    "corp_bbb_minus_3y",
]

# 스프레드 정의: 이름 -> (피감수 키, 감수 키). 둘 다 위 KR_YIELD_KEYS 또는 US 파생 키.
SPREAD_KEYS = [
    "ktb_3_10",
    "ktb_10_30",
    "credit_aa_minus_3y",
    "credit_bbb_minus_3y",
    "kr_us_10y",
]

ALL_METRIC_KEYS = KR_YIELD_KEYS + SPREAD_KEYS

_KR_CALENDAR_ANCHOR = "ktb_3y"  # 한국 영업일 목록을 판단할 기준 시리즈


def _calendar_days_for_business_days(n_business_days: int) -> int:
    """목표 영업일 수를 확보하기 위해 넉넉히 잡을 캘린더일 수 (주말/연휴 버퍼 포함)."""
    return int(n_business_days * 1.6) + 20


def _sub(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return a - b


def _bp(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    """퍼센트(%) 단위 두 레벨 간 변동을 bp로 환산 (1%p = 100bp)."""
    if curr is None or prev is None:
        return None
    return round((curr - prev) * 100, 4)


def _value_at_or_before(series: dict[str, float], cutoff_date: str) -> tuple[Optional[str], Optional[float]]:
    """series에서 cutoff_date 이하인 가장 최근 날짜의 (날짜, 값)을 반환."""
    candidates = [d for d in series if d <= cutoff_date]
    if not candidates:
        return None, None
    latest = max(candidates)
    return latest, series[latest]


def _fetch_kr_window(end_date: str, business_days: int) -> dict[str, dict[str, float]]:
    calendar_days = _calendar_days_for_business_days(business_days)
    start_date = (dt.date.fromisoformat(end_date) - dt.timedelta(days=calendar_days)).isoformat()
    result = ecos.get_kr_yields(start_date, end_date)
    return result["series"]


def _fetch_us_window(end_date: str, business_days: int) -> dict[str, dict[str, float]]:
    # 한미 시차 보정으로 cutoff을 하루 전으로 잡기 때문에, 하루치를 더 여유 있게 가져온다.
    calendar_days = _calendar_days_for_business_days(business_days) + 5
    start_date = (dt.date.fromisoformat(end_date) - dt.timedelta(days=calendar_days)).isoformat()
    result = fred.get_us_yields(start_date, end_date)
    return result["series"]


def _kr_business_days(kr_series: dict[str, dict[str, float]], end_date: str) -> list[str]:
    """end_date 이하, kr_series 기준 한국 영업일 날짜 목록 (오름차순)."""
    anchor = kr_series[_KR_CALENDAR_ANCHOR]
    return sorted(d for d in anchor if d <= end_date)


def _metrics_for_date(
    kr_series: dict[str, dict[str, float]],
    us_series: dict[str, dict[str, float]],
    date_: str,
) -> dict[str, Optional[str] | Optional[float]]:
    """주어진 한국 영업일 하루치의 레벨(수익률/스프레드)과 미국 데이터 출처 날짜."""
    m: dict[str, Optional[str] | Optional[float]] = {}
    for key in KR_YIELD_KEYS:
        m[key] = kr_series.get(key, {}).get(date_)

    us_cutoff = (dt.date.fromisoformat(date_) - dt.timedelta(days=1)).isoformat()
    dgs10_date, dgs10_val = _value_at_or_before(us_series.get("dgs10", {}), us_cutoff)
    m["dgs10"] = dgs10_val
    m["dgs10_asof_date"] = dgs10_date

    m["ktb_3_10"] = _sub(m["ktb_10y"], m["ktb_3y"])
    m["ktb_10_30"] = _sub(m["ktb_30y"], m["ktb_10y"])
    m["credit_aa_minus_3y"] = _sub(m["corp_aa_minus_3y"], m["ktb_3y"])
    m["credit_bbb_minus_3y"] = _sub(m["corp_bbb_minus_3y"], m["ktb_3y"])
    m["kr_us_10y"] = _sub(m["ktb_10y"], m["dgs10"])
    return m


def _prev_business_day(business_days: list[str], date_: str) -> Optional[str]:
    earlier = [d for d in business_days if d < date_]
    return max(earlier) if earlier else None


def calc_daily_changes(date: str) -> dict:
    """전일 대비 각 만기(수익률) 변동(bp), 한국 영업일 기준.

    Args:
        date: 기준일 "YYYY-MM-DD" (ECOS 상 한국 영업일이어야 함).

    Returns:
        {
          "as_of_date": date,
          "prev_business_date": str,
          "source": "ECOS",
          "levels": {key: float|None, ...},   # 기준일 레벨(%)
          "changes_bp": {key: float|None, ...},  # 전일 대비 변동(bp)
        }
    """
    kr_series = _fetch_kr_window(date, business_days=5)
    business_days = _kr_business_days(kr_series, date)
    if not business_days or business_days[-1] != date:
        raise ValueError(f"{date} 는 ECOS 기준 한국 영업일이 아니거나 데이터가 없습니다.")

    prev_date = _prev_business_day(business_days, date)
    if prev_date is None:
        raise ValueError(f"{date} 이전의 영업일 데이터를 찾을 수 없습니다 (조회 범위를 넓혀야 함).")

    levels_t = {k: kr_series.get(k, {}).get(date) for k in KR_YIELD_KEYS}
    levels_prev = {k: kr_series.get(k, {}).get(prev_date) for k in KR_YIELD_KEYS}
    changes = {k: _bp(levels_t[k], levels_prev[k]) for k in KR_YIELD_KEYS}

    return {
        "as_of_date": date,
        "prev_business_date": prev_date,
        "source": "ECOS",
        "levels": levels_t,
        "changes_bp": changes,
    }


def calc_spreads(date: str) -> dict:
    """국고 3/10, 10/30 스프레드, 회사채 AA-/BBB- 크레딧 스프레드, 한미 10년 금리차.

    수준(레벨, %/bp)과 전일 대비 변동(bp)을 모두 반환한다.

    Args:
        date: 기준일 "YYYY-MM-DD" (ECOS 상 한국 영업일이어야 함).

    Returns:
        {
          "as_of_date": date,
          "prev_business_date": str,
          "us_asof_date": str|None,       # 기준일에 사용한 미국 10년물 데이터 날짜
          "us_prev_asof_date": str|None,  # 전일에 사용한 미국 10년물 데이터 날짜
          "source": {"kr": "ECOS", "us": "FRED"},
          "levels": {
            "ktb_3_10": float,        # bp (국고10 - 국고3, 이미 %; 아래서 bp로도 제공)
            ...
          },
          "levels_bp": {...},  # 위 레벨을 bp 단위로 환산 (스프레드는 보통 bp로 인용)
          "changes_bp": {...},
        }
    """
    kr_series = _fetch_kr_window(date, business_days=5)
    us_series = _fetch_us_window(date, business_days=5)
    business_days = _kr_business_days(kr_series, date)
    if not business_days or business_days[-1] != date:
        raise ValueError(f"{date} 는 ECOS 기준 한국 영업일이 아니거나 데이터가 없습니다.")

    prev_date = _prev_business_day(business_days, date)
    if prev_date is None:
        raise ValueError(f"{date} 이전의 영업일 데이터를 찾을 수 없습니다 (조회 범위를 넓혀야 함).")

    m_t = _metrics_for_date(kr_series, us_series, date)
    m_prev = _metrics_for_date(kr_series, us_series, prev_date)

    levels = {k: m_t[k] for k in SPREAD_KEYS}
    levels_bp = {k: (None if levels[k] is None else round(levels[k] * 100, 4)) for k in SPREAD_KEYS}
    changes = {k: _bp(m_t[k], m_prev[k]) for k in SPREAD_KEYS}

    return {
        "as_of_date": date,
        "prev_business_date": prev_date,
        "us_asof_date": m_t["dgs10_asof_date"],
        "us_prev_asof_date": m_prev["dgs10_asof_date"],
        "source": {"kr": "ECOS", "us": "FRED"},
        "levels": levels,
        "levels_bp": levels_bp,
        "changes_bp": changes,
    }


def flag_anomalies(date: str, lookback: int = 60) -> dict:
    """각 지표의 일간 변동(bp)을 최근 lookback 영업일 변동의 표준편차로 나눈 z-score.

    표준편차는 [date-lookback business days ... date] 구간의 lookback개
    일간 변동(bp)을 모집단으로 보고 모표준편차(ddof=0)로 계산한다(외부 모집단을
    추정하는 것이 아니라, 이 구간 자체를 비교 기준으로 쓰기 때문).
    |z| > 2 이면 이상치로 표시한다.

    Args:
        date: 기준일 "YYYY-MM-DD".
        lookback: 표준편차 계산에 쓸 영업일 수 (기본 60).

    Returns:
        {
          "as_of_date": date,
          "lookback_business_days": lookback,
          "actual_window_days": int,   # 실제로 구한 일간 변동 개수 (초기 구간은 lookback보다 작을 수 있음)
          "metrics": {
            key: {
              "change_bp": float|None,
              "std_bp": float|None,
              "z_score": float|None,
              "is_anomaly": bool,
            },
            ...
          }
        }
    """
    kr_series = _fetch_kr_window(date, business_days=lookback + 5)
    us_series = _fetch_us_window(date, business_days=lookback + 5)
    business_days = _kr_business_days(kr_series, date)
    if not business_days or business_days[-1] != date:
        raise ValueError(f"{date} 는 ECOS 기준 한국 영업일이 아니거나 데이터가 없습니다.")

    window_dates = business_days[-(lookback + 1):]
    metrics_by_date = [_metrics_for_date(kr_series, us_series, d) for d in window_dates]

    result_metrics: dict[str, dict] = {}
    actual_window_days = max(0, len(window_dates) - 1)

    for key in ALL_METRIC_KEYS:
        levels = [m[key] for m in metrics_by_date]
        diffs_bp = []
        for i in range(1, len(levels)):
            d = _bp(levels[i], levels[i - 1])
            diffs_bp.append(d)

        # 오늘(기준일)의 변동은 반드시 마지막 두 날짜 사이의 값이어야 한다. 일부
        # 시리즈는 발표 시차 때문에 기준일 데이터가 아직 없을 수 있는데(예:
        # 722Y001 기준금리는 817Y002 시장금리보다 하루 늦게 갱신될 수 있음),
        # 그 경우 과거의 다른 날짜 변동을 대신 쓰지 않고 None으로 명확히 표시한다.
        today_change = diffs_bp[-1] if diffs_bp else None
        valid_diffs = [d for d in diffs_bp if d is not None]
        std_bp = statistics.pstdev(valid_diffs) if len(valid_diffs) >= 2 else None
        z_score = (
            round(today_change / std_bp, 4)
            if today_change is not None and std_bp and std_bp > 0
            else None
        )
        result_metrics[key] = {
            "change_bp": today_change,
            "std_bp": None if std_bp is None else round(std_bp, 4),
            "z_score": z_score,
            "is_anomaly": bool(z_score is not None and abs(z_score) > 2),
        }

    return {
        "as_of_date": date,
        "lookback_business_days": lookback,
        "actual_window_days": actual_window_days,
        "metrics": result_metrics,
    }
