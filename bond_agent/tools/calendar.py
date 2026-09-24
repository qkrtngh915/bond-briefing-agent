"""한국 영업일 판정: 주말 + `holidays` 패키지가 아는 한국 공휴일.

GitHub Actions 스케줄러(scripts/check_business_day.py)가 매 평일 아침에 이걸로
"오늘 브리핑을 돌려야 하는 날인지"를 판단한다. ecos.py의 국고채 데이터 자체도
휴일에는 값이 없어서 결과적으로 걸러지지만, 그건 "데이터가 이미 있는 과거
날짜"에만 통하는 방식이고, 스케줄러는 "아직 데이터가 없을 수도 있는 오늘"을
API를 부르기도 전에 걸러야 하므로 별도의 캘린더 판정이 필요하다.
"""

from __future__ import annotations

import datetime as dt

import holidays

_KR_HOLIDAYS = holidays.KR()


def is_kr_business_day(date: str) -> bool:
    """주말이 아니고 한국 공휴일도 아니면 True.

    Args:
        date: "YYYY-MM-DD"
    """
    d = dt.date.fromisoformat(date)
    if d.weekday() >= 5:  # 5=토, 6=일
        return False
    return d not in _KR_HOLIDAYS
