"""Unit tests for movarr.notifications — notification helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from movarr.config import Config, NotificationConfig
from movarr.notifications import (
    _build_links_section,
    _build_markdown_body,
    _build_subject,
    _build_text_body,
    _dispatch_apprise,
    _extract_body_fields,
    _format_result_details,
    _format_result_details_text,
    _is_markdown_service,
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


# _make_fields helper


def _make_fields(result: ResultDict, config: Config | None = None) -> dict[str, str]:
    """Call _extract_body_fields and return the formatted fields dict."""
    if config is None:
        config = Config()
    return _extract_body_fields(result, config)


# _build_markdown_body / _build_text_body


class TestBuildMarkdownBody:
    """Tests for the _build_markdown_body pure helper."""

    def test_full_result_contains_key_fields(self) -> None:
        """Body contains status, score, actors and directors from a full result."""
        fields = _make_fields(_make_full_result())
        body = _build_markdown_body(fields)
        assert "Status:" in body
        assert "Score:" in body
        assert "Leonardo DiCaprio" in body
        assert "Christopher Nolan" in body
        assert "Action" in body

    def test_body_opens_with_status_and_score_not_title(self) -> None:
        """Body starts with Status and Score lines, not a redundant Title line."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = True
        fields = _make_fields(_make_full_result(), cfg)
        body = _build_markdown_body(fields)
        # Should start with Status and Score, not repeat the movie title
        assert "**Status:** Paused" in body
        assert "**Score:** 8.8 from 2000000 users" in body
        assert "**Title:**" not in body

    def test_no_html_formatting_tags_in_body(self) -> None:
        """Body contains no HTML formatting tags."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = False
        fields = _make_fields(_make_full_result(), cfg)
        body = _build_markdown_body(fields)
        assert "<p>" not in body
        assert "<br>" not in body
        assert "<strong>" not in body
        # Each field is on its own line
        assert body.startswith("**Status:**")

    def test_empty_cast_list_shows_dash(self) -> None:
        """Body shows '—' for actors when cast list is empty."""
        result = _make_full_result(imdb_credits_cast_list=[])
        body = _build_markdown_body(_make_fields(result))
        assert "—" in body

    def test_empty_director_list_shows_dash(self) -> None:
        """Body shows '—' for directors when director list is empty."""
        result = _make_full_result(imdb_credits_director_list=[])
        body = _build_markdown_body(_make_fields(result))
        assert "—" in body

    def test_none_cast_shows_dash(self) -> None:
        """Body shows '—' for actors when cast list is None (null-guard)."""
        result = _make_full_result()
        result["imdb_credits_cast_list"] = None
        body = _build_markdown_body(_make_fields(result))
        assert "—" in body

    def test_add_paused_true_shows_paused(self) -> None:
        """Queue status is 'Paused' when add_paused=True."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = True
        fields = _make_fields(_make_full_result(), cfg)
        assert "Paused" in _build_markdown_body(fields)

    def test_add_paused_false_shows_started(self) -> None:
        """Queue status is 'Started' when add_paused=False."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = False
        fields = _make_fields(_make_full_result(), cfg)
        assert "Started" in _build_markdown_body(fields)

    def test_imdb_url_in_links_section(self) -> None:
        """Body contains IMDb URL in the Links section built from imdb_id."""
        fields = _make_fields(_make_full_result())
        body = _build_markdown_body(fields)
        assert "tt1375666" in body
        assert "[IMDb](https://imdb.com/title/tt1375666)" in body

    def test_no_links_section_when_both_absent(self) -> None:
        """Links section omitted when both imdb_id and index_details are absent."""
        result = _make_full_result()
        result.pop("imdb_id", None)
        result.pop("index_details", None)
        body = _build_markdown_body(_make_fields(result))
        assert "**Links:**" not in body
        assert "(\u003c#\u003e)" not in body  # no hash fallback leak

    def test_imdb_url_in_links_when_id_set(self) -> None:
        """Body contains IMDb URL in Links section when imdb_id is present."""
        result = _make_full_result(imdb_id="tt1375666")
        body = _build_markdown_body(_make_fields(result))
        assert "[IMDb](https://imdb.com/title/tt1375666)" in body
        assert "**IMDb:**" not in body  # not as a bare URL line

    def test_imdb_url_absent_when_id_missing(self) -> None:
        """Body omits the IMDb URL line when imdb_id is missing."""
        result = _make_full_result()
        result.pop("imdb_id", None)
        body = _build_markdown_body(_make_fields(result))
        assert "https://imdb.com/title/" not in body

    def test_poster_img_never_in_body(self) -> None:
        """Body never contains an inline poster <img> tag (poster removed from notifications)."""
        cfg = Config()
        cfg.notification.poster_embed_enabled = True
        result = _make_full_result(imdb_poster_url="https://m.media-amazon.com/images/M/MV5B._V1_.jpg")
        body = _build_markdown_body(_make_fields(result, cfg))
        assert '<img src="' not in body

    def test_body_uses_markdown_bold_labels(self) -> None:
        """Body uses **bold** Markdown syntax, not <strong> HTML tags."""
        fields = _make_fields(_make_full_result())
        body = _build_markdown_body(fields)
        assert "<strong>" not in body
        assert "**Status:**" in body
        assert "**Score:**" in body
        assert "**Links:**" in body
        assert "**Plot:**" in body
        assert "**Actors:**" in body
        assert "**Directors:**" in body
        assert "**Genres:**" in body
        assert "**Release:**" in body
        assert "**Size:**" in body

    def test_body_has_no_bare_imdb_label(self) -> None:
        """Body does NOT have a bare **IMDb:** line (IMDb goes into Links section)."""
        fields = _make_fields(_make_full_result())
        body = _build_markdown_body(fields)
        assert "**IMDb:**" not in body

    def test_result_details_is_markdown_list(self) -> None:
        """Result details rendered as italic summary with bullet items."""
        fields = _make_fields(_make_full_result())
        body = _build_markdown_body(fields)
        assert "_1 items_" in body
        assert "- Quality: Rating: 8.8" in body

    def test_release_is_plain_text_not_a_link(self) -> None:
        """Release title is plain text (not a markdown link)."""
        fields = _make_fields(
            _make_full_result(
                index_title="Inception 2010 1080p BluRay",
                index_details="http://example.com/details",
            ),
        )
        body = _build_markdown_body(fields)
        # Release title is plain text
        assert "**Release:** Inception 2010 1080p BluRay" in body
        # Torrent URL is NOT in the body (proxy URLs are not externally useful)
        assert "(\u003chttp://example.com/details\u003e)" not in body
        assert "[Inception 2010 1080p BluRay](" not in body

    def test_links_section_is_single_imdb_link(self) -> None:
        """Links section contains only the IMDb link (no torrent URL)."""
        fields = _make_fields(_make_full_result())
        body = _build_markdown_body(fields)
        assert "**Links:**" in body
        assert "[IMDb](https://imdb.com/title/tt1375666)" in body
        assert " | " not in body
        assert "[Inception" not in body  # no torrent link


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

    def test_no_html_entities_in_output(self) -> None:
        """HTML entities like &#x27; must NOT appear in Markdown output.

        The body format is Markdown, not HTML.  html.escape() produces
        entities such as ``&#x27;`` for apostrophes, which render
        literally on ntfy and other plain-text consumers.  Only
        Markdown-compatible escaping should be used.
        """
        details = [
            "Passed: Release group 'byndr' is not in reject list.",
            "Passed: Found via IMDbPie for 'Obsession 2025'.",
        ]
        result = _format_result_details(details)
        # HTML entities must never appear
        assert "&#x27;" not in result, "html.escape() entity leaked into Markdown output"
        # The actual apostrophes must be present
        assert "'byndr'" in result
        assert "'Obsession 2025'" in result


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
        mock_instance.add.assert_called_once_with("ntfy://topic?format=markdown")
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
        mock_instance.add.assert_any_call("ntfy://topic?format=markdown")
        mock_instance.add.assert_any_call("discord://id/token")

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


# _safe_url edge-cases — torrent URL intentionally excluded from links section


class TestSafeUrlEdgeCases:
    """_safe_url edge-cases (torrent URLs excluded from links by design)."""

    def test_missing_index_details_does_not_affect_links(self) -> None:
        """Missing index_details does not prevent IMDb link from appearing."""
        cfg = Config()
        result = _make_full_result()
        result.pop("index_details", None)
        fields = _make_fields(result, cfg)
        links = _build_links_section(fields, use_markdown=True)
        assert "[IMDb]" in links

    def test_non_http_scheme_does_not_affect_links(self) -> None:
        """Non-http index_details does not prevent IMDb link."""
        cfg = Config()
        result = _make_full_result(index_details="ftp://tracker.example.com/")
        fields = _make_fields(result, cfg)
        links = _build_links_section(fields, use_markdown=True)
        assert "[IMDb]" in links

    def test_urlparse_exception_does_not_affect_links(self, mocker: MockerFixture) -> None:
        """urlparse exception does not prevent IMDb link."""
        mocker.patch("movarr.notifications.urllib.parse.urlparse", side_effect=Exception("parse error"))
        cfg = Config()
        result = _make_full_result(index_details="http://example.com/details")
        fields = _make_fields(result, cfg)
        links = _build_links_section(fields, use_markdown=True)
        assert "[IMDb]" in links


class TestEnsureNtfyMarkdown:
    """Tests for _ensure_ntfy_markdown URL transformation."""

    def test_ntfy_bare_appends_format_markdown(self) -> None:
        """Bare ntfy:// URL gets ?format=markdown appended."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfy://topic")
        assert result == "ntfy://topic?format=markdown"

    def test_ntfys_bare_appends_format_markdown(self) -> None:
        """Secure ntfys:// URL gets ?format=markdown appended."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfys://topic")
        assert result == "ntfys://topic?format=markdown"

    def test_ntfy_with_existing_params_appends_correctly(self) -> None:
        """URL with existing query params uses & separator."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfy://topic?priority=5")
        assert result == "ntfy://topic?priority=5&format=markdown"

    def test_ntfy_already_has_format_markdown(self) -> None:
        """URL that already has format=markdown is left unchanged."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfy://topic?format=markdown")
        assert result == "ntfy://topic?format=markdown"

    def test_ntfy_with_multiple_params_and_format(self) -> None:
        """URL with format=markdown among other params is not modified."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfy://topic?priority=5&format=markdown")
        assert result == "ntfy://topic?priority=5&format=markdown"

    def test_non_ntfy_url_is_unchanged(self) -> None:
        """Non-ntfy URLs are returned verbatim."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("discord://webhook/token")
        assert result == "discord://webhook/token"

    def test_empty_url_returns_empty(self) -> None:
        """Empty string returns empty string."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("")
        assert result == ""


