"""tools/credit.py 스모크 테스트: 실제 KAP(한국자산평가) 공개 API를 호출해서 확인한다.

API 키가 필요 없는 공개 페이지의 AJAX 엔드포인트라 별도 조건 없이 항상 실행된다.
"""

from __future__ import annotations

from bond_agent.tools import credit


def test_get_credit_curve_smoke_has_expected_sectors_and_reasonable_values():
    result = credit.get_credit_curve("2026-09-23")

    assert result["source"] == "KAP(한국자산평가)"
    series = result["series"]
    assert len(series) > 30  # 국채/지방채/특수채/금융채/회사채 등 수십 개 시리즈

    # 회사채(공모, 무보증) AA-/BBB- 3년이 있어야 한다 (핵심 크레딧 지표).
    aa_key = "회사채(공모)_무보증_AA-"
    bbb_key = "회사채(공모)_무보증_BBB-"
    assert aa_key in series
    assert bbb_key in series
    assert "3Y" in series[aa_key]
    assert "3Y" in series[bbb_key]

    # 상식적인 범위(0~20%)인지, 그리고 BBB-가 AA-보다 항상 높은지(신용 스프레드 방향).
    for key, maturities in series.items():
        for label, value in maturities.items():
            assert 0.0 < value < 20.0, f"{key} {label}={value} 가 비정상적으로 보인다"
    assert series[bbb_key]["3Y"] > series[aa_key]["3Y"]


def test_get_credit_curve_history_returns_one_entry_per_date():
    dates = ["2026-09-22", "2026-09-23"]
    results = credit.get_credit_curve_history(dates)

    assert set(results.keys()) == set(dates)
    for date in dates:
        assert results[date]["date"] == date
