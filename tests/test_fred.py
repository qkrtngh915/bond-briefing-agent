"""fred.py 스모크 테스트: 실제 FRED API를 호출해서 값이 합리적인지 확인한다."""

from __future__ import annotations

import datetime as dt

import pytest

from bond_agent.config import FRED_API_KEY
from bond_agent.tools import fred

pytestmark = pytest.mark.skipif(not FRED_API_KEY, reason="FRED_API_KEY가 설정되지 않았습니다.")


def test_get_us_yields_smoke():
    end = dt.date.today()
    start = end - dt.timedelta(days=14)
    result = fred.get_us_yields(start.isoformat(), end.isoformat())

    assert result["source"] == "FRED"
    assert set(result["series"].keys()) == {"dgs2", "dgs10", "sofr"}

    dgs10 = result["series"]["dgs10"]
    assert len(dgs10) > 0, "최근 2주 안에는 DGS10 값이 최소 하나는 있어야 한다"

    for date_str, value in dgs10.items():
        # 최근 미국 10년물은 상식적으로 0~15% 범위를 벗어나지 않는다.
        assert 0.0 < value < 15.0, f"{date_str} DGS10={value} 가 비정상적으로 보인다"

    print("\nDGS10 최근 값:", sorted(dgs10.items())[-5:])
