"""Structured logging for the Prism analytics API.

One logging pipeline for the whole process. Application code, uvicorn and
SQLAlchemy all funnel through the same structlog processor chain, so a single
request produces one coherent, correlated stream of records rather than three
competing formats.

Two rendering modes, selected by :attr:`app.core.config.Settings.use_json_logs`:

* **console** — colourised key/value lines, readable while developing.
* **json** — one JSON object per line, ready for Loki/CloudWatch/Datadog.

Request correlation uses ``contextvars``, which are coroutine-local. Binding
``request_id`` once in middleware means every downstream log line in that
request carries it automatically, with no plumbing through call signatures.

Usage:
    >>> from app.core.logging import configure_logging, get_logger
    >>> configure_logging()
    >>> log = get_logger(__name__)
    >>> log.info("query_executed", query="dau", duration_ms=41.2, rows=180)
"""

from __future__ import annotations

import logging
import logging.config
import sys
from typing import TYPE_CHECKING, Any, Final
import uuid

import structlog

from app.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterable

    from structlog.typing import Processor

# ---------------------------------------------------------------------------
# Noise control
# ---------------------------------------------------------------------------

#: Third-party loggers that are chatty by default. Anything not listed inherits
#: the configured root level.
#:
#: ``uvicorn.access`` is silenced outright, not merely lowered: the API emits its
#: own access log from middleware with richer fields (request id, cache
#: hit/miss, duration), and keeping uvicorn's version would double every line.
_LOGGER_LEVELS: Final[dict[str, int]] = {
    "uvicorn": logging.INFO,
    "uvicorn.error": logging.INFO,
    "uvicorn.access": logging.CRITICAL,
    "watchfiles": logging.WARNING,
    "asyncio": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
    "sqlalchemy.pool": logging.WARNING,
    "alembic": logging.INFO,
    "faker": logging.WARNING,
    "httpx": logging.WARNING,
}

#: Context keys bound per request. Named so middleware and teardown agree.
REQUEST_ID_KEY: Final[str] = "request_id"

_CONFIGURED: bool = False


def _shared_processors() -> list[Processor]:
    """Return the processor chain applied to every record, structlog or stdlib.

    Ordering matters: context is merged first so later processors can see it,
    and the timestamp is added before rendering so it appears as a real field
    rather than being folded into the message.

    Returns:
        Processors shared by native structlog calls and wrapped stdlib records.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]


def _renderer(*, json_logs: bool) -> Processor:
    """Return the final processor that turns an event dict into a log line.

    Args:
        json_logs: Render newline-delimited JSON when true, colourised
            key/value pairs when false.

    Returns:
        The terminal processor for the chain.
    """
    if json_logs:
        # dict_tracebacks yields machine-parsable exception frames, which is
        # the whole reason to ship JSON in the first place.
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(
        colors=sys.stderr.isatty(),
        exception_formatter=structlog.dev.plain_traceback,
    )


def configure_logging(*, force: bool = False) -> None:
    """Install the logging pipeline for this process.

    Idempotent by default so that importing the app twice (uvicorn's reloader,
    pytest collection) cannot stack duplicate handlers and print every line
    twice.

    Called from the FastAPI lifespan handler in ``app/main.py`` and from the
    seeder's entrypoint.

    Args:
        force: Reconfigure even if logging was already set up. Tests use this
            to switch between console and JSON rendering.
    """
    global _CONFIGURED  # noqa: PLW0603 - module-level install guard
    if _CONFIGURED and not force:
        return

    settings = get_settings()
    json_logs = settings.use_json_logs
    level = logging.getLevelNamesMapping()[settings.log_level]
    shared = _shared_processors()

    # Native structlog calls: run the shared chain, then hand the event dict to
    # the stdlib formatter so both sources render identically.
    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Records from libraries that use plain `logging` are pre-processed with the
    # same chain via foreign_pre_chain, which is what unifies the output.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.processors.dict_tracebacks if json_logs else _passthrough,
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _renderer(json_logs=json_logs),
        ],
    )

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Uvicorn installs its own handlers before our lifespan runs; replace them
    # rather than adding to them.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for name, logger_level in _LOGGER_LEVELS.items():
        third_party = logging.getLogger(name)
        third_party.setLevel(max(logger_level, level) if name != "uvicorn.access" else logger_level)
        third_party.propagate = True

    if settings.db.echo_sql:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

    _CONFIGURED = True

    get_logger(__name__).debug(
        "logging_configured",
        level=settings.log_level,
        renderer="json" if json_logs else "console",
        environment=str(settings.env),
    )


def _passthrough(
    _logger: object,
    _name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Return the event dict untouched.

    Placeholder in the console chain where the JSON chain formats tracebacks;
    :class:`structlog.dev.ConsoleRenderer` renders exceptions itself, so
    pre-formatting them here would produce them twice.

    Args:
        _logger: Unused, required by the processor signature.
        _name: Unused, required by the processor signature.
        event_dict: The record under construction.

    Returns:
        ``event_dict``, unmodified.
    """
    return event_dict


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Args:
        name: Logger name, conventionally the calling module's ``__name__``.
            Defaults to the root logger when omitted.

    Returns:
        A logger whose keyword arguments become structured fields.
    """
    return structlog.stdlib.get_logger(name)


def new_request_id() -> str:
    """Return a fresh correlation id for an inbound request.

    Returns:
        A 32-character hex string, echoed to clients as ``X-Request-ID``.
    """
    return uuid.uuid4().hex


def bind_request_context(**fields: Any) -> None:
    """Bind fields to the current coroutine's logging context.

    Every subsequent log record emitted while handling this request carries
    these fields without them being passed explicitly.

    Args:
        **fields: Key/value pairs to attach, typically ``request_id``,
            ``method`` and ``path``.
    """
    structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    """Clear the coroutine-local logging context.

    Called in a ``finally`` block by the request middleware so a recycled worker
    task cannot leak one request's correlation id into the next.
    """
    structlog.contextvars.clear_contextvars()


def silence_loggers(names: Iterable[str], level: int = logging.WARNING) -> None:
    """Raise the threshold on specific loggers at runtime.

    Used by the seeder, where Faker and SQLAlchemy would otherwise drown out
    generation progress.

    Args:
        names: Logger names to adjust.
        level: Minimum level those loggers should emit.
    """
    for name in names:
        logging.getLogger(name).setLevel(level)
