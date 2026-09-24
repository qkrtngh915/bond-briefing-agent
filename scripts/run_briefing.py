"""데일리 채권시장 모닝 브리핑을 생성해서 reports/YYYY-MM-DD.md 로 저장한다.

실행:
    python scripts/run_briefing.py                # 최근 한국 영업일
    python scripts/run_briefing.py 2026-09-23      # 특정 날짜
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.loop import run_agent_loop
from bond_agent.config import REPORTS_DIR
from bond_agent.tools import ecos


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

    print(f"[{date}] 브리핑 생성 중...")
    result = run_agent_loop(date)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"{date}.md"
    out_path.write_text(result["report_markdown"], encoding="utf-8")

    print(f"저장: {out_path}")
    print(f"툴 호출 순서: {' -> '.join(result['tool_call_order'])}")
    print(f"턴 수: {result['num_turns']}")
    if result["verification_mismatches"]:
        print(f"검증 경고 {len(result['verification_mismatches'])}건 발견: {result['verification_mismatches']}")


if __name__ == "__main__":
    main()
