"""OpenDART API 툴: 회사채 관련 증권신고서(채무증권) 공시 검색 + 원문에서
"수요예측" 관련 섹션 추출.

DART_API_KEY 없이는 동작하지 않는다 (opendart.fss.or.kr에서 무료 발급).

API 근거 (2026-09-24, OpenDART 개발가이드 확인):
- 공시검색: GET https://opendart.fss.or.kr/api/list.json
  파라미터: crtfc_key, bgn_de, end_de, pblntf_detail_ty, page_count 등.
  pblntf_detail_ty="C002" = "증권신고-채무" (회사채 등 채무증권).
  정정신고서는 별도 코드가 없고, report_nm에 "정정"이 포함된 형태로 같은
  C002에서 함께 조회된다 -> report_nm으로 판별.
- 공시서류원본: GET https://opendart.fss.or.kr/api/document.xml
  파라미터: crtfc_key, rcept_no. 응답은 zip(내부에 DART 자체 XML 포맷 문서).

순수 함수: 입력은 날짜/문자열, 출력은 JSON 직렬화 가능한 dict/list.
"""

from __future__ import annotations

import io
import re
import zipfile

import requests

from bond_agent.config import BASE_DIR, DART_API_KEY

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"

DART_CACHE_DIR = BASE_DIR / "data" / "cache" / "dart"
DART_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 공시상세유형: 증권신고-채무 (회사채 등 채무증권 관련 증권신고서/정정신고서)
_BOND_DETAIL_TYPE = "C002"

_DEMAND_FORECAST_HEADING_RE = re.compile(r"수요\s*예측")
_COMPETITION_RATIO_RE = re.compile(r"경쟁률")
_RESULT_SECTION_HEADING_RE = re.compile(r"수요\s*예측\s*결과")
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")

# "수요예측"은 문서 전체에 수십~백여 번 나온다 (모범규준 설명, 정정사항 목록 제목
# 등) - 실제로 확인해보니 "수요예측" 등장 밀도로 점수를 매기면, 정작 결과표가 아닌
# 공모희망금리 산정방법/모범규준 설명 문단(그 안에서 "수요예측"이라는 단어 자체가
# 반복돼서 점수가 높게 나옴)을 고르는 오류가 있었다. "경쟁률"은 실제 수요예측
# 결과표에만 나오는 훨씬 더 구체적인 신호지만, 이것만 보면 "동일등급 최근 발행
# 내역" 같은 (다른 회사의) 비교 대상 표도 걸릴 수 있다는 것도 실제 공시로 확인함
# (예: SK 최초 증권신고서에 NH투자증권/삼성증권 비교 발행 사례의 경쟁률이 나옴).
# 그래서 "경쟁률" 앞에 "수요예측결과" 표제가 실제로 있는지까지 확인해서, 자기
# 회사의 결과표가 맞는지 한 번 더 거른다. 그런 지점이 없으면(아직 수요예측 전인
# 공시 등) "수요예측" 밀도 점수로 대체한다. 완벽한 섹션 경계 탐지는 아닌
# 휴리스틱이므로, 추출 결과는 agent/extract.py의 LLM 추출 결과와 함께 사람이
# 원문을 눈으로 대조해야 한다 (scripts/review_bond_extractions.py).
_RESULT_HEADING_LOOKBACK_CHARS = 3000
_DEMAND_RESULT_KEYWORDS = ["경쟁률", "참여", "배수", "가산금리", "희망금리", "개별민평", "확정금리", "배정"]
_SECTION_WINDOW_CHARS = 6000
_SECTION_LEAD_CHARS = 500  # 경쟁률 표 앞의 회차/제목 같은 맥락도 같이 포함하기 위한 여유


def _check_configured() -> None:
    if not DART_API_KEY:
        raise RuntimeError(
            "DART_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요 (opendart.fss.or.kr에서 발급)."
        )


