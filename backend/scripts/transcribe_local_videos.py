#!/usr/bin/env python3
"""Transcribe local video/audio files to timestamped .txt using MLX-Whisper."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.services.whisper_transcriber import (
    MLXWhisperBackend,
    WhisperConfig,
    WhisperModel,
)


def format_timestamp(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def transcribe_directory(
    target_dir: Path,
    *,
    model: WhisperModel = WhisperModel.SMALL,
    skip_existing: bool = True,
) -> int:
    config = WhisperConfig(model=model)
    backend = MLXWhisperBackend(config)

    videos = sorted(target_dir.glob("*.mp4"))
    if not videos:
        print(f"No MP4 files found in {target_dir}")
        return 1

    print(f"Using {backend.name} on {len(videos)} file(s)")

    for video in videos:
        output_path = video.with_suffix(".txt")
        if skip_existing and output_path.exists() and output_path.stat().st_size > 0:
            print(f"SKIP: {output_path.name}")
            continue

        print(f"START: {video.name}")
        segments = backend.transcribe(video)
        lines = [
            f"[{format_timestamp(segment.start)}] {segment.text}"
            for segment in segments
            if segment.text.strip()
        ]
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"DONE: {output_path.name} ({len(lines)} segments)")

    print("All done.")
    return 0


def main() -> int:
    target = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "/Users/eiteldagnin/Movies/Downloaded by MediaHuman"
    )
    return transcribe_directory(target)


if __name__ == "__main__":
    raise SystemExit(main())