class TestEnsureNtfyMarkdown:
    """Tests for _ensure_ntfy_markdown URL transformation."""

    def test_ntfy_bare_appends_format_markdown(self) -> None:
        """Bare ntfy:// URL gets ?format=markdown appended."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfy://topic")
        assert result == "ntfy://topic?format=markdown"

    def test_ntfys_bare_appends_format_markdown(self) -> None:
        """Secure ntfys:// URL gets ?format=markdown appended."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfys://topic")
        assert result == "ntfys://topic?format=markdown"

    def test_ntfy_with_existing_params_appends_correctly(self) -> None:
        """URL with existing query params uses & separator."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfy://topic?priority=5")
        assert result == "ntfy://topic?priority=5&format=markdown"

    def test_ntfy_already_has_format_markdown(self) -> None:
        """URL that already has format=markdown is left unchanged."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfy://topic?format=markdown")
        assert result == "ntfy://topic?format=markdown"

    def test_ntfy_with_multiple_params_and_format(self) -> None:
        """URL with format=markdown among other params is not modified."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("ntfy://topic?priority=5&format=markdown")
        assert result == "ntfy://topic?priority=5&format=markdown"

    def test_non_ntfy_url_is_unchanged(self) -> None:
        """Non-ntfy URLs are returned verbatim."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("discord://webhook/token")
        assert result == "discord://webhook/token"

    def test_empty_url_returns_empty(self) -> None:
        """Empty string returns empty string."""
        from movarr.notifications import _ensure_ntfy_markdown

        result = _ensure_ntfy_markdown("")
        assert result == ""


