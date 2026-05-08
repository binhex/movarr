"""Unit tests for movarr.post_processor."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    from pytest_mock import MockerFixture

from movarr.config import Config, CopyLibraryRuleConfig, DefaultCopyLibraryConfig, PathRemappingConfig
from movarr.filters import composite_quality_score
from movarr.post_processor import (
    _apply_path_remapping,
    _build_copy_list,
    _canonical_filename,
    _cert_acceptable,
    _first_level_dir,
    _largest_file,
    _parse_genres,
    _pick_path,
    _process_one,
    _resolution_from_index_title,
    _resolve_destination,
    _safe_path_component,
    run_post_processing,
)



class TestCompositeQualityScore:
    """composite_quality_score is the public scoring surface used by the deletion step."""

    def test_higher_score_for_remux(self) -> None:
        cfg = Config()
        new_san = "The Matrix 1999 1080p Remux"
        lib_san = "The Matrix 1999 1080p BluRay"
        assert composite_quality_score(new_san, lib_san, cfg) > composite_quality_score(lib_san, new_san, cfg)

    def test_equal_score_for_identical_titles(self) -> None:
        cfg = Config()
        san = "The Matrix 1999 1080p BluRay"
        assert composite_quality_score(san, san, cfg) == composite_quality_score(san, san, cfg)

    def test_preferred_group_bonus_applied(self) -> None:
        from movarr.config import FiltersConfig
        cfg = Config()
        cfg = cfg.model_copy(
            update={"filters": FiltersConfig(preferred_index_group_list=["PublicHD"])}
        )
        new_san = "The Matrix 1999 1080p BluRay PublicHD"
        lib_san = "The Matrix 1999 1080p BluRay OtherGroup"
        assert composite_quality_score(new_san, lib_san, cfg) > composite_quality_score(lib_san, new_san, cfg)


# _safe_path_component


class TestSafePathComponent:
    """Tests for _safe_path_component — pure function."""

    def test_clean_string_unchanged(self) -> None:
        assert _safe_path_component("The Matrix") == "The Matrix"

    def test_strips_forward_slash(self) -> None:
        assert _safe_path_component("movie/title") == "movietitle"

    def test_strips_backslash(self) -> None:
        assert _safe_path_component("movie\\title") == "movietitle"

    def test_strips_angle_brackets(self) -> None:
        assert _safe_path_component("<bad>") == "bad"

    def test_strips_colon(self) -> None:
        # colon is unsafe; space is kept
        assert _safe_path_component("title: subtitle") == "title subtitle"

    def test_strips_pipe(self) -> None:
        assert _safe_path_component("foo|bar") == "foobar"

    def test_strips_question_mark(self) -> None:
        assert _safe_path_component("what?") == "what"

    def test_strips_asterisk(self) -> None:
        assert _safe_path_component("star*") == "star"

    def test_strips_double_dot(self) -> None:
        assert _safe_path_component("..secret") == "secret"

    def test_strips_null_byte(self) -> None:
        assert _safe_path_component("null\x00byte") == "nullbyte"

    def test_strips_surrounding_whitespace(self) -> None:
        assert _safe_path_component("  title  ") == "title"

    def test_empty_string(self) -> None:
        assert _safe_path_component("") == ""


# _cert_acceptable


class TestCertAcceptable:
    """Tests for _cert_acceptable — BBFC ordering."""

    def test_cert_below_max_is_acceptable(self) -> None:
        assert _cert_acceptable("15", "18") is True

    def test_cert_equal_to_max_is_acceptable(self) -> None:
        assert _cert_acceptable("18", "18") is True

    def test_cert_above_max_is_not_acceptable(self) -> None:
        assert _cert_acceptable("18", "15") is False

    def test_u_cert_acceptable_for_all(self) -> None:
        assert _cert_acceptable("U", "R18") is True

    def test_u_cert_acceptable_for_u(self) -> None:
        assert _cert_acceptable("U", "U") is True

    def test_r18_above_18(self) -> None:
        assert _cert_acceptable("R18", "18") is False

    def test_unknown_movie_cert_returns_false(self) -> None:
        assert _cert_acceptable("NR", "18") is False

    def test_unknown_max_cert_returns_false(self) -> None:
        assert _cert_acceptable("15", "MPAA") is False

    def test_empty_movie_cert_returns_false(self) -> None:
        assert _cert_acceptable("", "18") is False

    def test_empty_max_cert_returns_false(self) -> None:
        assert _cert_acceptable("15", "") is False

    def test_pg_below_12(self) -> None:
        assert _cert_acceptable("PG", "12") is True

    def test_12_below_12a(self) -> None:
        assert _cert_acceptable("12", "12A") is True


# _parse_genres


class TestParseGenres:
    """Tests for _parse_genres — handles multiple input formats."""

    def test_list_returned_directly(self) -> None:
        assert _parse_genres(["Action", "Drama"]) == ["Action", "Drama"]

    def test_json_string_parsed(self) -> None:
        assert _parse_genres('["Action", "Drama"]') == ["Action", "Drama"]

    def test_python_repr_string_parsed(self) -> None:
        assert _parse_genres("['Action', 'Drama']") == ["Action", "Drama"]

    def test_json_strips_whitespace(self) -> None:
        assert _parse_genres('["  Action  ", " Drama "]') == ["Action", "Drama"]

    def test_non_string_non_list_returns_empty(self) -> None:
        assert _parse_genres(42) == []

    def test_none_returns_empty(self) -> None:
        assert _parse_genres(None) == []

    def test_invalid_string_returns_empty(self) -> None:
        assert _parse_genres("{not valid json}") == []

    def test_bytes_json_parsed(self) -> None:
        assert _parse_genres(b'["Action"]') == ["Action"]

    def test_empty_list(self) -> None:
        assert _parse_genres([]) == []

    def test_single_genre_list(self) -> None:
        assert _parse_genres('["Horror"]') == ["Horror"]


# _largest_file


class TestLargestFile:
    """Tests for _largest_file — pure helper."""

    def test_empty_file_list_returns_empty_strings(self) -> None:
        torrent: dict[str, Any] = {"torrent_file_list": []}
        assert _largest_file(torrent) == ("", "")

    def test_missing_file_list_returns_empty_strings(self) -> None:
        assert _largest_file({}) == ("", "")

    def test_single_file_returns_correct_name_and_dir(self) -> None:
        torrent: dict[str, Any] = {
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 5_000_000_000},
            ]
        }
        fname, fdir = _largest_file(torrent)
        assert fname == "movie.mkv"
        assert fdir == "movie"

    def test_picks_largest_of_multiple_files(self) -> None:
        torrent: dict[str, Any] = {
            "torrent_file_list": [
                {"file_name": "movie/small.nfo", "file_size": 1024},
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ]
        }
        fname, _ = _largest_file(torrent)
        assert fname == "movie.mkv"

    def test_file_at_root_level_has_empty_dir(self) -> None:
        torrent: dict[str, Any] = {
            "torrent_file_list": [
                {"file_name": "movie.mkv", "file_size": 8_000_000_000},
            ]
        }
        fname, fdir = _largest_file(torrent)
        assert fname == "movie.mkv"
        assert fdir == ""

    def test_nested_path_returns_correct_dirname(self) -> None:
        torrent: dict[str, Any] = {
            "torrent_file_list": [
                {"file_name": "movie/sub/movie.mkv", "file_size": 8_000_000_000},
            ]
        }
        _, fdir = _largest_file(torrent)
        assert fdir == "movie/sub"


# _first_level_dir


class TestFirstLevelDir:
    """Tests for _first_level_dir — pure function."""

    def test_nested_path_returns_first_component(self) -> None:
        assert _first_level_dir("movie/sub/file.mkv") == "movie"

    def test_single_component(self) -> None:
        assert _first_level_dir("movie") == "movie"

    def test_empty_string_returns_empty(self) -> None:
        assert _first_level_dir("") == ""

    def test_filename_no_dir_returns_filename(self) -> None:
        assert _first_level_dir("file.mkv") == "file.mkv"

    def test_two_level_path(self) -> None:
        assert _first_level_dir("parent/child.mkv") == "parent"


# _canonical_filename


class TestCanonicalFilename:
    """Tests for _canonical_filename — pure function."""

    def test_non_video_extension_returned_unchanged(self) -> None:
        result = _canonical_filename("subs.srt", "movie")
        assert result == "subs.srt"

    def test_no_first_level_dir_returns_filename(self) -> None:
        result = _canonical_filename("movie.mkv", "")
        assert result == "movie.mkv"

    def test_parent_longer_than_filename_renames_file(self) -> None:
        # "The Dark Knight 2008 1080p BluRay" (32 chars) > "movie.mkv" (9 chars)
        result = _canonical_filename("movie.mkv", "The Dark Knight 2008 1080p BluRay")
        assert result.endswith(".mkv")
        assert result != "movie.mkv"

    def test_parent_shorter_than_filename_keeps_original(self) -> None:
        long_fname = "The.Dark.Knight.2008.1080p.BluRay.mkv"
        result = _canonical_filename(long_fname, "movie")
        assert result == long_fname

    def test_mp4_extension_preserved(self) -> None:
        result = _canonical_filename("movie.mp4", "A Very Long Directory Name Indeed")
        assert result.endswith(".mp4")

    def test_avi_extension_preserved(self) -> None:
        result = _canonical_filename("movie.avi", "A Very Long Directory Name Here")
        assert result.endswith(".avi")


# _pick_path


class TestPickPath:
    """Tests for _pick_path — routing logic."""

    def _default(self, hd: str = "/media/hd", uhd: str = "/media/uhd") -> DefaultCopyLibraryConfig:
        return DefaultCopyLibraryConfig(hd_path=hd, uhd_path=uhd)

    def _rule(
        self,
        name: str,
        genres: list[str],
        hd: str,
        uhd: str = "",
        max_cert: str | None = None,
    ) -> CopyLibraryRuleConfig:
        return CopyLibraryRuleConfig(name=name, genres=genres, hd_path=hd, uhd_path=uhd, max_certification=max_cert)

    def test_no_rules_returns_default_hd_path(self) -> None:
        result = _pick_path(["Action"], "15", "1080", [], self._default())
        assert result == "/media/hd"

    def test_uhd_resolution_returns_default_uhd_path(self) -> None:
        result = _pick_path(["Action"], "15", "2160", [], self._default())
        assert result == "/media/uhd"

    def test_4k_resolution_returns_default_uhd_path(self) -> None:
        result = _pick_path(["Action"], "15", "4k", [], self._default())
        assert result == "/media/uhd"

    def test_matching_rule_overrides_default(self) -> None:
        rule = self._rule("action", ["Action"], "/media/action")
        result = _pick_path(["Action", "Drama"], "15", "1080", [rule], self._default())
        assert result == "/media/action"

    def test_cert_fails_max_cert_falls_back_to_default(self) -> None:
        rule = self._rule("kids", ["Animation"], "/media/kids", max_cert="PG")
        result = _pick_path(["Animation"], "15", "1080", [rule], self._default())
        assert result == "/media/hd"

    def test_cert_within_max_cert_uses_rule_path(self) -> None:
        rule = self._rule("kids", ["Animation"], "/media/kids", max_cert="PG")
        result = _pick_path(["Animation"], "U", "1080", [rule], self._default())
        assert result == "/media/kids"

    def test_genre_tie_between_rules_falls_back_to_default(self) -> None:
        rule_a = self._rule("a", ["Action", "Drama"], "/media/a")
        rule_b = self._rule("b", ["Action", "Drama"], "/media/b")
        result = _pick_path(["Action", "Drama"], "15", "1080", [rule_a, rule_b], self._default())
        assert result == "/media/hd"

    def test_no_genre_match_returns_default(self) -> None:
        rule = self._rule("action", ["Action"], "/media/action")
        result = _pick_path(["Romance"], "15", "1080", [rule], self._default())
        assert result == "/media/hd"

    def test_uhd_resolution_no_uhd_path_falls_back_to_hd(self) -> None:
        result = _pick_path(["Action"], "15", "2160", [], self._default(hd="/media/hd", uhd=""))
        assert result == "/media/hd"

    def test_rule_with_no_hd_path_falls_back_to_default(self) -> None:
        rule = CopyLibraryRuleConfig(name="empty", genres=["Action"], hd_path="", uhd_path="")
        result = _pick_path(["Action"], "15", "1080", [rule], self._default())
        assert result == "/media/hd"

    def test_case_insensitive_genre_matching(self) -> None:
        rule = self._rule("action", ["action"], "/media/action")
        result = _pick_path(["ACTION"], "15", "1080", [rule], self._default())
        assert result == "/media/action"

    def test_best_scoring_rule_wins(self) -> None:
        rule_one = self._rule("one-genre", ["Action"], "/media/one")
        rule_two = self._rule("two-genres", ["Action", "Crime"], "/media/two")
        result = _pick_path(["Action", "Crime", "Drama"], "15", "1080", [rule_one, rule_two], self._default())
        assert result == "/media/two"


# _build_copy_list


class TestBuildCopyList:
    """Tests for _build_copy_list — exclusion logic."""

    def _config(
        self,
        min_kb: int = 0,
        file_regex: list[str] | None = None,
        folder_regex: list[str] | None = None,
    ) -> Config:
        cfg = Config()
        cfg.post_process.exclude_file_min_kb = min_kb
        cfg.post_process.exclude_file_regex_list = file_regex or []
        cfg.post_process.exclude_folder_regex_list = folder_regex or []
        return cfg

    def test_normal_file_included(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config())
        assert len(result) == 1
        assert result[0].endswith("movie.mkv")

    def test_multiple_files_all_included(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
                {"file_name": "movie/movie.srt", "file_size": 50_000},
            ],
        }
        result = _build_copy_list(torrent, self._config())
        assert len(result) == 2

    def test_file_excluded_by_file_regex(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
                {"file_name": "movie/sample.mkv", "file_size": 50_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(file_regex=[r"sample"]))
        assert len(result) == 1
        assert not any("sample" in r for r in result)

    def test_file_excluded_by_folder_regex(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
                {"file_name": "extras/behind.mkv", "file_size": 500_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(folder_regex=[r"extras"]))
        assert len(result) == 1
        assert not any("behind" in r for r in result)

    def test_file_excluded_below_min_kb(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
                {"file_name": "movie/small.nfo", "file_size": 1024},  # 1 KB
            ],
        }
        result = _build_copy_list(torrent, self._config(min_kb=100))
        assert len(result) == 1
        assert not any("small" in r for r in result)

    def test_empty_file_name_skipped(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "", "file_size": 8_000_000_000},
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config())
        assert len(result) == 1

    def test_path_escape_attack_skipped(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "../sneaky.mkv", "file_size": 8_000_000_000},
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config())
        # Only the safe file should be included; the traversal file is rejected
        assert len(result) == 1
        assert result[0].endswith("movie.mkv")
        assert not any("sneaky" in r for r in result)

    def test_all_excluded_returns_empty_list(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/sample.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(file_regex=[r"sample"]))
        assert result == []

    def test_invalid_file_regex_skipped(self, tmp_path: Path) -> None:
        """Invalid regex in exclude_file_regex_list is skipped and file is kept."""
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(file_regex=[r"(invalid"]))
        assert len(result) == 1
        assert result[0].endswith("movie.mkv")

    def test_invalid_folder_regex_skipped(self, tmp_path: Path) -> None:
        """Invalid regex in exclude_folder_regex_list is skipped and file is kept."""
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(folder_regex=[r"(invalid"]))
        assert len(result) == 1
        assert result[0].endswith("movie.mkv")

    def test_empty_file_list_returns_empty(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [],
        }
        result = _build_copy_list(torrent, self._config())
        assert result == []

    def test_file_regex_is_case_insensitive(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/SAMPLE.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(file_regex=[r"sample"]))
        assert result == []


# _resolve_destination


class TestResolveDestination:
    """Tests for _resolve_destination — routing based on genre/cert/resolution."""

    def _config_with_default(self, hd: str = "/media/hd", uhd: str = "/media/uhd") -> Config:
        cfg = Config()
        cfg.post_process.default_copy_library.hd_path = hd
        cfg.post_process.default_copy_library.uhd_path = uhd
        return cfg

    def _db_record(
        self,
        mocker: MockerFixture,
        genres: str = '["Action"]',
        cert: str = "15",
        cert_source: str = "imdbpie",
        index_title: str = "Movie 2020 1080p BluRay",
    ) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_genres_list = genres
        rec.imdb_certification = cert
        rec.imdb_cert_source = cert_source
        rec.index_title = index_title
        return rec

    def test_returns_default_hd_for_hd_content(self, mocker: MockerFixture) -> None:
        config = self._config_with_default()
        rec = self._db_record(mocker)
        result = _resolve_destination(rec, config)
        assert result == "/media/hd"

    def test_no_default_paths_configured_returns_none(self, mocker: MockerFixture) -> None:
        config = self._config_with_default(hd="", uhd="")
        rec = self._db_record(mocker)
        result = _resolve_destination(rec, config)
        assert result is None

    def test_omdb_cert_not_applied_to_bbfc_rules(self, mocker: MockerFixture) -> None:
        """Bug #8: MPAA certs from omdb must not be routed via BBFC rules."""
        cfg = self._config_with_default()
        rule = CopyLibraryRuleConfig(name="kids", genres=["Animation"], max_certification="PG", hd_path="/media/kids")
        cfg.post_process.copy_library_rules = [rule]
        # MPAA "R" from omdb → effective_cert="" → cert check fails → default path used
        rec = self._db_record(mocker, genres='["Animation"]', cert="R", cert_source="omdb")
        result = _resolve_destination(rec, cfg)
        assert result == "/media/hd"

    def test_imdbpie_cert_applied_to_bbfc_routing(self, mocker: MockerFixture) -> None:
        cfg = self._config_with_default()
        rule = CopyLibraryRuleConfig(name="kids", genres=["Animation"], max_certification="PG", hd_path="/media/kids")
        cfg.post_process.copy_library_rules = [rule]
        # cert "15" > "PG" in BBFC → cert fails → default path
        rec = self._db_record(mocker, genres='["Animation"]', cert="15", cert_source="imdbpie")
        result = _resolve_destination(rec, cfg)
        assert result == "/media/hd"

    def test_imdbpie_u_cert_routes_to_kids_path(self, mocker: MockerFixture) -> None:
        cfg = self._config_with_default()
        rule = CopyLibraryRuleConfig(name="kids", genres=["Animation"], max_certification="PG", hd_path="/media/kids")
        cfg.post_process.copy_library_rules = [rule]
        rec = self._db_record(mocker, genres='["Animation"]', cert="U", cert_source="imdbpie")
        result = _resolve_destination(rec, cfg)
        assert result == "/media/kids"


