from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture

    from movarr.models import ResultDict

from loguru import logger as _loguru_logger

from movarr.config import Config, SearchCriteriaConfig
from movarr.database import Database
from movarr.search import _enrich_index_metadata, _process_criteria, _SearchSession, run_search

# Helpers


def _base_result(index_title: str = "The Matrix 1999 1080p BluRay") -> ResultDict:
    """Minimal result dict as returned by jackett.search."""
    return {"index_title": index_title}


# _enrich_index_metadata


class TestEnrichIndexMetadata:
    """Tests for _enrich_index_metadata — title parsing and enrichment."""

    def test_title_and_year_extracted(self) -> None:
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert result["movie_title"] == "The Matrix"
        assert result["movie_title_year"] == "1999"

    def test_resolution_extracted(self) -> None:
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert result.get("index_title_resolution") is not None

    def test_sanitised_title_stored(self) -> None:
        result = _enrich_index_metadata(_base_result("The.Matrix.1999.1080p.BluRay"))
        assert result.get("index_title_sanitised") is not None

    def test_compare_fields_set_when_title_and_year_present(self) -> None:
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert "movie_title_compare" in result
        assert "movie_title_and_year_compare" in result

    def test_movie_title_and_year_search_set(self) -> None:
        """movie_title_and_year_search must be set — it's the query string for IMDb search."""
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert result.get("movie_title_and_year_search") == "The Matrix 1999"

    def test_movie_title_compare_set(self) -> None:
        """movie_title_compare must be set — IMDb strategies use it to verify matches."""
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert result.get("movie_title_compare") is not None
        assert len(result["movie_title_compare"]) > 0  # type: ignore[arg-type]

    def test_movie_title_compare_set_even_without_year(self) -> None:
        """movie_title_compare is only set when both title and year are parseable.

        If the index title has no extractable year, the compare fields are not
        set (to avoid false-positive matches on year-less titles).
        """
        result = _enrich_index_metadata(_base_result("SomeTitle NoYear BluRay"))
        # Without a year, compare fields are intentionally absent.
        assert result.get("movie_title_compare") is None
        assert result.get("movie_title_and_year_compare") is None

    def test_result_set_to_passed(self) -> None:
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert result["result"] == "Passed"

    def test_result_details_initialised_to_empty_list(self) -> None:
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert result["result_details"] == []

    def test_title_without_year_has_no_compare_fields(self) -> None:
        """When no year can be extracted, compare fields must not be set."""
        result = _enrich_index_metadata(_base_result("SomeTitle NoYear BluRay"))
        year = result.get("movie_title_year")
        if not year:
            assert "movie_title_compare" not in result
            assert "movie_title_and_year_compare" not in result

    def test_empty_index_title_returns_result_unchanged(self) -> None:
        raw: ResultDict = {"index_title": ""}
        result = _enrich_index_metadata(raw)
        assert "movie_title" not in result
        assert "result" not in result

    def test_missing_index_title_key_returns_result_unchanged(self) -> None:
        raw: ResultDict = {}
        result = _enrich_index_metadata(raw)
        assert "movie_title" not in result

    def test_dotted_title_parsed_correctly(self) -> None:
        result = _enrich_index_metadata(_base_result("The.Matrix.1999.1080p.BluRay"))
        assert result.get("movie_title") == "The Matrix"

    def test_after_year_field_set(self) -> None:
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert "index_title_after_year_to_end" in result

    def test_existing_fields_preserved(self) -> None:
        raw: ResultDict = {
            "index_title": "The Matrix 1999 1080p BluRay",
            "index_size": "8000000000",
        }
        result = _enrich_index_metadata(raw)
        assert result["index_size"] == "8000000000"


# run_search