class TestDispatchApprise:
    """Unit tests for the _dispatch_apprise helper."""

    def test_returns_false_for_empty_urls(self) -> None:
        """Guard: empty URL list returns False without touching Apprise."""
        assert _dispatch_apprise("subject", [], body_markdown="body", body_text="body") is False

    def test_returns_false_for_empty_subject(self) -> None:
        """Guard: empty subject returns False without touching Apprise."""
        assert _dispatch_apprise("", ["apprise://test"], body_markdown="body", body_text="body") is False

    def test_returns_false_for_no_bodies(self) -> None:
        """Guard: both bodies None returns False."""
        assert _dispatch_apprise("subject", ["apprise://test"]) is False

    def test_markdown_urls_receive_markdown_body(self) -> None:
        """Markdown-capable URLs receive MARKDOWN format."""
        from apprise import NotifyFormat

        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            _dispatch_apprise("subject", ["ntfy://t"], body_markdown="**bold**", body_text="plain")
        mock_ap.notify.assert_called_once_with(
            title="subject", body="**bold**", body_format=NotifyFormat.MARKDOWN
        )

    def test_text_urls_receive_text_body(self) -> None:
        """Plain-text-only URLs receive TEXT format."""
        from apprise import NotifyFormat

        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            _dispatch_apprise("subject", ["json://localhost"], body_markdown="**bold**", body_text="plain")
        mock_ap.notify.assert_called_once_with(
            title="subject", body="plain", body_format=NotifyFormat.TEXT
        )

    def test_sends_to_both_groups_when_mixed(self) -> None:
        """Mixed markdown+text URLs get two separate Apprise calls."""
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            _dispatch_apprise(
                "subject",
                ["ntfy://t", "json://localhost"],
                body_markdown="**bold**",
                body_text="plain",
            )
        assert mock_ap.notify.call_count == 2

    def test_markdown_exception_caught(self) -> None:
        """Exception from markdown Apprise is caught and does not propagate."""
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("md failed")
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            # Should not raise, and return False because nothing succeeded
            result = _dispatch_apprise(
                "subject",
                ["ntfy://t"],
                body_markdown="**bold**",
            )
        assert result is False

    def test_text_exception_caught(self) -> None:
        """Exception from text Apprise is caught and does not propagate."""
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("text failed")
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = _dispatch_apprise(
                "subject",
                ["json://localhost"],
                body_text="plain",
            )
        assert result is False

    def test_markdown_raises_text_succeeds(self) -> None:
        """When md raises but text succeeds, result is True."""
        md_ap = MagicMock()
        md_ap.notify.side_effect = RuntimeError("md failed")
        text_ap = MagicMock()
        text_ap.notify.return_value = True

        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            url_args = kwargs.get("urls") or args[0] if args else None
            if isinstance(url_args, list):
                if any("ntfy" in str(u) for u in url_args):
                    return md_ap
            return text_ap

        with patch("movarr.notifications.apprise.Apprise", side_effect=_side_effect):
            result = _dispatch_apprise(
                "subject",
                ["ntfy://t", "json://localhost"],
                body_markdown="**bold**",
                body_text="plain",
            )
        assert result is True

    def test_markdown_fails_text_succeeds(self) -> None:
        """When md notify returns False but text succeeds, result is True."""
        md_ap = MagicMock()
        md_ap.notify.return_value = False
        text_ap = MagicMock()
        text_ap.notify.return_value = True

        call_count = [0]

        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            call_count[0] += 1
            if call_count[0] == 1:
                return md_ap
            return text_ap

        with patch("movarr.notifications.apprise.Apprise", side_effect=_side_effect):
            result = _dispatch_apprise(
                "subject",
                ["ntfy://t", "json://localhost"],
                body_markdown="**bold**",
                body_text="plain",
            )
        assert result is True


