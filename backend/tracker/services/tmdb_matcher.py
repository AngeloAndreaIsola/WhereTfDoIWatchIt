from __future__ import annotations

from tracker.scrapers.base import ContentMatcher

# Use the existing ContentMatcher instead of duplicating functionality

# The match_movie function is no longer needed as ContentMatcher
# already provides this functionality

# If you need to use TMDB directly, use the TMDbClient from tracker.services.tmdb
from tracker.services.tmdb import TMDbClient

# Example usage:
# matcher = ContentMatcher()
# matcher.match_and_store(scraped_item)