"""HTTP client with exponential backoff for movarr."""

from __future__ import annotations

import contextlib
import socket
from typing import Any

import backoff
import requests

__all__ = ["HttpClient", "HttpError"]

_USER_AGENT_CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_3) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/59.0.3071.115 Safari/537.36"
)

_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 299
_RESPONSE_PREVIEW_BYTES = 200


class HttpError(Exception):
    """Raised when an HTTP request returns a non-2xx status code."""


class HttpClient:
    """Reusable HTTP client with configurable timeouts and retry logic.

    Args:
        connect_timeout: Seconds to wait for a TCP connection.
        read_timeout: Seconds to wait between received bytes.
        user_agent: User-Agent header value.
        verify_ssl: Whether to verify TLS certificates (default True).
    """

    def __init__(
        self,
        connect_timeout: float = 30.0,
        read_timeout: float = 30.0,
        user_agent: str = _USER_AGENT_CHROME,
        verify_ssl: bool = True,
    ) -> None:
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._user_agent = user_agent
        self._verify_ssl = verify_ssl
        self._session = requests.Session()
        self._session.headers.update({"Accept-Encoding": "gzip", "User-Agent": user_agent})

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._session.close()

    @backoff.on_exception(
        backoff.expo,
        (
            socket.timeout,
            requests.exceptions.Timeout,
            # Note: requests.exceptions.HTTPError is NOT listed here because it is
            # never raised by _request (we raise HttpError instead). Only
            # network-level transient errors are retried.
            requests.exceptions.ConnectionError,
        ),
        max_tries=10,
    )
    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
        auth: tuple[str, str] | None = None,
        read_timeout: float | None = None,
    ) -> requests.Response:
        """Execute an HTTP request, retrying on transient errors.

        Args:
            method: HTTP method (``"get"``, ``"post"``, etc.).
            url: Target URL.
            headers: Extra request headers.
            data: Request body payload.
            auth: Optional ``(username, password)`` tuple.

        Raises:
            HttpError: On non-2xx responses.
            requests.exceptions.RequestException: On unrecoverable errors.
        """
        rt = read_timeout if read_timeout is not None else self._read_timeout
        response = self._session.request(
            method=method,
            url=url,
            headers=headers,
            auth=auth,
            timeout=(self._connect_timeout, rt),
            allow_redirects=True,
            verify=self._verify_ssl,
            data=data,
        )

        if not _HTTP_OK_MIN <= response.status_code <= _HTTP_OK_MAX:
            raise HttpError(
                f"HTTP {response.status_code} for {url!r}: {response.content[:_RESPONSE_PREVIEW_BYTES].decode('utf-8', errors='replace')}"
            )

        return response

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        read_timeout: float | None = None,
    ) -> requests.Response:
        """Perform a GET request.

        Args:
            url: Target URL.
            headers: Extra request headers.
            auth: Optional ``(username, password)`` tuple.
            read_timeout: Override the default read timeout for this call.
                Passed directly to ``_request`` rather than mutating
                instance state to avoid a thread-safety race condition.
        """
        return self._request("get", url, headers=headers, auth=auth, read_timeout=read_timeout)