class TestBuildLinksSection:
    """Tests for the _build_links_section pure helper."""

    def test_markdown_imdb_link(self) -> None:
        """Markdown mode returns IMDb link in [label](url) syntax."""
        result = _make_full_result(imdb_id="tt1375666")
        fields = _make_fields(result)
        assert _build_links_section(fields, use_markdown=True) == (
            "**Links:** [IMDb](https://imdb.com/title/tt1375666)"
        )

    def test_text_imdb_link(self) -> None:
        """Text mode returns bare IMDb URL."""
        result = _make_full_result(imdb_id="tt1375666")
        fields = _make_fields(result)
        assert _build_links_section(fields, use_markdown=False) == (
            "Links: https://imdb.com/title/tt1375666"
        )

    def test_missing_imdb_id_returns_empty(self) -> None:
        """Empty string when no imdb_id."""
        result = _make_full_result()
        result.pop("imdb_id", None)
        fields = _make_fields(result)
        assert _build_links_section(fields, use_markdown=True) == ""
        assert _build_links_section(fields, use_markdown=False) == ""

    def test_torrent_url_not_in_links_section(self) -> None:
        """Torrent/index details URL is NOT included in links section."""
        result = _make_full_result(
            imdb_id="tt1375666",
            index_details="http://example.com/torrent"
        )
        fields = _make_fields(result)
        result_str = _build_links_section(fields, use_markdown=True)
        assert "example.com" not in result_str
        assert "[IMDb]" in result_str
        assert " | " not in result_str


