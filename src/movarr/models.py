"""Shared data models for movarr."""

from __future__ import annotations

from typing import TypedDict

__all__ = ["ResultDict"]


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
    index_title_compare: str | None

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
    imdb_cert_source: str | None  # "bbfc" | "mpaa" | None — tracks cert origin
    imdb_poster_url: str | None
    imdb_trailer_url: str | None
    imdb_plot_summary: str | None
    imdb_plot_outline: str | None

    # --- Pipeline outcome ---
    result: str  # "Passed" | "Failed"
    result_details: list[str]  # human-readable chain of pass/fail reasons
    verified: str  # "true" once post-processing copy succeeds
