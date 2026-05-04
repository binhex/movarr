from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture

    from movarr.models import ResultDict

from loguru import logger as _loguru_logger

from movarr.config import Config, SearchCriteriaConfig
from movarr.search import _enrich_index_metadata, _process_criteria, _SearchSession, run_search

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_result(index_title: str = "The Matrix 1999 1080p BluRay") -> ResultDict:
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

    def test_movie_title_and_year_search_set(self) -> None:
        """movie_title_and_year_search must be set — it's the query string for IMDb search."""
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert result.get("movie_title_and_year_search") == "The Matrix 1999"

    def test_index_title_compare_set(self) -> None:
        """index_title_compare must be set — all IMDb strategies use it to verify matches."""
        result = _enrich_index_metadata(_base_result("The Matrix 1999 1080p BluRay"))
        assert result.get("index_title_compare") is not None
        assert len(result["index_title_compare"]) > 0  # type: ignore[arg-type]

    def test_index_title_compare_set_even_without_year(self) -> None:
        """index_title_compare is always set from the sanitised title, regardless of year."""
        result = _enrich_index_metadata(_base_result("SomeTitle NoYear BluRay"))
        assert result.get("index_title_compare") is not None

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


# ---------------------------------------------------------------------------
# run_search
# ---------------------------------------------------------------------------


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
        cfg = Config()
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_factory.return_value.is_reachable.return_value = False
        mocker.patch("movarr.search._process_criteria")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        mocker.patch("movarr.search._process_criteria").assert_not_called()

    def test_processes_each_criteria_tier(self, mocker: MockerFixture) -> None:
        cfg = Config()
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_factory.return_value.is_reachable.return_value = True
        mock_process = mocker.patch("movarr.search._process_criteria")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        assert mock_process.call_count == len(cfg.index_site.search)

    def test_passes_jackett_instance_to_process_criteria(self, mocker: MockerFixture) -> None:
        cfg = Config()
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_factory.return_value.is_reachable.return_value = True
        mock_process = mocker.patch("movarr.search._process_criteria")
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_search(cfg, qbt, db)

        call_kwargs = mock_process.call_args_list[0][1]
        assert call_kwargs["session"].indexer is mock_factory.return_value


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
        db.is_duplicate_exact.return_value = False

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
        db.is_duplicate_exact.return_value = False

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


# ---------------------------------------------------------------------------
# Log-level behaviour — "passed all filters" must emit SUCCESS
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DB deduplication — skip already-seen titles before any API call
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# run_search — library_walk and override_search branches
# ---------------------------------------------------------------------------


class TestRunSearchLibraryWalkAndOverride:
    """Tests for run_search covering library_walk population and override_search."""

    def _make_base(self, mocker: MockerFixture) -> tuple[Config, Any, Any]:
        cfg = Config()
        mock_factory = mocker.patch("movarr.search.get_indexer_client")
        mock_factory.return_value.is_reachable.return_value = True
        mocker.patch("movarr.search._process_criteria")
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
        mock_process = mocker.patch("movarr.search._process_criteria")
        mocker.patch("movarr.search.get_indexer_client").return_value.is_reachable.return_value = True
        run_search(cfg, qbt, db)
        called_categories = [call[1]["category"] for call in mock_process.call_args_list]
        assert all(c == "9999" for c in called_categories)


# ---------------------------------------------------------------------------
# _process_criteria — no movie_title_year skips the result
# ---------------------------------------------------------------------------


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
        session.db.write.assert_not_called()
