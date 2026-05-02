"""Unit tests for movarr.notifications — email notification helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from movarr.config import Config, EmailConfig
from movarr.notifications import (
    _build_apprise_url,
    _build_body,
    _build_subject,
    _format_result_details,
    send_queued_notification,
)

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_full_result(**overrides: Any) -> dict[str, Any]:
    """Return a fully-populated result dict for notification testing."""
    base: dict[str, Any] = {
        "imdb_title": "Inception",
        "imdb_year": "2010",
        "imdb_rating": "8.8",
        "imdb_votes": "2000000",
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
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _build_subject
# ---------------------------------------------------------------------------


class TestBuildSubject:
    """Tests for the _build_subject pure helper."""

    def test_with_year_and_rating(self) -> None:
        """Subject includes year and rating when both are present."""
        result = {"imdb_title": "Inception", "imdb_year": "2010", "imdb_rating": "8.8"}
        assert _build_subject(result) == "movarr: Inception (2010) — IMDb 8.8 — Queued"

    def test_without_year_omits_parentheses(self) -> None:
        """Subject omits year parentheses when imdb_year is absent."""
        result = {"imdb_title": "Unknown Film", "imdb_year": None, "imdb_rating": "7.5"}
        subject = _build_subject(result)
        assert "()" not in subject
        assert "Unknown Film" in subject

    def test_without_rating_uses_question_mark(self) -> None:
        """Subject shows '?' for rating when imdb_rating is absent."""
        result = {"imdb_title": "Film", "imdb_year": "2020", "imdb_rating": None}
        assert "IMDb ?" in _build_subject(result)

    def test_missing_title_defaults_to_unknown(self) -> None:
        """Subject defaults to 'Unknown' when imdb_title is missing."""
        assert "Unknown" in _build_subject({})


# ---------------------------------------------------------------------------
# _build_body
# ---------------------------------------------------------------------------


class TestBuildBody:
    """Tests for the _build_body pure helper."""

    def test_full_result_contains_key_fields(self) -> None:
        """Body contains title, actors and directors from a full result."""
        cfg = Config()
        body = _build_body(_make_full_result(), cfg)
        assert "Inception" in body
        assert "Leonardo DiCaprio" in body
        assert "Christopher Nolan" in body
        assert "Action" in body

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
        result["imdb_credits_cast_list"] = None  # type: ignore[typeddict-item]
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

    def test_missing_imdb_id_uses_hash_fallback(self) -> None:
        """Body uses '#' as href when imdb_id is absent."""
        cfg = Config()
        result = _make_full_result()
        result.pop("imdb_id", None)
        body = _build_body(result, cfg)
        assert 'href="#"' in body


# ---------------------------------------------------------------------------
# _build_apprise_url
# ---------------------------------------------------------------------------


class TestBuildAppriseUrl:
    """Tests for the _build_apprise_url pure helper."""

    def test_tls_enabled_uses_mailtos(self) -> None:
        """TLS flag produces a mailtos:// scheme."""
        cfg = EmailConfig(host="smtp.example.com", port=587, enable_tls=True)
        assert _build_apprise_url(cfg).startswith("mailtos://")

    def test_ssl_enabled_uses_mailtos(self) -> None:
        """SSL flag produces a mailtos:// scheme."""
        cfg = EmailConfig(host="smtp.example.com", port=465, enable_tls=False, enable_ssl=True)
        assert _build_apprise_url(cfg).startswith("mailtos://")

    def test_no_tls_no_ssl_uses_mailto(self) -> None:
        """Without TLS/SSL the scheme is mailto://."""
        cfg = EmailConfig(host="smtp.example.com", port=587, enable_tls=False, enable_ssl=False)
        assert _build_apprise_url(cfg).startswith("mailto://")

    def test_user_and_password_included(self) -> None:
        """User and password are embedded in the URL."""
        cfg = EmailConfig(host="smtp.example.com", port=587, username="user", password="secret")
        url = _build_apprise_url(cfg)
        assert "user" in url
        assert "secret" in url

    def test_special_chars_in_password_are_encoded(self) -> None:
        """Special characters in password are percent-encoded."""
        cfg = EmailConfig(host="smtp.example.com", port=587, username="user", password="p@$$w0rd!")
        url = _build_apprise_url(cfg)
        # Raw special chars must not appear unencoded in the URL
        assert "p@$$w0rd!" not in url
        assert "%" in url

    def test_user_without_password_omits_colon(self) -> None:
        """When only username is set, no colon-separated password appears."""
        cfg = EmailConfig(host="smtp.example.com", port=587, username="user", password="")
        url = _build_apprise_url(cfg)
        assert "user@" in url
        assert ":@" not in url

    def test_host_and_port_in_url(self) -> None:
        """Host and port are correctly placed in the URL."""
        cfg = EmailConfig(host="mail.test.com", port=2525)
        assert "mail.test.com:2525" in _build_apprise_url(cfg)


