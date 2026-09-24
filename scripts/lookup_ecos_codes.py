"""ECOS StatisticTableList / StatisticItemList 로 통계표·항목 코드를 조회하는 1회성 스크립트.

bond_agent/config.py 에 코드를 하드코딩하기 전에, 이 스크립트로 실제 API 응답을
확인한다. 실행:

    python scripts/lookup_ecos_codes.py table "시장금리"
    python scripts/lookup_ecos_codes.py items 817Y002
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from bond_agent.config import ECOS_API_KEY, ECOS_BASE_URL


def find_tables(keyword: str) -> None:
    url = f"{ECOS_BASE_URL}/StatisticTableList/{ECOS_API_KEY}/json/kr/1/2000"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "StatisticTableList" not in data:
        print("응답 오류:", data)
        return
    rows = data["StatisticTableList"]["row"]
    hits = [r for r in rows if keyword in r.get("STAT_NAME", "")]
    for r in hits:
        print(r["STAT_CODE"], "|", r["STAT_NAME"], "|", r.get("CYCLE"), "|", r.get("SRCH_YN"))
    print(f"\n총 {len(hits)}건 (키워드: {keyword!r})")


def list_items(stat_code: str) -> None:
    url = f"{ECOS_BASE_URL}/StatisticItemList/{ECOS_API_KEY}/json/kr/1/2000/{stat_code}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "StatisticItemList" not in data:
        print("응답 오류:", data)
        return
    rows = data["StatisticItemList"]["row"]
    for r in rows:
        print(
            r.get("ITEM_CODE"),
            "|",
            r.get("ITEM_NAME"),
            "| parent:",
            r.get("P_ITEM_CODE"),
            r.get("P_ITEM_NAME"),
            "|",
            r.get("CYCLE"),
            "|",
            r.get("START_TIME"),
            "~",
            r.get("END_TIME"),
            "|",
            r.get("UNIT_NAME"),
        )
    print(f"\n총 {len(rows)}건 (통계표: {stat_code})")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    mode, arg = sys.argv[1], sys.argv[2]
    if not ECOS_API_KEY:
        print("ECOS_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하세요.")
        sys.exit(1)
    if mode == "table":
        find_tables(arg)
    elif mode == "items":
        list_items(arg)
    else:
        print(__doc__)
        sys.exit(1)
