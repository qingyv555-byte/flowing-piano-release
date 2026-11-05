import logging
import io

import pytest

from ai_piano_studio.logging_config import configure_logging
from ai_piano_studio.music_agent.security import (
    SecretScrubber,
    SecretScrubbingFilter,
    validate_provider_endpoint,
    validate_redirect_target,
)


def test_secret_filter_scrubs_exception_traceback_and_cached_exception_text():
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(SecretScrubbingFilter())
    logger = logging.Logger("isolated-secret-audit")
    logger.addHandler(handler)
    try:
        raise ValueError("provider rejected api_key=fake-audit-key")
    except ValueError:
        logger.exception("Request failed")
    text = output.getvalue()
    assert "fake-audit-key" not in text
    assert "ValueError" in text and "[REDACTED]" in text
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", (), None)
    record.exc_text = "api_key=fake-cached-key"
    record.stack_info = "Authorization: Bearer fake-stack-key"
    handler.handle(record)
    assert "fake-cached-key" not in output.getvalue()
    assert "fake-stack-key" not in output.getvalue()


def test_https_ipv6_endpoint_retains_required_brackets():
    endpoint = validate_provider_endpoint("https://[2001:db8::1]:8443/v1", False)
    assert endpoint.url == "https://[2001:db8::1]:8443/v1"
    assert validate_provider_endpoint(endpoint.url, False).origin == endpoint.origin


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Authorization: Bearer fake-phase-one-key",
            "Authorization: Bearer [REDACTED]",
        ),
        ("Authorization: Basic fake-phase-one-key", "Authorization: [REDACTED]"),
        ("api_key=fake-phase-one-key", "api_key=[REDACTED]"),
        ("x-api-key=fake-phase-one-key", "x-api-key=[REDACTED]"),
        ('{"token":"fake-phase-one-key"}', '{"token":"[REDACTED]"}'),
    ],
)
def test_secret_scrubber_removes_common_credential_forms(raw, expected) -> None:
    assert SecretScrubber().scrub(raw) == expected


def test_configure_logging_redacts_formatted_credentials_before_writing(tmp_path, preserved_logging_handlers) -> None:
    log_path = configure_logging(tmp_path)
    logger = logging.getLogger("music_agent.provider")

    logger.error("Provider rejected %s", "Bearer fake-phase-one-key")
    for handler in logging.getLogger().handlers:
        handler.flush()

    contents = log_path.read_text(encoding="utf-8")
    assert "fake-phase-one-key" not in contents
    assert "Bearer [REDACTED]" in contents


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/secrets.txt",
        "ftp://provider.example/v1",
        "data:text/plain,credential",
        "https://key@provider.example/v1",
        "http://provider.example/v1",
    ],
)
def test_provider_endpoint_rejects_non_https_or_embedded_credentials(url) -> None:
    with pytest.raises(ValueError):
        validate_provider_endpoint(url, allow_local_http=False)


def test_explicit_local_http_allows_only_named_loopback_hosts() -> None:
    endpoint = validate_provider_endpoint("http://localhost:8123/v1", allow_local_http=True)

    assert endpoint.url == "http://localhost:8123/v1"
    with pytest.raises(ValueError, match="loopback"):
        validate_provider_endpoint("http://192.168.1.5/v1", allow_local_http=True)


def test_cross_origin_redirect_is_rejected_before_a_request_can_forward_auth() -> None:
    source = validate_provider_endpoint("https://api.example.com/v1", allow_local_http=False)

    assert validate_redirect_target(source, "https://api.example.com/v1/models").url.endswith(
        "/v1/models"
    )
    with pytest.raises(ValueError, match="cross-origin redirect"):
        validate_redirect_target(source, "https://other.example.com/v1")
    with pytest.raises(ValueError, match="cross-origin redirect"):
        validate_redirect_target(source, "https://api.example.com:8443/v1")
