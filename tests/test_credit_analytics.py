"""analytics/credit.py 단위 테스트: 가짜 데이터로 스프레드/분위수/이동평균/등급간·섹터간
비교 로직을 검증한다. 실제 KAP/ECOS API는 호출하지 않는다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from bond_agent.analytics import credit as credit_analytics


def _weekdays_ending(end_date: str, n: int) -> list[str]:
    d = dt.date.fromisoformat(end_date)
    days: list[str] = []
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return list(reversed(days))


@pytest.fixture
def fake_dates() -> list[str]:
    return _weekdays_ending("2026-09-24", 40)


@pytest.fixture(autouse=True)
def _patch_tools(monkeypatch, fake_dates):
    # ecos.get_kr_yields: ktb_3y 캘린더용 (값 자체는 안 씀, 날짜 키만 씀).
    ktb_series = {d: 4.0 for d in fake_dates}

    def fake_get_kr_yields(start_date, end_date):
        series = {"ktb_3y": {d: v for d, v in ktb_series.items() if start_date <= d <= end_date}}
        return {"series": series}

    monkeypatch.setattr(credit_analytics.ecos, "get_kr_yields", fake_get_kr_yields)

    # credit_curve_history: 국고채 3Y는 고정 4.0%, AA-/BBB-/기타금융채/카드채/은행채는
    # 매일 조금씩 다르게(오래된 날일수록 스프레드가 좁았다가 최근에 넓어지는 패턴).
    def build_history(dates):
        history = {}
        for i, d in enumerate(dates):
            # i가 클수록 최근(다음 for문에서 오름차순으로 채움) - fake_dates가 오름차순이므로 그대로 index 사용.
            aa_level = 4.0 + 0.60 + 0.001 * i  # 스프레드가 점점 넓어짐
            bbb_level = 4.0 + 6.00 + 0.004 * i  # BBB-는 훨씬 빠르게 넓어짐 (gap 변화가 임계값을 확실히 넘도록)
            etc_level = 4.0 + 0.70 + 0.003 * i  # 기타금융채도 AA-보다 확실히 빠르게 넓어짐
            card_level = 4.0 + 0.65 + 0.001 * i
            bank_aaa_level = 4.0 + 0.10
            bank_aa_minus_level = 4.0 + 0.50 + 0.0005 * i
            history[d] = {
                "date": d,
                "series": {
                    credit_analytics._KTB_KEY: {"3Y": 4.0},
                    "회사채(공모)_무보증_AA-": {"3Y": round(aa_level, 4)},
                    "회사채(공모)_무보증_BBB-": {"3Y": round(bbb_level, 4)},
                    "금융채II_기타금융채_AA-": {"3Y": round(etc_level, 4)},
                    "금융채II_카드채_AA-": {"3Y": round(card_level, 4)},
                    "금융채I_은행채_AAA": {"3Y": round(bank_aaa_level, 4)},
                    "금융채I_은행채_AA-": {"3Y": round(bank_aa_minus_level, 4)},
                },
            }
        return history

    def fake_get_credit_curve_history(dates):
        full_history = build_history(fake_dates)
        return {d: full_history[d] for d in dates if d in full_history}

    monkeypatch.setattr(credit_analytics.credit_tool, "get_credit_curve_history", fake_get_credit_curve_history)


def test_calc_credit_context_computes_spread_and_changes(fake_dates):
    date = fake_dates[-1]
    result = credit_analytics.calc_credit_context(date, lookback_days=30)

    aa = result["metrics"]["회사채(공모)_무보증_AA-"]
    # 마지막 날 i=39, 스프레드 = (4.0+0.60+0.001*39 - 4.0)*100 = (0.639)*100 = 63.9bp
    assert aa["spread_bp"] == pytest.approx(63.9, abs=0.01)
    # 전일 대비: i=39 vs i=38 -> 0.001%p = 0.1bp
    assert aa["change_1d_bp"] == pytest.approx(0.1, abs=1e-6)
    # 1주(5영업일) 전 대비: 0.001*5%p = 0.5bp
    assert aa["change_1w_bp"] == pytest.approx(0.5, abs=1e-6)


def test_calc_credit_context_percentile_and_regime_reflect_widening_trend(fake_dates):
    date = fake_dates[-1]
    result = credit_analytics.calc_credit_context(date, lookback_days=30)

    aa = result["metrics"]["회사채(공모)_무보증_AA-"]
    # 스프레드가 계속 넓어지는 추세이므로, 오늘(가장 넓음)의 분위수는 100(최댓값)이어야 한다.
    assert aa["percentile"] == 100.0
    assert aa["regime"] == "와이드"


def test_calc_credit_context_ma20_and_vs_ma20(fake_dates):
    date = fake_dates[-1]
    result = credit_analytics.calc_credit_context(date, lookback_days=30)

    aa = result["metrics"]["회사채(공모)_무보증_AA-"]
    assert aa["ma20_bp"] is not None
    # 오늘 스프레드가 계속 넓어지는 추세의 최댓값이므로 20일 이동평균보다 넓어야 한다(양수).
    assert aa["vs_ma20_bp"] > 0


def test_calc_credit_context_grade_spread_widens_for_bbb_vs_aa(fake_dates):
    date = fake_dates[-1]
    result = credit_analytics.calc_credit_context(date, lookback_days=30)

    gap = result["grade_spreads"]["공모회사채 BBB- vs AA-"]
    # BBB-가 AA-보다 더 빨리 넓어지므로(0.002 vs 0.001), gap도 확대돼야 한다.
    assert gap["gap_bp"] > 0
    assert gap["change_1w_bp"] > 0
    assert gap["direction"] == "확대"


def test_calc_credit_context_sector_relative_movement(fake_dates):
    date = fake_dates[-1]
    result = credit_analytics.calc_credit_context(date, lookback_days=30)

    rel = result["sector_relative"]["기타금융채(여전채 proxy) vs 공모회사채 (AA-)"]
    # 기타금융채(0.0015)가 공모회사채 AA-(0.001)보다 더 빨리 넓어지므로 gap도 확대.
    assert rel["direction"] == "확대"


def test_classify_spread_regime_thresholds():
    assert credit_analytics.classify_spread_regime(10) == "타이트"
    assert credit_analytics.classify_spread_regime(50) == "중립"
    assert credit_analytics.classify_spread_regime(90) == "와이드"
    assert credit_analytics.classify_spread_regime(None) is None


def test_calc_credit_context_raises_for_non_business_day(fake_dates):
    with pytest.raises(ValueError):
        credit_analytics.calc_credit_context("2099-01-01", lookback_days=10)
