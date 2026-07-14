from backend.gateway.errors import normalize_provider_error


def test_normalizes_rate_limit() -> None:
    error = normalize_provider_error(
        RuntimeError("Error code: 429 rate limit"),
        provider="openrouter",
    )

    assert error.code == "RATE_LIMIT"
    assert error.recoverable is True


def test_normalizes_credits_error() -> None:
    error = normalize_provider_error(
        RuntimeError("Error code: 402 insufficient credits"),
        provider="openrouter",
    )

    assert error.code == "INSUFFICIENT_CREDITS"
    assert error.recoverable is False
