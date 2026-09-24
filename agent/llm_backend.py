"""LLM 백엔드 인터페이스.

agent/loop.py는 이 인터페이스(LLMBackend.create_message)에만 의존한다.
실제로 Anthropic Messages API를 호출하는지(AnthropicLLMBackend), 미리 정해둔
응답을 순서대로 재생하는지(MockLLMBackend)는 loop.py가 몰라도 된다.
bond_agent.config.LLM_BACKEND ("mock" | "anthropic")로 어느 쪽을 쓸지 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Union


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


Block = Union[TextBlock, ToolUseBlock]


@dataclass
class LLMResponse:
    content: list[Block]
    stop_reason: str  # "tool_use" | "end_turn" | 그 외


class LLMBackend(Protocol):
    def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


class AnthropicLLMBackend:
    """실제 Anthropic Messages API를 호출하는 백엔드 (비용 발생)."""

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic  # 지연 임포트: mock만 쓸 때는 필요 없음

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        api_messages = [
            {"role": m["role"], "content": self._to_api_content(m["content"])} for m in messages
        ]
        kwargs: dict[str, Any] = {}
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=api_messages,
            tools=tools,
            **kwargs,
        )
        content: list[Block] = []
        for block in response.content:
            if block.type == "text":
                content.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(ToolUseBlock(id=block.id, name=block.name, input=block.input))
        return LLMResponse(content=content, stop_reason=response.stop_reason)

    @staticmethod
    def _to_api_content(content: Any) -> Any:
        """loop.py가 쌓아둔 메시지 content(우리 Block 객체 또는 이미 API 형태인
        dict, 예: tool_result)를 실제 Anthropic API가 기대하는 형태로 바꾼다."""
        if isinstance(content, str):
            return content
        api_blocks = []
        for b in content:
            if isinstance(b, TextBlock):
                api_blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ToolUseBlock):
                api_blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            else:
                api_blocks.append(b)  # 이미 dict (예: tool_result)
        return api_blocks


class MockLLMBackend:
    """미리 정의한 LLMResponse 시퀀스를 순서대로 반환하는 가짜 백엔드.

    실제 API 키 없이 agent/loop.py의 tool-use 루프, logs/*.jsonl 기록,
    agent/verify.py 검증 흐름을 테스트하기 위한 것. system/messages/tools는
    인자로 받기만 하고 내용은 무시한다 (스크립트를 그대로 순서대로 재생함).
    """

    def __init__(self, script: list[LLMResponse]) -> None:
        self._script = script
        self.call_count = 0

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if self.call_count >= len(self._script):
            raise RuntimeError(
                f"MockLLMBackend: 스크립트({len(self._script)}단계)가 끝났는데 "
                f"{self.call_count + 1}번째 호출이 들어왔습니다."
            )
        response = self._script[self.call_count]
        self.call_count += 1
        return response


def build_demo_mock_script(date: str) -> list[LLMResponse]:
    """get_market_snapshot -> get_anomalies -> search_news -> 최종 텍스트 순서의
    시연용 스크립트.

    마지막 텍스트에 일부러 틀린 숫자("국고채 10년 100.0%")를 하나 심어 두어서,
    agent/verify.py가 실제로 그걸 잡아내는지(경고 섹션이 붙는지) 확인할 수 있게
    한다 - mock 실행이 검증 레이어까지 실제로 행사하도록 만든 의도적 장치다.
    """
    return [
        LLMResponse(
            content=[ToolUseBlock(id="mock_1", name="get_market_snapshot", input={"date": date})],
            stop_reason="tool_use",
        ),
        LLMResponse(
            content=[ToolUseBlock(id="mock_2", name="get_anomalies", input={"date": date})],
            stop_reason="tool_use",
        ),
        LLMResponse(
            content=[
                ToolUseBlock(
                    id="mock_3", name="search_news", input={"query": "기준금리 채권", "days": 3}
                )
            ],
            stop_reason="tool_use",
        ),
        LLMResponse(
            content=[
                TextBlock(
                    text=(
                        "## 1. 한 줄 요약\n"
                        "(MockLLM 시연용 리포트입니다 - 실제 모델 호출 없음)\n\n"
                        "## 2. 금리 동향\n"
                        "검증 레이어 테스트를 위해 이 줄에는 일부러 틀린 숫자를 넣습니다: "
                        "국고채 10년이 100.0%로 폭등했습니다.\n\n"
                        "## 3. 스프레드\n"
                        "(mock 시연이므로 생략)\n\n"
                        "## 4. 특이사항\n"
                        "특이사항 없음\n\n"
                        "## 5. 체크포인트\n"
                        "특별한 예정 이벤트 없음\n"
                    )
                )
            ],
            stop_reason="end_turn",
        ),
    ]
