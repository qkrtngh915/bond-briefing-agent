"""캐시된 다중 회차 공시 2건으로 추출 결과와 원문 발췌를 나란히
docs/extract_check.md 에 저장한다 (사람이 눈으로 정확도를 검증하기 위함).

실행:
    LLM_BACKEND=anthropic python scripts/generate_extract_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.extract import extract_filing
from agent.loop import _default_backend
from bond_agent.config import BASE_DIR
from bond_agent.tools import dart

# (rcept_no, 회사명/설명) - 둘 다 실제로 2개 회차(만기)가 함께 실린 공시로 확인됨.
CASES = [
    ("20260921000241", "SK지오센트릭 [기재정정]증권신고서(채무증권) - 26-1회/26-2회"),
    ("20260921000404", "대한전선 [발행조건확정]증권신고서(채무증권) - 155-1회/155-2회"),
]


def _fmt_tranche(t: dict) -> str:
    lines = [f"  - series_label: {t.get('series_label')}"]
    for k in [
        "maturity",
        "initial_offering_amount_billion_won",
        "demand_participation_amount_billion_won",
        "competition_ratio",
        "coupon_guidance_band_bp",
        "final_spread_bp",
        "upsized",
        "final_issue_amount_billion_won",
    ]:
        lines.append(f"    {k}: {t.get(k)}")
    flags = t.get("_validation", {}).get("flags", [])
    lines.append(f"    _validation.flags: {flags if flags else '(없음)'}")
    return "\n".join(lines)


def main() -> None:
    backend = _default_backend("2026-09-21")

    out_lines = [
        "# 다중 회차 추출 정확도 검증 (Task 1)",
        "",
        "캐시된 실제 DART 공시 2건에 대해, LLM 추출 결과와 원문 발췌(수요예측",
        "섹션 앞부분)를 나란히 놓았다. 눈으로 확인할 것: 회차별로 숫자가 올바르게",
        "분리되어 들어갔는지, 원문에 없는 값이 null로 남아있는지.",
        "",
        "**발견 및 수정한 문제**: 첫 실행에서 대한전선 케이스의 모집금액이 "
        "\"이백일십억원(₩21,000,000,000)\"인데 210억이 아니라 21억으로 추출됨 "
        "(10배 축소, 억 단위 환산 오류). agent/extract.py의 시스템 프롬프트에 "
        "\"괄호 안 원화 숫자를 1억으로 나눠서 환산하라\"는 지시를 추가한 뒤 "
        "재실행하니 210억/280억으로 정확히 나왔다 (아래 결과는 수정 후 값). "
        "다만 이건 이번에 우연히 잡은 사례일 뿐, 다른 금액 표기 방식에서 "
        "같은 종류의 오류가 또 날 수 있으니 숫자는 항상 원문과 대조할 것.",
        "",
    ]

    for rcept_no, label in CASES:
        doc = dart.fetch_filing_text(rcept_no)
        section = doc["demand_forecast_section"] or ""
        result = extract_filing(section, backend)

        out_lines.append(f"## {label} (rcept_no={rcept_no})")
        out_lines.append("")
        out_lines.append(f"- issuer: {result['issuer']}")
        out_lines.append(f"- credit_rating: {result['credit_rating']}")
        out_lines.append(f"- 회차 수: {len(result['tranches'])}")
        out_lines.append("")
        out_lines.append("### 추출 결과")
        out_lines.append("")
        out_lines.append("```")
        for t in result["tranches"]:
            out_lines.append(_fmt_tranche(t))
            out_lines.append("")
        out_lines.append("```")
        out_lines.append("")
        out_lines.append("### 원문 발췌 (수요예측 섹션, 앞 2500자)")
        out_lines.append("")
        out_lines.append("```")
        out_lines.append(section[:2500])
        out_lines.append("```")
        out_lines.append("")
        out_lines.append("---")
        out_lines.append("")

    out_path = BASE_DIR / "docs" / "extract_check.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
