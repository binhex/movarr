"""Shared data models for movarr."""

from __future__ import annotations

from typing import TypedDict

__all__ = ["ResultDict", "build_result_dict"]


class ResultDict(TypedDict, total=False):
    """Carrier dict passed through every stage of the acquisition pipeline.

    Each stage reads from and appends to this dict. On failure at any stage
    the dict is written to the DB and the torrent is skipped.
    """

    # --- Index metadata ---
    index_title: str
    index_tracker: str
    index_size: str
    index_size_mb: str
    index_seeders: str
    index_peers: str
    index_pubdate: str
    index_details: str

    # --- Parsed index fields ---
    index_title_sanitised: str | None
    index_title_group: str
    index_title_resolution: str | None
    index_title_after_year_to_end: str | None

    # --- Parsed movie fields ---
    movie_title: str | None
    movie_title_year: str | None
    movie_title_and_year_search: str
    movie_title_compare: str | None
    movie_title_and_year_compare: str | None

    # --- Torrent fields ---
    torrent_url: str
    magnet_url: str
    torrent_hash: str
    category: str
    _filter_minimum_bitrate_mb: int
    torrent_tag: str

    # --- IMDb metadata ---
    imdb_id: str
    imdb_title: str | None
    imdb_year: int | None
    imdb_rating: float | None
    imdb_votes: int | None
    imdb_title_type: str | None
    imdb_running_time_in_minutes: int | None
    imdb_genres_list: list[str] | None
    imdb_credits_cast_list: list[str] | None
    imdb_credits_director_list: list[str] | None
    imdb_credits_writer_list: list[str] | None
    imdb_credits_character_list: list[str] | None
    imdb_language_list: list[str] | None
    imdb_country_list: list[str] | None
    imdb_certification: str | None
    imdb_cert_source: str | None  # "imdbpie" | "omdb" | None — tracks cert origin
    imdb_poster_url: str | None
    imdb_trailer_url: str | None
    imdb_plot_summary: str | None
    imdb_plot_outline: str | None

    # --- Pipeline outcome ---
    result: str  # "Passed" | "Failed"
    result_details: list[str]  # human-readable chain of pass/fail reasons
    verified: str  # "true" once post-processing copy succeeds


def build_result_dict(
    *,
    index_title: str,
    index_tracker: str,
    index_pubdate: str,
    index_details: str,
    index_seeders: str,
    index_peers: str,
    index_size: str,
    index_size_mb: str,
    torrent_url: str,
    magnet_url: str,
    category: str,
) -> ResultDict:
    """Return a base :class:`ResultDict` populated with the shared index fields.

    Both Jackett and Prowlarr parsers produce the same thirteen fields from
    different source shapes.  This factory centralises construction so that
    adding a new shared field only requires one edit.

    Args:
        index_title: Raw torrent title from the indexer.
        index_tracker: Indexer / tracker name.
        index_pubdate: Publication date string (ISO-8601 or RSS date).
        index_details: Detail / info URL or description string.
        index_seeders: Seeder count as a string.
        index_peers: Peer / leecher count as a string.
        index_size: Raw size in bytes as a string.
        index_size_mb: Size converted to megabytes (as a string).
        torrent_url: Direct torrent download URL.
        magnet_url: Magnet URI (may be empty).
        category: Torznab category ID string (may be empty).

    Returns:
        A :class:`ResultDict` with ``result`` preset to ``"Passed"`` and
        ``result_details`` preset to an empty list.
    """
    return {
        "index_title": index_title,
        "index_tracker": index_tracker,
        "index_pubdate": index_pubdate,
        "index_details": index_details,
        "index_seeders": index_seeders,
        "index_peers": index_peers,
        "index_size": index_size,
        "index_size_mb": index_size_mb,
        "torrent_url": torrent_url,
        "magnet_url": magnet_url,
        "category": category,
        "result": "Passed",
        "result_details": [],
    }
