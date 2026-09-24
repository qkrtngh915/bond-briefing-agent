"""dart.py 단위 테스트: 실제 OpenDART API를 호출하지 않고(DART_API_KEY 없음)
파싱/캐시 로직만 가짜 응답으로 검증한다.

DART_API_KEY가 없는 동안은 이 테스트들만으로 로직을 검증하고, 키가 생기면
실제 API에 대한 스모크 테스트를 별도로 추가한다.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from bond_agent.tools import dart


class _FakeResponse:
    def __init__(self, *, json_data=None, content=b""):
        self._json_data = json_data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    monkeypatch.setattr(dart, "DART_API_KEY", "FAKE_KEY_FOR_TEST")


def test_list_bond_filings_parses_rows_and_flags_corrections(monkeypatch):
    payload = {
        "status": "000",
        "message": "정상",
        "list": [
            {
                "corp_code": "00111111",
                "corp_name": "가나다전자",
                "report_nm": "증권신고서(채무증권)",
                "rcept_no": "20260918000111",
                "rcept_dt": "20260918",
            },
            {
                "corp_code": "00222222",
                "corp_name": "라마바건설",
                "report_nm": "[정정]증권신고서(채무증권)",
                "rcept_no": "20260920000222",
                "rcept_dt": "20260920",
            },
        ],
    }
    monkeypatch.setattr(dart.requests, "get", lambda url, params, timeout: _FakeResponse(json_data=payload))

    filings = dart.list_bond_filings("2026-09-01", "2026-09-24")

    assert len(filings) == 2
    # 접수일자 내림차순
    assert filings[0]["rcept_dt"] == "2026-09-20"
    assert filings[0]["is_correction"] is True
    assert filings[0]["corp_name"] == "라마바건설"
    assert filings[1]["rcept_dt"] == "2026-09-18"
    assert filings[1]["is_correction"] is False


def test_list_bond_filings_filters_out_non_registration_statements(monkeypatch):
    """C002에는 증권신고서 외에도 증권발행실적보고서/투자설명서 등이 섞여 오는데,
    report_nm에 "증권신고서"가 없는 것들은 제외해야 한다 (실제 API로 확인한 동작)."""
    payload = {
        "status": "000",
        "message": "정상",
        "list": [
            {"corp_code": "1", "corp_name": "가나다전자", "report_nm": "증권신고서(채무증권)", "rcept_no": "1", "rcept_dt": "20260918"},
            {"corp_code": "2", "corp_name": "하나증권", "report_nm": "증권발행실적보고서", "rcept_no": "2", "rcept_dt": "20260919"},
            {"corp_code": "3", "corp_name": "대신증권", "report_nm": "투자설명서(일괄신고)", "rcept_no": "3", "rcept_dt": "20260919"},
        ],
    }
    monkeypatch.setattr(dart.requests, "get", lambda url, params, timeout: _FakeResponse(json_data=payload))

    filings = dart.list_bond_filings("2026-09-01", "2026-09-24")

    assert len(filings) == 1
    assert filings[0]["corp_name"] == "가나다전자"


def test_list_bond_filings_paginates_through_all_pages(monkeypatch):
    page1 = {
        "status": "000",
        "total_page": 2,
        "list": [{"corp_code": "1", "corp_name": "1페이지사", "report_nm": "증권신고서(채무증권)", "rcept_no": "1", "rcept_dt": "20260918"}],
    }
    page2 = {
        "status": "000",
        "total_page": 2,
        "list": [{"corp_code": "2", "corp_name": "2페이지사", "report_nm": "증권신고서(채무증권)", "rcept_no": "2", "rcept_dt": "20260919"}],
    }
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["page_no"])
        return _FakeResponse(json_data=page1 if params["page_no"] == "1" else page2)

    monkeypatch.setattr(dart.requests, "get", fake_get)

    filings = dart.list_bond_filings("2026-09-01", "2026-09-24")

    assert calls == ["1", "2"]
    assert {f["corp_name"] for f in filings} == {"1페이지사", "2페이지사"}


def test_list_bond_filings_empty_result_returns_empty_list(monkeypatch):
    monkeypatch.setattr(
        dart.requests,
        "get",
        lambda url, params, timeout: _FakeResponse(json_data={"status": "013", "message": "조회된 데이타가 없습니다"}),
    )
    assert dart.list_bond_filings("2026-01-01", "2026-01-02") == []


def test_list_bond_filings_raises_on_api_error(monkeypatch):
    monkeypatch.setattr(
        dart.requests,
        "get",
        lambda url, params, timeout: _FakeResponse(json_data={"status": "010", "message": "등록되지 않은 키"}),
    )
    with pytest.raises(RuntimeError):
        dart.list_bond_filings("2026-01-01", "2026-01-02")


def _make_zip(xml_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("document.xml", xml_text.encode("utf-8"))
    return buf.getvalue()


def test_extract_text_from_zip_preserves_paragraph_breaks_and_strips_tags():
    xml_text = (
        "<DOCUMENT><TITLE>증권신고서</TITLE>"
        "<P>가. 수요예측 및 배정에 관한 사항</P>"
        "<P>경쟁률은 2.5배 입니다.</P>"
        "</DOCUMENT>"
    )
    text = dart._extract_text_from_zip(_make_zip(xml_text))

    assert "<P>" not in text and "</P>" not in text
    assert "수요예측" in text
    assert "경쟁률은 2.5배 입니다." in text
    lines = text.splitlines()
    assert any("수요예측" in ln for ln in lines)


def test_fetch_filing_text_finds_demand_forecast_section_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(dart, "DART_CACHE_DIR", tmp_path)

    xml_text = (
        "<DOCUMENT><P>이 앞부분은 수요예측과 무관한 내용입니다.</P>"
        "<P>가. 수요예측 및 배정에 관한 사항</P>"
        "<P>경쟁률은 2.5배이며 참여금액은 1250억원입니다.</P></DOCUMENT>"
    )
    zip_bytes = _make_zip(xml_text)

    call_count = {"n": 0}

    def fake_get(url, params, timeout):
        call_count["n"] += 1
        return _FakeResponse(content=zip_bytes)

    monkeypatch.setattr(dart.requests, "get", fake_get)

    result = dart.fetch_filing_text("20260918000111")
    assert result["rcept_no"] == "20260918000111"
    assert "경쟁률은 2.5배" in result["demand_forecast_section"]
    assert call_count["n"] == 1
    assert (tmp_path / "20260918000111.zip").exists()

    # 두 번째 호출은 캐시를 써서 requests.get을 다시 부르지 않아야 한다.
    dart.fetch_filing_text("20260918000111")
    assert call_count["n"] == 1


def test_find_demand_forecast_section_prefers_own_results_over_comparables_table():
    """실제 SK 증권신고서에서 확인된 문제: "경쟁률"이 자기 회사 결과표가 아니라
    "동일등급 최근 발행내역" 비교표에도 나온다. "수요예측결과" 표제가 앞에 있는
    진짜 결과표를 우선해야 한다."""
    text = (
        "동일등급 무보증 공모회사채 발행내역 비교\n"
        "NH투자증권 경쟁률 983% 참여금액 11800억원 (다른 회사 비교 사례)\n"
        + ("padding " * 200)
        + "라. 수요예측결과\n"
        "(1) 수요예측 참여 내역\n"
        "경쟁률 2.5:1 참여금액 750억원 (자기 회사 실제 결과)"
    )

    section = dart._find_demand_forecast_section(text)

    assert "2.5:1" in section
    assert "자기 회사 실제 결과" in section


def test_find_demand_forecast_section_falls_back_to_first_ratio_without_heading():
    """"수요예측결과" 표제를 못 찾으면(예: 아직 결과가 없는 최초 신고서), 그래도
    첫 "경쟁률" 등장 지점을 쓴다 (비교표라도 완전히 못 찾는 것보다는 낫다)."""
    text = "동일등급 비교 발행내역: NH투자증권 경쟁률 983%" + ("padding " * 500)

    section = dart._find_demand_forecast_section(text)

    assert "983%" in section


def test_fetch_filing_text_returns_none_section_when_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(dart, "DART_CACHE_DIR", tmp_path)
    xml_text = "<DOCUMENT><P>이 공시에는 수요예측 언급이 전혀 없습니다 - 아 방금 언급했네요.</P></DOCUMENT>"
    # 일부러 "수요예측"이 실제로 등장하게 만들었으니, 완전히 없는 케이스로 다시 작성
    xml_text_no_match = "<DOCUMENT><P>이 공시에는 그 키워드가 전혀 없습니다.</P></DOCUMENT>"
    monkeypatch.setattr(dart.requests, "get", lambda url, params, timeout: _FakeResponse(content=_make_zip(xml_text_no_match)))

    result = dart.fetch_filing_text("20260918000999")
    assert result["demand_forecast_section"] is None
