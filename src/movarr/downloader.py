"""HTTP client with exponential backoff for movarr."""

from __future__ import annotations

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

    @backoff.on_exception(
        backoff.expo,
        (socket.timeout, requests.exceptions.Timeout, requests.exceptions.HTTPError),
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
        merged_headers = {"Accept-Encoding": "gzip", "User-Agent": self._user_agent}
        if headers:
            merged_headers.update(headers)

        with requests.Session() as session:
            session.headers.update(merged_headers)
            if auth:
                session.auth = auth

            response = session.request(
                method=method,
                url=url,
                timeout=(self._connect_timeout, self._read_timeout),
                allow_redirects=True,
                verify=self._verify_ssl,
                data=data,
            )

        if not 200 <= response.status_code <= 299:
            raise HttpError(f"HTTP {response.status_code} for {url!r}: {response.content[:200]}")

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
        """
        if read_timeout is not None:
            original = self._read_timeout
            self._read_timeout = read_timeout
            try:
                return self._request("get", url, headers=headers, auth=auth)
            finally:
                self._read_timeout = original
        return self._request("get", url, headers=headers, auth=auth)

    def post(
        self,
        url: str,
        *,
        data: Any = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> requests.Response:
        """Perform a POST request.

        Args:
            url: Target URL.
            data: Request body payload.
            headers: Extra request headers.
            auth: Optional ``(username, password)`` tuple.
        """
        return self._request("post", url, headers=headers, data=data, auth=auth)
