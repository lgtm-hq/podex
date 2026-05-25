"""Anonymous search relevance analytics queries and commands."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from podex.models import SearchAnalyticsEvent

SearchSelectionType = Literal["media", "episode", "podcast"]


@dataclass(frozen=True, slots=True)
class SearchQueryMetricData:
    """Aggregated public-query metric for relevance review."""

    query: str
    searches: int
    zero_result_searches: int
    selections: int


@dataclass(frozen=True, slots=True)
class SearchAnalyticsSummaryData:
    """Search feedback totals and top query rows."""

    searches: int
    zero_result_searches: int
    selections: int
    queries: list[SearchQueryMetricData]


def normalize_search_query(*, query: str) -> str:
    """Normalize query text stored for aggregate relevance analysis."""
    return " ".join(query.casefold().split())[:200]


def record_search_query(
    *,
    db: Session,
    query: str,
    result_count: int,
    processing_time_ms: int,
) -> SearchAnalyticsEvent:
    """Persist an anonymous public query result signal."""
    event = SearchAnalyticsEvent(
        event_type="query",
        query=normalize_search_query(query=query),
        result_count=result_count,
        processing_time_ms=processing_time_ms,
    )
    db.add(event)
    db.flush()
    return event


def record_search_selection(
    *,
    db: Session,
    query: str,
    selected_type: SearchSelectionType,
    selected_id: str,
) -> SearchAnalyticsEvent:
    """Persist an anonymous selected-result relevance signal."""
    event = SearchAnalyticsEvent(
        event_type="selection",
        query=normalize_search_query(query=query),
        selected_type=selected_type,
        selected_id=selected_id,
    )
    db.add(event)
    db.flush()
    return event


def get_search_analytics_summary(
    *,
    db: Session,
    limit: int,
) -> SearchAnalyticsSummaryData:
    """Aggregate public search activity for operator tuning review."""
    query_rows = (
        db.query(
            SearchAnalyticsEvent.query,
            func.count(SearchAnalyticsEvent.id).label("searches"),
            func.sum(
                case(
                    (SearchAnalyticsEvent.result_count == 0, 1),
                    else_=0,
                )
            ).label("zero_result_searches"),
        )
        .filter(SearchAnalyticsEvent.event_type == "query")
        .group_by(SearchAnalyticsEvent.query)
        .order_by(func.count(SearchAnalyticsEvent.id).desc())
        .limit(limit)
        .all()
    )
    selection_counts = dict(
        db.query(
            SearchAnalyticsEvent.query,
            func.count(SearchAnalyticsEvent.id),
        )
        .filter(SearchAnalyticsEvent.event_type == "selection")
        .group_by(SearchAnalyticsEvent.query)
        .all()
    )
    searches = (
        db.query(SearchAnalyticsEvent)
        .filter(SearchAnalyticsEvent.event_type == "query")
        .count()
    )
    zero_result_searches = (
        db.query(SearchAnalyticsEvent)
        .filter(SearchAnalyticsEvent.event_type == "query")
        .filter(SearchAnalyticsEvent.result_count == 0)
        .count()
    )
    selections = (
        db.query(SearchAnalyticsEvent)
        .filter(SearchAnalyticsEvent.event_type == "selection")
        .count()
    )
    return SearchAnalyticsSummaryData(
        searches=searches,
        zero_result_searches=zero_result_searches,
        selections=selections,
        queries=[
            SearchQueryMetricData(
                query=query,
                searches=search_count,
                zero_result_searches=zero_count or 0,
                selections=selection_counts.get(query, 0),
            )
            for query, search_count, zero_count in query_rows
        ],
    )