class TestRunSearch:
    """Tests for run_search — top-level pipeline dispatcher."""

    def test_no_search_criteria_skips_jackett(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.index_site.search = []
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        mock_factory.assert_not_called()

    def test_qbittorrent_unreachable_skips_search(self, mocker: MockerFixture) -> None:
        cfg = Config()
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = False
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_process = mocker.patch("movarr.search._process_criteria")
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        mock_factory.assert_not_called()
        mock_process.assert_not_called()

    def test_jackett_not_reachable_skips_criteria_processing(self, mocker: MockerFixture) -> None:
        """When the indexer is unreachable, _process_criteria must not be called."""
        cfg = Config()
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_factory.return_value.is_reachable.return_value = False
        # Store the mock BEFORE calling run_search so the assertion is against
        # the same mock object that wrapped the function during the call.
        mock_process = mocker.patch("movarr.search._process_criteria")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        mock_process.assert_not_called()

    def test_processes_each_criteria_tier(self, mocker: MockerFixture) -> None:
        cfg = Config()
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_factory.return_value.is_reachable.return_value = True
        mock_process = mocker.patch("movarr.search._process_criteria", return_value=0)
        mocker.patch("movarr.search.check_and_notify")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        assert mock_process.call_count == len(cfg.index_site.search)

    def test_passes_jackett_instance_to_process_criteria(self, mocker: MockerFixture) -> None:
        cfg = Config()
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_factory.return_value.is_reachable.return_value = True
        mock_process = mocker.patch("movarr.search._process_criteria", return_value=0)
        mocker.patch("movarr.search.check_and_notify")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        call_kwargs = mock_process.call_args_list[0][1]
        assert call_kwargs["session"].indexer is mock_factory.return_value


# _process_criteria


class TestProcessCriteria:
    """Tests for _process_criteria — per-tier search and filter pipeline."""

    def _criteria_cfg(self, criteria: str = "1080p") -> SearchCriteriaConfig:
        return SearchCriteriaConfig(criteria=criteria)

    def _call(
        self,
        mocker: MockerFixture,
        jackett: Any,
        qbt: Any,
        db: Any,
        config: Config | None = None,
    ) -> None:
        session = _SearchSession(
            config=config or Config(),
            indexer=jackett,
            qbt=qbt,
            db=db,
            library_walk=None,
        )
        _process_criteria(
            criteria_cfg=self._criteria_cfg(),
            category="2000",
            indexer="all",
            session=session,
        )

    def test_happy_path_full_pipeline(self, mocker: MockerFixture) -> None:
        """All pipeline stages pass → torrent added, notification sent, DB written."""
        mock_filter_index = mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mock_imdb_search = mocker.patch(
            "movarr.search.search_for_imdb_id",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mock_metadata = mocker.patch(
            "movarr.search.fetch_metadata",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mock_filter_imdb = mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mock_notify = mocker.patch("movarr.search.send_queued_notification")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        # add_torrent must return non-None for a successful add.
        added_result: dict[str, Any] = {**_base_result(), "result": "Passed", "torrent_tag": "movarr-uuid"}
        qbt.add_torrent.return_value = added_result
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        mock_filter_index.assert_called_once()
        mock_imdb_search.assert_called_once()
        mock_metadata.assert_called_once()
        mock_filter_imdb.assert_called_once()
        mock_notify.assert_called_once()
        qbt.add_torrent.assert_called_once()
        db.write.assert_called_once()

    def test_add_torrent_failure_writes_failed_result_no_notification(self, mocker: MockerFixture) -> None:
        """When add_torrent returns None, result is marked Failed, no notification is sent."""
        mocker.patch("movarr.search.filter_by_index", side_effect=lambda r, *a, **kw: {**r, "result": "Passed"})
        mocker.patch(
            "movarr.search.search_for_imdb_id",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mocker.patch("movarr.search.fetch_metadata", side_effect=lambda r, *a, **kw: {**r, "result": "Passed"})
        mocker.patch("movarr.search.filter_by_imdb", side_effect=lambda r, *a, **kw: {**r, "result": "Passed"})
        mock_notify = mocker.patch("movarr.search.send_queued_notification")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        qbt.add_torrent.return_value = None  # add_torrent failed
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        # Notification must NOT be sent when add_torrent fails.
        mock_notify.assert_not_called()
        # DB write must record the failure.
        db.write.assert_called_once()
        written_result = db.write.call_args[0][0]
        assert written_result.get("result") == "Failed"

    def test_index_filter_failure_writes_db_skips_imdb_lookup(self, mocker: MockerFixture) -> None:
        """When index filter fails, result is persisted and IMDb lookup is skipped."""
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "result": "Failed"},
        )
        mock_imdb_search = mocker.patch("movarr.search.search_for_imdb_id")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        db.write.assert_called_once()
        mock_imdb_search.assert_not_called()

    def test_imdb_id_search_failure_writes_db_skips_metadata(self, mocker: MockerFixture) -> None:
        """When IMDb ID search fails, result is persisted and metadata fetch is skipped."""
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.search_for_imdb_id",
            side_effect=lambda r, *a, **kw: {**r, "result": "Failed"},
        )
        mock_metadata = mocker.patch("movarr.search.fetch_metadata")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        db.write.assert_called_once()
        mock_metadata.assert_not_called()

    def test_metadata_fetch_failure_writes_db_skips_imdb_filter(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.search_for_imdb_id",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.fetch_metadata",
            side_effect=lambda r, *a, **kw: {**r, "result": "Failed"},
        )
        mock_filter_imdb = mocker.patch("movarr.search.filter_by_imdb")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        db.write.assert_called_once()
        mock_filter_imdb.assert_not_called()

    def test_imdb_filter_failure_writes_db(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.search_for_imdb_id",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.fetch_metadata",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Failed"},
        )
        mock_notify = mocker.patch("movarr.search.send_queued_notification")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        db.write.assert_called_once()
        mock_notify.assert_not_called()

    def test_result_without_movie_title_skipped(self, mocker: MockerFixture) -> None:
        """Empty index_title → no parseable title → pipeline skipped entirely."""
        mock_filter_index = mocker.patch("movarr.search.filter_by_index")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([{"index_title": ""}])
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        qbt = mocker.MagicMock()

        self._call(mocker, jackett, qbt, db)

        mock_filter_index.assert_not_called()
        db.write.assert_not_called()

    def test_result_without_year_skipped(self, mocker: MockerFixture) -> None:
        """Title with no parseable year → pipeline skipped entirely."""
        mocker.patch("movarr.search.filter_by_index")
        jackett = mocker.MagicMock()
        # A title that sanitise() produces text from but extract_year() returns None for
        jackett.search.return_value = iter([{"index_title": "SomeTitle NoYearAtAll BluRay"}])
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        qbt = mocker.MagicMock()

        self._call(mocker, jackett, qbt, db)

        db.write.assert_not_called()

    def test_add_torrent_updated_result_written_to_db(self, mocker: MockerFixture) -> None:
        """When qbt.add_torrent returns a dict, that updated result is persisted."""
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.search_for_imdb_id",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.fetch_metadata",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch("movarr.search.send_queued_notification")
        updated_result: dict[str, Any] = {
            "index_title": "The Matrix 1999 1080p BluRay",
            "result": "Passed",
            "torrent_hash": "deadbeef",
        }
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        qbt.add_torrent.return_value = updated_result
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        db.write.assert_called_once_with(updated_result)

    def test_imdb_id_already_present_skips_imdb_search(self, mocker: MockerFixture) -> None:
        """When the index already provides an IMDb ID, search_for_imdb_id is not called."""
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mock_imdb_search = mocker.patch("movarr.search.search_for_imdb_id")
        mocker.patch(
            "movarr.search.fetch_metadata",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch("movarr.search.send_queued_notification")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        qbt.add_torrent.return_value = None
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        mock_imdb_search.assert_not_called()

    def test_imdb_metadata_cache_hit_skips_fetch_metadata(self, mocker: MockerFixture) -> None:
        """When cached IMDb metadata exists, fetch_metadata is not called."""
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mock_metadata = mocker.patch("movarr.search.fetch_metadata")
        mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch("movarr.search.send_queued_notification")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        qbt.add_torrent.return_value = None
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = {
            "imdb_title": "The Matrix",
            "imdb_year": "1999",
            "imdb_rating": "8.7",
            "imdb_genres_list": ["Action", "Sci-Fi"],
        }

        self._call(mocker, jackett, qbt, db)

        mock_metadata.assert_not_called()
        qbt.add_torrent.assert_called_once()
        db.write.assert_called_once()

    def test_imdb_metadata_cache_miss_calls_fetch_metadata(self, mocker: MockerFixture) -> None:
        """When no cached IMDb metadata exists, fetch_metadata is called as normal."""
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mock_metadata = mocker.patch(
            "movarr.search.fetch_metadata",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch("movarr.search.send_queued_notification")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        qbt.add_torrent.return_value = None
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = None

        self._call(mocker, jackett, qbt, db)

        mock_metadata.assert_called_once()

    def test_imdb_metadata_cache_hit_populates_result_dict(self, mocker: MockerFixture) -> None:
        """Cached fields are merged into the result dict before filtering."""
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mocker.patch("movarr.search.fetch_metadata")
        mock_filter_imdb = mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch("movarr.search.send_queued_notification")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        qbt.add_torrent.return_value = None
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = {
            "imdb_title": "The Matrix",
            "imdb_year": "1999",
            "imdb_rating": "8.7",
            "imdb_genres_list": ["Action", "Sci-Fi"],
        }

        self._call(mocker, jackett, qbt, db)

        call_args = mock_filter_imdb.call_args
        result_passed = call_args[0][0] if call_args else {}
        assert result_passed.get("imdb_title") == "The Matrix"
        assert result_passed.get("imdb_rating") == "8.7"
        assert result_passed.get("imdb_genres_list") == ["Action", "Sci-Fi"]

    def test_imdb_metadata_cache_hit_with_failed_filter_still_writes_db(self, mocker: MockerFixture) -> None:
        """Even if the IMDb filter fails, a cache hit still skips the API call."""
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mock_metadata = mocker.patch("movarr.search.fetch_metadata")
        mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Failed"},
        )
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([_base_result()])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False
        db.find_imdb_metadata.return_value = {
            "imdb_title": "The Matrix",
            "imdb_year": "1999",
        }

        self._call(mocker, jackett, qbt, db)

        mock_metadata.assert_not_called()
        db.write.assert_called_once()

    def test_multiple_results_each_processed(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "movarr.search.filter_by_index",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.search_for_imdb_id",
            side_effect=lambda r, *a, **kw: {**r, "imdb_id": "tt0133093", "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.fetch_metadata",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch(
            "movarr.search.filter_by_imdb",
            side_effect=lambda r, *a, **kw: {**r, "result": "Passed"},
        )
        mocker.patch("movarr.search.send_queued_notification")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter(
            [
                _base_result("The Matrix 1999 1080p BluRay"),
                _base_result("Inception 2010 1080p BluRay"),
            ]
        )
        qbt = mocker.MagicMock()
        qbt.add_torrent.return_value = None
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False

        self._call(mocker, jackett, qbt, db)

        assert db.write.call_count == 2


# Log-level behaviour — "passed all filters" must emit SUCCESS


class TestPassedAllFiltersLogLevel:
    """When a result passes every filter, the summary must log at SUCCESS."""

    def _capture_records(self, fn: Any) -> list[Any]:
        records: list[Any] = []

        def sink(msg: Any) -> None:
            records.append(msg.record)

        sink_id = _loguru_logger.add(sink, level=0)
        try:
            fn()
        finally:
            _loguru_logger.remove(sink_id)
        return records

    def test_passed_all_filters_logs_at_success(self, mocker: MockerFixture) -> None:
        """The 'passed all filters' summary must log at SUCCESS level."""
        from movarr.search import _process_criteria

        cfg = Config()
        criteria_cfg = SearchCriteriaConfig(criteria="1080p")

        # Jackett search returns already-mapped result dicts with index_title key.
        jackett_result: dict = {
            "index_title": "The Matrix 1999 1080p BluRay",
            "index_size": str(8_000_000_000),
            "link": "http://example.com/torrent.torrent",
            "magnet_uri": None,
            "imdb_id": "",
        }

        mock_jackett = mocker.MagicMock()
        mock_jackett.search.return_value = [jackett_result]

        mocker.patch(
            "movarr.search.filter_by_index",
            return_value={
                "result": "Passed",
                "result_details": [],
                "index_title": "The Matrix 1999 1080p BluRay",
                "index_title_sanitised": "The Matrix 1999 1080p BluRay",
                "index_title_resolution": "1080p",
                "index_size": str(8_000_000_000),
                "movie_title": "The Matrix",
                "movie_title_year": "1999",
                "index_title_compare": "the matrix 1999",
                "movie_title_and_year_search": "The Matrix 1999",
                "imdb_id": "tt0133093",
                "link": "http://example.com/torrent.torrent",
            },
        )
        mocker.patch(
            "movarr.search.fetch_metadata",
            return_value={
                "result": "Passed",
                "result_details": [],
                "index_title": "The Matrix 1999 1080p BluRay",
                "imdb_id": "tt0133093",
                "link": "http://example.com/torrent.torrent",
            },
        )
        mocker.patch(
            "movarr.search.filter_by_imdb",
            return_value={
                "result": "Passed",
                "result_details": [],
                "index_title": "The Matrix 1999 1080p BluRay",
                "imdb_id": "tt0133093",
                "link": "http://example.com/torrent.torrent",
            },
        )
        mocker.patch("movarr.search.send_queued_notification")

        mock_db = mocker.MagicMock()
        mock_db.is_duplicate_exact.return_value = False
        mock_qbt = mocker.MagicMock()
        session = _SearchSession(config=cfg, indexer=mock_jackett, qbt=mock_qbt, db=mock_db, library_walk=None)

        records = self._capture_records(
            lambda: _process_criteria(
                criteria_cfg=criteria_cfg,
                category="2000",
                indexer="my-indexer",
                session=session,
            )
        )
        success_records = [r for r in records if r["level"].name == "SUCCESS"]
        assert success_records, "Expected at least one SUCCESS-level log when result passes all filters"


# DB deduplication — skip already-seen titles before any API call


class TestDbDeduplication:
    """Already-seen index titles must be skipped before any filter/API work."""

    def _make_session(self, mocker: MockerFixture, *, seen: bool) -> tuple[_SearchSession, MagicMock]:
        cfg = Config()
        mock_jackett = mocker.MagicMock()
        mock_jackett.search.return_value = [{"index_title": "The Matrix 1999 1080p BluRay"}]
        mock_db = mocker.MagicMock()
        mock_db.is_duplicate_exact.return_value = seen
        mock_qbt = mocker.MagicMock()
        return _SearchSession(config=cfg, indexer=mock_jackett, qbt=mock_qbt, db=mock_db, library_walk=None), mock_db

    def test_seen_title_skips_filter_by_index(self, mocker: MockerFixture) -> None:
        """filter_by_index must NOT be called when the title is already in the DB."""
        session, _ = self._make_session(mocker, seen=True)
        mock_filter = mocker.patch("movarr.search.filter_by_index")

        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="my-indexer",
            session=session,
        )

        mock_filter.assert_not_called()

    def test_seen_title_does_not_write_to_db(self, mocker: MockerFixture) -> None:
        """db.write must NOT be called when skipping a duplicate title."""
        session, mock_db = self._make_session(mocker, seen=True)
        mocker.patch("movarr.search.filter_by_index")

        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="my-indexer",
            session=session,
        )

        mock_db.write.assert_not_called()

    def test_new_title_proceeds_to_filter_by_index(self, mocker: MockerFixture) -> None:
        """A title not in the DB must proceed through filter_by_index."""
        session, _ = self._make_session(mocker, seen=False)
        mock_filter = mocker.patch(
            "movarr.search.filter_by_index",
            return_value={
                "result": "Failed",
                "result_details": ["size too small"],
                "index_title": "The Matrix 1999 1080p BluRay",
            },
        )

        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="my-indexer",
            session=session,
        )

        mock_filter.assert_called_once()


# run_search — library_walk and override_search branches


class TestRunSearchLibraryWalkAndOverride:
    """Tests for run_search covering library_walk population and override_search."""

    def _make_base(self, mocker: MockerFixture) -> tuple[Config, Any, Any]:
        cfg = Config()
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_factory.return_value.is_reachable.return_value = True
        mocker.patch("movarr.search._process_criteria", return_value=0)
        mocker.patch("movarr.search.check_and_notify")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        return cfg, qbt, db

    def test_walk_library_called_when_library_path_list_set(self, mocker: MockerFixture) -> None:
        """walk_library is called when library_path_list is non-empty."""
        cfg, qbt, db = self._make_base(mocker)
        cfg.general.library_path_list = ["/media/movies"]
        mock_walk = mocker.patch("movarr.search.walk_library", return_value=iter([("/", [], ["a.mkv"])]))
        run_search(cfg, qbt, db)
        mock_walk.assert_called_once_with(["/media/movies"])

    def test_override_search_replaces_category(self, mocker: MockerFixture) -> None:
        """When override_search sets a category, that category is forwarded to _process_criteria."""
        cfg, qbt, db = self._make_base(mocker)
        indexer = cfg.index_site.jackett_indexer
        cfg.index_site.override_search = {indexer: {"category": "9999"}}
        mock_process = mocker.patch("movarr.search._process_criteria", return_value=0)
        mocker.patch("movarr.search.get_indexer_client").return_value.is_reachable.return_value = True
        run_search(cfg, qbt, db)
        called_categories = [call[1]["category"] for call in mock_process.call_args_list]
        assert all(c == "9999" for c in called_categories)


# _process_criteria — no movie_title_year skips the result


class TestProcessCriteriaNoYear:
    """result without movie_title_year is skipped (lines 127-129)."""

    def _make_session(self, mocker: MockerFixture) -> _SearchSession:
        cfg = Config()
        mock_jackett = mocker.MagicMock()
        # Return one hit so iteration enters the loop
        mock_jackett.search.return_value = [
            {"title": "The.Matrix.1999.1080p.BluRay", "size": 8_000_000_000, "imdb_id": ""}
        ]
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_seen.return_value = False
        db.is_duplicate_exact.return_value = False
        return _SearchSession(config=cfg, indexer=mock_jackett, qbt=qbt, db=db, library_walk=None)

    def test_no_year_result_is_skipped(self, mocker: MockerFixture) -> None:
        """Result with movie_title but no movie_title_year is skipped without writing to db."""
        session = self._make_session(mocker)
        # _enrich_index_metadata returns no year
        mocker.patch(
            "movarr.search._enrich_index_metadata",
            return_value={
                "index_title": "The Matrix 1999 1080p",
                "movie_title": "The Matrix",
                "movie_title_year": "",
                "result": "Passed",
            },
        )
        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="my-indexer",
            session=session,
        )
        session.db.write.assert_not_called()  # type: ignore[attr-defined]


