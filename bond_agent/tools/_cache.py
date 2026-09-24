"""로컬 파일 캐시. 같은 날 같은 요청이면 API를 다시 호출하지 않는다.

캐시는 요청 인자(namespace + key)로 해시한 JSON 파일에 저장하고, 저장된 날짜가
오늘이면 그대로 재사용한다. 날짜가 바뀌면(다음 영업일) 다시 API를 호출해서
새로 캐시한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from bond_agent.config import CACHE_DIR


def _cache_path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{namespace}__{digest}.json"


def cached_call(namespace: str, key: str, fetch_fn: Callable[[], Any]) -> Any:
    """fetch_fn()의 결과를 오늘 날짜 기준으로 캐시한다.

    Args:
        namespace: 캐시 파일 구분용 접두어 (예: "ecos", "fred", "news").
        key: 요청을 고유하게 식별하는 문자열 (예: "kr_yields:2026-08-01:2026-09-24").
        fetch_fn: 캐시 미스일 때 실제로 호출할 함수. JSON 직렬화 가능한 dict/list를 반환해야 함.

    Returns:
        캐시되었거나 새로 가져온 값.
    """
    path = _cache_path(namespace, key)
    today = dt.date.today().isoformat()

    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = None
        if payload and payload.get("cached_on") == today and payload.get("key") == key:
            return payload["data"]

    data = fetch_fn()
    path.write_text(
        json.dumps({"cached_on": today, "key": key, "data": data}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data