class TestBuildTextBody:
    """Tests for the _build_text_body pure helper."""

    def test_text_body_no_markdown_bold_labels(self) -> None:
        """Text body uses plain labels, not **bold**."""
        fields = _make_fields(_make_full_result())
        body = _build_text_body(fields)
        assert "**Status:**" not in body
        assert "Status:" in body
        assert "**Score:**" not in body
        assert "Score:" in body

    def test_text_body_links_as_bare_urls(self) -> None:
        """Text body links are bare URLs, not markdown links."""
        fields = _make_fields(_make_full_result())
        body = _build_text_body(fields)
        assert "[IMDb](" not in body
        assert "https://imdb.com/title/" in body
        assert "Links:" in body

    def test_text_body_result_details_no_markdown(self) -> None:
        """Text body result details have no markdown formatting."""
        fields = _make_fields(_make_full_result())
        body = _build_text_body(fields)
        assert "_1 items_" not in body
        assert "- Quality" in body or "1 items" in body
        assert "**Result Details:**" not in body
        assert "Result Details:" in body

    def test_text_body_status_and_fields_present(self) -> None:
        """All core fields are present in text body."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = True
        fields = _make_fields(_make_full_result(), cfg)
        body = _build_text_body(fields)
        assert "Status: Paused" in body
        assert "Score: 8.8" in body
        assert "Leonardo DiCaprio" in body
        assert "Christopher Nolan" in body


class TestIsMarkdownService:
    """Tests for the _is_markdown_service pure helper."""

    def test_ntfy_is_markdown(self) -> None:
        assert _is_markdown_service("ntfy://host/topic") is True

    def test_ntfys_is_markdown(self) -> None:
        assert _is_markdown_service("ntfys://host/topic") is True

    def test_discord_is_markdown(self) -> None:
        assert _is_markdown_service("discord://webhook_id/webhook_token") is True

    def test_slack_is_markdown(self) -> None:
        assert _is_markdown_service("slack://token/room") is True

    def test_tgram_is_markdown(self) -> None:
        assert _is_markdown_service("tgram://bot/chat") is True

    def test_tg_is_markdown(self) -> None:
        assert _is_markdown_service("tg://bot/chat") is True

    def test_matrix_is_markdown(self) -> None:
        assert _is_markdown_service("matrix://host/room") is True

    def test_matrixs_is_markdown(self) -> None:
        assert _is_markdown_service("matrixs://host/room") is True

    def test_json_is_not_markdown(self) -> None:
        assert _is_markdown_service("json://localhost") is False

    def test_mailto_is_not_markdown(self) -> None:
        assert _is_markdown_service("mailto://user:pass@gmail.com") is False

    def test_malformed_url(self) -> None:
        assert _is_markdown_service("not-a-url") is False

    def test_non_string_input_returns_false(self) -> None:
        """Non-string input (e.g. int) triggers the exception handler and returns False."""
        assert _is_markdown_service(None) is False  # type: ignore[arg-type]
        assert _is_markdown_service(123) is False  # type: ignore[arg-type]


class TestFormatResultDetailsText:
    """Tests for the _format_result_details_text pure helper."""

    def test_all_passed(self) -> None:
        details = ["Passed: a", "Passed: b", "Passed: c"]
        result = _format_result_details_text(details)
        assert "3 checks passed" in result
        assert "  - Passed: a" in result

    def test_mixed_pass_fail(self) -> None:
        details = ["Passed: a", "Failed: b", "Passed: c"]
        result = _format_result_details_text(details)
        assert "2 passed, 1 failed" in result

    def test_all_failed(self) -> None:
        details = ["Failed: a", "Failed: b"]
        result = _format_result_details_text(details)
        assert "0 passed, 2 failed" in result

    def test_empty_list(self) -> None:
        result = _format_result_details_text([])
        assert "0 checks" in result

    def test_no_markdown_formatting(self) -> None:
        """Output must not contain markdown formatting like _italic_ or - bullet."""
        details = ["Passed: check 'byndr'"]
        result = _format_result_details_text(details)
        assert "_" not in result
        # No leading dash bullet (uses two-space indent instead)


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