class TestRunSearchHealthMonitor:
    """Tests that run_search() calls check_and_notify() correctly."""

    def _make_config(self) -> Config:
        from movarr.config import NotificationConfig

        config = Config()
        return config.model_copy(
            update={
                "notification": NotificationConfig(
                    apprise_urls=["ntfy://t"],
                    index_proxy_alert_hours=2.0,
                )
            }
        )

    def test_check_and_notify_called_with_false_when_not_reachable(self, tmp_path: Path) -> None:
        """When indexer is unreachable, check_and_notify called with has_results=False."""
        from unittest.mock import MagicMock, patch

        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = True

        mock_indexer = MagicMock()
        mock_indexer.is_reachable.return_value = False

        with (
            patch("movarr.search.get_indexer_client", return_value=mock_indexer),
            patch("movarr.search.check_and_notify") as mock_health,
        ):
            run_search(config, qbt, db)

        mock_health.assert_called_once()
        call_kwargs = mock_health.call_args.kwargs
        assert call_kwargs["has_results"] is False

    def test_check_and_notify_called_with_true_when_results_returned(self, tmp_path: Path) -> None:
        """When indexer returns results, check_and_notify called with has_results=True."""
        from unittest.mock import MagicMock, patch

        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = True

        # A minimal ResultDict that IS yielded as a raw result from the indexer
        # (it will fail filters and not be queued, but it still counts as a raw result)
        raw_result = {
            "index_title": "Some.Movie.2020.1080p",
            "result": "Passed",
            "result_details": [],
        }

        mock_indexer = MagicMock()
        mock_indexer.is_reachable.return_value = True
        mock_indexer.search.return_value = iter([raw_result])

        with (
            patch("movarr.search.get_indexer_client", return_value=mock_indexer),
            patch("movarr.search.check_and_notify") as mock_health,
            patch("movarr.search.walk_library", return_value=[]),
        ):
            run_search(config, qbt, db)

        mock_health.assert_called_once()
        call_kwargs = mock_health.call_args.kwargs
        assert call_kwargs["has_results"] is True

    def test_check_and_notify_called_with_false_when_indexer_returns_empty(self, tmp_path: Path) -> None:
        """When indexer returns zero raw results, check_and_notify called with has_results=False."""
        from unittest.mock import MagicMock, patch

        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = True

        mock_indexer = MagicMock()
        mock_indexer.is_reachable.return_value = True
        mock_indexer.search.return_value = iter([])  # empty generator

        with (
            patch("movarr.search.get_indexer_client", return_value=mock_indexer),
            patch("movarr.search.check_and_notify") as mock_health,
            patch("movarr.search.walk_library", return_value=[]),
        ):
            run_search(config, qbt, db)

        mock_health.assert_called_once()
        call_kwargs = mock_health.call_args.kwargs
        assert call_kwargs["has_results"] is False

    def test_check_and_notify_not_called_when_qbt_unreachable(self, tmp_path: Path) -> None:
        """When qBittorrent is unreachable, search exits early — check_and_notify not called."""
        from unittest.mock import MagicMock, patch

        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = False

        with patch("movarr.search.check_and_notify") as mock_health:
            run_search(config, qbt, db)

        mock_health.assert_not_called()


