"""최근 N일 회사채 수요예측 공시를 일괄 추출해서 data/extracted/ 캐시를 채우는 스크립트.

get_bond_demand_forecasts는 실행당 신규 추출을 5건으로 제한하기 때문에, 검색
기간이 길거나 밀린 공시가 많으면 캐시가 다 채워지기까지 여러 번의 브리핑
실행이 필요하다. 이 스크립트로 한 번에 미리 캐시를 채워두면 그 뒤 브리핑
실행에서는 캐시만 읽는다.

기본은 --dry-run: 실제로 추출하지 않고 대상 건수/목록만 출력한다.
--dry-run 없이 실행하면 실제 Anthropic 호출이 발생한다 (비용 주의).

실행:
    python scripts/backfill_extract.py --days 14              # 대상 건수만 출력 (기본 dry-run)
    python scripts/backfill_extract.py --days 14 --no-dry-run # 실제로 추출 (비용 발생)
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.extract import extract_filing_cached, load_cached_extraction
from agent.loop import _default_backend
from bond_agent.tools import dart

_KST = dt.timezone(dt.timedelta(hours=9))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=14, help="오늘(KST)로부터 며칠 전까지 조회할지 (기본 14일)")
    parser.add_argument("--end-date", type=str, default=None, help="기준일 YYYY-MM-DD (생략 시 오늘 KST)")
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="실제로 추출하지 않고 대상 건수/목록만 출력 (기본값)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="실제로 추출을 실행한다 (Anthropic 실제 호출, 비용 발생)",
    )
    args = parser.parse_args()

    end = dt.date.fromisoformat(args.end_date) if args.end_date else dt.datetime.now(_KST).date()
    start = end - dt.timedelta(days=args.days)

    print(f"조회 기간: {start.isoformat()} ~ {end.isoformat()} ({args.days}일)")
    filings = dart.list_bond_filings(start.isoformat(), end.isoformat())
    print(f"전체 공시(증권신고서/정정신고서): {len(filings)}건")

    already_cached = [f for f in filings if load_cached_extraction(f["rcept_no"]) is not None]
    to_extract = [f for f in filings if load_cached_extraction(f["rcept_no"]) is None]

    print(f"이미 캐시됨: {len(already_cached)}건")
    print(f"신규 추출 대상: {len(to_extract)}건")
    for f in to_extract:
        print(f"  - {f['rcept_dt']} {f['corp_name']} {f['report_nm']} (rcept_no={f['rcept_no']})")

    if args.dry_run:
        print("\n--dry-run 모드라 실제 추출은 하지 않았습니다. 실행하려면 --no-dry-run을 추가하세요.")
        return

    if not to_extract:
        print("\n신규 추출 대상이 없습니다.")
        return

    backend = _default_backend(end.isoformat())
    for i, filing in enumerate(to_extract, 1):
        rcept_no = filing["rcept_no"]
        print(f"[{i}/{len(to_extract)}] {filing['corp_name']} ({rcept_no}) 추출 중...")
        doc = dart.fetch_filing_text(rcept_no)
        section = doc["demand_forecast_section"]
        if not section:
            print("  수요예측 섹션을 찾지 못해 스킵")
            continue
        extract_filing_cached(rcept_no, section, backend)
        print("  완료")

    print("\n백필 완료.")


if __name__ == "__main__":
    main()
