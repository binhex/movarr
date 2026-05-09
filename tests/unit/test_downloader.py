"""Unit tests for movarr.downloader — HttpClient and HttpError."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import requests

from movarr.downloader import HttpClient, HttpError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


# Helpers


def _make_session_mock(mocker: MockerFixture, *, status_code: int = 200, content: bytes = b"ok") -> Any:
    """Patch requests.Session and return the mock response.

    The mock is set up so that ``requests.Session()`` in ``HttpClient.__init__``
    returns a mock whose ``.request()`` returns a response with the given
    status_code and content.  Callers **must** create ``HttpClient()`` *after*
    calling this helper so that the constructor receives the mock session.
    """
    mock_response = mocker.MagicMock(spec=requests.Response)
    mock_response.status_code = status_code
    mock_response.content = content

    mock_session = mocker.MagicMock()
    mock_session.request.return_value = mock_response

    mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
    return mock_response


# Constructor


class TestHttpClientInit:
    """HttpClient stores its configuration at construction time."""

    def test_default_connect_timeout(self) -> None:
        assert HttpClient()._connect_timeout == 30.0

    def test_default_read_timeout(self) -> None:
        assert HttpClient()._read_timeout == 30.0

    def test_default_verify_ssl_is_true(self) -> None:
        assert HttpClient()._verify_ssl is True

    def test_custom_connect_timeout(self) -> None:
        assert HttpClient(connect_timeout=5.0)._connect_timeout == 5.0

    def test_custom_read_timeout(self) -> None:
        assert HttpClient(read_timeout=10.0)._read_timeout == 10.0

    def test_ssl_verify_disabled(self) -> None:
        assert HttpClient(verify_ssl=False)._verify_ssl is False

    def test_custom_user_agent(self) -> None:
        assert HttpClient(user_agent="TestBot/1.0")._user_agent == "TestBot/1.0"

    def test_default_user_agent_is_set(self) -> None:
        assert HttpClient()._user_agent  # non-empty


# get()


class TestHttpClientGet:
    """HttpClient.get() calls _request with the correct arguments."""

    def test_calls_request_with_get_method(self, mocker: MockerFixture) -> None:
        """get() must delegate to _request(method='get', ...)."""
        client = HttpClient()
        mock_req = mocker.patch.object(client, "_request", return_value=mocker.MagicMock(spec=requests.Response))

        client.get("https://example.com")

        mock_req.assert_called_once_with("get", "https://example.com", headers=None, auth=None, read_timeout=None)

    def test_passes_headers(self, mocker: MockerFixture) -> None:
        """Extra headers should be forwarded to _request."""
        client = HttpClient()
        mock_req = mocker.patch.object(client, "_request", return_value=mocker.MagicMock(spec=requests.Response))

        client.get("https://example.com", headers={"X-Custom": "value"})

        mock_req.assert_called_once_with(
            "get", "https://example.com", headers={"X-Custom": "value"}, auth=None, read_timeout=None
        )

    def test_passes_auth(self, mocker: MockerFixture) -> None:
        """Auth credentials should be forwarded to _request."""
        client = HttpClient()
        mock_req = mocker.patch.object(client, "_request", return_value=mocker.MagicMock(spec=requests.Response))

        client.get("https://example.com", auth=("user", "secret"))

        mock_req.assert_called_once_with(
            "get", "https://example.com", headers=None, auth=("user", "secret"), read_timeout=None
        )

    def test_read_timeout_override_restores_original(self, mocker: MockerFixture) -> None:
        """After a read_timeout override, the original value on the instance is unchanged."""
        client = HttpClient(read_timeout=30.0)
        mock_req = mocker.patch.object(client, "_request", return_value=mocker.MagicMock(spec=requests.Response))

        client.get("https://example.com", read_timeout=5.0)

        # The instance attribute must not be mutated.
        assert client._read_timeout == 30.0
        mock_req.assert_called_once_with("get", "https://example.com", headers=None, auth=None, read_timeout=5.0)

    def test_read_timeout_override_applied_during_call(self, mocker: MockerFixture) -> None:
        """The overriding read_timeout is passed as a kwarg to _request."""
        client = HttpClient(read_timeout=30.0)
        captured: list[float] = []

        def _capture(*args: object, **kwargs: object) -> object:
            raw = kwargs.get("read_timeout", client._read_timeout)
            captured.append(raw if isinstance(raw, float) else 0.0)
            return mocker.MagicMock(spec=requests.Response)

        mocker.patch.object(client, "_request", side_effect=_capture)
        client.get("https://example.com", read_timeout=5.0)

        assert captured == [5.0]
        assert client._read_timeout == 30.0

    def test_read_timeout_restored_even_on_exception(self, mocker: MockerFixture) -> None:
        """read_timeout must be restored even when _request raises."""
        client = HttpClient(read_timeout=30.0)
        mocker.patch.object(client, "_request", side_effect=HttpError("boom"))

        with pytest.raises(HttpError):
            client.get("https://example.com", read_timeout=5.0)

        assert client._read_timeout == 30.0

    def test_no_timeout_override_calls_directly(self, mocker: MockerFixture) -> None:
        """Without read_timeout, _request is called without the override path."""
        client = HttpClient()
        mock_req = mocker.patch.object(client, "_request", return_value=mocker.MagicMock(spec=requests.Response))

        client.get("https://example.com")

        mock_req.assert_called_once()


# post()


# _request() — tested directly because its session/header wiring cannot be
# fully observed through the public API's return value alone.


class TestHttpClientRequest:
    """HttpClient._request() wraps requests.Session and enforces status codes.

    _request is tested directly here because the internal behaviour being
    verified (SSL verify flag, timeout tuple, header merging, auth assignment)
    is set up *inside* the method and cannot be observed through the value
    returned by get() or post().
    """

    def test_returns_response_on_200(self, mocker: MockerFixture) -> None:
        """A 200 response must be returned without raising."""
        mock_response = _make_session_mock(mocker, status_code=200)
        client = HttpClient()

        result = client._request("get", "https://example.com")

        assert result is mock_response

    def test_returns_response_on_201(self, mocker: MockerFixture) -> None:
        """Any 2xx status code (e.g. 201 Created) must be accepted."""
        mock_response = _make_session_mock(mocker, status_code=201)
        client = HttpClient()

        result = client._request("post", "https://example.com/create")

        assert result is mock_response

    def test_returns_response_on_299(self, mocker: MockerFixture) -> None:
        """The upper boundary of 2xx range (299) must also be accepted."""
        mock_response = _make_session_mock(mocker, status_code=299)
        client = HttpClient()

        result = client._request("get", "https://example.com")

        assert result is mock_response

    def test_raises_http_error_on_400(self, mocker: MockerFixture) -> None:
        """_request must raise HttpError for a 400 Bad Request response."""
        _make_session_mock(mocker, status_code=400, content=b"Bad Request")
        client = HttpClient()

        with pytest.raises(HttpError, match="HTTP 400"):
            client._request("get", "https://example.com/bad")

    def test_raises_http_error_on_401(self, mocker: MockerFixture) -> None:
        """_request must raise HttpError for a 401 Unauthorized response."""
        _make_session_mock(mocker, status_code=401, content=b"Unauthorized")
        client = HttpClient()

        with pytest.raises(HttpError):
            client._request("get", "https://example.com/protected")

    def test_raises_http_error_on_404(self, mocker: MockerFixture) -> None:
        """_request must raise HttpError for a 404 Not Found response."""
        _make_session_mock(mocker, status_code=404, content=b"Not Found")
        client = HttpClient()

        with pytest.raises(HttpError, match="HTTP 404"):
            client._request("get", "https://example.com/missing")

    def test_raises_http_error_on_500(self, mocker: MockerFixture) -> None:
        """_request must raise HttpError for a 500 Internal Server Error response."""
        _make_session_mock(mocker, status_code=500, content=b"Server Error")
        client = HttpClient()

        with pytest.raises(HttpError, match="HTTP 500"):
            client._request("get", "https://example.com/broken")

    def test_raises_http_error_on_503(self, mocker: MockerFixture) -> None:
        """_request must raise HttpError for a 503 Service Unavailable response."""
        _make_session_mock(mocker, status_code=503, content=b"Unavailable")
        client = HttpClient()

        with pytest.raises(HttpError, match="https://example.com/down"):
            client._request("get", "https://example.com/down")

    def test_error_message_contains_url(self, mocker: MockerFixture) -> None:
        """The HttpError message should reference the target URL."""
        _make_session_mock(mocker, status_code=404, content=b"Not Found")
        client = HttpClient()

        with pytest.raises(HttpError, match="https://example.com/target"):
            client._request("get", "https://example.com/target")

    def test_ssl_verify_false_forwarded(self, mocker: MockerFixture) -> None:
        """verify_ssl=False must be passed through to session.request()."""
        mock_session = mocker.MagicMock()
        mock_response = mocker.MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.content = b"ok"
        mock_session.request.return_value = mock_response
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient(verify_ssl=False)

        client._request("get", "https://example.com")

        _, call_kwargs = mock_session.request.call_args
        assert call_kwargs.get("verify") is False
    def test_ssl_verify_true_forwarded(self, mocker: MockerFixture) -> None:
        """verify_ssl=True (default) must be passed through to session.request()."""
        mock_session = mocker.MagicMock()
        mock_response = mocker.MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.content = b"ok"
        mock_session.request.return_value = mock_response
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient(verify_ssl=True)

        client._request("get", "https://example.com")

        _, call_kwargs = mock_session.request.call_args
        assert call_kwargs.get("verify") is True

    def test_timeout_tuple_passed(self, mocker: MockerFixture) -> None:
        """Timeout must be forwarded as a (connect, read) tuple."""
        mock_session = mocker.MagicMock()
        mock_response = mocker.MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.content = b"ok"
        mock_session.request.return_value = mock_response
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient(connect_timeout=5.0, read_timeout=15.0)

        client._request("get", "https://example.com")

        _, call_kwargs = mock_session.request.call_args
        assert call_kwargs.get("timeout") == (5.0, 15.0)

    def test_default_headers_always_sent(self, mocker: MockerFixture) -> None:
        """Accept-Encoding and User-Agent headers must always be set on the session."""
        mock_session = mocker.MagicMock()
        mock_response = mocker.MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.content = b"ok"
        mock_session.request.return_value = mock_response
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient(user_agent="TestBot/1.0")

        client._request("get", "https://example.com")

        headers_sent = mock_session.headers.update.call_args.args[0]
        assert headers_sent["User-Agent"] == "TestBot/1.0"
        assert headers_sent["Accept-Encoding"] == "gzip"

    def test_custom_headers_merged(self, mocker: MockerFixture) -> None:
        """Extra headers passed to _request must be forwarded to session.request()."""
        mock_session = mocker.MagicMock()
        mock_response = mocker.MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.content = b"ok"
        mock_session.request.return_value = mock_response
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient()

        client._request("get", "https://example.com", headers={"X-Token": "abc"})

        _, call_kwargs = mock_session.request.call_args
        assert call_kwargs.get("headers") == {"X-Token": "abc"}
        # Base headers are set on the session at construction, not per-request
        assert "User-Agent" in mock_session.headers.update.call_args.args[0]

    def test_auth_assigned_to_session(self, mocker: MockerFixture) -> None:
        """Auth tuple must be forwarded to session.request() when provided."""
        mock_session = mocker.MagicMock()
        mock_response = mocker.MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.content = b"ok"
        mock_session.request.return_value = mock_response
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient()

        client._request("get", "https://example.com", auth=("alice", "secret"))

        _, call_kwargs = mock_session.request.call_args
        assert call_kwargs.get("auth") == ("alice", "secret")

    def test_no_auth_does_not_set_session_auth(self, mocker: MockerFixture) -> None:
        """When auth is None, session.request() must receive auth=None."""
        mock_session = mocker.MagicMock()
        mock_response = mocker.MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.content = b"ok"
        mock_session.request.return_value = mock_response
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient()

        client._request("get", "https://example.com")

        _, call_kwargs = mock_session.request.call_args
        assert call_kwargs.get("auth") is None

    def test_connection_error_is_retried(self, mocker: MockerFixture) -> None:
        """ConnectionError is in the backoff retry tuple and should be retried."""
        mock_session = mocker.MagicMock()
        # First two calls raise ConnectionError, third succeeds
        mock_session.request.side_effect = [
            requests.exceptions.ConnectionError("conn reset"),
            requests.exceptions.ConnectionError("conn reset"),
            mocker.MagicMock(spec=requests.Response, status_code=200, content=b"ok"),
        ]
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient()

        result = client._request("get", "https://example.com")
        assert result.status_code == 200
        assert mock_session.request.call_count == 3

    def test_socket_timeout_is_retried(self, mocker: MockerFixture) -> None:
        """socket.timeout is in the backoff retry tuple and should be retried."""
        mock_session = mocker.MagicMock()
        mock_session.request.side_effect = [
            TimeoutError("timed out"),
            mocker.MagicMock(spec=requests.Response, status_code=200, content=b"ok"),
        ]
        mocker.patch("movarr.downloader.requests.Session", return_value=mock_session)
        client = HttpClient()

        result = client._request("get", "https://example.com")
        assert result.status_code == 200
        assert mock_session.request.call_count == 2