# run_post_processing


class TestRunPostProcessing:
    """Tests for run_post_processing — main entry point."""

    def test_disabled_returns_early_without_connection_check(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = False
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        qbt.is_connected.assert_not_called()

    def test_not_connected_returns_early_without_listing(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = False
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        qbt.list_completed.assert_not_called()

    def test_no_completed_torrents_returns_early(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = []
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        db.find_by_tag.assert_not_called()

    def test_processes_each_completed_torrent(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        mock_process_one = mocker.patch("movarr.post_processor._process_one")
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [
            {"torrent_hash": "abc", "torrent_tag": "tag1"},
            {"torrent_hash": "def", "torrent_tag": "tag2"},
        ]
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        assert mock_process_one.call_count == 2

    def test_process_one_receives_correct_args(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        mock_process_one = mocker.patch("movarr.post_processor._process_one")
        torrent = {"torrent_hash": "abc", "torrent_tag": "tag1"}
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [torrent]
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        mock_process_one.assert_called_once_with(torrent, cfg, qbt, db)


# _resolution_from_index_title


class TestResolutionFromIndexTitle:
    """Tests for _resolution_from_index_title — thin wrapper around parsing helpers."""

    def test_empty_string_returns_none(self) -> None:
        assert _resolution_from_index_title("") is None

    def test_whitespace_returns_none(self) -> None:
        assert _resolution_from_index_title("   ") is None

    def test_title_with_resolution_returns_digits(self) -> None:
        result = _resolution_from_index_title("The Matrix 1999 1080p BluRay")
        assert result == "1080"

    def test_title_without_resolution_returns_none(self) -> None:
        result = _resolution_from_index_title("The Matrix 1999 BluRay")
        assert result is None


# _canonical_filename — additional edge-cases


class TestCanonicalFilenameEdgeCases:
    """Additional edge-cases for _canonical_filename not covered in TestCanonicalFilename."""

    def test_whitespace_only_first_level_dir_returns_filename(self) -> None:
        """When _first_level_dir returns a string that sanitise() strips to None."""
        # "   " is a valid PurePosixPath component but sanitise("   ") → None
        result = _canonical_filename("movie.mkv", "   ")
        assert result == "movie.mkv"


# _pick_path — additional edge-cases


class TestPickPathEdgeCases:
    """Additional edge-cases for _pick_path not covered in TestPickPath."""

    def _default(self, hd: str = "/media/hd", uhd: str | None = "/media/uhd") -> DefaultCopyLibraryConfig:
        return DefaultCopyLibraryConfig(hd_path=hd, uhd_path=uhd)

    def _rule(self, name: str, genres: list[str], hd: str = "/media/hd", uhd: str = "") -> CopyLibraryRuleConfig:
        return CopyLibraryRuleConfig(name=name, genres=genres, hd_path=hd, uhd_path=uhd)

    def test_uhd_matching_rule_without_uhd_path_uses_rule_hd_path(self) -> None:
        """UHD resolution; matching rule has no uhd_path → falls back to rule's hd_path (line 302)."""
        rule = self._rule("Action", ["Action"], hd="/action/hd", uhd="")
        result = _pick_path(["Action"], "", "2160", [rule], self._default())
        assert result == "/action/hd"


# _build_copy_list — exception path


class TestBuildCopyListOSError:
    """Covers the OSError/ValueError handler in _build_copy_list (lines 169-171)."""

    def test_oserror_during_resolve_skips_file(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """When pathlib.Path.resolve() raises OSError inside the loop, file is skipped."""
        import pathlib as _pathlib

        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        cfg = Config()
        # First call (save_root) succeeds; second call (per-file abs_path) raises OSError
        mocker.patch(
            "movarr.post_processor.pathlib.Path.resolve",
            side_effect=[_pathlib.Path(str(tmp_path)), OSError("permission denied")],
        )
        result = _build_copy_list(torrent, cfg)
        assert result == []


# _process_one


class TestProcessOne:
    """Tests for _process_one — per-torrent processing pipeline."""

    def _config(self, remove_completed: bool = False) -> Config:
        cfg = Config()
        cfg.post_process.remove_completed = remove_completed
        cfg.post_process.post_process_enabled = True
        cfg.post_process.default_copy_library = DefaultCopyLibraryConfig(hd_path="/media/hd", uhd_path="")
        return cfg

    def _torrent(self, tag: str = "tag1", torrent_hash: str = "abc123") -> dict[str, Any]:
        return {
            "torrent_tag": tag,
            "torrent_hash": torrent_hash,
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 4_000_000_000},
            ],
        }

    def _db_record(self, mocker: MockerFixture, title: str = "The Matrix", year: str = "1999") -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = title
        rec.imdb_year = year
        rec.imdb_genres_list = "[]"
        rec.imdb_certification = ""
        rec.imdb_cert_source = "imdbpie"
        rec.index_title = "The.Matrix.1999.1080p.BluRay"
        return rec

    def test_no_db_record_returns_early(self, mocker: MockerFixture) -> None:
        """When db.find_by_tag returns None, everything else is skipped."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = None
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()

    def test_no_files_to_copy_returns_early(self, mocker: MockerFixture) -> None:
        """When _build_copy_list returns empty, processing is skipped."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=[])
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()

    def test_no_destination_returns_early(self, mocker: MockerFixture) -> None:
        """When _resolve_destination returns None, files are not copied."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value=None)
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()

    def test_make_directory_fails_returns_early(self, mocker: MockerFixture) -> None:
        """When make_directory returns False, copy is not attempted."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=False)
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()

    def test_copy_verify_fails_does_not_set_verified(self, mocker: MockerFixture) -> None:
        """When copy_with_verify returns False, mark_completed is not called."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=False)
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()
        qbt.delete_torrent.assert_not_called()

    def test_full_happy_path_sets_verified_and_deletes_torrent(self, mocker: MockerFixture) -> None:
        """All steps succeed: mark_completed called and torrent deleted (remove_completed=True)."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        qbt = mocker.MagicMock()
        cfg = self._config(remove_completed=True)
        torrent = self._torrent(tag="tag1", torrent_hash="deadbeef")

        _process_one(torrent, cfg, qbt, db)

        db.mark_completed.assert_called_once_with("tag1")
        qbt.delete_torrent.assert_called_once_with("deadbeef", delete_data=True, state="completed")

    def test_copy_succeeds_without_remove_completed(self, mocker: MockerFixture) -> None:
        """All steps succeed but remove_completed=False: mark_completed but no torrent deletion."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(remove_completed=False), qbt, db)

        db.mark_completed.assert_called_once()
        qbt.delete_torrent.assert_not_called()

    def test_title_with_only_dots_uses_unknown(self, mocker: MockerFixture) -> None:
        """imdb_title that sanitises to '.' (single dot) is replaced with 'Unknown'."""
        db = mocker.MagicMock()
        rec = self._db_record(mocker, title="...")
        db.find_by_tag.return_value = rec
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mock_mkdir = mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)

        _process_one(self._torrent(), self._config(), mocker.MagicMock(), db)

        dst_dir_arg = mock_mkdir.call_args[0][0]
        assert "Unknown" in dst_dir_arg


# _apply_path_remapping


class TestApplyPathRemapping:
    """Tests for _apply_path_remapping — prefix substitution."""

    def _remap(self, from_path: str, to_path: str) -> PathRemappingConfig:
        return PathRemappingConfig(from_path=from_path, to_path=to_path)

    def test_exact_match_returns_to_path(self) -> None:
        result = _apply_path_remapping("/downloads", [self._remap("/downloads", "/media")])
        assert result == "/media"

    def test_prefix_with_slash_is_replaced(self) -> None:
        result = _apply_path_remapping("/downloads/movie.mkv", [self._remap("/downloads", "/media")])
        assert result == "/media/movie.mkv"

    def test_prefix_with_backslash_is_replaced(self) -> None:
        result = _apply_path_remapping("C:\\downloads\\movie.mkv", [self._remap("C:\\downloads", "D:\\media")])
        assert result == "D:\\media\\movie.mkv"

    def test_no_match_returns_original_path(self) -> None:
        result = _apply_path_remapping("/other/path", [self._remap("/downloads", "/media")])
        assert result == "/other/path"

    def test_empty_from_path_is_skipped(self) -> None:
        result = _apply_path_remapping("/downloads/movie.mkv", [self._remap("", "/media")])
        assert result == "/downloads/movie.mkv"

    def test_empty_remappings_list_returns_original(self) -> None:
        result = _apply_path_remapping("/downloads/movie.mkv", [])
        assert result == "/downloads/movie.mkv"


# _process_one — non-largest-file branch (line 141)


class TestProcessOneMultiFile:
    """_process_one with multiple files — covers the else branch for non-largest files."""

    def _config(self) -> Config:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        cfg.post_process.remove_completed = False
        return cfg

    def _db_record(self, mocker: Any) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = "The Matrix"
        rec.imdb_year = "1999"
        rec.imdb_genres = '["Action"]'
        rec.imdb_certification = "15"
        rec.imdb_cert_source = "imdbpie"
        rec.index_title_resolution = "1080p"
        return rec

    def test_secondary_file_uses_original_filename(self, mocker: MockerFixture) -> None:
        """When src_files has two entries and only one matches largest_fname,
        the other uses src_fname directly (covering line 141)."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        # Two files: largest is "movie.mkv", secondary is "movie.nfo"
        mocker.patch(
            "movarr.post_processor._build_copy_list",
            return_value=["/dl/movie.mkv", "/dl/movie.nfo"],
        )
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        # _largest_file returns "movie.mkv" as the largest
        torrent = {
            "torrent_tag": "tag1",
            "torrent_hash": "abc123",
            "content_path": "/dl",
            "files": [
                {"name": "movie.mkv", "size": 8_000_000_000},
                {"name": "movie.nfo", "size": 1_000},
            ],
        }
        mocker.patch(
            "movarr.post_processor._largest_file",
            return_value=("movie.mkv", "movie.mkv"),
        )
        mocker.patch(
            "movarr.post_processor._canonical_filename",
            return_value="The Matrix (1999).mkv",
        )
        qbt = mocker.MagicMock()

        _process_one(torrent, self._config(), qbt, db)

        db.mark_completed.assert_called_once_with("tag1")


class TestProcessOneCopyCompletedFalse:
    """Tests for _process_one when copy_completed=False."""

    def _config(self, remove_completed: bool = False, copy_completed: bool = False) -> Config:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        cfg.post_process.copy_completed = copy_completed
        cfg.post_process.remove_completed = remove_completed
        return cfg

    def _torrent(self, tag: str = "tag1", torrent_hash: str = "abc123") -> dict[str, Any]:
        return {
            "torrent_tag": tag,
            "torrent_hash": torrent_hash,
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 4_000_000_000},
            ],
        }

    def _db_record(self, mocker: MockerFixture) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = "The Matrix"
        rec.imdb_year = "1999"
        return rec

    def test_copy_completed_false_marks_completed_skips_copy(self, mocker: MockerFixture, tmp_path: Any) -> None:
        """When copy_completed=False, DB is marked completed and no file copy occurs."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mock_copy = mocker.patch("movarr.post_processor.copy_with_verify")
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(copy_completed=False), qbt, db)

        db.mark_completed.assert_called_once_with("tag1")
        mock_copy.assert_not_called()

    def test_copy_completed_false_remove_completed_deletes_torrent_not_data(self, mocker: MockerFixture) -> None:
        """When copy_completed=False and remove_completed=True, torrent is removed
        without deleting data files (delete_data=False).

        This covers the use-case where qBittorrent writes directly to the final
        library path \u2014 movarr should remove the torrent entry from qBittorrent's
        queue but NEVER delete the downloaded files.
        """
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        _process_one(
            self._torrent(),
            self._config(copy_completed=False, remove_completed=True),
            qbt,
            db,
        )

        qbt.delete_torrent.assert_called_once_with("abc123", delete_data=False, state="completed")
        db.mark_completed.assert_called_once_with("tag1")
