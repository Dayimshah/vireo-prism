"""Typed application configuration for the Prism analytics API.

Every runtime knob lives here and nowhere else. Modules import the cached
:func:`get_settings` accessor rather than reading ``os.environ`` directly, which
keeps configuration validated in one place and trivially overridable in tests.

Environment contract
--------------------
Settings are read from the process environment (and a local ``.env`` during
development) using the ``PRISM_`` prefix. Nested sections use a double
underscore::

    PRISM_LOG_LEVEL=INFO        -> settings.log_level
    PRISM_DB__HOST=postgres     -> settings.db.host
    PRISM_CACHE__ENABLED=false  -> settings.cache.enabled

See ``.env.example`` at the repository root for the full documented list.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, PostgresDsn, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Filesystem anchors
# ---------------------------------------------------------------------------

#: ``backend/`` — the Python project root. Resolved from this file's location so
#: it stays correct whether the app runs from Docker, uvicorn, or pytest.
BACKEND_DIR: Path = Path(__file__).resolve().parents[2]

#: Repository root, one level above ``backend/``.
REPO_ROOT: Path = BACKEND_DIR.parent

#: Directory holding the 46 named ``.sql`` analytics queries.
SQL_QUERIES_DIR: Path = BACKEND_DIR / "app" / "sql" / "queries"


class Environment(StrEnum):
    """Deployment environment.

    Controls log formatting, OpenAPI exposure and error verbosity.
    """

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class SeedProfile(StrEnum):
    """Named synthetic-dataset scale.

    The concrete row counts for each profile are declared in
    ``seeder/config.py``; this enum only selects between them so that changing
    dataset size never requires a code change.
    """

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection and pool configuration.

    Two DSNs are derived from the same credentials because the project uses two
    drivers deliberately: ``asyncpg`` for the API's hot read path, and
    ``psycopg`` for Alembic migrations and the seeder's binary ``COPY`` loader
    (asyncpg exposes no equivalent to ``COPY ... FROM STDIN WITH (FORMAT
    BINARY)``).
    """

    host: str = "postgres"
    port: int = Field(default=5432, ge=1, le=65535)
    user: str = "prism"
    password: str = "prism"
    name: str = "vireo"

    #: SSL mode for the connection. Set to ``require`` for cloud databases
    #: (Neon, Supabase, RDS, etc.) that mandate encrypted connections.
    sslmode: str = ""

    pool_size: int = Field(default=10, ge=1, le=100)
    max_overflow: int = Field(default=20, ge=0, le=200)
    pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    pool_recycle_seconds: int = Field(default=1800, ge=60)

    #: Server-side guard against a runaway analytical query. Applied per
    #: connection in ``app/db/session.py``.
    statement_timeout_ms: int = Field(default=30_000, ge=1_000, le=600_000)

    #: Echo every emitted statement. Extremely noisy; debugging only.
    echo_sql: bool = False

    def _ssl_query(self) -> str:
        """Return the query string portion for SSL, or empty string."""
        if self.sslmode:
            return f"?sslmode={self.sslmode}"
        return ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_dsn(self) -> str:
        """Return the SQLAlchemy async DSN used by the API (asyncpg driver)."""
        base = str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                path=self.name,
            )
        )
        return base + self._ssl_query()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_dsn(self) -> str:
        """Return the synchronous DSN used by Alembic and the seeder (psycopg)."""
        base = str(
            PostgresDsn.build(
                scheme="postgresql+psycopg",
                username=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                path=self.name,
            )
        )
        return base + self._ssl_query()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def libpq_dsn(self) -> str:
        """Return a plain libpq DSN for direct ``psycopg.connect`` calls.

        Distinct from :attr:`sync_dsn`, which carries SQLAlchemy's
        ``+psycopg`` dialect marker. SQLAlchemy strips that marker before handing
        the URL to the driver; ``psycopg.connect`` does not, and rejects it as a
        malformed connection string. The seeder connects directly, so it needs
        this form.
        """
        base = str(
            PostgresDsn.build(
                scheme="postgresql",
                username=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                path=self.name,
            )
        )
        return base + self._ssl_query()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safe_dsn(self) -> str:
        """Return a credential-free DSN that is safe to write to logs."""
        return f"postgresql://{self.user}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseSettings):
    """Redis connection settings.

    Redis is genuinely optional. When ``enabled`` is false, or when the server
    is unreachable at startup, the cache layer falls back to a bounded
    in-process LRU so the API keeps serving rather than failing.
    """

    enabled: bool = True
    host: str = "redis"
    port: int = Field(default=6379, ge=1, le=65535)
    db: int = Field(default=0, ge=0, le=15)
    password: str = ""
    socket_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dsn(self) -> str:
        """Return the Redis URL, including credentials when configured."""
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class CacheSettings(BaseSettings):
    """Cache-aside policy.

    Analytics answers describe a fixed historical window, so they tolerate
    generous TTLs. Heavy endpoints (cohort matrices, funnels by segment) get a
    longer TTL than headline KPIs, which users expect to feel live.
    """

    enabled: bool = True
    default_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    kpi_ttl_seconds: int = Field(default=180, ge=0, le=86_400)
    heavy_ttl_seconds: int = Field(default=900, ge=0, le=86_400)
    local_max_entries: int = Field(default=512, ge=16, le=65_536)
    key_namespace: str = "prism:v1"


