"""Whisper-based transcription service for YouTube videos."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, auto
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


class WhisperBackend(StrEnum):
    """Available Whisper backends."""

    FASTER_WHISPER = auto()  # Local, free, fast on CPU
    OPENAI = auto()  # API, paid, reliable
    GROQ = auto()  # API, cheap, very fast
    MLX_WHISPER = auto()  # Local, Apple Silicon optimized


class WhisperModel(StrEnum):
    """Whisper model sizes."""

    TINY = "tiny"
    BASE = "base"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large-v3"


@dataclass
class TranscriptSegment:
    """A single transcript segment with timing."""

    text: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, str | float]:
        return {
            "text": self.text,
            "start": self.start,
            "duration": self.duration,
        }


@dataclass
class TranscriptResult:
    """Result of a transcription."""

    provider: str
    raw_text: str
    segments: list[dict[str, str | float]]
    fetched_at: datetime
    audio_duration_seconds: float = 0.0
    model_used: str = ""

    @classmethod
    def from_segments(
        cls,
        segments: list[TranscriptSegment],
        *,
        provider: str,
        model: str = "",
        audio_duration: float = 0.0,
    ) -> TranscriptResult:
        raw_text = " ".join(seg.text.strip() for seg in segments)
        return cls(
            provider=provider,
            raw_text=raw_text,
            segments=[seg.to_dict() for seg in segments],
            fetched_at=datetime.now(UTC),
            audio_duration_seconds=audio_duration,
            model_used=model,
        )


@dataclass
class WhisperConfig:
    """Configuration for Whisper transcription."""

    backend: WhisperBackend = WhisperBackend.FASTER_WHISPER
    model: WhisperModel = WhisperModel.BASE
    language: str = "en"

    # API keys (for OpenAI/Groq backends)
    openai_api_key: str | None = None
    groq_api_key: str | None = None

    # Local model settings
    device: str = "auto"  # "auto", "cpu", "cuda"
    compute_type: str = "auto"  # "auto", "int8", "float16", "float32"

    # Audio settings
    audio_format: str = "mp3"
    audio_quality: str = "192K"  # Options: "64K", "128K", "192K", "256K", "320K"

    # Transcription settings
    beam_size: int = 5  # Higher = more accurate but slower
    vad_filter: bool = True  # Voice activity detection to filter silence

    # Processing settings
    chunk_length_seconds: int = 600  # 10 minutes per chunk for API limits
    temp_dir: Path | None = None

    # Prompt configuration
    prompt_config_dir: Path | None = None  # Directory containing prompt JSON files

    @classmethod
    def from_env(cls) -> WhisperConfig:
        """Load configuration from environment variables."""
        config = cls()

        if backend := os.getenv("WHISPER_BACKEND"):
            config.backend = WhisperBackend(backend.lower())

        if model := os.getenv("WHISPER_MODEL"):
            config.model = WhisperModel(model.lower())

        config.openai_api_key = os.getenv("OPENAI_API_KEY")
        config.groq_api_key = os.getenv("GROQ_API_KEY")

        if device := os.getenv("WHISPER_DEVICE"):
            config.device = device

        if compute_type := os.getenv("WHISPER_COMPUTE_TYPE"):
            config.compute_type = compute_type

        if audio_quality := os.getenv("WHISPER_AUDIO_QUALITY"):
            config.audio_quality = audio_quality

        if beam_size := os.getenv("WHISPER_BEAM_SIZE"):
            config.beam_size = int(beam_size)

        if vad_filter := os.getenv("WHISPER_VAD_FILTER"):
            config.vad_filter = vad_filter.lower() in ("true", "1", "yes")

        if prompt_dir := os.getenv("WHISPER_PROMPT_CONFIG_DIR"):
            config.prompt_config_dir = Path(prompt_dir)

        return config


class AudioDownloader:
    """Downloads audio from YouTube videos using yt-dlp."""

    # Transient errors that warrant a retry
    TRANSIENT_ERRORS = [
        "HTTP Error 429",  # Rate limited
        "HTTP Error 503",  # Service unavailable
        "HTTP Error 502",  # Bad gateway
        "Connection reset",
        "Connection refused",
        "timed out",
        "Temporary failure",
        "Unable to download webpage",
    ]

    def __init__(self, config: WhisperConfig, max_retries: int = 3) -> None:
        self.config = config
        self.max_retries = max_retries

    def download(self, video_id: str, output_dir: Path) -> Path:
        """
        Download audio from a YouTube video with retry logic.

        Returns the path to the downloaded audio file.

        Raises:
            RuntimeError: If download fails after all retries
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        output_template = str(output_dir / f"{video_id}.%(ext)s")

        # Add deno to PATH if available
        env = os.environ.copy()
        deno_path = Path.home() / ".deno" / "bin"
        if deno_path.exists():
            env["PATH"] = f"{deno_path}:{env.get('PATH', '')}"

        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format",
            self.config.audio_format,
            "--audio-quality",
            self.config.audio_quality,
            "--output",
            output_template,
            "--no-playlist",
            "--quiet",
            "--no-warnings",
            # Retry settings (yt-dlp internal retries)
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            # Use remote components for better compatibility
            "--remote-components",
            "ejs:github",
            url,
        ]

        logger.info(f"Downloading audio for {video_id}")

        last_error = None
        for attempt in range(self.max_retries):
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)

            if result.returncode == 0:
                break

            error_msg = result.stderr
            last_error = error_msg

            # Check if this is a transient error worth retrying
            is_transient = any(err in error_msg for err in self.TRANSIENT_ERRORS)

            if is_transient and attempt < self.max_retries - 1:
                wait_time = 10 * (2**attempt)  # Exponential backoff: 10s, 20s, 40s
                retry_num = attempt + 1
                logger.warning(
                    f"yt-dlp transient error ({retry_num}/{self.max_retries}), "
                    f"retrying in {wait_time}s: {error_msg[:200]}"
                )
                time.sleep(wait_time)
                continue

            # Non-transient error or final attempt
            raise RuntimeError(f"yt-dlp failed: {error_msg}")

        if result.returncode != 0:
            raise RuntimeError(
                f"yt-dlp failed after {self.max_retries} attempts: {last_error}"
            )

        # Find the downloaded file
        audio_file = output_dir / f"{video_id}.{self.config.audio_format}"
        if not audio_file.exists():
            # Try common extensions
            for ext in ["mp3", "m4a", "opus", "webm"]:
                alt_file = output_dir / f"{video_id}.{ext}"
                if alt_file.exists():
                    audio_file = alt_file
                    break

        if not audio_file.exists():
            raise RuntimeError(f"Audio file not found after download: {audio_file}")

        size_mb = audio_file.stat().st_size / 1024 / 1024
        logger.info(f"Downloaded audio: {audio_file} ({size_mb:.1f} MB)")
        return audio_file

    def get_duration(self, audio_path: Path) -> float:
        """Get the duration of an audio file in seconds."""
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning(f"Could not get duration: {result.stderr}")
            return 0.0
        return float(result.stdout.strip())


