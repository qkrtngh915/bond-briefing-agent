"""analytics/issuance.py 단위 테스트: 가짜 캐시 데이터로 집계 로직을 검증한다.
LLM/DART 실제 호출 없음 (dart.list_bond_filings, load_cached_extraction 모킹).
"""

from __future__ import annotations

from bond_agent.analytics import issuance


def _tranche(competition_ratio, offering, participation, band, final_spread, upsized):
    return {
        "series_label": "제1회",
        "competition_ratio": competition_ratio,
        "initial_offering_amount_billion_won": offering,
        "demand_participation_amount_billion_won": participation,
        "coupon_guidance_band_bp": band,
        "final_spread_bp": final_spread,
        "upsized": upsized,
    }


def test_calc_issuance_summary_aggregates_across_filings(monkeypatch):
    filings = [
        {"rcept_no": "1", "corp_name": "A사", "rcept_dt": "2026-09-22", "is_correction": False},
        {"rcept_no": "2", "corp_name": "B사", "rcept_dt": "2026-09-21", "is_correction": False},
        {"rcept_no": "3", "corp_name": "C사", "rcept_dt": "2026-09-20", "is_correction": False},  # 캐시 없음 -> 제외
    ]
    monkeypatch.setattr(issuance.dart, "list_bond_filings", lambda start, end: filings)

    import agent.extract as extract_module

    cache = {
        "1": {
            "credit_rating": "AA-",
            "tranches": [
                _tranche(1.63, 400, 650, {"low_bp": -40, "high_bp": 40}, 38, True),  # 밴드 안, 증액
            ],
        },
        "2": {
            "credit_rating": "BBB-",
            "tranches": [
                _tranche(0.8, 300, 240, {"low_bp": -20, "high_bp": 20}, -25, False),  # 밴드 하단 이하 -> 언더발행
            ],
        },
    }

    def fake_load(rcept_no):
        return cache.get(rcept_no)

    monkeypatch.setattr(extract_module, "load_cached_extraction", fake_load)

    result = issuance.calc_issuance_summary("2026-09-23", 5)

    assert result["num_tranches"] == 2
    assert result["total_offering_billion_won"] == 700.0
    assert result["total_participation_billion_won"] == 890.0
    assert result["avg_competition_ratio"] == round((1.63 + 0.8) / 2, 2)
    assert result["pct_under_issued"] == 50.0  # 2건 중 1건이 밴드 하단 이하
    assert result["pct_upsized"] == 50.0  # 2건 중 1건 증액
    assert result["by_rating"]["AA-"]["count"] == 1
    assert result["by_rating"]["BBB-"]["count"] == 1


def test_calc_issuance_summary_empty_when_nothing_cached(monkeypatch):
    monkeypatch.setattr(
        issuance.dart,
        "list_bond_filings",
        lambda start, end: [{"rcept_no": "1", "corp_name": "A사", "rcept_dt": "2026-09-22", "is_correction": False}],
    )

    import agent.extract as extract_module

    monkeypatch.setattr(extract_module, "load_cached_extraction", lambda rcept_no: None)

    result = issuance.calc_issuance_summary("2026-09-23", 5)

    assert result["num_tranches"] == 0
    assert result["total_offering_billion_won"] is None
    assert result["avg_competition_ratio"] is None
    assert result["temperature"] is None


def test_classify_temperature_hot_cold_neutral():
    assert issuance._classify_temperature(60, 3.5) == "강세"
    assert issuance._classify_temperature(5, 1.0) == "약세"
    assert issuance._classify_temperature(30, 2.0) == "중립"
    assert issuance._classify_temperature(None, 2.0) is None
