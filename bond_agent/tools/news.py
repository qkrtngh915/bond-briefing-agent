"""뉴스/이벤트 캘린더 툴.

두 가지를 제공한다:
- get_market_events(date): 한국은행 공식 RSS(보도자료 - 통화정책, 금융통화위원회
  의결사항)에서 지정한 날짜에 발표된 항목.
- search_news(query, days): 연합뉴스/한국경제 경제 RSS에서 최근 N일 안에
  제목/요약에 검색어가 들어간 기사를 찾는다 (제목+요약만, 본문 전체는 가져오지
  않음).

전부 API 키가 필요 없는 공개 RSS만 사용한다 (네이버 뉴스 검색 API는
NAVER_CLIENT_ID/SECRET이 없어 바로 RSS로 진행함).

RSS는 최근 항목 몇 건만 내려주기 때문에, 오래된 과거 날짜/기간을 조회하면
빈 결과가 나올 수 있다 (해당 피드 자체의 한계이며 버그가 아니다).

순수 함수: 입력은 문자열/정수, 출력은 JSON 직렬화 가능한 list[dict].
"""

from __future__ import annotations

import datetime as dt
import email.utils
import re
import xml.etree.ElementTree as ET

import requests

from bond_agent.tools._cache import cached_call

# (RSS URL, 사람이 읽을 출처 이름)
FEEDS: list[tuple[str, str]] = [
    ("https://www.bok.or.kr/portal/bbs/P0000559/news.rss?menuNo=200690", "한국은행 보도자료(통화정책)"),
    ("https://www.bok.or.kr/portal/bbs/P0000093/news.rss?menuNo=200761", "한국은행 금융통화위원회 의결사항"),
]

# search_news용 일반 경제 뉴스 피드 (네이버 뉴스 검색 API는 키가 없어 바로 이쪽으로 전환).
NEWS_SEARCH_FEEDS: list[tuple[str, str]] = [
    ("https://www.yna.co.kr/rss/economy.xml", "연합뉴스 경제"),
    ("https://www.hankyung.com/feed/economy", "한국경제"),
]

# 제목에 이 키워드가 있으면 채권시장에 직접적인 영향이 큰 이벤트로 간주.
_HIGH_IMPORTANCE_KEYWORDS = ["기준금리", "금융통화위원회", "통화정책방향", "기자간담회"]

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _importance_for(title: str) -> str:
    return "high" if any(k in title for k in _HIGH_IMPORTANCE_KEYWORDS) else "medium"


def _strip_html(text: str, max_len: int = 200) -> str:
    """HTML 태그를 제거하고 공백을 정리한 뒤 max_len 자로 자른다 (요약용)."""
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "..."
    return text


def _fetch_feed_items(url: str) -> list[dict]:
    """RSS 피드를 가져와 [{"title", "link", "description", "pub_dt"}] 로 반환.

    개별 피드 호출이 실패해도(네트워크 오류 등) 예외를 삼키고 빈 리스트를
    반환한다 - 뉴스 피드 하나가 잠깐 안 된다고 전체 브리핑 파이프라인이
    죽어서는 안 되기 때문이다 (숫자 데이터인 ecos/fred와는 다른 성격).
    """
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.exceptions.RequestException, ET.ParseError):
        return []

    items = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_date_raw = item.findtext("pubDate")
        if not title or not pub_date_raw:
            continue
        try:
            pub_dt = email.utils.parsedate_to_datetime(pub_date_raw)
        except (TypeError, ValueError):
            continue
        if pub_dt.tzinfo is None:
            continue
        items.append({"title": title, "link": link, "description": description, "pub_dt": pub_dt})
    return items


def get_market_events(date: str) -> list[dict]:
    """지정한 날짜(KST)에 발표된 한국은행 통화정책 관련 이벤트/뉴스 목록.

    Args:
        date: "YYYY-MM-DD" 형식 기준일 (KST).

    Returns:
        각 원소는 다음 키를 가진 dict, 시간순 정렬:
            - time: "HH:MM" (KST)
            - headline: str
            - source: str
            - importance: "high" | "medium"
            - link: str (원문 URL)

    같은 날짜로 같은 날 다시 호출하면 로컬 캐시를 쓴다.
    """

    def _fetch() -> list[dict]:
        events = []
        for url, source in FEEDS:
            for item in _fetch_feed_items(url):
                if item["pub_dt"].date().isoformat() != date:
                    continue
                events.append(
                    {
                        "time": item["pub_dt"].strftime("%H:%M"),
                        "headline": item["title"],
                        "source": source,
                        "importance": _importance_for(item["title"]),
                        "link": item["link"],
                    }
                )
        events.sort(key=lambda e: e["time"])
        return events

    return cached_call("news", f"market_events:{date}", _fetch)


def search_news(query: str, days: int = 1) -> list[dict]:
    """연합뉴스/한국경제 경제 RSS에서 최근 days일 안에, 제목 또는 요약에
    query의 단어가 하나라도 들어간 기사를 찾는다 (본문 전체가 아니라 제목+요약만).

    Args:
        query: 검색어. 공백으로 여러 단어를 넣으면 그중 하나라도 매칭되면 포함
            (예: "기준금리 인상" -> "기준금리" 또는 "인상"이 있으면 매칭).
        days: 오늘(KST)로부터 며칠 전까지 볼지 (기본 1일).

    Returns:
        각 원소는 다음 키를 가진 dict, 최신순 정렬:
            - title: str
            - source: str
            - published_at: ISO 8601 문자열 (KST, 오프셋 포함)
            - url: str
            - summary: str (요약, 최대 200자. 해당 피드에 요약이 없으면 빈 문자열)

    같은 (query, days) 조합으로 같은 날 다시 호출하면 로컬 캐시를 쓴다.
    """

    def _fetch() -> list[dict]:
        tokens = [t for t in query.strip().split() if t]
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        cutoff = now - dt.timedelta(days=days)

        results = []
        for url, source in NEWS_SEARCH_FEEDS:
            for item in _fetch_feed_items(url):
                if item["pub_dt"] < cutoff:
                    continue
                haystack = item["title"] + " " + item["description"]
                if tokens and not any(t in haystack for t in tokens):
                    continue
                results.append(
                    {
                        "title": item["title"],
                        "source": source,
                        "published_at": item["pub_dt"].isoformat(),
                        "url": item["link"],
                        "summary": _strip_html(item["description"]),
                    }
                )
        results.sort(key=lambda r: r["published_at"], reverse=True)
        return results

    return cached_call("news", f"search_news:{query}:{days}", _fetch)
