#!/usr/bin/env python3
"""Compare transcripts from different sources.

This script compares:
- MLX-Whisper transcripts vs podscripts.co transcripts
- Calculates similarity metrics (word error rate, etc.)
- Helps determine if external transcripts are accurate enough to use
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.database import SessionLocal
from podex.models import Episode, Podcast, Transcript

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class ComparisonResult:
    """Result of comparing two transcripts."""

    episode_number: int
    episode_title: str
    source_a: str
    source_b: str
    length_a: int
    length_b: int
    similarity_ratio: float
    word_count_a: int
    word_count_b: int
    word_diff_pct: float
    sample_diffs: list[str]


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    # Lowercase
    text = text.lower()
    # Remove punctuation except apostrophes
    text = re.sub(r"[^\w\s']", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_words(text: str) -> list[str]:
    """Get list of words from text."""
    return normalize_text(text).split()


def calculate_similarity(text_a: str, text_b: str) -> float:
    """Calculate similarity ratio between two texts."""
    norm_a = normalize_text(text_a)
    norm_b = normalize_text(text_b)

    matcher = difflib.SequenceMatcher(None, norm_a, norm_b)
    return matcher.ratio()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Calculate word error rate (WER).

    WER = (S + D + I) / N
    Where S=substitutions, D=deletions, I=insertions, N=words in reference
    """
    ref_words = get_words(reference)
    hyp_words = get_words(hypothesis)

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    # Use difflib to find operations
    matcher = difflib.SequenceMatcher(None, ref_words, hyp_words)

    substitutions = 0
    deletions = 0
    insertions = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            substitutions += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            deletions += i2 - i1
        elif tag == "insert":
            insertions += j2 - j1

    wer = (substitutions + deletions + insertions) / len(ref_words)
    return min(wer, 1.0)  # Cap at 100%


def get_sample_diffs(text_a: str, text_b: str, num_samples: int = 5) -> list[str]:
    """Get sample differences between two texts."""
    words_a = get_words(text_a)
    words_b = get_words(text_b)

    matcher = difflib.SequenceMatcher(None, words_a, words_b)
    diffs: list[str] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal" and len(diffs) < num_samples:
            a_segment = " ".join(words_a[i1:i2])
            b_segment = " ".join(words_b[j1:j2])
            diffs.append(f"  {tag}: '{a_segment}' vs '{b_segment}'")

    return diffs


def compare_transcripts(
    transcript_a: Transcript,
    transcript_b: Transcript,
    episode: Episode,
) -> ComparisonResult:
    """Compare two transcripts."""
    text_a = transcript_a.raw_text or ""
    text_b = transcript_b.raw_text or ""

    words_a = get_words(text_a)
    words_b = get_words(text_b)

    word_diff = abs(len(words_a) - len(words_b))
    word_diff_pct = word_diff / max(len(words_a), len(words_b), 1) * 100

    return ComparisonResult(
        episode_number=episode.episode_number or 0,
        episode_title=episode.title,
        source_a=transcript_a.provider,
        source_b=transcript_b.provider,
        length_a=len(text_a),
        length_b=len(text_b),
        similarity_ratio=calculate_similarity(text_a, text_b),
        word_count_a=len(words_a),
        word_count_b=len(words_b),
        word_diff_pct=word_diff_pct,
        sample_diffs=get_sample_diffs(text_a, text_b),
    )


def main() -> None:
    """Compare transcripts from different providers."""
    # Configuration
    source_a = os.getenv(
        "COMPARE_SOURCE_A", "whisper"
    )  # Match provider containing this
    source_b = os.getenv("COMPARE_SOURCE_B", "podscripts")

    podcast_slug = os.getenv("COMPARE_PODCAST", "joe-rogan-experience")

    episode_min_env = os.getenv("COMPARE_EPISODE_MIN")
    episode_min = int(episode_min_env) if episode_min_env else None

    episode_max_env = os.getenv("COMPARE_EPISODE_MAX")
    episode_max = int(episode_max_env) if episode_max_env else None

    logger.info(f"Comparing: {source_a} vs {source_b}")
    logger.info(f"Podcast: {podcast_slug}")

    with SessionLocal() as session:
        # Get podcast
        podcast = session.query(Podcast).filter(Podcast.slug == podcast_slug).first()
        if not podcast:
            logger.error(f"Podcast '{podcast_slug}' not found")
            return

        # Get episodes with both transcript types
        query = session.query(Episode).filter(Episode.podcast_id == podcast.id)

        if episode_min:
            query = query.filter(Episode.episode_number >= episode_min)
        if episode_max:
            query = query.filter(Episode.episode_number <= episode_max)

        episodes = query.order_by(Episode.episode_number).all()
        logger.info(f"Found {len(episodes)} episodes in range")

        results: list[ComparisonResult] = []

        for episode in episodes:
            # Find transcripts matching each source
            transcript_a = None
            transcript_b = None

            for t in episode.transcripts:
                if source_a.lower() in t.provider.lower():
                    transcript_a = t
                elif source_b.lower() in t.provider.lower():
                    transcript_b = t

            if not transcript_a or not transcript_b:
                continue

            result = compare_transcripts(transcript_a, transcript_b, episode)
            results.append(result)

            logger.info(
                f"Episode {result.episode_number}: "
                f"similarity={result.similarity_ratio:.1%}, "
                f"word_diff={result.word_diff_pct:.1f}%"
            )

    if not results:
        print("\nNo episodes found with both transcript sources.")
        print(f"Make sure you have both '{source_a}' and '{source_b}' transcripts.")
        return

    # Print summary
    print("\n" + "=" * 70)
    print("TRANSCRIPT COMPARISON RESULTS")
    print("=" * 70)
    print(f"  Source A:      {source_a}")
    print(f"  Source B:      {source_b}")
    print(f"  Episodes:      {len(results)}")
    print()

    avg_similarity = sum(r.similarity_ratio for r in results) / len(results)
    avg_word_diff = sum(r.word_diff_pct for r in results) / len(results)

    print(f"  Avg similarity:     {avg_similarity:.1%}")
    print(f"  Avg word diff:      {avg_word_diff:.1f}%")
    print()

    # Show per-episode results
    print("Per-episode results:")
    print("-" * 70)
    print(
        f"{'Ep#':<6} {'Similarity':<12} {'Words A':<10} {'Words B':<10} {'Diff %':<10}"
    )
    print("-" * 70)

    for r in results:
        print(
            f"{r.episode_number:<6} "
            f"{r.similarity_ratio:<12.1%} "
            f"{r.word_count_a:<10} "
            f"{r.word_count_b:<10} "
            f"{r.word_diff_pct:<10.1f}"
        )

    print("-" * 70)

    # Show sample differences for first episode
    if results and results[0].sample_diffs:
        print(f"\nSample differences (Episode {results[0].episode_number}):")
        for diff in results[0].sample_diffs[:5]:
            print(diff)

    print("\n" + "=" * 70)

    # Recommendation
    if avg_similarity >= 0.9:
        print("RECOMMENDATION: High similarity - external transcripts appear reliable")
    elif avg_similarity >= 0.7:
        print("RECOMMENDATION: Moderate similarity - review differences before using")
    else:
        print("RECOMMENDATION: Low similarity - prefer MLX-Whisper transcripts")

    print("=" * 70)


if __name__ == "__main__":
    main()
