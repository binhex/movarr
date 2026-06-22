"""Unit tests for movarr.notifications — notification helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from movarr.config import Config, NotificationConfig
from movarr.notifications import (
    _build_body,
    _build_subject,
    _dispatch_apprise,
    _format_result_details,
    _poster_url_with_width,
    _strip_poster_resolution,
    send_index_proxy_alert,
    send_queued_notification,
    send_service_alert,
)

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from movarr.models import ResultDict

# Helpers


def _make_full_result(**overrides: object) -> ResultDict:
    """Return a fully-populated result dict for notification testing."""
    base: ResultDict = {
        "imdb_title": "Inception",
        "imdb_year": 2010,
        "imdb_rating": 8.8,
        "imdb_votes": 2_000_000,
        "imdb_id": "tt1375666",
        "imdb_plot_outline": "A thief who steals corporate secrets.",
        "imdb_credits_cast_list": ["Leonardo DiCaprio", "Joseph Gordon-Levitt"],
        "imdb_credits_director_list": ["Christopher Nolan"],
        "imdb_genres_list": ["Action", "Adventure", "Sci-Fi"],
        "index_title": "Inception 2010 1080p BluRay",
        "index_details": "http://example.com/details",
        "index_size_mb": "8192",
        "result_details": ["Quality: Rating: 8.8"],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _make_config(urls: list[str]) -> Config:
    """Return a Config with apprise_urls set to *urls*."""
    return Config().model_copy(update={"notification": NotificationConfig(apprise_urls=urls)})


# _build_subject


class TestBuildSubject:
    """Tests for the _build_subject pure helper."""

    def test_with_year_and_rating(self) -> None:
        """Subject is a clean headline without rating or status."""
        result: ResultDict = {"imdb_title": "Inception", "imdb_year": 2010, "imdb_rating": 8.8}
        assert _build_subject(result) == "movarr: Inception (2010)"

    def test_without_year_omits_parentheses(self) -> None:
        """Subject omits year parentheses when imdb_year is absent."""
        result: ResultDict = {"imdb_title": "Unknown Film", "imdb_year": None, "imdb_rating": 7.5}
        subject = _build_subject(result)
        assert "()" not in subject
        assert "Unknown Film" in subject

    def test_without_rating_omits_rating(self) -> None:
        """Subject omits rating entirely when imdb_rating is absent."""
        result: ResultDict = {"imdb_title": "Film", "imdb_year": 2020, "imdb_rating": None}
        assert _build_subject(result) == "movarr: Film (2020)"

    def test_missing_title_defaults_to_unknown(self) -> None:
        """Subject defaults to 'Unknown' when imdb_title is missing."""
        assert "Unknown" in _build_subject({})


# _build_body


class TestBuildBody:
    """Tests for the _build_body pure helper."""

    def test_full_result_contains_key_fields(self) -> None:
        """Body contains status, score, actors and directors from a full result."""
        cfg = Config()
        body = _build_body(_make_full_result(), cfg)
        assert "Status:" in body
        assert "Score:" in body
        assert "Leonardo DiCaprio" in body
        assert "Christopher Nolan" in body
        assert "Action" in body

    def test_body_opens_with_status_and_score_not_title(self) -> None:
        """Body starts with Status and Score lines, not a redundant Title line."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = True
        body = _build_body(_make_full_result(), cfg)
        # Should start with Status and Score, not repeat the movie title
        assert "**Status:** Paused" in body
        assert "**Score:** 8.8 from 2000000 users" in body
        assert "**Title:**" not in body

    def test_no_html_formatting_tags_in_body(self) -> None:
        """Body contains no HTML formatting tags — pure Markdown with <details> block."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = False
        body = _build_body(_make_full_result(), cfg)
        assert "<p>" not in body
        assert "<br>" not in body
        assert "<strong>" not in body
        # Each field is on its own line
        assert body.startswith("**Status:**")

    def test_empty_cast_list_shows_dash(self) -> None:
        """Body shows '—' for actors when cast list is empty."""
        cfg = Config()
        result = _make_full_result(imdb_credits_cast_list=[])
        assert "—" in _build_body(result, cfg)

    def test_empty_director_list_shows_dash(self) -> None:
        """Body shows '—' for directors when director list is empty."""
        cfg = Config()
        result = _make_full_result(imdb_credits_director_list=[])
        assert "—" in _build_body(result, cfg)

    def test_none_cast_shows_dash(self) -> None:
        """Body shows '—' for actors when cast list is None (null-guard)."""
        cfg = Config()
        result = _make_full_result()
        result["imdb_credits_cast_list"] = None
        assert "—" in _build_body(result, cfg)

    def test_add_paused_true_shows_paused(self) -> None:
        """Queue status is 'Paused' when add_paused=True."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = True
        assert "Paused" in _build_body(_make_full_result(), cfg)

    def test_add_paused_false_shows_started(self) -> None:
        """Queue status is 'Started' when add_paused=False."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = False
        assert "Started" in _build_body(_make_full_result(), cfg)

    def test_imdb_url_built_from_id(self) -> None:
        """Body contains an IMDb URL built from imdb_id."""
        cfg = Config()
        assert "tt1375666" in _build_body(_make_full_result(), cfg)

    def test_missing_imdb_id_and_index_details_uses_hash_fallback(self) -> None:
        """Body uses '#' as href when both imdb_id and index_details are absent."""
        cfg = Config()
        result = _make_full_result()
        result.pop("imdb_id", None)
        result.pop("index_details", None)
        body = _build_body(result, cfg)
        assert "(<#>)" in body

    def test_imdb_bare_url_present_when_id_set(self) -> None:
        """Body contains a bare IMDb URL line when imdb_id is present."""
        cfg = Config()
        result = _make_full_result(imdb_id="tt1375666")
        body = _build_body(result, cfg)
        assert "https://imdb.com/title/tt1375666" in body

    def test_imdb_bare_url_absent_when_id_missing(self) -> None:
        """Body omits the IMDb bare URL line when imdb_id is missing."""
        cfg = Config()
        result = _make_full_result()
        result.pop("imdb_id", None)
        body = _build_body(result, cfg)
        assert "https://imdb.com/title/" not in body

    def test_poster_img_never_in_body(self) -> None:
        """Body never contains an inline poster <img> tag (poster removed from notifications)."""
        cfg = Config()
        cfg.notification.poster_embed_enabled = True
        result = _make_full_result(imdb_poster_url="https://m.media-amazon.com/images/M/MV5B._V1_.jpg")
        body = _build_body(result, cfg)
        assert '<img src="' not in body

    def test_body_uses_markdown_bold_labels(self) -> None:
        """Body uses **bold** Markdown syntax, not <strong> HTML tags."""
        cfg = Config()
        body = _build_body(_make_full_result(), cfg)
        assert "<strong>" not in body
        assert "**Status:**" in body
        assert "**Score:**" in body
        assert "**IMDb:**" in body
        assert "**Plot:**" in body
        assert "**Actors:**" in body
        assert "**Directors:**" in body
        assert "**Genres:**" in body
        assert "**Release:**" in body
        assert "**Size:**" in body

    def test_result_details_is_markdown_list(self) -> None:
        """Result details rendered as italic summary with bullet items."""
        cfg = Config()
        body = _build_body(_make_full_result(), cfg)
        assert "_1 items_" in body
        assert "- Quality: Rating: 8.8" in body

    def test_imdb_link_is_bare_url(self) -> None:
        """IMDb link is a bare URL (auto-linked by Markdown renderers)."""
        cfg = Config()
        body = _build_body(_make_full_result(imdb_id="tt1375666"), cfg)
        assert "https://imdb.com/title/tt1375666" in body
        # Should not be an HTML <a> tag
        assert "<a href=" not in body

    def test_release_link_is_markdown(self) -> None:
        """Release title links to index details via Markdown [text](url) syntax."""
        cfg = Config()
        body = _build_body(
            _make_full_result(
                index_title="Inception 2010 1080p BluRay",
                index_details="http://example.com/details",
            ),
            cfg,
        )
        assert "[Inception 2010 1080p BluRay](<http://example.com/details>)" in body


# _format_result_details


class TestFormatResultDetails:
    """Tests for the _format_result_details pure helper."""

    def test_details_wraps_list_with_summary_and_pass_count(self) -> None:
        """Output has italic summary line followed by bullet items."""
        details = [
            "Passed: check alpha",
            "Passed: check beta",
            "Passed: check gamma",
        ]
        result = _format_result_details(details)
        assert "_3 checks passed_" in result
        assert "- Passed: check alpha" in result

    def test_summary_counts_mixed_pass_fail(self) -> None:
        """Summary shows separate pass and fail counts."""
        details = [
            "Passed: check a",
            "Failed: check b",
            "Passed: check c",
        ]
        result = _format_result_details(details)
        assert "_2 passed, 1 failed_" in result

    def test_summary_all_failed(self) -> None:
        """Summary shows 0 passed when all checks failed."""
        details = [
            "Failed: check a",
            "Failed: check b",
        ]
        result = _format_result_details(details)
        assert "_0 passed, 2 failed_" in result

    def test_empty_list_shows_zero_checks(self) -> None:
        """Empty list produces italic '0 checks' summary."""
        result = _format_result_details([])
        assert "_0 checks_" in result


# send_queued_notification


class TestSendQueuedNotification:
    """Tests for the public send_queued_notification function."""

    def test_empty_url_list_returns_false_immediately(self) -> None:
        """Returns False without calling apprise when apprise_urls is empty."""
        cfg = Config()
        cfg.notification.apprise_urls = []
        assert send_queued_notification(_make_full_result(), cfg) is False

    def test_single_url_notifies_and_returns_true(self, mocker: MockerFixture) -> None:
        """Returns True when apprise successfully delivers the notification."""
        cfg = Config()
        cfg.notification.apprise_urls = ["ntfy://topic"]

        mock_instance = mocker.MagicMock()
        mock_instance.notify.return_value = True
        mock_cls = mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

        result = send_queued_notification(_make_full_result(), cfg)

        assert result is True
        mock_cls.assert_called_once()
        mock_instance.add.assert_called_once_with("ntfy://topic")
        mock_instance.notify.assert_called_once()

    def test_multiple_urls_all_added(self, mocker: MockerFixture) -> None:
        """All URLs in apprise_urls are added to the Apprise instance."""
        cfg = Config()
        cfg.notification.apprise_urls = ["ntfy://topic", "discord://id/token"]

        mock_instance = mocker.MagicMock()
        mock_instance.notify.return_value = True
        mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

        send_queued_notification(_make_full_result(), cfg)

        assert mock_instance.add.call_count == 2

    def test_apprise_returns_false_returns_false(self, mocker: MockerFixture) -> None:
        """Returns False when apprise.notify() returns False."""
        cfg = Config()
        cfg.notification.apprise_urls = ["ntfy://topic"]

        mock_instance = mocker.MagicMock()
        mock_instance.notify.return_value = False
        mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

        assert send_queued_notification(_make_full_result(), cfg) is False

    def test_apprise_raises_exception_returns_false(self, mocker: MockerFixture) -> None:
        """Returns False when apprise.notify() raises an unexpected exception."""
        cfg = Config()
        cfg.notification.apprise_urls = ["ntfy://topic"]

        mock_instance = mocker.MagicMock()
        mock_instance.notify.side_effect = RuntimeError("connection refused")
        mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

        assert send_queued_notification(_make_full_result(), cfg) is False

    def test_notify_without_attach(self, mocker: MockerFixture) -> None:
        """Calls apprise.notify() without attach parameter (poster removed from notifications)."""
        cfg = Config()
        cfg.notification.apprise_urls = ["ntfy://topic"]

        mock_instance = mocker.MagicMock()
        mock_instance.notify.return_value = True
        mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

        result = _make_full_result(imdb_poster_url="https://m.media-amazon.com/images/M/MV5B._V1_.jpg")
        send_queued_notification(result, cfg)

        _call_kwargs = mock_instance.notify.call_args[1]
        assert "attach" not in _call_kwargs


# NotificationConfig defaults (via Config)


class TestNotificationConfigDefaults:
    """NotificationConfig must default to empty URL list."""

    def test_default_apprise_urls_is_empty_list(self) -> None:
        """Default config has no apprise URLs configured."""
        cfg = Config()
        assert cfg.notification.apprise_urls == []

    def test_apprise_urls_accepts_list_of_strings(self) -> None:
        """apprise_urls accepts a list of service URL strings."""
        nc = NotificationConfig(apprise_urls=["ntfy://alerts", "mailtos://user:pass@smtp.host:587"])
        assert len(nc.apprise_urls) == 2

    def test_poster_embed_enabled_defaults_to_true(self) -> None:
        """poster_embed_enabled defaults to True."""
        cfg = Config()
        assert cfg.notification.poster_embed_enabled is True

    def test_poster_embed_width_defaults_to_500(self) -> None:
        """poster_embed_width defaults to 500."""
        cfg = Config()
        assert cfg.notification.poster_embed_width == 500

    def test_poster_art_filename_defaults_to_empty(self) -> None:
        """poster_art.filename defaults to empty string (disabled)."""
        cfg = Config()
        assert cfg.post_process.poster_art.filename == ""

    def test_poster_art_download_width_defaults_to_0(self) -> None:
        """poster_art.download_width defaults to 0 (largest available)."""
        cfg = Config()
        assert cfg.post_process.poster_art.download_width == 0


class TestSendIndexProxyAlert:
    """Tests for send_index_proxy_alert()."""

    def test_returns_false_when_no_urls_configured(self) -> None:
        """Returns False immediately when apprise_urls is empty."""
        config = _make_config([])
        result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=3.0, config=config)
        assert result is False

    def test_calls_apprise_when_urls_configured(self) -> None:
        """Calls apprise.Apprise.notify() when URLs are present."""
        config = _make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Jackett", hours_elapsed=2.5, config=config)
        assert result is True
        mock_ap.notify.assert_called_once()

    def test_subject_contains_proxy_name_and_hours(self) -> None:
        """Notification subject includes the proxy name and elapsed hours."""
        config = _make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=4.0, config=config)
        _, kwargs = mock_ap.notify.call_args
        assert "Prowlarr" in kwargs["title"]
        assert "4" in kwargs["title"]

    def test_returns_false_when_apprise_returns_false(self) -> None:
        """Returns False when apprise.notify() returns False."""
        config = _make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = False
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=1.0, config=config)
        assert result is False

    def test_returns_false_when_apprise_raises(self) -> None:
        """Returns False (does not propagate) when apprise.notify() raises."""
        config = _make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("boom")
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=1.0, config=config)
        assert result is False


class TestSendServiceAlert:
    """Tests for the generic send_service_alert() function."""

    def test_returns_false_when_no_urls_configured(self) -> None:
        """Returns False immediately when apprise_urls is empty."""
        config = _make_config([])
        assert send_service_alert(service_name="qBittorrent", hours_elapsed=3.0, config=config) is False

    def test_calls_apprise_when_urls_configured(self) -> None:
        """Calls apprise.Apprise.notify() and returns True on success."""
        config = _make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=2.0, config=config) is True
        mock_ap.notify.assert_called_once()

    def test_subject_contains_service_name_and_hours(self) -> None:
        """Subject line includes service name and elapsed hours."""
        config = _make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            send_service_alert(service_name="qBittorrent", hours_elapsed=4.0, config=config)
        _, kwargs = mock_ap.notify.call_args
        assert "qBittorrent" in kwargs["title"]
        assert "4" in kwargs["title"]

    def test_returns_false_when_apprise_returns_false(self) -> None:
        """Returns False when apprise.notify() returns False."""
        config = _make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = False
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=1.0, config=config) is False

    def test_returns_false_when_apprise_raises(self) -> None:
        """Returns False and does not propagate when apprise raises."""
        config = _make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("boom")
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=1.0, config=config) is False

    def test_body_uses_markdown_bold_labels(self) -> None:
        """Service alert body uses **bold** Markdown, not <strong> HTML."""
        config = _make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            send_service_alert(service_name="qBittorrent", hours_elapsed=2.0, config=config)
        _, kwargs = mock_ap.notify.call_args
        body = kwargs["body"]
        assert "<strong>" not in body
        assert "**" in body


class TestSendIndexProxyAlertDelegates:
    """send_index_proxy_alert() must delegate to send_service_alert()."""

    def test_delegates_to_send_service_alert(self) -> None:
        """send_index_proxy_alert() calls send_service_alert() with correct args."""
        config = Config()
        with patch("movarr.notifications.send_service_alert", return_value=True) as mock_generic:
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=3.0, config=config)
        mock_generic.assert_called_once_with(service_name="Prowlarr", hours_elapsed=3.0, config=config)
        assert result is True


# _safe_url via _build_body — edge cases


class TestSafeUrlEdgeCases:
    """_safe_url edge-cases exercised through _build_body."""

    def test_missing_index_details_uses_hash_href(self) -> None:
        """When index_details is absent the Markdown link uses href='#'."""
        cfg = Config()
        result = _make_full_result()
        result.pop("index_details", None)
        body = _build_body(result, cfg)
        assert "(<#>)" in body

    def test_non_http_scheme_uses_hash_href(self) -> None:
        """A non-http/https scheme like ftp:// must produce href='#'."""
        cfg = Config()
        result = _make_full_result(index_details="ftp://tracker.example.com/")
        body = _build_body(result, cfg)
        assert "(<#>)" in body

    def test_urlparse_exception_falls_back_to_hash(self, mocker: MockerFixture) -> None:
        """When urlparse raises, _safe_url returns '#'."""
        mocker.patch("movarr.notifications.urllib.parse.urlparse", side_effect=Exception("parse error"))
        cfg = Config()
        result = _make_full_result(index_details="http://example.com/details")
        body = _build_body(result, cfg)
        assert "(<#>)" in body


