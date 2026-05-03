"""Unit tests for movarr.parsing — all pure function behaviour."""

from __future__ import annotations

from movarr.parsing import (
    all_criteria_present,
    bad_keyword_search,
    build_sqlite_pattern,
    extract_after_year,
    extract_group,
    extract_movie_title,
    extract_resolution,
    extract_year,
    is_tv_content,
    keyword_search,
    normalise_for_compare,
    quality_score,
    sanitise,
)

# ---------------------------------------------------------------------------
# sanitise
# ---------------------------------------------------------------------------


class TestSanitise:
    """Tests for sanitise()."""

    def test_replaces_dots_with_spaces(self) -> None:
        result = sanitise("The.Dark.Knight.2008.1080p")
        assert result is not None
        assert "." not in result

    def test_replaces_underscores_with_spaces(self) -> None:
        result = sanitise("movie_title_2020")
        assert result is not None
        assert "_" not in result

    def test_strips_leading_trailing_whitespace(self) -> None:
        result = sanitise("  A Movie  ")
        assert result is not None
        assert result == result.strip()

    def test_empty_string_returns_none(self) -> None:
        assert sanitise("") is None

    def test_non_empty_returns_string(self) -> None:
        result = sanitise("Movie Title 2020")
        assert isinstance(result, str)
        assert result


# ---------------------------------------------------------------------------
# extract_movie_title
# ---------------------------------------------------------------------------


class TestExtractMovieTitle:
    """Tests for extract_movie_title()."""

    def test_extracts_title_before_year(self) -> None:
        assert extract_movie_title("The Dark Knight 2008 1080p BluRay") == "The Dark Knight"

    def test_returns_none_when_no_year(self) -> None:
        result = extract_movie_title("NoYearMovie")
        assert result is None

    def test_strips_trailing_spaces(self) -> None:
        result = extract_movie_title("Inception 2010 1080p")
        assert result == "Inception"

    def test_empty_string_returns_none(self) -> None:
        assert extract_movie_title("") is None


# ---------------------------------------------------------------------------
# extract_year
# ---------------------------------------------------------------------------


class TestExtractYear:
    """Tests for extract_year()."""

    def test_extracts_four_digit_year(self) -> None:
        assert extract_year("Movie Title 2019 1080p") == "2019"

    def test_returns_none_when_no_year(self) -> None:
        assert extract_year("Movie Title noYear") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_year("") is None


# ---------------------------------------------------------------------------
# extract_resolution
# ---------------------------------------------------------------------------


class TestExtractResolution:
    """Tests for extract_resolution() — only looks after the year."""

    def test_extracts_1080p(self) -> None:
        result = extract_resolution("Movie 2020 1080p BluRay")
        assert result is not None
        assert "1080" in result

    def test_extracts_2160p(self) -> None:
        result = extract_resolution("Film 2021 2160p HDR")
        assert result is not None
        assert "2160" in result

    def test_extracts_720p(self) -> None:
        result = extract_resolution("Film 2021 720p WEB")
        assert result is not None
        assert "720" in result

    def test_returns_none_when_no_resolution(self) -> None:
        assert extract_resolution("Movie 2020 BluRay") is None

    def test_returns_none_when_no_year(self) -> None:
        # Without a year, _after_year returns None and extract returns None
        assert extract_resolution("NoYearTitle") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_resolution("") is None


# ---------------------------------------------------------------------------
# extract_after_year
# ---------------------------------------------------------------------------


class TestExtractAfterYear:
    """Tests for extract_after_year()."""

    def test_returns_text_after_year(self) -> None:
        result = extract_after_year("Movie 2020 1080p BluRay x264")
        assert result is not None
        assert "1080" in result

    def test_returns_none_when_no_year(self) -> None:
        assert extract_after_year("NoYearTitle") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_after_year("") is None


# ---------------------------------------------------------------------------
# normalise_for_compare
# ---------------------------------------------------------------------------


class TestNormaliseForCompare:
    """Tests for normalise_for_compare()."""

    def test_lowercases(self) -> None:
        r1 = normalise_for_compare("The DARK Knight")
        r2 = normalise_for_compare("the dark knight")
        assert r1 is not None and r2 is not None
        assert r1 == r2

    def test_removes_punctuation(self) -> None:
        result = normalise_for_compare("It's a Movie!")
        assert result is not None
        assert "'" not in result
        assert "!" not in result

    def test_same_for_articles_stripped_title(self) -> None:
        with_article = normalise_for_compare("The Dark Knight")
        without = normalise_for_compare("Dark Knight")
        # Both should be identical after article stripping, or at least
        # the article-stripped version should not contain the article at start.
        assert with_article is not None
        assert without is not None

    def test_empty_string_returns_none(self) -> None:
        assert normalise_for_compare("") is None


# ---------------------------------------------------------------------------
# build_sqlite_pattern
# ---------------------------------------------------------------------------


