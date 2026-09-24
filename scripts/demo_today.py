"""최근 영업일 기준으로 전일 대비 변동, 스프레드, 이상치를 표로 출력하는 데모.

실행:
    python scripts/demo_today.py
    python scripts/demo_today.py 2026-09-23   # 특정 날짜 지정
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tabulate import tabulate

from bond_agent.analytics import curve
from bond_agent.tools import ecos

LABELS = {
    "base_rate": "기준금리",
    "ktb_3y": "국고채 3년",
    "ktb_5y": "국고채 5년",
    "ktb_10y": "국고채 10년",
    "ktb_30y": "국고채 30년",
    "corp_aa_minus_3y": "회사채 3년 AA-",
    "corp_bbb_minus_3y": "회사채 3년 BBB-",
    "ktb_3_10": "국고 3/10 스프레드",
    "ktb_10_30": "국고 10/30 스프레드",
    "credit_aa_minus_3y": "크레딧 스프레드(AA-, 국고3 대비)",
    "credit_bbb_minus_3y": "크레딧 스프레드(BBB-, 국고3 대비)",
    "kr_us_10y": "한미 10년 금리차",
}


def _latest_kr_business_date() -> str:
    """오늘로부터 최근 며칠 안에서 ECOS 상 가장 최근 한국 영업일을 찾는다."""
    end = dt.date.today()
    start = end - dt.timedelta(days=14)
    series = ecos.get_kr_yields(start.isoformat(), end.isoformat())["series"]
    dates = sorted(series["ktb_3y"].keys())
    if not dates:
        raise RuntimeError("최근 14일 안에 ECOS 국고채(3년) 데이터가 없습니다.")
    return dates[-1]


def main() -> None:
    date = sys.argv[1] if len(sys.argv) > 1 else _latest_kr_business_date()

    print(f"===== 데일리 채권시장 브리핑 데이터 ({date} 기준) =====\n")

    changes = curve.calc_daily_changes(date)
    print(f"[1] 전일 대비 변동  (전일: {changes['prev_business_date']}, 출처: {changes['source']})")
    rows = [
        [LABELS.get(k, k), f"{changes['levels'][k]:.3f}%" if changes['levels'][k] is not None else "-",
         f"{changes['changes_bp'][k]:+.1f}bp" if changes['changes_bp'][k] is not None else "-"]
        for k in curve.KR_YIELD_KEYS
    ]
    print(tabulate(rows, headers=["지표", "레벨", "전일대비"], tablefmt="github"))
    print()

    spreads = curve.calc_spreads(date)
    print(
        f"[2] 스프레드  (전일: {spreads['prev_business_date']}, "
        f"미국 10년물 기준일: {spreads['us_asof_date']} / 전일 기준: {spreads['us_prev_asof_date']})"
    )
    rows = [
        [
            LABELS.get(k, k),
            f"{spreads['levels_bp'][k]:+.1f}bp" if spreads['levels_bp'][k] is not None else "-",
            f"{spreads['changes_bp'][k]:+.1f}bp" if spreads['changes_bp'][k] is not None else "-",
        ]
        for k in curve.SPREAD_KEYS
    ]
    print(tabulate(rows, headers=["스프레드", "레벨(bp)", "전일대비"], tablefmt="github"))
    print()

    anomalies = curve.flag_anomalies(date, lookback=60)
    print(
        f"[3] 이상치 탐지  (lookback={anomalies['lookback_business_days']}영업일, "
        f"실제 확보={anomalies['actual_window_days']}영업일)"
    )
    rows = []
    for k in curve.ALL_METRIC_KEYS:
        m = anomalies["metrics"][k]
        rows.append(
            [
                LABELS.get(k, k),
                f"{m['change_bp']:+.1f}bp" if m['change_bp'] is not None else "-",
                f"{m['std_bp']:.2f}" if m['std_bp'] is not None else "-",
                f"{m['z_score']:+.2f}" if m['z_score'] is not None else "-",
                "⚠ 이상치" if m["is_anomaly"] else "",
            ]
        )
    print(tabulate(rows, headers=["지표", "일간변동", "60일 표준편차(bp)", "z-score", "플래그"], tablefmt="github"))


if __name__ == "__main__":
    main()
