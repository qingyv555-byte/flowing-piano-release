from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


_AUTHORIZATION = re.compile(
    r"(?i)(?P<prefix>\bauthorization\s*:\s*)(?:(?P<bearer>bearer)\s+|(?:[a-z][a-z0-9_-]*)\s+)?(?P<secret>[^\s,;]+)"
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")
_KEY_VALUE = re.compile(
    r"""(?ix)
    (?P<key_quote>[\"']?)
    (?P<key>\b(?:api[_-]?key|x-api-key|token)\b)
    (?P=key_quote)
    (?P<delimiter>\s*[:=]\s*)
    (?P<quote>[\"']?)
    (?P<secret>[^\"'\s,;&}]+)
    (?P=quote)
    """
)
_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1"})


class SecretScrubber:
    """Redacts credential-shaped values before they can leave process memory."""

    REDACTED = "[REDACTED]"

    def scrub(self, value: object) -> str:
        text = str(value)
        text = _AUTHORIZATION.sub(self._replace_authorization, text)
        text = _KEY_VALUE.sub(self._replace_key_value, text)
        return _BEARER.sub(r"\1" + self.REDACTED, text)

    def _replace_authorization(self, match: re.Match[str]) -> str:
        scheme = match.group("bearer")
        scheme_text = f"{scheme} " if scheme else ""
        prefix = match.group("prefix")
        return f"{prefix}{scheme_text}{self.REDACTED}"

    def _replace_key_value(self, match: re.Match[str]) -> str:
        return "{key}{delimiter}{quote}{redacted}{quote}".format(
            key=f"{match.group('key_quote')}{match.group('key')}{match.group('key_quote')}",
            delimiter=match.group("delimiter"),
            quote=match.group("quote"),
            redacted=self.REDACTED,
        )


class SecretScrubbingFilter(logging.Filter):
    def __init__(self, scrubber: SecretScrubber | None = None) -> None:
        super().__init__()
        self._scrubber = scrubber or SecretScrubber()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrubber.scrub(record.getMessage())
        record.args = ()
        # Formatters append exception/stack text after getMessage(). Sanitize
        # those fields too, including text cached by an earlier handler.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._scrubber.scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = self._scrubber.scrub(record.stack_info)
        return True


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    scheme: str
    host: str
    port: int | None
    base_path: str
    allows_local_http: bool

    @property
    def origin(self) -> tuple[str, str, int | None]:
        return self.scheme, self.host, self.port

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        netloc = host if self.port is None else f"{host}:{self.port}"
        return urlunsplit((self.scheme, netloc, self.base_path, "", ""))


def validate_provider_endpoint(raw_url: object, allow_local_http: bool) -> ProviderEndpoint:
    if not isinstance(raw_url, str) or not raw_url or raw_url != raw_url.strip():
        raise ValueError("provider URL must be a non-empty string without surrounding whitespace")
    if not isinstance(allow_local_http, bool):
        raise ValueError("allow_local_http must be a boolean")
    parsed = urlsplit(raw_url)
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"}:
        raise ValueError("provider URL must use HTTPS or explicitly allowed local HTTP")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("provider URL must not contain credentials")
    host = parsed.hostname
    if host is None:
        raise ValueError("provider URL must include a host")
    host = host.lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("provider URL contains an invalid port") from error
    if parsed.query or parsed.fragment:
        raise ValueError("provider URL must not include a query or fragment")
    if scheme == "http":
        if not allow_local_http:
            raise ValueError("provider URL must use HTTPS")
        if host not in _LOCAL_HTTP_HOSTS:
            raise ValueError("plain HTTP is allowed only for explicit loopback hosts")
    base_path = parsed.path.rstrip("/")
    return ProviderEndpoint(scheme, host, port, base_path, allow_local_http)


def validate_redirect_target(source: ProviderEndpoint, target_url: object) -> ProviderEndpoint:
    target = validate_provider_endpoint(target_url, source.allows_local_http)
    if target.origin != source.origin:
        raise ValueError("cross-origin redirect is not allowed")
    return target
