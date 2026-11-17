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
from typing import Any, cast
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    DerivativeGenerationRun,
    DerivativeGenerationRunStatus,
    Episode,
    EpisodeSummary,
    Media,
    MediaSummary,
)
from podex.services import derivative_generation, derivative_summaries
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
from tests.takedown_support import take_down_episode

_INVALID_MODEL_NAME = "summary_invalid_model_name"


@dataclass(slots=True)
class RecordingProvider:
    """Summary provider recording each source it is asked to summarise."""

    name: Callable[[], object]
    requests: list[SummarySourceData] = field(default_factory=list)
    reads: int = 0

    @property
    def model_name(self) -> object:
        """Count the read; return the configured name or raise what it raises."""
        self.reads += 1
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
    pytest.param(_derivative_run, _unreadable_property, id="derivative-run-property"),
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


_GOOD_NAME = "summary-model-v1"
_OVER_LONG_NAME = "m" * (MODEL_COLUMN_LENGTH + 1)


def _changing_name(*names: str) -> Callable[[], object]:
    """Return a name reader giving ``names`` in turn, then the last one forever."""
    remaining = list(names)

    def read() -> object:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return read


def test_derivative_run_reads_a_good_name_once_and_ignores_later_changes(
    db_session: Session,
) -> None:
    """The orchestrator reads the name once and hands it to every summary.

    The property turns unstorable after its first read; that change is never
    seen, so the run, the episode summary and the media summary all complete.
    """
    seed_catalog_graph(db_session)
    provider = RecordingProvider(name=_changing_name(_GOOD_NAME, _OVER_LONG_NAME))

    run = generate_episode_derivatives(
        db=db_session,
        episode=db_session.query(Episode).one(),
        summary_provider=cast(SummaryProvider, provider),
    )
    db_session.commit()

    assert_that(provider.reads).is_equal_to(1)
    assert_that(provider.requests).is_length(2)
    assert_that(run.status).is_equal_to(DerivativeGenerationRunStatus.COMPLETED.value)
    assert_that(_stored_model_names(db_session)).is_equal_to([_GOOD_NAME] * 3)


def test_derivative_run_refuses_a_bad_first_read_whatever_follows(
    db_session: Session,
) -> None:
    """A bad first read refuses the run; a later good name is never read."""
    seed_catalog_graph(db_session)
    provider = RecordingProvider(name=_changing_name(_OVER_LONG_NAME, _GOOD_NAME))

    with pytest.raises(PipelineFailure):
        _derivative_run(db_session, cast(SummaryProvider, provider))
    db_session.commit()

    assert_that(provider.reads).is_equal_to(1)
    assert_that(provider.requests).is_empty()
    assert_that(_derived_rows(db_session)).is_empty()


def _handed_episode_summary(
    db: Session,
    provider: SummaryProvider,
    model_name: str,
) -> None:
    generate_episode_summary(
        db=db,
        episode=db.query(Episode).one(),
        provider=provider,
        model_name=model_name,
        pipeline_version="v1",
        prompt_version="p1",
    )


def _handed_media_summary(
    db: Session,
    provider: SummaryProvider,
    model_name: str,
) -> None:
    generate_media_summary(
        db=db,
        media=db.query(Media).one(),
        provider=provider,
        model_name=model_name,
        pipeline_version="v1",
        prompt_version="p1",
    )


_HANDED_WRITES = {
    "episode-summary": _handed_episode_summary,
    "media-summary": _handed_media_summary,
}


@pytest.mark.parametrize(
    "write",
    _HANDED_WRITES.values(),
    ids=_HANDED_WRITES.keys(),
)
def test_handed_model_name_is_used_without_reading_the_provider(
    db_session: Session,
    write: Callable[[Session, SummaryProvider, str], None],
) -> None:
    """A name handed in is validated and stored; the provider's is never read."""
    seed_catalog_graph(db_session)
    provider = RecordingProvider(name=_unreadable_property)

    write(db_session, cast(SummaryProvider, provider), _GOOD_NAME)
    db_session.commit()
    with pytest.raises(PipelineFailure):
        write(db_session, cast(SummaryProvider, provider), _OVER_LONG_NAME)

    assert_that(provider.reads).is_zero()
    assert_that(provider.requests).is_length(1)
    assert_that(_stored_model_names(db_session)).is_equal_to([_GOOD_NAME])


_SUPPRESSIBLE_WRITES = {
    "episode-summary": _episode_summary,
    "derivative-run": _derivative_run,
}


@pytest.mark.parametrize(
    "write",
    _SUPPRESSIBLE_WRITES.values(),
    ids=_SUPPRESSIBLE_WRITES.keys(),
)
def test_suppressed_episode_is_refused_before_the_model_name_is_read(
    db_session: Session,
    write: Callable[[Session, SummaryProvider], None],
) -> None:
    """Authorisation comes first: a takedown wins over an unstorable name."""
    graph = seed_catalog_graph(db_session)
    take_down_episode(db_session, graph.episode_id, "suppress_derivatives")
    provider = RecordingProvider(name=lambda: _OVER_LONG_NAME)

    with pytest.raises(ContentSuppressedError):
        write(db_session, cast(SummaryProvider, provider))

    assert_that(provider.reads).is_zero()
    assert_that(provider.requests).is_empty()


# Reads of the episode's text or transcript that must follow the name check.
_SOURCE_READS = (
    (derivative_summaries, "build_episode_summary_source"),
    (derivative_summaries, "build_media_summary_source"),
    (derivative_generation, "build_episode_summary_source"),
    (derivative_generation, "_latest_transcript"),
    (derivative_generation, "_run_source_hash"),
)


@pytest.mark.parametrize("write", _WRITES.values(), ids=_WRITES.keys())
def test_model_name_is_checked_before_any_source_is_read(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    write: Callable[[Session, SummaryProvider], None],
) -> None:
    """A refused name stops the write before the text or transcript is read."""
    seed_catalog_graph(db_session)
    reads: list[str] = []
    for module, name in _SOURCE_READS:
        original = getattr(module, name)

        def record(
            *args: Any,
            _name: str = name,
            _original: Callable[..., Any] = original,
            **kwargs: Any,
        ) -> Any:
            reads.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, record)
    provider = RecordingProvider(name=lambda: _OVER_LONG_NAME)

    with pytest.raises(PipelineFailure):
        write(db_session, cast(SummaryProvider, provider))

    assert_that(reads).is_empty()
    assert_that(provider.reads).is_equal_to(1)
