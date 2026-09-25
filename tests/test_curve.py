"""curve.py 단위 테스트: 가짜 데이터로 bp 변환 / 스프레드 / z-score 계산을 검증.

ecos.get_kr_yields, fred.get_us_yields 를 monkeypatch 해서 실제 API를 호출하지
않는다.
"""

from __future__ import annotations

import datetime as dt
import statistics

import pytest

from bond_agent.analytics import curve


def _weekdays_ending(end_date: str, n: int) -> list[str]:
    """end_date로 끝나는 평일(주말만 제외) n개 날짜를 오름차순으로 반환."""
    d = dt.date.fromisoformat(end_date)
    days: list[str] = []
    while len(days) < n:
        if d.weekday() < 5:  # 0=월 ... 4=금
            days.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return list(reversed(days))


@pytest.fixture
def fake_dates() -> list[str]:
    return _weekdays_ending("2026-09-24", 70)


@pytest.fixture
def fake_kr_us_series(fake_dates):
    """70영업일치 가짜 KR/US 시계열. 마지막 날 ktb_10y에 큰 점프를 넣어 이상치를 만든다."""
    kr = {
        "base_rate": {},
        "ktb_3y": {},
        "ktb_5y": {},
        "ktb_10y": {},
        "ktb_30y": {},
        "corp_aa_minus_3y": {},
        "corp_bbb_minus_3y": {},
    }
    us = {"dgs2": {}, "dgs10": {}}

    for i, day in enumerate(fake_dates):
        kr["base_rate"][day] = 2.50
        kr["ktb_3y"][day] = 2.80 + 0.001 * i
        kr["ktb_5y"][day] = 2.90 + 0.001 * i
        kr["ktb_10y"][day] = 3.00 + 0.001 * i
        kr["ktb_30y"][day] = 3.10 + 0.001 * i
        kr["corp_aa_minus_3y"][day] = 3.60 + 0.001 * i
        kr["corp_bbb_minus_3y"][day] = 6.50 + 0.001 * i

        us_day = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
        us["dgs2"][us_day] = 3.60 + 0.0005 * i
        us["dgs10"][us_day] = 4.00 + 0.0005 * i

    # 마지막 날 국고10년에 50bp(0.50%p) 점프 -> 큰 z-score 기대
    last_day = fake_dates[-1]
    kr["ktb_10y"][last_day] = kr["ktb_10y"][last_day] + 0.50

    return kr, us


@pytest.fixture(autouse=True)
def _patch_tools(monkeypatch, fake_kr_us_series):
    kr, us = fake_kr_us_series

    def fake_get_kr_yields(start_date, end_date):
        series = {
            k: {d: v for d, v in s.items() if start_date <= d <= end_date} for k, s in kr.items()
        }
        return {"source": "ECOS", "table_code": "TEST", "start_date": start_date, "end_date": end_date, "series": series}

    def fake_get_us_yields(start_date, end_date):
        series = {
            k: {d: v for d, v in s.items() if start_date <= d <= end_date} for k, s in us.items()
        }
        return {"source": "FRED", "start_date": start_date, "end_date": end_date, "series": series}

    monkeypatch.setattr(curve.ecos, "get_kr_yields", fake_get_kr_yields)
    monkeypatch.setattr(curve.fred, "get_us_yields", fake_get_us_yields)


def test_bp_helper_converts_percent_points_to_bp():
    assert curve._bp(3.05, 3.00) == pytest.approx(5.0)
    assert curve._bp(2.90, 3.00) == pytest.approx(-10.0)
    assert curve._bp(None, 3.00) is None


def test_calc_daily_changes_matches_manual_bp(fake_dates):
    date = fake_dates[-1]
    prev = fake_dates[-2]

    result = curve.calc_daily_changes(date)

    assert result["as_of_date"] == date
    assert result["prev_business_date"] == prev
    # ktb_3y는 하루당 0.001%p = 0.1bp씩 증가하도록 만들었다.
    assert result["changes_bp"]["ktb_3y"] == pytest.approx(0.1, abs=1e-6)
    # ktb_10y는 마지막 날 +50bp 점프를 추가로 심었다.
    assert result["changes_bp"]["ktb_10y"] == pytest.approx(0.1 + 50.0, abs=1e-6)


