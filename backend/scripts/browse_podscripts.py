#!/usr/bin/env python3
"""Browse the podscripts.co catalog of available podcasts.

This script lists all podcasts available on podscripts.co so you can
add them to your tracking list.

Usage:
    python browse_podscripts.py
    python browse_podscripts.py --search "joe rogan"
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.services.discovery_podscripts import PodscriptsDiscovery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Browse the podscripts.co catalog",
    )
    parser.add_argument(
        "--search",
        "-s",
        default=None,
        help="Filter by search term",
    )

    args = parser.parse_args()

    print("Fetching podscripts.co catalog...")
    print()

    with PodscriptsDiscovery() as discovery:
        podcasts = discovery.list_available_podcasts()

    if not podcasts:
        print("No podcasts found on podscripts.co")
        return

    # Filter by search term if provided
    if args.search:
        search_lower = args.search.lower()
        podcasts = [
            p
            for p in podcasts
            if search_lower in p["name"].lower() or search_lower in p["slug"].lower()
        ]
        print(f"Found {len(podcasts)} podcasts matching '{args.search}'")
    else:
        print(f"Found {len(podcasts)} podcasts on podscripts.co")

    print()
    print(f"{'SLUG':<40} NAME")
    print("-" * 80)

    for podcast in sorted(podcasts, key=lambda p: p["name"]):
        slug = podcast["slug"]
        name = podcast["name"]
        print(f"{slug:<40} {name}")

    print()
    print("To add a podcast, run:")
    print("  just add-podcast --podscripts <slug>")


if __name__ == "__main__":
    main()