def split_audio(audio_path: Path, chunk_length_seconds: int = 600) -> Iterator[Path]:
    """Split audio into chunks using ffmpeg.

    Args:
        audio_path: Path to the audio file to split
        chunk_length_seconds: Length of each chunk in seconds

    Yields:
        Paths to the chunk files (caller is responsible for cleanup)
    """
    temp_dir = Path(tempfile.mkdtemp())

    # Get total duration
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    total_duration = float(result.stdout.strip())

    chunk_num = 0
    start_time = 0

    while start_time < total_duration:
        chunk_path = temp_dir / f"chunk_{chunk_num:03d}.mp3"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-ss",
            str(start_time),
            "-t",
            str(chunk_length_seconds),
            "-acodec",
            "libmp3lame",
            "-ab",
            "128k",
            str(chunk_path),
        ]
        subprocess.run(cmd, capture_output=True)

        if chunk_path.exists():
            yield chunk_path

        start_time += chunk_length_seconds
        chunk_num += 1


class BaseWhisperBackend(ABC):
    """Abstract base class for Whisper backends."""

    def __init__(self, config: WhisperConfig) -> None:
        self.config = config

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        initial_prompt: str | None = None,
    ) -> list[TranscriptSegment]:
        """Transcribe an audio file and return segments.

        Args:
            audio_path: Path to the audio file to transcribe
            initial_prompt: Optional prompt to guide transcription with
                vocabulary hints (names, terminology, etc.)
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the backend name."""
        pass

    def _split_audio(self, audio_path: Path) -> Iterator[Path]:
        """Split audio into chunks for API backends."""
        return split_audio(audio_path, self.config.chunk_length_seconds)


