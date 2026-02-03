"""Services package."""

from podex.services.discovery import (
    DiscoveredEpisode,
    DiscoveredPodcast,
    DiscoveryResult,
    DiscoverySource,
)
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_podscripts import PodscriptsDiscovery
from podex.services.discovery_rss import RSSDiscovery
from podex.services.discovery_spotify import SpotifyDiscovery
from podex.services.episode_iterator import (
    EpisodeFilter,
    EpisodeIterator,
    ProcessingStage,
    ProcessingStats,
)
from podex.services.llm_extraction import (
    ExtractedMedia,
    ExtractionAPIError,
    ExtractionError,
    ExtractionParseError,
    ExtractionResult,
    LLMExtractor,
)
from podex.services.llm_transcript_cleanup import (
    CleanupCorrection,
    CleanupResult,
    TranscriptCleaner,
)
from podex.services.podcast_config import (
    PodcastConfig as PodcastYAMLConfig,
)
from podex.services.podcast_config import (
    PodcastConfigManager,
    PodcastsConfig,
    PodcastSourceConfig,
)
from podex.services.prompt_config import (
    EpisodeConfig,
    GuestContext,
    PodcastConfig,
    PromptConfig,
    PromptConfigManager,
)
from podex.services.transcript_source import (
    PodscriptsSource,
    TranscriptAcquirer,
    TranscriptAcquisitionResult,
    TranscriptSource,
    WhisperSource,
)
from podex.services.whisper_transcriber import (
    WhisperBackend,
    WhisperConfig,
    WhisperModel,
    WhisperTranscriber,
    transcribe_video,
)

__all__ = [
    # Discovery services
    "DiscoveredEpisode",
    "DiscoveredPodcast",
    "DiscoveryOrchestrator",
    "DiscoveryResult",
    "DiscoverySource",
    "PodscriptsDiscovery",
    "RSSDiscovery",
    "SpotifyDiscovery",
    # Episode iteration
    "EpisodeFilter",
    "EpisodeIterator",
    "ProcessingStage",
    "ProcessingStats",
    # LLM extraction (analyzes transcripts to find media mentions)
    "ExtractedMedia",
    "ExtractionError",
    "ExtractionAPIError",
    "ExtractionParseError",
    "ExtractionResult",
    "LLMExtractor",
    # LLM transcript cleanup
    "CleanupCorrection",
    "CleanupResult",
    "TranscriptCleaner",
    # Podcast YAML configuration
    "PodcastConfigManager",
    "PodcastsConfig",
    "PodcastSourceConfig",
    "PodcastYAMLConfig",
    # Prompt configuration
    "EpisodeConfig",
    "GuestContext",
    "PodcastConfig",
    "PromptConfig",
    "PromptConfigManager",
    # Transcript acquisition
    "PodscriptsSource",
    "TranscriptAcquirer",
    "TranscriptAcquisitionResult",
    "TranscriptSource",
    "WhisperSource",
    # Whisper transcription (downloads audio and transcribes with AI)
    "WhisperBackend",
    "WhisperConfig",
    "WhisperModel",
    "WhisperTranscriber",
    "transcribe_video",
]
