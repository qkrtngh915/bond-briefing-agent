"""calendar.py 단위 테스트: 이번 주 추석 연휴로 실제 공휴일 판정이 되는지 확인.

`holidays` 패키지는 실제 음력 계산이라 외부 API 없이도 정확하다 (모킹 불필요).
"""

from __future__ import annotations

from bond_agent.tools.calendar import is_kr_business_day


def test_chuseok_2026_is_not_a_business_day():
    # 2026년 추석 연휴: 9/24(목, 추석 연휴), 9/25(금, 추석) - holidays 패키지로 실측 확인.
    assert is_kr_business_day("2026-09-24") is False
    assert is_kr_business_day("2026-09-25") is False


def test_weekend_is_not_a_business_day():
    assert is_kr_business_day("2026-09-26") is False  # 토
    assert is_kr_business_day("2026-09-27") is False  # 일


def test_day_after_chuseok_holiday_resumes_as_business_day():
    assert is_kr_business_day("2026-09-28") is True  # 월, 연휴 끝나고 재개장


def test_ordinary_weekday_is_a_business_day():
    assert is_kr_business_day("2026-09-21") is True  # 월, 평범한 평일


def test_hangeul_day_is_not_a_business_day():
    assert is_kr_business_day("2026-10-09") is False  # 한글날
