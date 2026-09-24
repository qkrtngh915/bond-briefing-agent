"""ecos.py 스모크 테스트: 실제 ECOS API를 호출해서 값이 합리적인지 확인한다."""

from __future__ import annotations

import datetime as dt

import pytest

from bond_agent.config import ECOS_API_KEY
from bond_agent.tools import ecos

pytestmark = pytest.mark.skipif(not ECOS_API_KEY, reason="ECOS_API_KEY가 설정되지 않았습니다.")


def test_get_kr_yields_smoke():
    end = dt.date.today()
    start = end - dt.timedelta(days=30)
    result = ecos.get_kr_yields(start.isoformat(), end.isoformat())

    assert result["source"] == "ECOS"
    expected_keys = {
        "base_rate",
        "ktb_3y",
        "ktb_5y",
        "ktb_10y",
        "ktb_30y",
        "corp_aa_minus_3y",
        "corp_bbb_minus_3y",
    }
    assert set(result["series"].keys()) == expected_keys

    ktb_10y = result["series"]["ktb_10y"]
    assert len(ktb_10y) > 0, "최근 30일 안에는 국고채(10년) 값이 최소 하나는 있어야 한다"

    for date_str, value in ktb_10y.items():
        # 국고채(10년)은 상식적으로 0~10% 범위를 벗어나지 않는다.
        assert 0.0 < value < 10.0, f"{date_str} ktb_10y={value} 가 비정상적으로 보인다"

    base_rate = result["series"]["base_rate"]
    for date_str, value in base_rate.items():
        assert 0.0 <= value < 10.0, f"{date_str} base_rate={value} 가 비정상적으로 보인다"

    # 국고채 시리즈는 주말/휴일에는 값이 없어야 한다 (한국 영업일 기준).
    ktb_dates = sorted(ktb_10y.keys())
    for d in ktb_dates:
        weekday = dt.date.fromisoformat(d).weekday()
        assert weekday < 5, f"{d} 는 주말인데 ktb_10y 값이 존재한다"

    print("\nktb_10y 최근 값:", sorted(ktb_10y.items())[-5:])
    print("base_rate 최근 값:", sorted(base_rate.items())[-3:])
