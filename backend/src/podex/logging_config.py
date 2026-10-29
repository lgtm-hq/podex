"""Structured logging configuration."""

import logging

import httpx

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_CREDENTIAL_QUERY_PARAMS = ("api_key", "apikey", "key")


def _safe_httpx_argument(value: object) -> object:
    """Remove provider credentials from a logged URL without changing requests."""
    if isinstance(value, httpx.URL):
        for name in _CREDENTIAL_QUERY_PARAMS:
            value = value.copy_remove_param(name)
    return value


def _redact_httpx_credentials(record: logging.LogRecord) -> bool:
    """Keep HTTPX request diagnostics while removing credential query fields."""
    if isinstance(record.args, tuple):
        record.args = tuple(_safe_httpx_argument(value) for value in record.args)
    return True


def configure_logging(*, level: str = "INFO") -> None:
    """Configure root logging once for the application."""
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    logging.getLogger("httpx").addFilter(_redact_httpx_credentials)


def get_logger(name: str) -> logging.Logger:
    """Return a named application logger."""
    return logging.getLogger(name)
