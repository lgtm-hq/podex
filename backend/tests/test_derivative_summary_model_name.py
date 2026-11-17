"""Summary provider model names the summary and run columns cannot store.

``source_model`` on episode and media summaries and ``summary_model`` on the
derivative run are ``String(120)``. A provider whose model name is not a
non-empty string of at most 120 characters without control characters or lone
surrogates is refused before it is called, with the rule the embedding path
uses. The name is read as an exact ``str``, so a ``str`` subclass cannot change
what is checked or stored.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    DerivativeGenerationRun,
    Episode,
    EpisodeSummary,
    Media,
    MediaSummary,
)
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.derivative_summaries import (
    GeneratedSummaryData,
    SummaryProvider,
    SummarySourceData,
    generate_episode_summary,
    generate_media_summary,
)
from podex.services.failure_codes import PipelineFailure
from tests.conftest import seed_catalog_graph
from tests.model_name_support import (
    MODEL_COLUMN_LENGTH,
    REJECTED_NAMES,
    RaisingStripName,
    UnreadableName,
)
from tests.sdk_log_support import logged, synthetic_secret

_INVALID_MODEL_NAME = "summary_invalid_model_name"


@dataclass(slots=True)
class RecordingProvider:
    """Summary provider recording each source it is asked to summarise."""

    name: Callable[[], object]
    requests: list[SummarySourceData] = field(default_factory=list)

    @property
    def model_name(self) -> object:
        """Return the configured name, or raise what reading it raises."""
        return self.name()

    def summarize(self, source: SummarySourceData) -> GeneratedSummaryData:
        """Record the request and return a fixed summary."""
        self.requests.append(source)
        return GeneratedSummaryData(summary_text="summary")


def _unreadable_property() -> object:
    """Fail as a provider's ``model_name`` property would."""
    raise OSError("model name unavailable")


def _episode_summary(db: Session, provider: SummaryProvider) -> None:
    generate_episode_summary(
        db=db,
        episode=db.query(Episode).one(),
        provider=provider,
        pipeline_version="v1",
        prompt_version="p1",
    )


def _media_summary(db: Session, provider: SummaryProvider) -> None:
    generate_media_summary(
        db=db,
        media=db.query(Media).one(),
        provider=provider,
        pipeline_version="v1",
        prompt_version="p1",
    )


def _derivative_run(db: Session, provider: SummaryProvider) -> None:
    generate_episode_derivatives(
        db=db,
        episode=db.query(Episode).one(),
        summary_provider=provider,
    )


_WRITES: dict[str, Callable[[Session, SummaryProvider], None]] = {
    "episode-summary": _episode_summary,
    "media-summary": _media_summary,
    "derivative-run": _derivative_run,
}

_CASES = [
    pytest.param(write, build, id=f"{write_id}-{name_id}")
    for write_id, write in _WRITES.items()
    for name_id, build in REJECTED_NAMES.items()
]


def _derived_rows(db: Session) -> list[object]:
    """Return every summary and run row the writes could have added."""
    return [
        *db.query(EpisodeSummary).all(),
        *db.query(MediaSummary).all(),
        *db.query(DerivativeGenerationRun).all(),
    ]


def _provider(model_name: object) -> Mock:
    provider = Mock(model_name=model_name)
    provider.summarize.return_value = GeneratedSummaryData(summary_text="summary")
    return provider


@pytest.mark.parametrize(("write", "build"), _CASES)
def test_unstorable_model_name_is_refused_before_the_provider_is_called(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    write: Callable[[Session, SummaryProvider], None],
    build: Callable[[str], object],
) -> None:
    """No text is sent, nothing is written, and the name is never reported."""
    seed_catalog_graph(db_session)
    secret = synthetic_secret("summary-model-name")
    provider = _provider(build(secret))

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(PipelineFailure) as refused,
    ):
        write(db_session, cast(SummaryProvider, provider))
    db_session.flush()
    db_session.commit()

    provider.summarize.assert_not_called()
    assert_that(_derived_rows(db_session)).is_empty()
    refusal = refused.value
    assert_that(getattr(refusal, "failure_reason", None)).is_equal_to(
        _INVALID_MODEL_NAME,
    )
    assert_that(f"{refusal}{refusal.args!r}").does_not_contain(secret)
    assert_that(logged(caplog)).does_not_contain(secret)


def _stored_model_names(db: Session) -> list[str]:
    """Return every stored summary and run model name."""
    db.expire_all()
    return [
        *(row.source_model for row in db.query(EpisodeSummary)),
        *(row.source_model for row in db.query(MediaSummary)),
        *(row.summary_model or "" for row in db.query(DerivativeGenerationRun)),
    ]


@pytest.mark.parametrize("write", _WRITES.values(), ids=_WRITES.keys())
def test_model_name_of_exactly_the_column_length_is_the_longest_accepted(
    db_session: Session,
    write: Callable[[Session, SummaryProvider], None],
) -> None:
    """120 characters are summarised and stored; 121 are refused unsent."""
    seed_catalog_graph(db_session)
    longest = "m" * MODEL_COLUMN_LENGTH
    accepted = _provider(longest)
    over_long = _provider(longest + "m")

    write(db_session, cast(SummaryProvider, accepted))
    db_session.commit()
    with pytest.raises(PipelineFailure):
        write(db_session, cast(SummaryProvider, over_long))
    db_session.commit()

    accepted.summarize.assert_called()
    over_long.summarize.assert_not_called()
    stored = _stored_model_names(db_session)
    assert_that(stored).is_not_empty()
    assert_that(set(stored)).is_equal_to({longest})


@pytest.mark.parametrize("write", _WRITES.values(), ids=_WRITES.keys())
def test_str_subclass_name_is_stored_as_an_exact_str(
    db_session: Session,
    write: Callable[[Session, SummaryProvider], None],
) -> None:
    """A subclass's own methods never run; its characters are stored as ``str``."""
    seed_catalog_graph(db_session)
    provider = _provider(RaisingStripName("hostile-strip-v1"))

    write(db_session, cast(SummaryProvider, provider))
    db_session.commit()

    provider.summarize.assert_called()
    stored = _stored_model_names(db_session)
    assert_that(stored).is_not_empty()
    assert_that({(type(value), value) for value in stored}).is_equal_to(
        {(str, "hostile-strip-v1")},
    )


_UNREADABLE_CASES = [
    pytest.param(_episode_summary, _unreadable_property, id="episode-property"),
    pytest.param(_media_summary, _unreadable_property, id="media-property"),
    *(
        pytest.param(write, UnreadableName, id=f"{write_id}-type-check")
        for write_id, write in _WRITES.items()
    ),
]


@pytest.mark.parametrize(("write", "name"), _UNREADABLE_CASES)
def test_model_name_read_that_raises_is_the_providers_failure(
    db_session: Session,
    write: Callable[[Session, SummaryProvider], None],
    name: Callable[[], object],
) -> None:
    """Reading the name is provider code: its exception propagates unchanged.

    It is raised before any text is sent, like a failing ``summarize`` call,
    and is not turned into a type or attribute error of this module's own.
    """
    seed_catalog_graph(db_session)
    provider = RecordingProvider(name=name)

    with pytest.raises(OSError, match="model name unavailable"):
        write(db_session, cast(SummaryProvider, provider))

    assert_that(provider.requests).is_empty()
    assert_that(_derived_rows(db_session)).is_empty()