def test_calc_spreads_levels_and_asof_dates(fake_dates):
    date = fake_dates[-1]

    result = curve.calc_spreads(date)

    # 국고 3/10 스프레드 = ktb_10y - ktb_3y (기준일 레벨 그대로, %p 단위)
    assert result["levels"]["ktb_3_10"] == pytest.approx(0.2 + 0.50, abs=1e-6)
    # bp 단위 환산도 같이 제공되어야 한다.
    assert result["levels_bp"]["ktb_3_10"] == pytest.approx((0.2 + 0.50) * 100, abs=1e-4)
    # 한미 10년 금리차 사용 시, 미국 값은 기준일 하루 전(캘린더) 이하의 최신값이어야 한다.
    assert result["us_asof_date"] < date


def test_flag_anomalies_detects_seeded_jump(fake_dates):
    date = fake_dates[-1]

    result = curve.flag_anomalies(date, lookback=60)

    ktb10 = result["metrics"]["ktb_10y"]
    assert ktb10["is_anomaly"] is True
    assert ktb10["z_score"] is not None and abs(ktb10["z_score"]) > 2

    # 매일 거의 일정하게 움직이는 ktb_3y는 이상치가 아니어야 한다.
    ktb3 = result["metrics"]["ktb_3y"]
    assert ktb3["is_anomaly"] is False


def test_flag_anomalies_zscore_matches_manual_pstdev(fake_dates):
    date = fake_dates[-1]

    result = curve.flag_anomalies(date, lookback=60)

    # kr_us_10y 스프레드로 z-score 계산식을 수동 검증한다.
    metric = result["metrics"]["kr_us_10y"]
    if metric["std_bp"] is not None:
        # curve.py는 z_score/std_bp를 소수 4자리로 반올림해서 반환하므로 tolerance를 맞춘다.
        assert metric["z_score"] == pytest.approx(metric["change_bp"] / metric["std_bp"], abs=1e-3)


def test_classify_curve_label_seeded_jump_is_steepening(fake_dates):
    # 마지막 날은 ktb_10y만 +50bp 튀고 ktb_3y는 평소처럼 거의 안 움직임
    # -> 3-10 스프레드가 크게 벌어짐 -> 스티프닝. 단, ktb_3y 변동은 threshold(1bp) 아래라
    # "불/베어" 방향 조건(둘 다 threshold 초과)을 만족 못해 "혼조" 취급 -> label은 "스티프닝".
    date = fake_dates[-1]

    result = curve.classify_curve_label(date)

    assert result["as_of_date"] == date
    assert result["spread_change_bp"] > curve.CURVE_LABEL_PARALLEL_THRESHOLD_BP
    assert result["label"] == "스티프닝"


def test_classify_curve_label_quiet_day_is_flat(fake_dates):
    # 시드된 점프가 없는 평소 날짜는 3y/10y 둘 다 하루 0.1bp만 움직여 threshold(1bp) 아래
    # -> 평행이동 + 혼조 -> "보합".
    date = fake_dates[-2]

    result = curve.classify_curve_label(date)

    assert abs(result["spread_change_bp"]) <= curve.CURVE_LABEL_PARALLEL_THRESHOLD_BP
    assert result["label"] == "보합"


def test_classify_curve_label_invalid_date_raises():
    # calc_daily_changes와 동일하게, ECOS 기준 한국 영업일이 아니거나 데이터가
    # 없는 날짜는 ValueError를 그대로 전파한다 (None 라벨로 조용히 넘어가지 않음).
    with pytest.raises(ValueError):
        curve.classify_curve_label("1999-01-04")
