"""news.py 스모크 테스트: 실제 한국은행 RSS를 호출해서 동작을 확인한다."""

from __future__ import annotations

import datetime as dt

from bond_agent.tools import news


def test_get_market_events_smoke_recent_date_has_reasonable_shape():
    """RSS에 실제로 최근 항목이 있는 날짜를 찾아서, 반환 형태를 검증한다.

    RSS는 최근 항목만 내려주므로 오늘 날짜에 이벤트가 없을 수도 있다 -
    최근 항목들의 날짜를 훑어서 이벤트가 있는 날짜 하나를 찾아 검증한다.
    """
    from bond_agent.tools.news import FEEDS, _fetch_feed_items

    all_dates = set()
    for url, _source in FEEDS:
        for item in _fetch_feed_items(url):
            all_dates.add(item["pub_dt"].date().isoformat())

    assert all_dates, "RSS 피드에서 항목을 하나도 가져오지 못했다 (네트워크 문제 또는 피드 변경 가능성)"

    sample_date = sorted(all_dates)[-1]
    events = news.get_market_events(sample_date)

    assert isinstance(events, list)
    assert len(events) > 0

    for e in events:
        assert set(e.keys()) == {"time", "headline", "source", "importance", "link"}
        assert e["importance"] in ("high", "medium", "low")
        dt.datetime.strptime(e["time"], "%H:%M")  # 형식 검증
        assert e["headline"]
        assert e["link"].startswith("http")

    # 시간순 정렬 확인
    times = [e["time"] for e in events]
    assert times == sorted(times)

    print(f"\n{sample_date} 이벤트 {len(events)}건:", events)


def test_get_market_events_empty_for_far_future_date():
    far_future = (dt.date.today() + dt.timedelta(days=3650)).isoformat()
    assert news.get_market_events(far_future) == []


def test_search_news_smoke_broad_query_returns_recent_items():
    """넓은 검색어(경제)로 최근 7일 범위를 조회해서 형태/필드를 검증한다."""
    results = news.search_news("경제", days=7)

    assert isinstance(results, list)
    assert len(results) > 0, "최근 7일 경제 뉴스가 하나도 안 잡혔다 (피드 문제 가능성)"

    for r in results:
        assert set(r.keys()) == {"title", "source", "published_at", "url", "summary"}
        assert r["title"]
        assert r["url"].startswith("http")
        dt.datetime.fromisoformat(r["published_at"])  # 형식 검증

    # 최신순 정렬 확인
    published = [r["published_at"] for r in results]
    assert published == sorted(published, reverse=True)

    print(f"\n'경제' 검색 결과 {len(results)}건, 최신 3건:", results[:3])


def test_search_news_narrow_query_filters_out_unrelated_titles():
    """검색어와 무관한 기사는 결과에 안 들어가는지 확인 (필터링 동작 검증)."""
    all_items = []
    for url, _source in news.NEWS_SEARCH_FEEDS:
        all_items.extend(news._fetch_feed_items(url))
    assert all_items, "뉴스 피드에서 항목을 하나도 가져오지 못했다"

    unlikely_token = "쿼카아기펭귄우주선"
    results = news.search_news(unlikely_token, days=30)
    assert results == []
