"""최근 회사채 수요예측 공시 중 몇 건을 골라, 원문(수요예측 섹션)과 LLM 추출
결과를 나란히 출력해서 사람이 눈으로 검증할 수 있게 하는 스크립트.

DART_API_KEY가 필요하다 (opendart.fss.or.kr에서 발급). 아직 키가 없으면
이 스크립트는 바로 에러를 내며 종료한다 - 코드/로직 자체는 완성돼 있으니
키가 생기면 바로 실행해서 확인하면 된다.

실행:
    python scripts/review_bond_extractions.py                    # 최근 7일, 최대 3건
    python scripts/review_bond_extractions.py 2026-09-01 2026-09-24 5
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.extract import extract_filing
from agent.loop import _default_backend
from bond_agent.tools import dart


def main() -> None:
    if len(sys.argv) >= 3:
        start_date, end_date = sys.argv[1], sys.argv[2]
    else:
        end = dt.date.today()
        start = end - dt.timedelta(days=7)
        start_date, end_date = start.isoformat(), end.isoformat()
    limit = int(sys.argv[3]) if len(sys.argv) >= 4 else 3

    print(f"[{start_date} ~ {end_date}] 회사채 증권신고서(채무증권) 공시 검색 중...")
    filings = dart.list_bond_filings(start_date, end_date)
    print(f"총 {len(filings)}건 발견, 상위 {min(limit, len(filings))}건만 검토합니다.\n")

    backend = _default_backend(end_date)

    for filing in filings[:limit]:
        print("=" * 80)
        print(f"{filing['corp_name']} | {filing['report_nm']} | {filing['rcept_dt']} | rcept_no={filing['rcept_no']}")
        print("=" * 80)

        doc = dart.fetch_filing_text(filing["rcept_no"])
        section = doc["demand_forecast_section"]

        if not section:
            print("(수요예측 섹션을 찾지 못했습니다. full_text_length=" + str(doc["full_text_length"]) + ")\n")
            continue

        print("--- 원문 (수요예측 섹션, 앞부분 3000자) ---")
        print(section[:3000])
        print()

        extraction = extract_filing(section, backend)
        print("--- LLM 추출 결과 ---")
        for key, value in extraction.items():
            if key == "_validation":
                continue
            print(f"  {key}: {value}")
        flags = extraction["_validation"]["flags"]
        if flags:
            print("  ⚠ 검증 실패:")
            for f in flags:
                print(f"    - {f}")
        else:
            print("  검증 통과 (경쟁률/가산금리 일치)")
        print()


if __name__ == "__main__":
    main()
