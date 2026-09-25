"""회사채 발행시장 집계 - 최근 N영업일 수요예측 결과를 이미 캐시된 DART 추출
데이터에서 집계한다. LLM 호출이 전혀 없다 - 새 공시를 추출하는 것은 이
모듈의 책임이 아니고(그건 agent.tools_schema.get_bond_demand_forecasts나
scripts/backfill_extract.py의 역할), 이 모듈은 data/extracted/*.json에 이미
있는 캐시된 결과만 회차(tranche) 단위로 모아서 통계를 낸다.

캐시에 없는 공시는 그냥 집계에서 빠진다 (에러 아님) - 그래서 이 집계의
완전성은 그 시점까지 얼마나 캐시가 채워져 있는지에 좌우된다.

"발행시장 온도"는 여기서 규칙 기반(코드)으로 산출한다 - 임계값은 config.py에.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from bond_agent.config import (
    ISSUANCE_TEMPERATURE_COLD_RATIO,
    ISSUANCE_TEMPERATURE_COLD_UNDER_PCT,
    ISSUANCE_TEMPERATURE_HOT_RATIO,
    ISSUANCE_TEMPERATURE_HOT_UNDER_PCT,
)
from bond_agent.tools import dart


def _flatten_cached_tranches(filings: list[dict]) -> list[dict]:
    """필링 중 캐시된 추출이 있는 것만 회차 단위로 펼친다."""
    from agent.extract import load_cached_extraction  # 지연 임포트: 순환 임포트 방지

    rows: list[dict[str, Any]] = []
    for filing in filings:
        cached = load_cached_extraction(filing["rcept_no"])
        if cached is None:
            continue
        for tranche in cached.get("tranches", []):
            rows.append(
                {
                    "rcept_no": filing["rcept_no"],
                    "corp_name": filing["corp_name"],
                    "rcept_dt": filing["rcept_dt"],
                    "credit_rating": cached.get("credit_rating"),
                    **tranche,
                }
            )
    return rows


def _classify_temperature(pct_under: Optional[float], avg_ratio: Optional[float]) -> Optional[str]:
    if pct_under is None or avg_ratio is None:
        return None
    if pct_under >= ISSUANCE_TEMPERATURE_HOT_UNDER_PCT and avg_ratio >= ISSUANCE_TEMPERATURE_HOT_RATIO:
        return "강세"
    if pct_under <= ISSUANCE_TEMPERATURE_COLD_UNDER_PCT and avg_ratio <= ISSUANCE_TEMPERATURE_COLD_RATIO:
        return "약세"
    return "중립"


def calc_issuance_summary(date: str, window_business_days: int) -> dict:
    """최근 window_business_days영업일(캘린더로 넉넉히 환산)의 회사채 수요예측 집계.

    Args:
        date: 기준일 "YYYY-MM-DD"
        window_business_days: 집계 기간(영업일). 보통 5 또는 20을 쓴다.

    Returns:
        {
          "as_of_date": date,
          "window_business_days": window_business_days,
          "num_tranches": int,                              # 캐시된 회차 기준 건수
          "total_offering_billion_won": float|None,
          "total_participation_billion_won": float|None,
          "avg_competition_ratio": float|None,
          "pct_under_issued": float|None,   # 확정 가산금리가 밴드 하단 이하로 끝난 비중(%)
          "pct_upsized": float|None,        # 증액 발행 비중(%)
          "by_rating": {rating: {"count": int, "avg_competition_ratio": float|None}, ...},
          "temperature": "강세"|"중립"|"약세"|None,  # 규칙 기반 (config 임계값)
        }
    """
    calendar_days = int(window_business_days * 1.6) + 20
    start = (dt.date.fromisoformat(date) - dt.timedelta(days=calendar_days)).isoformat()
    filings = dart.list_bond_filings(start, date)
    rows = _flatten_cached_tranches(filings)

    ratios = [r["competition_ratio"] for r in rows if r.get("competition_ratio") is not None]
    offerings = [
        r["initial_offering_amount_billion_won"] for r in rows if r.get("initial_offering_amount_billion_won") is not None
    ]
    participations = [
        r["demand_participation_amount_billion_won"]
        for r in rows
        if r.get("demand_participation_amount_billion_won") is not None
    ]

    under_flags = []
    for r in rows:
        band = r.get("coupon_guidance_band_bp")
        final_spread = r.get("final_spread_bp")
        if band and final_spread is not None and band.get("low_bp") is not None:
            under_flags.append(final_spread <= band["low_bp"])

    upsized_flags = [r["upsized"] for r in rows if r.get("upsized") is not None]

    pct_under = round(100 * sum(under_flags) / len(under_flags), 1) if under_flags else None
    pct_upsized = round(100 * sum(upsized_flags) / len(upsized_flags), 1) if upsized_flags else None
    avg_ratio = round(sum(ratios) / len(ratios), 2) if ratios else None

    by_rating: dict[str, dict] = {}
    for r in rows:
        rating = r.get("credit_rating") or "미확인"
        bucket = by_rating.setdefault(rating, {"count": 0, "_ratios": []})
        bucket["count"] += 1
        if r.get("competition_ratio") is not None:
            bucket["_ratios"].append(r["competition_ratio"])
    for bucket in by_rating.values():
        ratios_r = bucket.pop("_ratios")
        bucket["avg_competition_ratio"] = round(sum(ratios_r) / len(ratios_r), 2) if ratios_r else None

    return {
        "as_of_date": date,
        "window_business_days": window_business_days,
        "num_tranches": len(rows),
        "total_offering_billion_won": round(sum(offerings), 1) if offerings else None,
        "total_participation_billion_won": round(sum(participations), 1) if participations else None,
        "avg_competition_ratio": avg_ratio,
        "pct_under_issued": pct_under,
        "pct_upsized": pct_upsized,
        "by_rating": by_rating,
        "temperature": _classify_temperature(pct_under, avg_ratio),
    }
