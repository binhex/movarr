"""Index proxy protocol and client factory for movarr."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Generator

    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["IndexProxyProtocol", "get_indexer_client"]


@runtime_checkable
class IndexProxyProtocol(Protocol):
    """Protocol satisfied by any indexer proxy client (Jackett, Prowlarr, …)."""

    def is_reachable(self) -> bool: ...

    def search(
        self,
        index_site: str,
        criteria: str,
        category: str,
    ) -> Generator[ResultDict, None, None]: ...


def get_indexer_client(config: Config) -> IndexProxyProtocol:
    """Return the configured indexer proxy client.

    Args:
        config: Application configuration.

    Raises:
        ValueError: If ``config.index_proxy.selected`` is not a supported value.
    """
    selected = config.index_proxy.selected
    if selected == "jackett":
        from movarr.jackett import JackettClient  # noqa: PLC0415

        return JackettClient(config)
    if selected == "prowlarr":
        from movarr.prowlarr import ProwlarrClient  # noqa: PLC0415

        return ProwlarrClient(config)
    raise ValueError(f"Unknown index proxy '{selected}'. Supported values: 'jackett', 'prowlarr'.")
