"""Search utilities for flexible title matching.

Provides functions to generate search variations and improve match rates
across all enrichment providers.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from podex.services.enrichment.data import STOP_WORDS


def generate_search_variations(title: str, author: str | None = None) -> list[str]:
    """Generate multiple search variations for a title.

    Returns variations in priority order (most specific first).

    Args:
        title: Original title.
        author: Optional author/creator name.

    Returns:
        List of search query variations to try.
    """
    variations = []
    original = title.strip()

    # 1. Original title (always first)
    variations.append(original)

    # 2. Strip leading articles
    stripped = strip_articles(original)
    if stripped != original:
        variations.append(stripped)

    # 3. Remove subtitle (after colon or dash)
    main_title = remove_subtitle(original)
    if main_title != original:
        variations.append(main_title)
        # Also try stripped version without subtitle
        stripped_main = strip_articles(main_title)
        if stripped_main != main_title:
            variations.append(stripped_main)

    # 4. Remove parenthetical info (year, country, etc.)
    no_parens = remove_parentheticals(original)
    if no_parens != original:
        variations.append(no_parens)

    # 5. Handle "X with Y" pattern -> try just "X"
    without_with = remove_with_clause(original)
    if without_with != original:
        variations.append(without_with)

    # 6. If author provided, try "title author" combination
    if author:
        # Get primary author (first name before comma or "and")
        primary_author = get_primary_name(author)
        if primary_author:
            variations.append(f"{stripped} {primary_author}")

    # 7. Extract key words (for very long titles)
    if len(original.split()) > 5:
        key_words = extract_key_words(original)
        if key_words and key_words != original:
            variations.append(key_words)

    # Remove duplicates while preserving order
    seen = set()
    unique_variations = []
    for v in variations:
        v_lower = v.lower()
        if v_lower not in seen and v.strip():
            seen.add(v_lower)
            unique_variations.append(v)

    return unique_variations


def strip_articles(title: str) -> str:
    """Remove leading articles (The, A, An) from title."""
    return re.sub(r"^(the|a|an)\s+", "", title, flags=re.IGNORECASE).strip()


def remove_subtitle(title: str) -> str:
    """Remove subtitle after colon or em-dash."""
    # Handle "Title: Subtitle" or "Title - Subtitle"
    patterns = [
        r"^(.+?)\s*:\s*.+$",  # Colon
        r"^(.+?)\s+[-–—]\s+.+$",  # Various dashes
    ]
    for pattern in patterns:
        match = re.match(pattern, title)
        if match:
            main = match.group(1).strip()
            # Only use if main title is substantial
            if len(main) > 3:
                return main
    return title


def remove_parentheticals(title: str) -> str:
    """Remove parenthetical content like (2019) or (US)."""
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", title)
    return " ".join(cleaned.split())


def remove_with_clause(title: str) -> str:
    """Remove 'with X' or 'starring X' clauses.

    Handles: "The Tonight Show with David Letterman" -> "The Tonight Show"
    """
    patterns = [
        r"^(.+?)\s+with\s+.+$",
        r"^(.+?)\s+starring\s+.+$",
        r"^(.+?)\s+featuring\s+.+$",
        r"^(.+?)\s+hosted by\s+.+$",
    ]
    for pattern in patterns:
        match = re.match(pattern, title, re.IGNORECASE)
        if match:
            main = match.group(1).strip()
            if len(main) > 3:
                return main
    return title


def get_primary_name(name: str) -> str | None:
    """Extract primary name from author/creator string.

    Handles: "John Smith, Jane Doe" -> "John Smith"
             "John Smith and Jane Doe" -> "John Smith"
    """
    if not name:
        return None

    # Split on common separators
    for sep in [",", " and ", " & ", ";"]:
        if sep in name:
            name = name.split(sep)[0].strip()
            break

    # Get last name for search purposes
    parts = name.split()
    if parts:
        return parts[-1]  # Return last name
    return None


def extract_key_words(title: str) -> str:
    """Extract key words from a long title.

    Removes common words and returns significant terms.
    """
    words = re.findall(r"\b\w+\b", title.lower())
    key_words = [w for w in words if w not in STOP_WORDS and len(w) > 2]

    # Return first 4 key words
    return " ".join(key_words[:4])


def normalize_title(title: str) -> str:
    """Normalize title for comparison.

    Lowercase, remove articles, remove punctuation, normalize whitespace.
    """
    title = title.lower().strip()
    title = strip_articles(title)
    title = re.sub(r"[^\w\s]", "", title)
    title = " ".join(title.split())
    return title


def calculate_similarity(title1: str, title2: str) -> float:
    """Calculate similarity between two titles.

    Uses multiple strategies and returns the best score.

    Args:
        title1: First title.
        title2: Second title.

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    # Normalize both titles
    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)

    if not norm1 or not norm2:
        return 0.0

    # Exact match after normalization
    if norm1 == norm2:
        return 1.0

    # Sequence matcher (good for typos and minor differences)
    seq_ratio = SequenceMatcher(None, norm1, norm2).ratio()

    # Word overlap (good for reordered words)
    words1 = set(norm1.split())
    words2 = set(norm2.split())

    if words1 and words2:
        intersection = words1 & words2
        union = words1 | words2
        jaccard = len(intersection) / len(union)

        # Weighted combination
        # Higher weight on jaccard for longer titles
        if len(words1) > 3 or len(words2) > 3:
            return max(seq_ratio, jaccard * 1.1, (seq_ratio + jaccard) / 2)
        return max(seq_ratio, jaccard)

    return seq_ratio


def is_likely_match(
    search_title: str,
    result_title: str,
    threshold: float = 0.6,
) -> tuple[bool, float]:
    """Check if a result title is likely a match for the search title.

    Args:
        search_title: The title we searched for.
        result_title: The title returned from the API.
        threshold: Minimum similarity threshold.

    Returns:
        Tuple of (is_match, confidence_score).
    """
    similarity = calculate_similarity(search_title, result_title)

    # Boost if one contains the other
    norm_search = normalize_title(search_title)
    norm_result = normalize_title(result_title)

    if norm_search in norm_result or norm_result in norm_search:
        # One is a substring of the other
        shorter = min(len(norm_search), len(norm_result))
        longer = max(len(norm_search), len(norm_result))
        containment_ratio = shorter / longer if longer > 0 else 0
        similarity = max(similarity, containment_ratio + 0.1)

    is_match = similarity >= threshold
    return is_match, min(similarity, 1.0)


def clean_for_api_search(title: str) -> str:
    """Clean a title for use in API search queries.

    Removes characters that might confuse search APIs.
    """
    # Remove quotes
    title = title.replace('"', "").replace("'", "")
    # Remove special characters but keep spaces and alphanumeric
    title = re.sub(r"[^\w\s-]", " ", title)
    # Normalize whitespace
    title = " ".join(title.split())
    return title