class TestDispatchApprise:
    """Unit tests for the _dispatch_apprise helper."""

    def test_returns_false_for_empty_urls(self) -> None:
        """Guard: empty URL list returns False without touching Apprise."""
        assert _dispatch_apprise("subject", "body", []) is False

    def test_returns_false_for_empty_subject(self) -> None:
        """Guard: empty subject returns False without touching Apprise."""
        assert _dispatch_apprise("", "body", ["apprise://test"]) is False

    def test_returns_false_for_empty_body(self) -> None:
        """Guard: empty body returns False without touching Apprise."""
        assert _dispatch_apprise("subject", "", ["apprise://test"]) is False

    def test_notify_uses_markdown_body_format(self) -> None:
        """_dispatch_apprise sends with NotifyFormat.MARKDOWN by default."""
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            _dispatch_apprise("subj", "body", ["ntfy://t"])
        mock_ap.notify.assert_called_once()
        _, kwargs = mock_ap.notify.call_args
        from apprise import NotifyFormat

        assert kwargs["body_format"] == NotifyFormat.MARKDOWN

    def test_notify_respects_explicit_body_format(self) -> None:
        """_dispatch_apprise passes through an explicit body_format kwarg."""
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            from apprise import NotifyFormat

            _dispatch_apprise("subj", "body", ["ntfy://t"], body_format=NotifyFormat.TEXT)
        mock_ap.notify.assert_called_once()
        _, kwargs = mock_ap.notify.call_args
        from apprise import NotifyFormat

        assert kwargs["body_format"] == NotifyFormat.TEXT


