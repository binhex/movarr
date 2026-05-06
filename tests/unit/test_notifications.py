"""Unit tests for movarr.notifications — notification helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from movarr.config import Config
from movarr.notifications import (
    _build_body,
    _build_subject,
    _format_result_details,
    send_queued_notification,
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


# _build_subject


class TestBuildSubject:
    """Tests for the _build_subject pure helper."""

    def test_with_year_and_rating(self) -> None:
        """Subject includes year and rating when both are present."""
        result: ResultDict = {"imdb_title": "Inception", "imdb_year": 2010, "imdb_rating": 8.8}
        assert _build_subject(result) == "movarr: Inception (2010) — IMDb 8.8 — Queued"

    def test_without_year_omits_parentheses(self) -> None:
        """Subject omits year parentheses when imdb_year is absent."""
        result: ResultDict = {"imdb_title": "Unknown Film", "imdb_year": None, "imdb_rating": 7.5}
        subject = _build_subject(result)
        assert "()" not in subject
        assert "Unknown Film" in subject

    def test_without_rating_uses_question_mark(self) -> None:
        """Subject shows '?' for rating when imdb_rating is absent."""
        result: ResultDict = {"imdb_title": "Film", "imdb_year": 2020, "imdb_rating": None}
        assert "IMDb ?" in _build_subject(result)

    def test_missing_title_defaults_to_unknown(self) -> None:
        """Subject defaults to 'Unknown' when imdb_title is missing."""
        assert "Unknown" in _build_subject({})


# _build_body


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

    def test_missing_imdb_id_uses_hash_fallback(self) -> None:
        """Body uses '#' as href when imdb_id is absent."""
        cfg = Config()
        result = _make_full_result()
        result.pop("imdb_id", None)
        body = _build_body(result, cfg)
        assert 'href="#"' in body


# _format_result_details


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


# NotificationConfig defaults (via Config)


class TestNotificationConfigDefaults:
    """NotificationConfig must default to empty URL list."""

    def test_default_apprise_urls_is_empty_list(self) -> None:
        """Default config has no apprise URLs configured."""
        cfg = Config()
        assert cfg.notification.apprise_urls == []

    def test_apprise_urls_accepts_list_of_strings(self) -> None:
        """apprise_urls accepts a list of service URL strings."""
        from movarr.config import NotificationConfig

        nc = NotificationConfig(apprise_urls=["ntfy://alerts", "mailtos://user:pass@smtp.host:587"])
        assert len(nc.apprise_urls) == 2


class TestSendIndexProxyAlert:
    """Tests for send_index_proxy_alert()."""

    def _make_config(self, urls: list[str]) -> Config:
        from movarr.config import NotificationConfig
        config = Config()
        return config.model_copy(update={"notification": NotificationConfig(apprise_urls=urls)})

    def test_returns_false_when_no_urls_configured(self) -> None:
        """Returns False immediately when apprise_urls is empty."""
        from movarr.notifications import send_index_proxy_alert
        config = self._make_config([])
        result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=3.0, config=config)
        assert result is False

    def test_calls_apprise_when_urls_configured(self) -> None:
        """Calls apprise.Apprise.notify() when URLs are present."""
        from unittest.mock import MagicMock, patch

        from movarr.notifications import send_index_proxy_alert
        config = self._make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Jackett", hours_elapsed=2.5, config=config)
        assert result is True
        mock_ap.notify.assert_called_once()

    def test_subject_contains_proxy_name_and_hours(self) -> None:
        """Notification subject includes the proxy name and elapsed hours."""
        from unittest.mock import MagicMock, patch

        from movarr.notifications import send_index_proxy_alert
        config = self._make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=4.0, config=config)
        _, kwargs = mock_ap.notify.call_args
        assert "Prowlarr" in kwargs["title"]
        assert "4" in kwargs["title"]

    def test_returns_false_when_apprise_returns_false(self) -> None:
        """Returns False when apprise.notify() returns False."""
        from unittest.mock import MagicMock, patch

        from movarr.notifications import send_index_proxy_alert
        config = self._make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = False
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=1.0, config=config)
        assert result is False

    def test_returns_false_when_apprise_raises(self) -> None:
        """Returns False (does not propagate) when apprise.notify() raises."""
        from unittest.mock import MagicMock, patch

        from movarr.notifications import send_index_proxy_alert
        config = self._make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("boom")
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=1.0, config=config)
        assert result is False


class TestSendServiceAlert:
    """Tests for the generic send_service_alert() function."""

    def _make_config(self, urls: list[str]) -> Config:
        from movarr.config import NotificationConfig
        config = Config()
        return config.model_copy(update={"notification": NotificationConfig(apprise_urls=urls)})

    def test_returns_false_when_no_urls_configured(self) -> None:
        """Returns False immediately when apprise_urls is empty."""
        from movarr.notifications import send_service_alert
        config = self._make_config([])
        assert send_service_alert(service_name="qBittorrent", hours_elapsed=3.0, config=config) is False

    def test_calls_apprise_when_urls_configured(self) -> None:
        """Calls apprise.Apprise.notify() and returns True on success."""
        from unittest.mock import MagicMock, patch

        from movarr.notifications import send_service_alert
        config = self._make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=2.0, config=config) is True
        mock_ap.notify.assert_called_once()

    def test_subject_contains_service_name_and_hours(self) -> None:
        """Subject line includes service name and elapsed hours."""
        from unittest.mock import MagicMock, patch

        from movarr.notifications import send_service_alert
        config = self._make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            send_service_alert(service_name="qBittorrent", hours_elapsed=4.0, config=config)
        _, kwargs = mock_ap.notify.call_args
        assert "qBittorrent" in kwargs["title"]
        assert "4" in kwargs["title"]

    def test_returns_false_when_apprise_returns_false(self) -> None:
        """Returns False when apprise.notify() returns False."""
        from unittest.mock import MagicMock, patch

        from movarr.notifications import send_service_alert
        config = self._make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = False
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=1.0, config=config) is False

    def test_returns_false_when_apprise_raises(self) -> None:
        """Returns False and does not propagate when apprise raises."""
        from unittest.mock import MagicMock, patch

        from movarr.notifications import send_service_alert
        config = self._make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("boom")
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=1.0, config=config) is False


class TestSendIndexProxyAlertDelegates:
    """send_index_proxy_alert() must delegate to send_service_alert()."""

    def test_delegates_to_send_service_alert(self) -> None:
        """send_index_proxy_alert() calls send_service_alert() with correct args."""
        from unittest.mock import patch

        from movarr.notifications import send_index_proxy_alert
        config = Config()
        with patch("movarr.notifications.send_service_alert", return_value=True) as mock_generic:
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=3.0, config=config)
        mock_generic.assert_called_once_with(
            service_name="Prowlarr", hours_elapsed=3.0, config=config
        )
        assert result is True