# ---------------------------------------------------------------------------
# _format_result_details
# ---------------------------------------------------------------------------


class TestFormatResultDetails:
    """Tests for the _format_result_details pure helper."""

    def test_three_part_entry_renders_nested_list(self) -> None:
        """A 'main: sub: detail' entry produces a nested <ul>."""
        html = _format_result_details(["Check: Rating: 8.8"])
        assert "<ul>" in html
        assert "Check" in html
        assert "Rating" in html
        assert "8.8" in html
        assert "<ul><li>8.8</li></ul>" in html

    def test_non_three_part_entry_renders_flat_item(self) -> None:
        """An entry without two colons renders as a plain <li>."""
        html = _format_result_details(["Passed simple check"])
        assert "<li>Passed simple check</li>" in html

    def test_empty_list_produces_empty_ul(self) -> None:
        """Empty input produces an empty <ul> wrapper."""
        assert _format_result_details([]) == "<ul></ul>"

    def test_mixed_entries_both_rendered(self) -> None:
        """A mix of 3-part and simple entries are all rendered."""
        html = _format_result_details(["A: B: C", "simple"])
        assert "A" in html
        assert "simple" in html


# ---------------------------------------------------------------------------
# send_queued_notification
# ---------------------------------------------------------------------------


class TestSendQueuedNotification:
    """Tests for the public send_queued_notification function."""

    def test_disabled_returns_false_immediately(self) -> None:
        """Returns False without calling apprise when notifications are disabled."""
        cfg = Config()
        cfg.notification.email.enabled = False
        assert send_queued_notification(_make_full_result(), cfg) is False

    def test_enabled_notifies_and_returns_true(self, mocker: MockerFixture) -> None:
        """Returns True when apprise successfully delivers the notification."""
        cfg = Config()
        cfg.notification.email.enabled = True
        cfg.notification.email.host = "smtp.example.com"
        cfg.notification.email.port = 587

        mock_instance = mocker.MagicMock()
        mock_instance.notify.return_value = True
        mock_cls = mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

        result = send_queued_notification(_make_full_result(), cfg)

        assert result is True
        mock_cls.assert_called_once()
        mock_instance.add.assert_called_once()
        mock_instance.notify.assert_called_once()

    def test_apprise_returns_false_logs_warning_and_returns_false(self, mocker: MockerFixture) -> None:
        """Returns False when apprise.notify() returns False."""
        cfg = Config()
        cfg.notification.email.enabled = True
        cfg.notification.email.host = "smtp.example.com"

        mock_instance = mocker.MagicMock()
        mock_instance.notify.return_value = False
        mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

        assert send_queued_notification(_make_full_result(), cfg) is False

    def test_apprise_raises_exception_returns_false(self, mocker: MockerFixture) -> None:
        """Returns False when apprise.notify() raises an unexpected exception."""
        cfg = Config()
        cfg.notification.email.enabled = True
        cfg.notification.email.host = "smtp.example.com"

        mock_instance = mocker.MagicMock()
        mock_instance.notify.side_effect = RuntimeError("connection refused")
        mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

        assert send_queued_notification(_make_full_result(), cfg) is False