class TestRunSearchTorrentClientHealthMonitor:
    """Tests that run_search() calls torrent_client_health.check_and_notify() correctly."""

    def _make_config(self) -> Config:
        from movarr.config import NotificationConfig

        config = Config()
        return config.model_copy(
            update={
                "notification": NotificationConfig(
                    apprise_urls=["ntfy://t"],
                    torrent_client_alert_hours=2.0,
                )
            }
        )

    def test_called_with_false_when_qbt_unreachable(self, tmp_path: Path) -> None:
        """When qBittorrent is unreachable, check_and_notify called with is_reachable=False."""
        from unittest.mock import MagicMock, patch

        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = False

        with patch("movarr.search.torrent_client_health") as mock_health:
            run_search(config, qbt, db)

        mock_health.check_and_notify.assert_called_once_with(is_reachable=False, db=db, config=config)

    def test_called_with_true_when_qbt_reachable(self, tmp_path: Path) -> None:
        """When qBittorrent is reachable, check_and_notify called with is_reachable=True."""
        from unittest.mock import MagicMock, patch

        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = True

        mock_indexer = MagicMock()
        mock_indexer.is_reachable.return_value = True
        mock_indexer.search.return_value = iter([])

        with (
            patch("movarr.search.get_indexer_client", return_value=mock_indexer),
            patch("movarr.search.check_and_notify"),
            patch("movarr.search.torrent_client_health") as mock_health,
            patch("movarr.search.walk_library", return_value=[]),
        ):
            run_search(config, qbt, db)

        mock_health.check_and_notify.assert_called_once_with(is_reachable=True, db=db, config=config)


