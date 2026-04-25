"""Logging configuration with environment-based log levels.

Sets up structured logging for the application and third-party libraries,
with configurable verbosity through environment variables.
"""

from __future__ import annotations

import logging
import os


def _log_level(name: str, default: str) -> int:
    value = str(os.getenv(name, default) or default).strip().upper()
    return getattr(logging, value, getattr(logging, default, logging.INFO))


def _configure_third_party_log_levels(level: int) -> None:
    noisy_loggers = (
        "elastic_transport",
        "elasticsearch",
        "elasticsearch.trace",
        "opensearch",
        "opensearchpy",
        "opensearchpy.trace",
        "urllib3",
    )
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(level)


def configure_logging() -> None:
    app_level = _log_level("APP_LOG_LEVEL", "INFO")
    transport_level = _log_level("SEARCH_TRANSPORT_LOG_LEVEL", "ERROR")
    logging.basicConfig(
        level=app_level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _configure_third_party_log_levels(transport_level)


configure_logging()
log = logging.getLogger("pepagora")
