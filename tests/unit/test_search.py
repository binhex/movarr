"""Unit tests for movarr.search."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

from movarr.config import Config, SearchCriteriaConfig
from movarr.search import _enrich_index_metadata, _process_criteria, run_search

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_result(index_title: str = "The Matrix 1999 1080p BluRay") -> dict[str, Any]:
    """Minimal result dict as returned by jackett.search."""
    return {"index_title": index_title}


# ---------------------------------------------------------------------------
# _enrich_index_metadata
# ---------------------------------------------------------------------------


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
        raw: dict[str, Any] = {"index_title": ""}
        result = _enrich_index_metadata(raw)
        assert "movie_title" not in result
        assert "result" not in result

    def test_missing_index_title_key_returns_result_unchanged(self) -> None:
        raw: dict[str, Any] = {}
        result = _enrich_index_metadata(raw)
        assert "movie_title" not in result

    def test_dotted_title_parsed_correctly(self) -> None:
        result = _enrich_index_metadata(_base_result("The.Matrix.1999.1080p.BluRay"))
        assert result.get("movie_title") == "The Matrix"

    def test_after_year_field_set(self) -> None:
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert "index_title_after_year_to_end" in result

    def test_existing_fields_preserved(self) -> None:
        raw: dict[str, Any] = {
            "index_title": "The Matrix 1999 1080p BluRay",
            "index_size": "8000000000",
        }
        result = _enrich_index_metadata(raw)
        assert result["index_size"] == "8000000000"


# ---------------------------------------------------------------------------
# run_search
# ---------------------------------------------------------------------------


class TestRunSearch:
    """Tests for run_search — top-level pipeline dispatcher."""

    def test_no_search_criteria_skips_jackett(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.index_site.search = []
        mock_jackett_cls = mocker.patch("movarr.search.JackettClient")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        mock_jackett_cls.assert_not_called()

    def test_jackett_not_reachable_skips_criteria_processing(self, mocker: MockerFixture) -> None:
        cfg = Config()
        mock_jackett_cls = mocker.patch("movarr.search.JackettClient")
        mock_jackett_cls.return_value.is_reachable.return_value = False
        mocker.patch("movarr.search._process_criteria")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        mocker.patch("movarr.search._process_criteria").assert_not_called()

    def test_processes_each_criteria_tier(self, mocker: MockerFixture) -> None:
        cfg = Config()
        mock_jackett_cls = mocker.patch("movarr.search.JackettClient")
        mock_jackett_cls.return_value.is_reachable.return_value = True
        mock_process = mocker.patch("movarr.search._process_criteria")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        assert mock_process.call_count == len(cfg.index_site.search)

    def test_passes_jackett_instance_to_process_criteria(self, mocker: MockerFixture) -> None:
        cfg = Config()
        mock_jackett_cls = mocker.patch("movarr.search.JackettClient")
        mock_jackett_cls.return_value.is_reachable.return_value = True
        mock_process = mocker.patch("movarr.search._process_criteria")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        call_kwargs = mock_process.call_args_list[0][1]
        assert call_kwargs["jackett"] is mock_jackett_cls.return_value


# ---------------------------------------------------------------------------
# _process_criteria
# ---------------------------------------------------------------------------


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
        _process_criteria(
            criteria_cfg=self._criteria_cfg(),
            category="2000",
            indexer="all",
            config=config or Config(),
            jackett=jackett,
            qbt=qbt,
            db=db,
            library_walk=None,
        )

    def test_happy_path_full_pipeline(self, mocker: MockerFixture) -> None:
        """All pipeline stages pass → notification sent, torrent added, DB written."""
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
        qbt.add_torrent.return_value = None
        db = mocker.MagicMock()

        self._call(mocker, jackett, qbt, db)

        mock_filter_index.assert_called_once()
        mock_imdb_search.assert_called_once()
        mock_metadata.assert_called_once()
        mock_filter_imdb.assert_called_once()
        mock_notify.assert_called_once()
        qbt.add_torrent.assert_called_once()
        db.write.assert_called_once()

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

        self._call(mocker, jackett, qbt, db)

        db.write.assert_called_once()
        mock_notify.assert_not_called()

    def test_result_without_movie_title_skipped(self, mocker: MockerFixture) -> None:
        """Empty index_title → no parseable title → pipeline skipped entirely."""
        mock_filter_index = mocker.patch("movarr.search.filter_by_index")
        jackett = mocker.MagicMock()
        jackett.search.return_value = iter([{"index_title": ""}])
        db = mocker.MagicMock()
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

        self._call(mocker, jackett, qbt, db)

        mock_imdb_search.assert_not_called()

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
        jackett.search.return_value = iter([
            _base_result("The Matrix 1999 1080p BluRay"),
            _base_result("Inception 2010 1080p BluRay"),
        ])
        qbt = mocker.MagicMock()
        qbt.add_torrent.return_value = None
        db = mocker.MagicMock()

        self._call(mocker, jackett, qbt, db)

        assert db.write.call_count == 2