class FasterWhisperBackend(BaseWhisperBackend):
    """Local transcription using faster-whisper."""

    def __init__(self, config: WhisperConfig) -> None:
        super().__init__(config)
        self._model = None

    @property
    def name(self) -> str:
        return f"faster-whisper:{self.config.model}"

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise ImportError(
                    "faster-whisper not installed. Run: uv pip install faster-whisper"
                ) from e

            device = self.config.device
            if device == "auto":
                # Check if CUDA is available
                try:
                    import torch

                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"

            compute_type = self.config.compute_type
            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"

            logger.info(
                f"Loading faster-whisper model: {self.config.model} "
                f"(device={device}, compute_type={compute_type})"
            )
            self._model = WhisperModel(
                self.config.model,
                device=device,
                compute_type=compute_type,
            )

        return self._model

    def transcribe(
        self,
        audio_path: Path,
        initial_prompt: str | None = None,
    ) -> list[TranscriptSegment]:
        model = self._get_model()

        logger.info(f"Transcribing with faster-whisper: {audio_path}")
        if initial_prompt:
            logger.info(f"Using initial prompt: {initial_prompt[:100]}...")

        segments_iter, info = model.transcribe(
            str(audio_path),
            language=self.config.language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            initial_prompt=initial_prompt,
        )

        segments = []
        for segment in segments_iter:
            segments.append(
                TranscriptSegment(
                    text=segment.text.strip(),
                    start=segment.start,
                    end=segment.end,
                )
            )

        logger.info(f"Transcribed {len(segments)} segments")
        return segments


class OpenAIWhisperBackend(BaseWhisperBackend):
    """Transcription using OpenAI's Whisper API."""

    def __init__(self, config: WhisperConfig) -> None:
        super().__init__(config)
        if not config.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI backend")

    @property
    def name(self) -> str:
        return "openai:whisper-1"

    def transcribe(
        self,
        audio_path: Path,
        initial_prompt: str | None = None,
    ) -> list[TranscriptSegment]:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError("openai not installed. Run: uv pip install openai") from e

        client = OpenAI(api_key=self.config.openai_api_key)

        # Check file size - OpenAI has 25MB limit
        file_size_mb = audio_path.stat().st_size / 1024 / 1024
        if file_size_mb > 25:
            logger.info(f"Audio file is {file_size_mb:.1f}MB, chunking required")
            return self._transcribe_chunked(audio_path, client, initial_prompt)

        logger.info(f"Transcribing with OpenAI API: {audio_path}")
        if initial_prompt:
            logger.info(f"Using initial prompt: {initial_prompt[:100]}...")

        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=self.config.language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                prompt=initial_prompt,
            )

        segments = []
        for seg in response.segments or []:
            segments.append(
                TranscriptSegment(
                    text=seg.get("text", "").strip(),
                    start=seg.get("start", 0),
                    end=seg.get("end", 0),
                )
            )

        logger.info(f"Transcribed {len(segments)} segments")
        return segments

    def _transcribe_chunked(
        self,
        audio_path: Path,
        client: Any,
        initial_prompt: str | None = None,
    ) -> list[TranscriptSegment]:
        """Transcribe long audio by chunking."""
        chunks = list(self._split_audio(audio_path))
        all_segments: list[TranscriptSegment] = []
        time_offset = 0.0

        for i, chunk_path in enumerate(chunks):
            logger.info(f"Transcribing chunk {i + 1}/{len(chunks)}")
            with open(chunk_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language=self.config.language,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    prompt=initial_prompt,
                )

            for seg in response.segments or []:
                all_segments.append(
                    TranscriptSegment(
                        text=seg.get("text", "").strip(),
                        start=seg.get("start", 0) + time_offset,
                        end=seg.get("end", 0) + time_offset,
                    )
                )

            # Update offset for next chunk
            time_offset += self.config.chunk_length_seconds

            # Clean up chunk
            chunk_path.unlink()

        return all_segments


