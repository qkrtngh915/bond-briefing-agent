"""tools/ktb_supply.py 스모크 테스트: 실제 ECOS API(191Y001)를 호출해서 확인한다."""

from __future__ import annotations

from bond_agent.tools import ktb_supply


def test_get_issuance_plan_smoke_has_reasonable_values():
    result = ktb_supply.get_issuance_plan("2026-07")

    assert result["source"] == "ECOS"
    assert result["table_code"] == "191Y001"
    assert result["month"] == "2026-07"
    assert result["prior_year_month"] == "2025-07"

    # 월 발행액은 십억원 단위 - 국고채권 월 발행 규모는 대략 수조~수십조원대.
    issuance = result["issuance_billion_won"]
    assert issuance is not None
    assert 100_000 < issuance < 5_000_000

    prior = result["prior_year_issuance_billion_won"]
    assert prior is not None
    assert 100_000 < prior < 5_000_000


def test_get_issuance_plan_future_month_has_null_issuance():
    # 아직 ECOS에 발표되지 않은 미래 월은 issuance_billion_won이 None이어야 한다.
    result = ktb_supply.get_issuance_plan("2027-12")

    assert result["issuance_billion_won"] is None
    assert result["yoy_change_pct"] is None
