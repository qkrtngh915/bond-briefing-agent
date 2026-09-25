"""오늘(KST)이 한국 영업일인지 확인하는 스크립트. GitHub Actions 워크플로가
이 종료 코드로 나머지 스텝(실제 브리핑 실행)을 건너뛸지 판단한다.

영업일이면 exit 0, 아니면 exit 1. --force가 있으면 공휴일/주말이어도
무조건 exit 0 (workflow_dispatch의 force 입력을 그대로 통과시키는 용도 -
수동 실행 때 "오늘은 공휴일이라 스킵됐다"를 무시하고 강제로 돌리고 싶을 때).

실행:
    python scripts/check_business_day.py                        # 오늘(KST) 기준
    python scripts/check_business_day.py 2026-09-24              # 특정 날짜 지정 (테스트용)
    python scripts/check_business_day.py 2026-09-23 --force      # 휴일이어도 강제 진행
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bond_agent.tools.calendar import is_kr_business_day

_KST = dt.timezone(dt.timedelta(hours=9))


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]
    date = args[0] if args else dt.datetime.now(_KST).date().isoformat()

    if is_kr_business_day(date):
        print(f"{date} 는 한국 영업일입니다. 브리핑을 진행합니다.")
        sys.exit(0)
    elif force:
        print(f"{date} 는 한국 영업일이 아니지만 --force가 지정되어 강제로 진행합니다.")
        sys.exit(0)
    else:
        print(f"{date} 는 한국 영업일이 아닙니다 (주말 또는 공휴일). 브리핑을 건너뜁니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()
