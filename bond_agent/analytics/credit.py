"""크레딧 스프레드 맥락 지표. 전부 코드로 계산한다 (LLM은 서술만).

각 등급/섹터 스프레드(국고 3년 대비, bp)에 대해:
- 레벨, 1일/1주(5영업일)/1개월(20영업일) 변화(bp)
- 지정한 lookback 구간 내 분위수(percentile) - 스펙상 "1년"이지만, 최초
  백필은 실제 스크래핑(요청 1건당 1페이지)이 필요해 시간이 걸린다. 이
  모듈은 lookback_days를 그대로 받아 1년(252영업일)도 지원하지만, 이번
  작업에서 실제로 돌려본 것은 60영업일(약 3개월)이다 - WORK_LOG.md에 이유
  기록.
- 20일 이동평균 대비 위치
- 등급간 스프레드(예: AA- vs BBB-)의 확대/축소
- 섹터간 상대 움직임(예: 기타금융채(여전채 proxy) vs 공모회사채)

스프레드는 이 모듈이 직접 계산한다 - tools/credit.py는 레벨만 가져오고,
국고채 대비 스프레드는 여기서 우리 자체 국고채 커브(ecos.py) 기준으로 뺀다.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Optional

from bond_agent.config import (
    CREDIT_GAP_DIRECTION_THRESHOLD_BP,
    CREDIT_SPREAD_REGIME_TIGHT_PERCENTILE,
    CREDIT_SPREAD_REGIME_WIDE_PERCENTILE,
)
from bond_agent.tools import credit as credit_tool
from bond_agent.tools import ecos

# 분석 대상 시리즈 (tools/credit.py의 series 키와 동일). 국고채 대비 스프레드는
# 항상 3년물 기준(프로젝트 기존 관례 - curve.py의 AA-/BBB- 3년 스프레드와 통일).
CREDIT_SERIES_KEYS: list[str] = [
    "특수채_공사채_AAA",
    "특수채_공사채_AA+",
    "특수채_공사채_AA",
    "특수채_공사채_AA-",
    "금융채I_은행채_AAA",
    "금융채I_은행채_AA+",
    "금융채I_은행채_AA",
    "금융채I_은행채_AA-",
    "금융채I_은행채_A+",
    "금융채I_은행채_A",
    "금융채II_카드채_AA+",
    "금융채II_카드채_AA",
    "금융채II_카드채_AA-",
    "금융채II_카드채_A+",
    "금융채II_카드채_A",
    "금융채II_카드채_A-",
    "금융채II_기타금융채_AA+",
    "금융채II_기타금융채_AA",
    "금융채II_기타금융채_AA-",
    "금융채II_기타금융채_BBB+",
    "금융채II_기타금융채_BBB",
    "금융채II_기타금융채_BBB-",
    "회사채(공모)_무보증_AAA",
    "회사채(공모)_무보증_AA+",
    "회사채(공모)_무보증_AA",
    "회사채(공모)_무보증_AA-",
    "회사채(공모)_무보증_A+",
    "회사채(공모)_무보증_A",
    "회사채(공모)_무보증_A-",
    "회사채(공모)_무보증_BBB+",
    "회사채(공모)_무보증_BBB",
    "회사채(공모)_무보증_BBB-",
]

_KTB_KEY = "국채_국고채_국고/양곡/외평/재정"
_SPREAD_TENOR = "3Y"

DEFAULT_LOOKBACK_DAYS = 252  # 1년(영업일). 실제 데모/검증에는 60을 썼다 (모듈 docstring 참고).

# 등급간 스프레드: (낮은등급_키, 높은등급_키, 라벨) - gap = 낮은등급 스프레드 - 높은등급 스프레드.
_GRADE_PAIRS: list[tuple[str, str, str]] = [
    ("회사채(공모)_무보증_BBB-", "회사채(공모)_무보증_AA-", "공모회사채 BBB- vs AA-"),
    ("금융채I_은행채_AA-", "금융채I_은행채_AAA", "은행채 AA- vs AAA"),
]

# 섹터간 상대 움직임: (섹터A_키, 섹터B_키, 라벨) - gap = A 스프레드 - B 스프레드.
_SECTOR_PAIRS: list[tuple[str, str, str]] = [
    ("금융채II_기타금융채_AA-", "회사채(공모)_무보증_AA-", "기타금융채(여전채 proxy) vs 공모회사채 (AA-)"),
    ("금융채II_카드채_AA-", "회사채(공모)_무보증_AA-", "카드채 vs 공모회사채 (AA-)"),
]


def _bp_diff(current: Optional[float], prior: Optional[float]) -> Optional[float]:
    if current is None or prior is None:
        return None
    return round(current - prior, 4)


def _percentile_rank(value: float, population: list[float]) -> Optional[float]:
    """population 중 value 이하인 비율(0~100). population이 비어 있으면 None."""
    if not population:
        return None
    n_le = sum(1 for x in population if x <= value)
    return round(100.0 * n_le / len(population), 2)


def classify_spread_regime(percentile: Optional[float]) -> Optional[str]:
    """1년 분위수 기준 타이트(하위 20%)/중립/와이드(상위 20%) 라벨."""
    if percentile is None:
        return None
    if percentile <= CREDIT_SPREAD_REGIME_TIGHT_PERCENTILE:
        return "타이트"
    if percentile >= CREDIT_SPREAD_REGIME_WIDE_PERCENTILE:
        return "와이드"
    return "중립"


def _direction(change: Optional[float]) -> Optional[str]:
    """등급간/섹터간 스프레드 변화 방향 라벨."""
    if change is None:
        return None
    if change > CREDIT_GAP_DIRECTION_THRESHOLD_BP:
        return "확대"
    if change < -CREDIT_GAP_DIRECTION_THRESHOLD_BP:
        return "축소"
    return "유지"


def _kr_business_days_desc(end_date: str, n: int) -> list[str]:
    """ecos.py의 ktb_3y 시리즈를 캘린더로 써서 end_date 이하 최근 n영업일을 내림차순으로 구한다."""
    calendar_days = int(n * 1.6) + 20
    start = (dt.date.fromisoformat(end_date) - dt.timedelta(days=calendar_days)).isoformat()
    series = ecos.get_kr_yields(start, end_date)["series"]["ktb_3y"]
    business_days = sorted(d for d in series if d <= end_date)
    return list(reversed(business_days[-n:]))


def _combine(low: dict, high: dict, key: str) -> Optional[float]:
    a, b = low.get(key), high.get(key)
    if a is None or b is None:
        return None
    return round(a - b, 4)


def calc_credit_context(date: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """지정일 기준 CREDIT_SERIES_KEYS 각각의 맥락 지표와 등급간/섹터간 상대 움직임을 계산한다.

    Args:
        date: 기준일 "YYYY-MM-DD" (한국 영업일).
        lookback_days: 분위수/이동평균 계산에 쓸 과거 영업일 수 (기본 252 = 1년).

    Returns:
        {
          "as_of_date": date,
          "lookback_business_days": lookback_days,
          "actual_window_days": int,
          "metrics": {
            series_key: {
              "level_pct": float|None,        # 원 레벨(%), 3년물
              "spread_bp": float|None,         # 국고 3년 대비 스프레드(bp)
              "change_1d_bp": float|None,
              "change_1w_bp": float|None,      # 5영업일 전 대비
              "change_1m_bp": float|None,      # 20영업일 전 대비
              "percentile": float|None,        # lookback 구간 내 스프레드 분위수(0~100)
              "regime": "타이트"|"중립"|"와이드"|None,
              "ma20_bp": float|None,           # 20영업일 이동평균(스프레드, bp)
              "vs_ma20_bp": float|None,        # 오늘 스프레드 - ma20 (양수=평균보다 넓음)
            },
            ...
          },
          "grade_spreads": {label: {"gap_bp":.., "change_1d_bp":.., "change_1w_bp":.., "direction":..}, ...},
          "sector_relative": {label: {"gap_bp":.., "change_1d_bp":.., "change_1w_bp":.., "direction":..}, ...},
        }
    """
    window_dates = _kr_business_days_desc(date, lookback_days + 1)  # 내림차순, [0]=date
    if not window_dates or window_dates[0] != date:
        raise ValueError(f"{date} 는 한국 영업일이 아니거나 국고채 데이터가 없습니다.")

    history = credit_tool.get_credit_curve_history(window_dates)

    def spread_series(series_key: str) -> list[Optional[float]]:
        out = []
        for d in window_dates:
            day_series = history.get(d, {}).get("series", {})
            credit_level = day_series.get(series_key, {}).get(_SPREAD_TENOR)
            ktb_level = day_series.get(_KTB_KEY, {}).get(_SPREAD_TENOR)
            if credit_level is None or ktb_level is None:
                out.append(None)
            else:
                out.append(round((credit_level - ktb_level) * 100, 4))
        return out

    metrics: dict[str, dict] = {}
    for key in CREDIT_SERIES_KEYS:
        spreads = spread_series(key)  # spreads[0] = 오늘, 이후 과거로 갈수록 인덱스 증가
        today_level = history.get(date, {}).get("series", {}).get(key, {}).get(_SPREAD_TENOR)
        today_spread = spreads[0] if spreads else None

        def _at(n_days_ago: int) -> Optional[float]:
            return spreads[n_days_ago] if len(spreads) > n_days_ago else None

        change_1d = _bp_diff(today_spread, _at(1))
        change_1w = _bp_diff(today_spread, _at(5))
        change_1m = _bp_diff(today_spread, _at(20))

        valid_spreads = [s for s in spreads if s is not None]
        percentile = _percentile_rank(today_spread, valid_spreads) if today_spread is not None else None
        regime = classify_spread_regime(percentile)

        ma20_values = [s for s in spreads[:20] if s is not None]
        ma20 = round(statistics.mean(ma20_values), 4) if len(ma20_values) >= 5 else None
        vs_ma20 = round(today_spread - ma20, 4) if (today_spread is not None and ma20 is not None) else None

        metrics[key] = {
            "level_pct": today_level,
            "spread_bp": today_spread,
            "change_1d_bp": change_1d,
            "change_1w_bp": change_1w,
            "change_1m_bp": change_1m,
            "percentile": percentile,
            "regime": regime,
            "ma20_bp": ma20,
            "vs_ma20_bp": vs_ma20,
        }

    grade_spreads = {}
    for low_key, high_key, label in _GRADE_PAIRS:
        low_m, high_m = metrics.get(low_key, {}), metrics.get(high_key, {})
        change_1w = _combine(low_m, high_m, "change_1w_bp")
        grade_spreads[label] = {
            "gap_bp": _combine(low_m, high_m, "spread_bp"),
            "change_1d_bp": _combine(low_m, high_m, "change_1d_bp"),
            "change_1w_bp": change_1w,
            "direction": _direction(change_1w),
        }

    sector_relative = {}
    for a_key, b_key, label in _SECTOR_PAIRS:
        a_m, b_m = metrics.get(a_key, {}), metrics.get(b_key, {})
        change_1w = _combine(a_m, b_m, "change_1w_bp")
        sector_relative[label] = {
            "gap_bp": _combine(a_m, b_m, "spread_bp"),
            "change_1d_bp": _combine(a_m, b_m, "change_1d_bp"),
            "change_1w_bp": change_1w,
            "direction": _direction(change_1w),
        }

    return {
        "as_of_date": date,
        "lookback_business_days": lookback_days,
        "actual_window_days": len(window_dates) - 1,
        "metrics": metrics,
        "grade_spreads": grade_spreads,
        "sector_relative": sector_relative,
    }