class TestProcessCriteriaIgnoreList:
    """_process_criteria must skip results from indexers in ignore_list."""

    def test_ignored_tracker_skips_result(self, mocker: MockerFixture) -> None:
        """Result from a tracker in jackett ignore_list must not be written to DB or added to qbt."""
        jackett = mocker.MagicMock()
        raw = {**_base_result(), "index_tracker": "some-tracker"}
        jackett.search.return_value = iter([raw])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False

        cfg = Config()
        cfg.index_proxy.jackett.ignore_list = ["some-tracker"]

        session = _SearchSession(
            config=cfg,
            indexer=jackett,
            qbt=qbt,
            db=db,
            library_walk=None,
        )
        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="all",
            session=session,
        )

        db.write.assert_not_called()
        qbt.add_torrent.assert_not_called()

    def test_ignored_tracker_case_insensitive(self, mocker: MockerFixture) -> None:
        """ignore_list matching must be case-insensitive."""
        jackett = mocker.MagicMock()
        raw = {**_base_result(), "index_tracker": "bitmagnet"}
        jackett.search.return_value = iter([raw])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False

        cfg = Config()
        cfg.index_proxy.jackett.ignore_list = ["BitMagnet"]

        session = _SearchSession(
            config=cfg,
            indexer=jackett,
            qbt=qbt,
            db=db,
            library_walk=None,
        )
        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="all",
            session=session,
        )

        db.write.assert_not_called()
        qbt.add_torrent.assert_not_called()

    def test_ignore_list_does_not_apply_to_explicit_indexer(self, mocker: MockerFixture) -> None:
        """ignore_list must NOT filter results when a specific indexer is named.

        If the user sets jackett_indexer='my-indexer' AND ignore_list=['my-indexer'],
        results from that indexer must still pass through — ignore_list only applies
        when indexer=='all'.
        """
        jackett = mocker.MagicMock()
        raw = {**_base_result(), "index_tracker": "my-indexer"}
        jackett.search.return_value = iter([raw])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False

        cfg = Config()
        cfg.index_proxy.jackett.ignore_list = ["my-indexer"]

        session = _SearchSession(
            config=cfg,
            indexer=jackett,
            qbt=qbt,
            db=db,
            library_walk=None,
        )
        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="my-indexer",
            session=session,
        )

        # The result must reach the DB write path (not silently skipped)
        db.write.assert_called_once()

    def test_prowlarr_ignore_list_filters_results(self, mocker: MockerFixture) -> None:
        """prowlarr.ignore_list must filter results when proxy is prowlarr and indexer is 'all'."""
        prowlarr = mocker.MagicMock()
        raw = {**_base_result(), "index_tracker": "some-tracker"}
        prowlarr.search.return_value = iter([raw])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False

        cfg = Config()
        cfg.index_proxy.selected = "prowlarr"
        cfg.index_proxy.prowlarr.ignore_list = ["some-tracker"]

        session = _SearchSession(
            config=cfg,
            indexer=prowlarr,
            qbt=qbt,
            db=db,
            library_walk=None,
        )
        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="all",
            session=session,
        )

        db.write.assert_not_called()
        qbt.add_torrent.assert_not_called()

    def test_jackett_ignore_list_does_not_affect_prowlarr(self, mocker: MockerFixture) -> None:
        """jackett.ignore_list must NOT filter results when proxy is prowlarr."""
        prowlarr = mocker.MagicMock()
        raw = {**_base_result(), "index_tracker": "some-tracker"}
        prowlarr.search.return_value = iter([raw])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False

        cfg = Config()
        cfg.index_proxy.selected = "prowlarr"
        cfg.index_proxy.jackett.ignore_list = ["some-tracker"]

        session = _SearchSession(
            config=cfg,
            indexer=prowlarr,
            qbt=qbt,
            db=db,
            library_walk=None,
        )
        _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="all",
            session=session,
        )

        # jackett ignore_list must not bleed into prowlarr
        db.write.assert_called_once()

    def test_all_ignored_returns_zero(self, mocker: MockerFixture) -> None:
        """When every result is from an ignored tracker, _process_criteria returns 0."""
        jackett = mocker.MagicMock()
        raw1 = {**_base_result(), "index_tracker": "ignored-tracker"}
        raw2 = {**_base_result("Other Movie 2021 1080p"), "index_tracker": "ignored-tracker"}
        jackett.search.return_value = iter([raw1, raw2])
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        db.is_duplicate_exact.return_value = False

        cfg = Config()
        cfg.index_proxy.jackett.ignore_list = ["ignored-tracker"]

        session = _SearchSession(
            config=cfg,
            indexer=jackett,
            qbt=qbt,
            db=db,
            library_walk=None,
        )
        count = _process_criteria(
            criteria_cfg=SearchCriteriaConfig(criteria="1080p"),
            category="2000",
            indexer="all",
            session=session,
        )

        assert count == 0