class GroqWhisperBackend(BaseWhisperBackend):
    """Transcription using Groq's Whisper API (very fast)."""

    def __init__(self, config: WhisperConfig) -> None:
        super().__init__(config)
        if not config.groq_api_key:
            raise ValueError("GROQ_API_KEY is required for Groq backend")

    @property
    def name(self) -> str:
        return "groq:whisper-large-v3"

    def transcribe(
        self,
        audio_path: Path,
        initial_prompt: str | None = None,
    ) -> list[TranscriptSegment]:
        try:
            from groq import Groq
        except ImportError as e:
            raise ImportError("groq not installed. Run: uv pip install groq") from e

        client = Groq(api_key=self.config.groq_api_key)

        # Groq has 25MB limit too
        file_size_mb = audio_path.stat().st_size / 1024 / 1024
        if file_size_mb > 25:
            logger.info(f"Audio file is {file_size_mb:.1f}MB, chunking required")
            return self._transcribe_chunked(audio_path, client, initial_prompt)

        logger.info(f"Transcribing with Groq API: {audio_path}")
        if initial_prompt:
            logger.info(f"Using initial prompt: {initial_prompt[:100]}...")

        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=f,
                language=self.config.language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                prompt=initial_prompt,
            )

        segments = []
        for seg in response.segments or []:
            segments.append(
                TranscriptSegment(
                    text=seg.get("text", "").strip(),
                    start=seg.get("start", 0),
                    end=seg.get("end", 0),
                )
            )

        logger.info(f"Transcribed {len(segments)} segments")
        return segments

    def _transcribe_chunked(
        self,
        audio_path: Path,
        client: Any,
        initial_prompt: str | None = None,
    ) -> list[TranscriptSegment]:
        """Transcribe long audio by chunking."""
        chunks = list(self._split_audio(audio_path))
        all_segments: list[TranscriptSegment] = []
        time_offset = 0.0

        for i, chunk_path in enumerate(chunks):
            logger.info(f"Transcribing chunk {i + 1}/{len(chunks)}")
            with open(chunk_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model="whisper-large-v3",
                    file=f,
                    language=self.config.language,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    prompt=initial_prompt,
                )

            for seg in response.segments or []:
                all_segments.append(
                    TranscriptSegment(
                        text=seg.get("text", "").strip(),
                        start=seg.get("start", 0) + time_offset,
                        end=seg.get("end", 0) + time_offset,
                    )
                )

            time_offset += self.config.chunk_length_seconds
            chunk_path.unlink()

        return all_segments


class MLXWhisperBackend(BaseWhisperBackend):
    """Local transcription using mlx-whisper for Apple Silicon.

    MLX-Whisper is optimized for Apple Silicon (M1/M2/M3) chips and
    uses the GPU and Neural Engine for fast inference.
    """

    # Map our model names to MLX community repo names
    MODEL_REPO_MAP = {
        "tiny": "mlx-community/whisper-tiny-mlx",
        "base": "mlx-community/whisper-base-mlx",
        "small": "mlx-community/whisper-small-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "large-v3": "mlx-community/whisper-large-v3-mlx",
    }

    def __init__(self, config: WhisperConfig) -> None:
        super().__init__(config)
        self._mlx_whisper: ModuleType | None = None

    @property
    def name(self) -> str:
        return f"mlx-whisper:{self.config.model}"

    def _get_mlx_whisper(self) -> ModuleType:
        """Lazy load mlx_whisper module."""
        if self._mlx_whisper is None:
            try:
                import mlx_whisper
            except ImportError as e:
                raise ImportError(
                    "mlx-whisper not installed. Run: uv pip install mlx-whisper"
                ) from e
            self._mlx_whisper = mlx_whisper
        assert self._mlx_whisper is not None
        return self._mlx_whisper

    def _get_model_repo(self) -> str:
        """Get the Hugging Face repo for the configured model."""
        model_name = self.config.model.value
        if model_name not in self.MODEL_REPO_MAP:
            available = list(self.MODEL_REPO_MAP.keys())
            raise ValueError(f"Unknown model {model_name}. Available: {available}")
        return self.MODEL_REPO_MAP[model_name]

    def transcribe(
        self,
        audio_path: Path,
        initial_prompt: str | None = None,
    ) -> list[TranscriptSegment]:
        mlx_whisper = self._get_mlx_whisper()
        model_repo = self._get_model_repo()

        logger.info(f"Transcribing with mlx-whisper ({model_repo}): {audio_path}")
        if initial_prompt:
            logger.info(f"Using initial prompt: {initial_prompt[:100]}...")

        # MLX-Whisper uses similar API to openai-whisper
        # Note: MLX-Whisper doesn't support beam search yet, uses greedy decoding
        result = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=model_repo,
            language=self.config.language,
            initial_prompt=initial_prompt,
        )

        segments = []
        for segment in result.get("segments", []):
            segments.append(
                TranscriptSegment(
                    text=segment["text"].strip(),
                    start=segment["start"],
                    end=segment["end"],
                )
            )

        logger.info(f"Transcribed {len(segments)} segments")
        return segments


