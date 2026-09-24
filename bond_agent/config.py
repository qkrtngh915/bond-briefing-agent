"""프로젝트 전역 설정: 경로, API 키, ECOS 통계표/항목 코드.

ECOS 통계표 코드와 항목 코드는 절대 추측해서 하드코딩하지 않는다.
scripts/lookup_ecos_codes.py 로 StatisticTableList / StatisticItemList API를
직접 조회해서 확인한 값만 아래에 채운다 (각 항목에 조회 근거를 주석으로 남긴다).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"

load_dotenv(BASE_DIR / ".env")

ECOS_API_KEY = os.environ.get("ECOS_API_KEY", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# agent/loop.py가 실제 tool-use 루프를 돌릴 때 쓰는 모델. 기본값은 이 환경의
# 최신 모델(Sonnet 5)이고, 환경변수 ANTHROPIC_MODEL로 덮어쓸 수 있다.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5"
DART_API_KEY = os.environ.get("DART_API_KEY", "")

# "mock"(기본값): API 키 없이 미리 정의한 tool_use 시퀀스를 재생하는 MockLLMBackend 사용.
# "anthropic": 실제 Anthropic Messages API 호출 (ANTHROPIC_API_KEY 필요, 비용 발생).
LLM_BACKEND = os.environ.get("LLM_BACKEND") or "mock"

ECOS_BASE_URL = "https://ecos.bok.or.kr/api"
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# ---------------------------------------------------------------------------
# ECOS 통계표/항목 코드
#
# scripts/lookup_ecos_codes.py 로 실제 API를 조회해서 확인한 값만 채운다.
#
#   python scripts/lookup_ecos_codes.py table "시장금리"
#     -> 817Y002 | 1.3.2.1. 시장금리(일별) | D | Y
#   python scripts/lookup_ecos_codes.py items 817Y002
#     -> 010200000 국고채(3년), 010200001 국고채(5년), 010210000 국고채(10년),
#        010230000 국고채(30년), 010300000 회사채(3년, AA-), 010320000 회사채(3년, BBB-)
#        (817Y002에는 "한국은행 기준금리"는 없다 — 별도 통계표에 있음)
#   python scripts/lookup_ecos_codes.py table "기준금리"
#     -> 722Y001 | 1.3.1. 한국은행 기준금리 및 여수신금리 | D | Y
#   python scripts/lookup_ecos_codes.py items 722Y001
#     -> 0101000 한국은행 기준금리 (CYCLE=D, 1999-05-06 ~)
#
# 위 조회는 2026-09-24에 실행해서 확인했고, 응답의 START_TIME/END_TIME으로
# 각 시리즈가 현재까지 갱신되고 있음을 확인했다 (예: 722Y001/0101000 D 값이
# 2026-09-24 무렵까지, 817Y002 항목들도 2026-09-23까지 존재).
# ---------------------------------------------------------------------------

# 시리즈 키 -> (통계표 코드, 항목 코드(ITEM_CODE1), 설명)
ECOS_SERIES_DEFS: dict[str, tuple[str, str, str]] = {
    "base_rate": ("722Y001", "0101000", "한국은행 기준금리"),
    "ktb_3y": ("817Y002", "010200000", "국고채(3년)"),
    "ktb_5y": ("817Y002", "010200001", "국고채(5년)"),
    "ktb_10y": ("817Y002", "010210000", "국고채(10년)"),
    "ktb_30y": ("817Y002", "010230000", "국고채(30년)"),
    "corp_aa_minus_3y": ("817Y002", "010300000", "회사채(3년, AA-)"),
    "corp_bbb_minus_3y": ("817Y002", "010320000", "회사채(3년, BBB-)"),
}