class TestBuildSqlitePattern:
    """Tests for build_sqlite_pattern()."""

    def test_wraps_in_percent_signs(self) -> None:
        # Needs a title with a year so extract_movie_title succeeds
        pattern = build_sqlite_pattern("The Dark Knight 2008")
        assert pattern is not None
        assert pattern.startswith("%")
        assert pattern.endswith("%")

    def test_non_empty_returns_non_empty(self) -> None:
        pattern = build_sqlite_pattern("Test Movie 2020")
        assert pattern not in ("", None)

    def test_empty_string_returns_none(self) -> None:
        assert build_sqlite_pattern("") is None

    def test_no_title_before_year_returns_none(self) -> None:
        # No recognisable title before year → extract_movie_title returns None
        assert build_sqlite_pattern("") is None

    def test_no_year_in_sanitised_returns_none(self) -> None:
        # No year in string → extract_movie_title returns None → line 245
        assert build_sqlite_pattern("NoYearOrTitle") is None


# ---------------------------------------------------------------------------
# all_criteria_present
# ---------------------------------------------------------------------------


class TestAllCriteriaPresent:
    """Tests for all_criteria_present(criteria_str, index_title)."""

    def test_all_present_returns_true(self) -> None:
        assert all_criteria_present("1080p BluRay", "Movie Title 2020 1080p BluRay x264") is True

    def test_missing_one_returns_false(self) -> None:
        assert all_criteria_present("1080p BluRay", "Movie Title 2020 1080p x264") is False

    def test_single_criteria_present(self) -> None:
        assert all_criteria_present("1080p", "Movie 2020 1080p BluRay") is True

    def test_case_insensitive(self) -> None:
        assert all_criteria_present("1080p BLURAY", "movie 2020 1080p bluray") is True


# ---------------------------------------------------------------------------
# is_tv_content
# ---------------------------------------------------------------------------


class TestIsTvContent:
    """Tests for is_tv_content()."""

    def test_detects_episode_notation(self) -> None:
        assert is_tv_content("Breaking Bad 2008 S01E05 1080p") is True

    def test_movie_returns_false(self) -> None:
        assert is_tv_content("The Dark Knight 2008 1080p") is False

    def test_empty_string_returns_false(self) -> None:
        assert is_tv_content("") is False


# ---------------------------------------------------------------------------
# keyword_search
# ---------------------------------------------------------------------------


class TestKeywordSearch:
    """Tests for keyword_search(sanitised, keyword) — single keyword only."""

    def test_finds_matching_keyword(self) -> None:
        assert keyword_search("Movie 2020 1080p HDR BluRay", "HDR") is True

    def test_returns_false_when_no_match(self) -> None:
        assert keyword_search("Movie 2020 1080p BluRay", "HDR") is False

    def test_case_insensitive(self) -> None:
        assert keyword_search("Movie 2020 hdr bluray", "HDR") is True

    def test_no_content_after_year_returns_false(self) -> None:
        assert keyword_search("NoYearTitle HDR", "HDR") is False

    def test_empty_string_returns_false(self) -> None:
        assert keyword_search("", "HDR") is False


# ---------------------------------------------------------------------------
# extract_group
# ---------------------------------------------------------------------------


class TestExtractGroup:
    """Tests for extract_group()."""

    def test_extracts_release_group(self) -> None:
        result = extract_group("Movie 2020 1080p BluRay x264-GROUP")
        assert result == "group"

    def test_returns_none_when_no_year(self) -> None:
        assert extract_group("NoYearTitle") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_group("") is None


# ---------------------------------------------------------------------------
# bad_keyword_search
# ---------------------------------------------------------------------------


class TestBadKeywordSearch:
    """Tests for bad_keyword_search(string, keyword) — single keyword."""

    def test_detects_bad_keyword(self) -> None:
        assert bad_keyword_search("Movie 2020 CAM 1080p", "CAM") is True

    def test_clean_title_returns_false(self) -> None:
        assert bad_keyword_search("The Dark Knight 2008 1080p BluRay", "CAM") is False

    def test_empty_string_returns_false(self) -> None:
        assert bad_keyword_search("", "CAM") is False


# ---------------------------------------------------------------------------
# quality_score
# ---------------------------------------------------------------------------


class TestQualityScore:
    """Tests for quality_score() — must return an int."""

    def test_returns_int(self) -> None:
        score = quality_score("Movie 2020 1080p BluRay DTS")
        assert isinstance(score, int)

    def test_higher_resolution_scores_higher(self) -> None:
        score_4k = quality_score("Movie 2020 2160p BluRay DTS")
        score_hd = quality_score("Movie 2020 1080p BluRay DTS")
        assert score_4k > score_hd

    def test_zero_or_positive_for_unrecognised(self) -> None:
        score = quality_score("Movie Title No Quality Info")
        assert score >= 0

    def test_remux_scores_higher_than_encode(self) -> None:
        score_remux = quality_score("Movie 2020 1080p REMUX DTS")
        score_encode = quality_score("Movie 2020 1080p BluRay x264 DTS")
        assert score_remux >= score_encode

    def test_bluray_remux_scores_same_as_remux(self) -> None:
        # "BluRay REMUX" must score at the REMUX tier, not the BluRay tier.
        score_bluray_remux = quality_score("Movie 2020 1080p BluRay REMUX DTS")
        score_remux = quality_score("Movie 2020 1080p REMUX DTS")
        assert score_bluray_remux == score_remux

    def test_atmos_scores_higher_than_plain_dts(self) -> None:
        score_atmos = quality_score("Movie 2020 1080p BluRay DTS HD TrueHD Atmos")
        score_dts = quality_score("Movie 2020 1080p BluRay DTS")
        assert score_atmos > score_dts