class TestPosterUrlHelpers:
    """Tests for poster URL resolution/strip helpers."""

    def test_strip_removes_sx500(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SX500.jpg"
        assert _strip_poster_resolution(url) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_strip_removes_sy1080(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SY1080.jpg"
        assert _strip_poster_resolution(url) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_strip_removes_sw300(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SW300.jpg"
        assert _strip_poster_resolution(url) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_strip_leaves_unmodified_url_without_resolution(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        assert _strip_poster_resolution(url) == url

    def test_strip_handles_url_without_v1_suffix(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B.jpg"
        assert _strip_poster_resolution(url) == url

    def test_width_500_inserts_sx500(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        assert _poster_url_with_width(url, 500) == "https://m.media-amazon.com/images/M/MV5B._V1_SX500.jpg"

    def test_width_0_strips_existing_resolution(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SX1080.jpg"
        assert _poster_url_with_width(url, 0) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_width_negative_treated_as_zero(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SX500.jpg"
        assert _poster_url_with_width(url, -1) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_width_500_on_already_resized_url_replaces_resolution(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SX1080.jpg"
        assert _poster_url_with_width(url, 500) == "https://m.media-amazon.com/images/M/MV5B._V1_SX500.jpg"

    def test_width_positive_without_v1_returns_original(self) -> None:
        """URL with resolution modifier but no _V1_ returns original unchanged."""
        url = "https://m.media-amazon.com/images/M/MV5B_SX500.jpg"
        assert _poster_url_with_width(url, 500) == url