class WhisperTranscriber:
    """Main transcription service using Whisper."""

    def __init__(self, config: WhisperConfig | None = None) -> None:
        self.config = config or WhisperConfig.from_env()
        self.downloader = AudioDownloader(self.config)
        self._backend: BaseWhisperBackend | None = None

    def _get_backend(self) -> BaseWhisperBackend:
        """Get or create the appropriate backend."""
        if self._backend is None:
            if self.config.backend == WhisperBackend.FASTER_WHISPER:
                self._backend = FasterWhisperBackend(self.config)
            elif self.config.backend == WhisperBackend.OPENAI:
                self._backend = OpenAIWhisperBackend(self.config)
            elif self.config.backend == WhisperBackend.GROQ:
                self._backend = GroqWhisperBackend(self.config)
            elif self.config.backend == WhisperBackend.MLX_WHISPER:
                self._backend = MLXWhisperBackend(self.config)
            else:
                raise ValueError(f"Unknown backend: {self.config.backend}")
        return self._backend

    def transcribe(
        self,
        video_id: str,
        initial_prompt: str | None = None,
    ) -> TranscriptResult:
        """
        Transcribe a YouTube video.

        Downloads the audio, transcribes it with Whisper, and returns
        the result in the same format as the scraper.

        Args:
            video_id: YouTube video ID
            initial_prompt: Optional prompt to guide transcription with
                vocabulary hints (names, terminology, etc.)
        """
        temp_dir = self.config.temp_dir or Path(tempfile.mkdtemp())
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Download audio
            audio_path = self.downloader.download(video_id, temp_dir)
            audio_duration = self.downloader.get_duration(audio_path)

            # Transcribe
            backend = self._get_backend()
            segments = backend.transcribe(audio_path, initial_prompt=initial_prompt)

            # Build result
            result = TranscriptResult.from_segments(
                segments,
                provider=f"whisper:{backend.name}",
                model=self.config.model,
                audio_duration=audio_duration,
            )

            return result

        finally:
            # Cleanup temp files (only if we created them)
            if self.config.temp_dir is None:
                shutil.rmtree(temp_dir, ignore_errors=True)


# Convenience function
def transcribe_video(
    video_id: str,
    *,
    backend: WhisperBackend = WhisperBackend.FASTER_WHISPER,
    model: WhisperModel = WhisperModel.BASE,
    initial_prompt: str | None = None,
) -> TranscriptResult:
    """
    Transcribe a YouTube video using Whisper.

    Args:
        video_id: YouTube video ID
        backend: Which Whisper backend to use
        model: Which model size to use (for local backends)
        initial_prompt: Optional prompt to guide transcription with
            vocabulary hints (names, terminology, etc.)

    Returns:
        TranscriptResult with raw_text and timestamped segments
    """
    config = WhisperConfig.from_env()
    config.backend = backend
    config.model = model

    transcriber = WhisperTranscriber(config)
    return transcriber.transcribe(video_id, initial_prompt=initial_prompt)