# _supersede — in-queue quality supersession


class TestSupersede:
    """Tests for _supersede — in-queue quality supersession."""

    @staticmethod
    def _make_result(
        imdb_id: str = "tt0133093",
        title: str = "The Matrix 1999 2160p BluRay 4K HDR",
    ) -> ResultDict:
        from movarr.parsing import sanitise

        san = sanitise(title) or title
        return {
            "imdb_id": imdb_id,
            "index_title": title,
            "index_title_sanitised": san,
            "result": "Passed",
            "result_details": [],
        }

    @staticmethod
    def _make_torrent_map(
        entries: list[tuple[str, str, str, int]],
    ) -> dict[str, dict[str, str]]:
        """Build a ``{hash: info}`` torrent map from entry tuples.

        Each entry: ``(hash, name, imdb_id, quality_score)``
        """
        from movarr.qbittorrent import _build_supersede_tag

        result: dict[str, dict[str, str]] = {}
        for h, name, imdb, score in entries:
            tag = _build_supersede_tag(imdb, score)
            result[h] = {"name": name, "tags": tag, "hash": h}
        return result

    def _make_session(self, mocker: MockerFixture, torrent_map: dict | None = None) -> Any:
        """Create a _SearchSession with mocked qbt and db."""
        cfg = Config()
        cfg.queue_management.supersede_enabled = True
        qbt = mocker.MagicMock()
        if torrent_map is not None:
            qbt.list_by_category.return_value = torrent_map
        else:
            qbt.list_by_category.return_value = {}
        db = mocker.MagicMock()
        return _SearchSession(config=cfg, indexer=mocker.MagicMock(), qbt=qbt, db=db, library_walk=None)

    def test_no_match_when_queue_empty(self, mocker: MockerFixture) -> None:
        """When list_by_category returns empty dict, _supersede must be a no-op."""
        from movarr.search import _supersede

        result = self._make_result()
        session = self._make_session(mocker, torrent_map={})

        _supersede(result, session)

        assert result["result"] == "Passed"
        session.qbt.delete_torrent.assert_not_called()
        session.db.mark_stalled.assert_not_called()

    def test_no_match_when_different_imdb_id(self, mocker: MockerFixture) -> None:
        """When queue torrent has different IMDb ID, no supersession occurs."""
        from movarr.search import _supersede

        result = self._make_result(imdb_id="tt0133093")
        torrent_map = self._make_torrent_map(
            [
                ("hash1", "Inception 2010 1080p BluRay", "tt1375666", 70),
            ]
        )
        session = self._make_session(mocker, torrent_map=torrent_map)

        _supersede(result, session)

        assert result["result"] == "Passed"
        session.qbt.delete_torrent.assert_not_called()

    def test_no_match_with_old_format_tag(self, mocker: MockerFixture) -> None:
        """Old-format tags (no imdb- segment) must not match."""
        from movarr.search import _supersede

        result = self._make_result(imdb_id="tt0133093")
        # Old-format tag: no imdb- segment
        qbt = mocker.MagicMock()
        qbt.list_by_category.return_value = {
            "hash1": {
                "name": "The Matrix 1999 1080p BluRay",
                "tags": "movarr-abc12345",
                "hash": "hash1",
            },
        }
        cfg = Config()
        db = mocker.MagicMock()
        session = _SearchSession(config=cfg, indexer=mocker.MagicMock(), qbt=qbt, db=db, library_walk=None)

        _supersede(result, session)

        assert result["result"] == "Passed"
        qbt.delete_torrent.assert_not_called()

    def test_supersedes_when_new_higher_score(self, mocker: MockerFixture) -> None:
        """When new result has higher quality score, existing torrent is deleted after queue."""
        from movarr.search import _delete_superseded_matches, _supersede

        # New: 2160p 4K HDR (score 90+), existing: 1080p BluRay (score 70)
        result = self._make_result(
            imdb_id="tt0133093",
            title="The Matrix 1999 2160p BluRay 4K HDR",
        )
        torrent_map = self._make_torrent_map(
            [
                ("hash1", "The Matrix 1999 1080p BluRay", "tt0133093", 70),
            ]
        )
        session = self._make_session(mocker, torrent_map=torrent_map)

        to_delete = _supersede(result, session)

        assert result["result"] == "Passed"
        assert len(to_delete) == 1

        # Deletion happens AFTER queuing (simulated by calling here)
        _delete_superseded_matches(session, to_delete, "tt0133093", result["index_title"])
        session.qbt.delete_torrent.assert_called_once_with(
            "hash1", delete_data=True, state="superseded", name="The Matrix 1999 1080p BluRay"
        )
        session.db.mark_stalled.assert_called_once()

    def test_skips_new_when_existing_higher_score(self, mocker: MockerFixture) -> None:
        """When existing has higher score, new result is marked Failed."""
        from movarr.search import _supersede

        # New: 1080p BluRay (score 70), existing: 2160p 4K HDR (score 90+)
        result = self._make_result(
            imdb_id="tt0133093",
            title="The Matrix 1999 1080p BluRay",
        )
        torrent_map = self._make_torrent_map(
            [
                ("hash1", "The Matrix 1999 2160p BluRay 4K HDR", "tt0133093", 90),
            ]
        )
        session = self._make_session(mocker, torrent_map=torrent_map)

        _supersede(result, session)

        assert result["result"] == "Failed"
        assert any("Superseded" in d for d in result.get("result_details", []))
        session.qbt.delete_torrent.assert_not_called()

    def test_graceful_when_qbt_raises(self, mocker: MockerFixture) -> None:
        """When qbt.list_by_category raises, _supersede logs warning and returns."""
        from movarr.search import _supersede

        result = self._make_result()
        qbt = mocker.MagicMock()
        qbt.list_by_category.side_effect = Exception("Connection refused")
        cfg = Config()
        cfg.queue_management.supersede_enabled = True
        db = mocker.MagicMock()
        session = _SearchSession(config=cfg, indexer=mocker.MagicMock(), qbt=qbt, db=db, library_walk=None)

        _supersede(result, session)

        assert result["result"] == "Passed"  # unchanged
        qbt.delete_torrent.assert_not_called()
        db.mark_stalled.assert_not_called()

    def test_noop_when_no_imdb_id(self, mocker: MockerFixture) -> None:
        """When result has no imdb_id, _supersede returns immediately."""
        from movarr.search import _supersede

        result = self._make_result(imdb_id="")
        session = self._make_session(mocker, torrent_map={"hash1": {"name": "x", "tags": ""}})

        _supersede(result, session)

        assert result["result"] == "Passed"
        session.qbt.list_by_category.assert_not_called()

    def test_multiple_inferiors_all_deleted(self, mocker: MockerFixture) -> None:
        """When multiple same-IMDb torrents all have lower scores, all are deleted after queue."""
        from movarr.search import _delete_superseded_matches, _supersede

        # New: 2160p (score 90+)
        result = self._make_result(
            imdb_id="tt0133093",
            title="The Matrix 1999 2160p BluRay 4K HDR",
        )
        torrent_map = self._make_torrent_map(
            [
                ("hash1", "The Matrix 1999 1080p BluRay", "tt0133093", 70),
                ("hash2", "The Matrix 1999 720p BluRay", "tt0133093", 60),
                ("hash3", "The Matrix 1999 1080p WEB-DL", "tt0133093", 60),
            ]
        )
        session = self._make_session(mocker, torrent_map=torrent_map)

        to_delete = _supersede(result, session)

        assert result["result"] == "Passed"
        assert len(to_delete) == 3

        _delete_superseded_matches(session, to_delete, "tt0133093", result["index_title"])
        assert session.qbt.delete_torrent.call_count == 3
        assert session.db.mark_stalled.call_count == 3

    def test_mixed_superior_inferior_new_blocked(self, mocker: MockerFixture) -> None:
        """When some existing have higher and some lower scores, new is blocked, nothing deleted."""
        from movarr.search import _supersede

        # New: 1080p BluRay (score 70)
        result = self._make_result(
            imdb_id="tt0133093",
            title="The Matrix 1999 1080p BluRay",
        )
        # One existing is 2160p (score 90), the other is 720p (score 60)
        torrent_map = self._make_torrent_map(
            [
                ("hash1", "The Matrix 1999 2160p BluRay 4K HDR", "tt0133093", 90),
                ("hash2", "The Matrix 1999 720p BluRay", "tt0133093", 60),
            ]
        )
        session = self._make_session(mocker, torrent_map=torrent_map)

        _supersede(result, session)

        assert result["result"] == "Failed"
        assert any("Superseded" in d for d in result.get("result_details", []))
        session.qbt.delete_torrent.assert_not_called()

    def test_noop_when_supersede_disabled_in_config(self, mocker: MockerFixture) -> None:
        """When config.supersede_enabled is False, _supersede returns without any action."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.config.queue_management.supersede_enabled = False
        torrent_map = self._make_torrent_map([("hash1", "The Matrix 1999 1080p BluRay", "tt0133093", 60)])
        session.qbt.list_by_category.return_value = torrent_map
        result = self._make_result()

        _supersede(result, session)

        assert result["result"] == "Passed"
        session.qbt.delete_torrent.assert_not_called()

    def test_skips_match_with_empty_torrent_name(self, mocker: MockerFixture) -> None:
        """Matching IMDb torrent with empty name is skipped (cannot sanitise)."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.return_value = {
            "hash1": {"name": "", "tags": "movarr-deadbeef-imdb-tt0133093-score-60"}
        }
        result = self._make_result()

        _supersede(result, session)

        assert result["result"] == "Passed"
        session.qbt.delete_torrent.assert_not_called()

    def test_mark_stalled_exception_handled_gracefully(self, mocker: MockerFixture) -> None:
        """When mark_stalled raises during deletion, remaining deletions still proceed."""
        from movarr.search import _delete_superseded_matches, _supersede

        session = self._make_session(mocker)
        torrent_map = self._make_torrent_map([("hash1", "The Matrix 1999 1080p WebDL", "tt0133093", 60)])
        session.qbt.list_by_category.return_value = torrent_map
        session.db.mark_stalled.side_effect = RuntimeError("db down")
        result = self._make_result()

        to_delete = _supersede(result, session)

        assert result["result"] == "Passed"
        assert len(to_delete) == 1

        _delete_superseded_matches(session, to_delete, "tt0133093", result["index_title"])
        session.qbt.delete_torrent.assert_called_once()
