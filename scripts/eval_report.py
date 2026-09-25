"""생성된 브리핑 리포트의 품질을 코드로만 채점한다 (LLM 호출 없음).

4가지 지표:
  1. 비교 기준(전일/전주/percentile 등) 없이 등장한 %/bp 숫자의 비율
  2. 섹션별 "운용 시사점" 존재 여부
  3. 근거 없는 인과 서술(고정 면책 문구도, 출처 표시도 없는 "때문"/"영향" 류) 개수
  4. logs/{date}.jsonl의 마지막 run_end 이벤트에 기록된 verify.py 불일치 개수

리포트 텍스트와 로그 파일만 읽는다 - 실제 ECOS/DART/Anthropic API를
호출하지 않는다.

실행:
    python scripts/eval_report.py 2026-09-23
    python scripts/eval_report.py --report-path path/to/report.md --log-path path/to/log.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bond_agent.config import LOGS_DIR, REPORTS_DIR

NUMBER_PATTERN = re.compile(r"[+-]?\d[\d,]*\.?\d*\s*(?:%|bp)")

_BASELINE_KEYWORDS = [
    "전일", "전주", "전월", "동월", "1주", "1개월", "1년",
    "percentile", "퍼센타일", "%ile", "분위",
    "regime", "타이트", "중립", "와이드",
    "온도", "강세", "약세",
    "이동평균", "MA20", "20일",
]

_CAUSAL_MARKERS = [
    "때문", "영향", "요인", "기인", "배경으로", "여파",
    "판단", "추정", "시그널로", "신호로",
]
# 주의: "원인 불명확"류 문구는 일부러 면책 목록에 넣지 않았다. 실제 구 리포트에서
# "원인 불명확한 부분이 있으나 ... 주요 배경으로 추정됨"처럼 한 문장 안에서 먼저
# 애매하다고 인정한 뒤 바로 이어서 근거 없는 인과를 단정하는 패턴이 나왔는데,
# 이 문구를 면책으로 넣으면 그런 문장까지 "출처 있음"으로 잘못 봐준다. 새
# 프롬프트가 요구하는 고정 문구("수급/뉴스상 뚜렷한 원인 확인 안 됨")만 면책으로
# 인정하고, 그 문구가 문장의 전부(즉 인과를 단정하는 말이 더 붙지 않음)일 때만
# 빠지게 한다.
_CAUSAL_EXCUSE_MARKERS = [
    "수급/뉴스상 뚜렷한 원인 확인 안 됨",
    "출처",
    '"',  # 기사 제목 인용
    "「", "」", "'", "'",
]

_SECTION_HEADER_PATTERN = re.compile(r"^##\s+\d+\.\s*(.+)$", re.MULTILINE)
_SECTIONS_REQUIRING_INSIGHT = ["금리 동향", "국채수급", "크레딧", "특이사항"]


def _split_sentences(text: str) -> list[str]:
    """한국어 문장 종결(다./요./함./음. + 줄바꿈) 기준으로 대충 쪼갠다.

    정확한 NLP 문장 분리가 아니라, "숫자 하나와 그 주변 맥락"을 묶어보기
    위한 휴리스틱이다.
    """
    rough = re.split(r"(?<=[다요함음]\.)\s+|\n+", text)
    return [s.strip() for s in rough if s.strip()]


def numbers_without_baseline(text: str) -> dict[str, Any]:
    """%/bp 숫자가 들어간 문장 중, 비교 기준 키워드가 같은 문장에 없는 비율."""
    sentences_with_numbers = [s for s in _split_sentences(text) if NUMBER_PATTERN.search(s)]
    total = len(sentences_with_numbers)
    without_baseline = [
        s for s in sentences_with_numbers if not any(kw in s for kw in _BASELINE_KEYWORDS)
    ]
    pct = round(100 * len(without_baseline) / total, 1) if total else 0.0
    return {
        "total_sentences_with_numbers": total,
        "sentences_without_baseline": len(without_baseline),
        "pct_without_baseline": pct,
        "examples": without_baseline[:5],
    }


def sections_missing_insight(text: str) -> dict[str, Any]:
    """섹션별로 "운용 시사점"이 있는지 확인.

    섹션 경계는 "## N. 제목" 헤더로 나눈다. 체크포인트(6번)는 스펙상 운용
    시사점을 요구하지 않으므로 제외한다.
    """
    headers = list(_SECTION_HEADER_PATTERN.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(headers):
        title = m.group(1).strip()
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        sections[title] = text[start:end]

    missing = []
    for title in _SECTIONS_REQUIRING_INSIGHT:
        body = sections.get(title)
        if body is None:
            continue  # 그 섹션 자체가 없으면(구 포맷 등) 이 체크 대상에서 제외
        if "운용 시사점" not in body:
            missing.append(title)

    return {
        "sections_found": list(sections.keys()),
        "sections_checked": [t for t in _SECTIONS_REQUIRING_INSIGHT if t in sections],
        "sections_missing_insight": missing,
    }


def unsourced_causal_statements(text: str) -> dict[str, Any]:
    """근거(출처/인용/면책 문구) 없이 인과 관계를 서술한 문장을 센다."""
    flagged = []
    for sentence in _split_sentences(text):
        if not any(marker in sentence for marker in _CAUSAL_MARKERS):
            continue
        if any(marker in sentence for marker in _CAUSAL_EXCUSE_MARKERS):
            continue
        flagged.append(sentence)
    return {"count": len(flagged), "examples": flagged[:5]}


def _last_run_end(log_path: Path) -> Optional[dict[str, Any]]:
    if not log_path.exists():
        return None
    last: Optional[dict[str, Any]] = None
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "run_end":
                last = event
    return last


def verify_mismatches_from_log(log_path: Path) -> dict[str, Any]:
    run_end = _last_run_end(log_path)
    if run_end is None:
        return {"found_log": False, "count": None, "mismatches": []}
    mismatches = run_end.get("verification_mismatches", [])
    return {"found_log": True, "count": len(mismatches), "mismatches": mismatches}


def _strip_verify_warning_section(text: str) -> str:
    """agent.verify.annotate_with_warnings가 하단에 붙인 '## ⚠ 검증 경고' 섹션은
    모델이 쓴 리포트 본문이 아니라 verify.py가 기계적으로 덧붙인 메타 텍스트다.
    이 함수들이 채점 대상으로 보면 안 되므로(예: 경고 문구 자체의 "추정했거나"가
    인과 서술로 오탐되는 문제가 실제로 있었음) 채점 전에 잘라낸다.
    """
    idx = text.find("## ⚠ 검증 경고")
    return text[:idx] if idx != -1 else text


def evaluate_report(report_text: str, log_path: Path) -> dict[str, Any]:
    """리포트 텍스트 + 로그 파일 하나를 4개 지표로 채점한다."""
    body = _strip_verify_warning_section(report_text)
    return {
        "numbers_without_baseline": numbers_without_baseline(body),
        "sections_missing_insight": sections_missing_insight(body),
        "unsourced_causal_statements": unsourced_causal_statements(body),
        "verify_mismatches": verify_mismatches_from_log(log_path),
    }


def evaluate_report_file(date: str, report_path: Optional[Path] = None, log_path: Optional[Path] = None) -> dict[str, Any]:
    report_path = report_path or (REPORTS_DIR / f"{date}.md")
    log_path = log_path or (LOGS_DIR / f"{date}.jsonl")
    text = report_path.read_text(encoding="utf-8")
    result = evaluate_report(text, log_path)
    result["date"] = date
    result["report_path"] = str(report_path)
    return result


def main() -> None:
    # Windows 콘솔(cp949) 등 UTF-8이 아닌 스트림에서도 한글 출력이 깨지지 않도록.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", nargs="?", help="YYYY-MM-DD (reports/logs 디렉토리 기본 경로 사용)")
    parser.add_argument("--report-path", type=str, default=None)
    parser.add_argument("--log-path", type=str, default=None)
    args = parser.parse_args()

    if args.report_path:
        report_path = Path(args.report_path)
        log_path = Path(args.log_path) if args.log_path else None
        text = report_path.read_text(encoding="utf-8")
        result = evaluate_report(text, log_path or Path("__no_log__"))
        result["report_path"] = str(report_path)
    else:
        if not args.date:
            parser.error("date 인자 또는 --report-path 중 하나는 필요합니다.")
        result = evaluate_report_file(args.date)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