def _fetch_list_page(start: str, end: str, page_no: int) -> dict:
    params = {
        "crtfc_key": DART_API_KEY,
        "bgn_de": start,
        "end_de": end,
        "pblntf_detail_ty": _BOND_DETAIL_TYPE,
        "page_count": "100",
        "page_no": str(page_no),
    }
    resp = requests.get(DART_LIST_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def list_bond_filings(start_date: str, end_date: str) -> list[dict]:
    """기간 내 회사채 관련 증권신고서(채무증권) 및 정정신고서 목록.

    OpenDART의 pblntf_detail_ty=C002("증권신고-채무")는 일반 회사채뿐 아니라
    증권사의 ELS/DLS 등 파생결합증권 발행실적보고서/투자설명서도 함께 잡히고
    한 기간에 수백~수천 건일 수 있어(실측: 2026-08-01~09-25에 2,736건, 28페이지),
    페이지네이션으로 전부 가져온 다음 report_nm에 "증권신고서"가 실제로 들어간
    것만(=진짜 증권신고서/정정신고서) 남긴다.

    Args:
        start_date, end_date: "YYYY-MM-DD"

    Returns:
        [{"rcept_no": str, "corp_name": str, "report_nm": str, "rcept_dt": "YYYY-MM-DD",
          "is_correction": bool}, ...] 접수일자 내림차순.
    """
    _check_configured()
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")

    first_page = _fetch_list_page(start, end, 1)
    status = first_page.get("status")
    if status == "013":  # "조회된 데이타가 없습니다" - 정상적인 빈 결과
        return []
    if status != "000":
        raise RuntimeError(f"DART API 오류: {status} {first_page.get('message')}")

    rows = list(first_page.get("list", []))
    total_page = int(first_page.get("total_page", 1))
    for page_no in range(2, total_page + 1):
        page = _fetch_list_page(start, end, page_no)
        if page.get("status") != "000":
            break
        rows.extend(page.get("list", []))

    filings = []
    for row in rows:
        if "증권신고서" not in row["report_nm"]:
            continue  # 증권발행실적보고서/투자설명서 등 다른 채무증권 관련 공시는 제외
        rcept_dt = row["rcept_dt"]
        filings.append(
            {
                "rcept_no": row["rcept_no"],
                "corp_name": row["corp_name"],
                "report_nm": row["report_nm"],
                "rcept_dt": f"{rcept_dt[0:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}",
                "is_correction": "정정" in row["report_nm"],
            }
        )
    filings.sort(key=lambda f: f["rcept_dt"], reverse=True)
    return filings


def _extract_text_from_zip(zip_bytes: bytes) -> str:
    """zip 안의 XML(들)을 텍스트로 풀어낸다. 태그는 공백으로, 문단 경계로 보이는
    닫는 태그는 줄바꿈으로 바꿔서 최대한 문단 구조를 보존한다."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        parts = []
        for name in zf.namelist():
            raw = zf.read(name)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp949", errors="ignore")
            parts.append(text)
    combined = "\n".join(parts)
    combined = re.sub(r"</(P|TR|TITLE|TABLE)>", "\n", combined, flags=re.IGNORECASE)
    combined = _TAG_RE.sub(" ", combined)
    combined = _MULTI_SPACE_RE.sub(" ", combined)
    lines = [ln.strip() for ln in combined.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _find_demand_forecast_section(full_text: str) -> str | None:
    """수요예측 "결과"가 있는 지점을 찾아 그 주변을 잘라낸다.

    1순위: "수요예측결과" 표제 뒤에 나오는 "경쟁률" (자기 회사의 실제 결과표일
        가능성이 높음).
    2순위: "경쟁률"만 등장 (표제는 못 찾았지만 그래도 구체적인 신호).
    3순위(경쟁률이 아예 없는 공시 - 아직 수요예측 전이거나 결과 미기재):
        "수요예측" 등장 지점 중 수요예측 결과 관련 키워드가 가장 몰려 있는 곳.
    """
    ratio_matches = list(_COMPETITION_RATIO_RE.finditer(full_text))
    heading_matches = list(_RESULT_SECTION_HEADING_RE.finditer(full_text))

    for ratio_match in ratio_matches:
        lookback_start = max(0, ratio_match.start() - _RESULT_HEADING_LOOKBACK_CHARS)
        if any(lookback_start <= h.start() < ratio_match.start() for h in heading_matches):
            start = max(0, ratio_match.start() - _SECTION_LEAD_CHARS)
            return full_text[start : start + _SECTION_WINDOW_CHARS]

    if ratio_matches:
        start = max(0, ratio_matches[0].start() - _SECTION_LEAD_CHARS)
        return full_text[start : start + _SECTION_WINDOW_CHARS]

    matches = list(_DEMAND_FORECAST_HEADING_RE.finditer(full_text))
    if not matches:
        return None

    best_section = None
    best_score = -1
    for m in matches:
        candidate = full_text[m.start() : m.start() + _SECTION_WINDOW_CHARS]
        score = sum(candidate.count(k) for k in _DEMAND_RESULT_KEYWORDS)
        if score > best_score:
            best_score = score
            best_section = candidate
    return best_section


def fetch_filing_text(rcept_no: str) -> dict:
    """공시서류원본을 받아서 텍스트로 만들고 "수요예측" 관련 섹션만 잘라낸다.

    원문 zip은 data/cache/dart/{rcept_no}.zip 에 영구 캐시한다 (접수번호별
    원문 내용은 바뀌지 않으므로, ecos/fred처럼 "오늘 날짜"가 아니라 파일
    존재 여부로 캐시한다).

    Args:
        rcept_no: 접수번호 (14자리).

    Returns:
        {
          "rcept_no": rcept_no,
          "full_text_length": int,
          "demand_forecast_section": str | None,  # 못 찾으면 None
        }
    """
    _check_configured()
    cache_path = DART_CACHE_DIR / f"{rcept_no}.zip"
    if cache_path.exists():
        zip_bytes = cache_path.read_bytes()
    else:
        resp = requests.get(
            DART_DOCUMENT_URL,
            params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no},
            timeout=30,
        )
        resp.raise_for_status()
        zip_bytes = resp.content
        cache_path.write_bytes(zip_bytes)

    full_text = _extract_text_from_zip(zip_bytes)
    section = _find_demand_forecast_section(full_text)

    return {
        "rcept_no": rcept_no,
        "full_text_length": len(full_text),
        "demand_forecast_section": section,
    }
