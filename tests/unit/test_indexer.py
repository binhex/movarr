"""Unit tests for movarr.indexer — IndexProxyProtocol and client factory."""

from __future__ import annotations

import pytest

from movarr.config import Config
from movarr.indexer import get_indexer_client
from movarr.jackett import JackettClient
from movarr.prowlarr import ProwlarrClient


class TestGetIndexerClient:
    """Tests for get_indexer_client factory."""

    def test_returns_jackett_client_when_selected_is_jackett(self) -> None:
        """Returns a JackettClient when index_proxy.selected == 'jackett'."""
        cfg = Config()
        cfg.index_proxy.selected = "jackett"
        client = get_indexer_client(cfg)
        assert isinstance(client, JackettClient)

    def test_returns_prowlarr_client_when_selected_is_prowlarr(self) -> None:
        """Returns a ProwlarrClient when index_proxy.selected == 'prowlarr'."""
        cfg = Config()
        cfg.index_proxy.selected = "prowlarr"
        client = get_indexer_client(cfg)
        assert isinstance(client, ProwlarrClient)

    def test_raises_value_error_for_unknown_value(self) -> None:
        """Raises ValueError for an unrecognised index proxy name."""
        cfg = Config()
        cfg.index_proxy.selected = "unknown_proxy"
        with pytest.raises(ValueError, match="Unknown index proxy"):
            get_indexer_client(cfg)