class ApiSettings(BaseSettings):
    """HTTP surface configuration."""

    title: str = "Prism API"
    version: str = "1.0.0"
    #: Mount prefix for every versioned route.
    prefix: str = "/api/v1"
    #: Set when the app is served behind a path-rewriting reverse proxy.
    root_path: str = ""

    #: Comma-separated in the environment; use :attr:`cors_origin_list` in code.
    #: Declared as a plain string because pydantic-settings attempts JSON
    #: decoding on complex-typed fields, which makes ``a,b`` an error rather
    #: than a list.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    default_page_size: int = Field(default=50, ge=1, le=500)
    max_page_size: int = Field(default=200, ge=1, le=1_000)

    #: Upper bound on any requested reporting window, so a single crafted
    #: request cannot force a full-table scan of the events partitions.
    max_date_range_days: int = Field(default=730, ge=1, le=3_650)

    #: Shared secret for ``POST /api/v1/admin/refresh``, the only mutating
    #: endpoint in the service.
    admin_key: str = "local-dev-admin-key"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        """Return CORS origins as a de-duplicated, order-preserving list."""
        seen: dict[str, None] = {}
        for raw in self.cors_origins.split(","):
            origin = raw.strip().rstrip("/")
            if origin:
                seen.setdefault(origin, None)
        return list(seen)


class SeedSettings(BaseSettings):
    """Synthetic data generation controls.

    ``random_seed`` is the reason this project is reproducible: the same seed
    yields a byte-identical dataset on any machine, which is what allows the
    documented findings and screenshots in ``docs/`` to stay true.
    """

    profile: SeedProfile = SeedProfile.MEDIUM
    random_seed: int = Field(default=20_240_817, ge=0)

    #: Last day of the simulated window. ``None`` means "today at generation
    #: time", which keeps a freshly cloned repo looking current.
    window_end: date | None = None
    window_months: int = Field(default=18, ge=1, le=60)

    #: Rows per ``COPY`` batch. Trades peak memory against round trips.
    copy_batch_rows: int = Field(default=50_000, ge=1_000, le=1_000_000)

    @field_validator("window_end", mode="before")
    @classmethod
    def _empty_string_is_none(cls, value: object) -> object:
        """Treat ``PRISM_SEED__WINDOW_END=`` (blank) as unset rather than invalid."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class Settings(BaseSettings):
    """Root settings object; the single source of truth for runtime behaviour."""

    model_config = SettingsConfigDict(
        env_prefix="PRISM_",
        env_nested_delimiter="__",
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    #: Human-facing product name (the platform), distinct from the fictional
    #: company whose data it analyses.
    app_name: str = "Prism"
    org_name: str = "Vireo"

    env: Annotated[Environment, Field(validation_alias=AliasChoices("PRISM_ENV", "env"))] = (
        Environment.DEVELOPMENT
    )
    debug: bool = False
    log_level: LogLevel = "INFO"

    #: Emit JSON logs. Defaults to "structured everywhere except development",
    #: resolved by :attr:`use_json_logs` when left unset.
    log_json: bool | None = None

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    seed: SeedSettings = Field(default_factory=SeedSettings)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Accept ``info``/``Info``/``INFO`` interchangeably."""
        return value.strip().upper() if isinstance(value, str) else value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        """Return whether the app is running in the production environment."""
        return self.env is Environment.PRODUCTION

    @computed_field  # type: ignore[prop-decorator]
    @property
    def use_json_logs(self) -> bool:
        """Return whether logs should be rendered as JSON lines."""
        if self.log_json is not None:
            return self.log_json
        return self.env is not Environment.DEVELOPMENT

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expose_docs(self) -> bool:
        """Return whether interactive OpenAPI docs should be mounted.

        Docs are the whole point of a portfolio API, so they stay on in
        development and test. Production hides them by default, which is the
        behaviour a reviewer expects to see considered.
        """
        return not self.is_production


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that validation and ``.env`` parsing happen exactly once. Tests
    that need a different configuration should call
    ``get_settings.cache_clear()`` after patching the environment.

    Returns:
        The validated :class:`Settings` instance for this process.
    """
    return Settings()
