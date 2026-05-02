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
    index_size: str
    index_size_mb: str
    index_seeders: str
    index_peers: str
    index_pubdate: str
    index_details: str

    # --- Parsed index fields ---
    index_title_sanitised: str
    index_title_group: str
    index_title_resolution: str
    index_title_after_year_to_end: str
    index_title_compare: str

    # --- Parsed movie fields ---
    movie_title: str
    movie_title_year: str
    movie_title_and_year_search: str
    movie_title_compare: str
    movie_title_and_year_compare: str

    # --- Torrent fields ---
    torrent_url: str
    magnet_url: str
    category: str
    torrent_tag: str

    # --- IMDb metadata ---
    imdb_id: str
    imdb_title: str
    imdb_year: str
    imdb_rating: str
    imdb_votes: str
    imdb_title_type: str
    imdb_running_time_in_minutes: str
    imdb_genres_list: str
    imdb_credits_cast_list: str
    imdb_credits_director_list: str
    imdb_credits_writer_list: str
    imdb_credits_character_list: str
    imdb_language_list: str
    imdb_country_list: str
    imdb_certification: str
    imdb_cert_source: str  # "bbfc" | "mpaa" | None — tracks cert origin
    imdb_poster_url: str
    imdb_trailer_url: str
    imdb_plot_summary: str
    imdb_plot_outline: str

    # --- Pipeline outcome ---
    result: str  # "Passed" | "Failed"
    result_details: str  # human-readable chain of pass/fail reasons
    verified: str  # "true" once post-processing copy succeeds
