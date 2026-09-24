"""리포트 검증 레이어.

최종 리포트 텍스트에서 %/bp 단위가 붙은 숫자를 정규식으로 추출해서, 이번
실행에서 호출된 툴 결과에 실제로 존재하는 값인지 대조한다. 모델이 숫자를
스스로 계산했거나 잘못 인용했을 가능성을 잡아내기 위한 마지막 안전망이다.
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*(%|bp)")

# 단위별 허용 오차: 반올림/표시 방식 차이(예: 소수 1자리 표시 vs 원본 소수 4자리)를
# 오탐으로 잡지 않기 위함.
#
# 알려진 한계: "3~7bp대 하락"처럼 부호 없이 범위로 뭉뚱그려 쓰는 산문 표현은,
# 실제 값이 음수(-7.2)여도 여기서는 양수(7.0)로 파싱되어 오탐이 날 수 있다.
# 부호 방향이 아예 다른 경우까지 관용적으로 봐주면 진짜 오류(예: 부호가 뒤집힌
# 서술)도 놓치게 되므로, 이 케이스는 허용 오차를 늘려서 고치지 않고 사람이
# 검증 경고를 보고 "이건 그냥 범위 표현이었네" 하고 넘기는 쪽으로 남겨둔다.
_TOLERANCE = {"%": 0.01, "bp": 0.15}


def collect_known_numbers(tool_outputs: list[Any]) -> dict[str, set[float]]:
    """이번 실행에서 나온 모든 툴 결과의 숫자를 %/bp 그룹으로 모은다.

    필드 이름에 "bp"가 들어가면(changes_bp, levels_bp, std_bp 등) bp 그룹으로,
    z_score도 bp와 같은 오차 허용 범위로 비교하는 게 실무적으로 합리적이라
    bp 그룹에 넣는다. 그 외 숫자(레벨 %, DGS10 등)는 % 그룹으로 취급한다.

    문자열 값(예: search_news의 기사 제목 "美 10년물 국채금리 5.079%로 폭등")에
    박혀 있는 %/bp 숫자도 뽑아서 넣는다 - 실제로 모델이 뉴스 헤드라인의 숫자를
    그대로 인용했는데, 그게 숫자형 필드가 아니라 문자열 안에 있다는 이유만으로
    "확인 안 됨"으로 오탐 처리되는 문제가 실제 실행에서 발견됐다.
    """
    percent_values: set[float] = set()
    bp_values: set[float] = set()

    def _is_bp_key(key: str) -> bool:
        lowered = key.lower()
        return "bp" in lowered or "z_score" in lowered or "std" in lowered

    def _add_numbers_from_text(text: str) -> None:
        for match in _NUMBER_RE.finditer(text):
            value = round(float(match.group(1)), 4)
            if match.group(2) == "%":
                percent_values.add(value)
            else:
                bp_values.add(value)

    def walk(obj: Any, in_bp_context: bool) -> None:
        # in_bp_context는 조상 키들 중 하나라도 "bp 계열"이었으면 True로 유지된다
        # (예: changes_bp -> ktb_3y -> -3.4 처럼, 숫자 바로 위 키는 "ktb_3y"라 힌트가
        # 없지만 한 단계 위의 "changes_bp"가 이미 힌트를 준 상태이므로 계속 전파해야 함).
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, in_bp_context or _is_bp_key(k))
        elif isinstance(obj, list):
            for v in obj:
                walk(v, in_bp_context)
        elif isinstance(obj, bool):
            return
        elif isinstance(obj, (int, float)):
            if in_bp_context:
                bp_values.add(round(float(obj), 4))
            else:
                percent_values.add(round(float(obj), 4))
        elif isinstance(obj, str):
            _add_numbers_from_text(obj)

    for output in tool_outputs:
        walk(output, False)

    return {"%": percent_values, "bp": bp_values}


def verify_report(report_text: str, known_values: dict[str, set[float]]) -> list[dict[str, Any]]:
    """report_text에서 %/bp 숫자를 뽑아 known_values와 대조, 불일치 목록을 반환.

    Returns:
        [{"raw": "4.006%", "value": 4.006, "unit": "%"}, ...] (불일치한 것만)
    """
    mismatches = []
    for match in _NUMBER_RE.finditer(report_text):
        num_str, unit = match.group(1), match.group(2)
        try:
            value = float(num_str)
        except ValueError:
            continue
        tolerance = _TOLERANCE[unit]
        candidates = known_values.get(unit, set())
        if not any(abs(value - c) <= tolerance for c in candidates):
            mismatches.append({"raw": match.group(0), "value": value, "unit": unit})
    return mismatches


def annotate_with_warnings(report_text: str, mismatches: list[dict[str, Any]]) -> str:
    """불일치가 있으면 리포트 하단에 경고 섹션을 붙인다. 없으면 원문 그대로 반환."""
    if not mismatches:
        return report_text

    lines = [
        "",
        "## ⚠ 검증 경고",
        "다음 숫자가 이번 실행에서 호출한 툴 결과에서 확인되지 않았습니다 "
        "(모델이 계산/추정했거나 잘못 인용했을 가능성이 있습니다):",
        "",
    ]
    for m in mismatches:
        lines.append(f"- `{m['raw']}`")
    return report_text.rstrip() + "\n" + "\n".join(lines) + "\n"
