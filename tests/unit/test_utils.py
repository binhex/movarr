"""Unit tests for movarr.utils."""

from __future__ import annotations

from movarr.utils import bytes_to_mb, redact_secrets


class TestBytesToMb:
    """Tests for bytes_to_mb shared helper."""

    def test_converts_bytes_to_mb(self) -> None:
        assert bytes_to_mb(8_589_934_592) == "8589"

    def test_truncates_remainder(self) -> None:
        assert bytes_to_mb(1_000_001) == "1"

    def test_zero_returns_zero(self) -> None:
        assert bytes_to_mb(0) == "0"

    def test_float_input_works(self) -> None:
        assert bytes_to_mb(1_000_000.0) == "1"
        assert bytes_to_mb(8_589_934_592.0) == "8589"

    def test_invalid_string_returns_zero(self) -> None:
        assert bytes_to_mb("not-a-number") == "0"

    def test_empty_string_returns_zero(self) -> None:
        assert bytes_to_mb("") == "0"

    def test_none_returns_zero(self) -> None:
        assert bytes_to_mb(None) == "0"

    def test_infinity_returns_zero(self) -> None:
        assert bytes_to_mb(float("inf")) == "0"
        assert bytes_to_mb("inf") == "0"

    def test_large_integer_handled_correctly(self) -> None:
        """Very large integers are handled without overflow (Python arbitrary precision).

        The old implementation used float() conversion which raised OverflowError
        for numbers > 2^53. The new implementation uses int() directly, so large
        values produce a large (but correct) result rather than returning '0'.
        """
        # 10**18 bytes = 10**12 MB = 1 000 000 000 000
        assert bytes_to_mb(10**18) == str(10**18 // 1_000_000)


class TestRedactSecrets:
    """Tests for redact_secrets - credential scrubbing for logs and stored details."""

    def test_redacts_tmdb_api_key(self) -> None:
        msg = "HTTP 401 for 'https://api.themoviedb.org/3/movie/603?api_key=abc123def': denied"
        assert "abc123def" not in redact_secrets(msg)
        assert "api_key=REDACTED" in redact_secrets(msg)

    def test_redacts_jackett_apikey(self) -> None:
        url = "http://jackett:9117/api/v2.0/indexers/all/results/torznab/api?apikey=s3cr3t&t=search"
        assert "s3cr3t" not in redact_secrets(url)

    def test_redacts_omdb_apikey(self) -> None:
        url = "https://www.omdbapi.com/?apikey=deadbeef&t=Alien&y=1979"
        assert "deadbeef" not in redact_secrets(url)

    def test_redacts_token_and_password_parameters(self) -> None:
        msg = "https://host/path?token=abc&password=hunter2&safe=1"
        redacted = redact_secrets(msg)
        assert "abc" not in redacted.replace("REDACTED", "")
        assert "hunter2" not in redacted
        assert "safe=1" in redacted

    def test_leaves_ordinary_text_unchanged(self) -> None:
        assert redact_secrets("Searching Jackett indexer 'all'.") == "Searching Jackett indexer 'all'."

    def test_accepts_non_string_input(self) -> None:
        assert "REDACTED" in redact_secrets(ValueError("?apikey=zzz"))


class TestNegativeSize:
    """Negative sizes are invalid input and yield "0"."""

    def test_negative_returns_zero(self) -> None:
        assert bytes_to_mb(-3) == "0"
        assert bytes_to_mb("-3000000") == "0"
