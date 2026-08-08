"""FastAPI dependencies: request-scoped sessions and the dimension catalogue.

Two things live here, both of which every router needs.

**Sessions.** :data:`SessionDep` yields one read-only session per request, closed
when the response is sent. Routers annotate a parameter with it and never import
the engine.

**The dimension catalogue.** Filter values arriving over HTTP are checked against
the real contents of ``core.countries``, ``core.devices`` and the other four
dimension tables *before* they reach SQL. This is the project's primary defence
against injection through filter parameters, and it is stricter than parameter
binding alone: binding stops a value being interpreted as SQL, but it does not
stop ``?country=Wakanda`` silently producing an empty chart that a reader
mistakes for a real finding. Validation turns that into a 422 naming the valid
options.

The catalogue is loaded once at startup and held in memory. It is roughly 70 rows
across six tables and changes only when a migration adds a dimension row, so
re-reading it per request would be six joins in exchange for nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import UnknownDimensionValueError
from app.core.logging import get_logger
from app.db.session import get_sessionmaker, translate_db_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request-scoped session
# ---------------------------------------------------------------------------


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a read-only session for the lifetime of one request.

    The transaction is set read-only at the database level, so a stray write
    anywhere in the service layer fails rather than succeeding quietly. See
    :mod:`app.db.session` for why that is enforced here rather than by convention.

    Yields:
        A session in a read-only transaction.

    Raises:
        DatabaseError: Any driver failure, translated so raw SQL never reaches a
            client.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            yield session
        except SQLAlchemyError as exc:
            logger.error("request_session_error", error=str(exc), exc_info=True)
            raise translate_db_error(exc) from exc
        finally:
            # Nothing to commit on a read path; rolling back releases the
            # snapshot immediately so it cannot block a concurrent MV refresh.
            await session.rollback()


#: Annotated session dependency. Routers declare ``session: SessionDep``.
SessionDep = Annotated["AsyncSession", Depends(get_session)]


# ---------------------------------------------------------------------------
# Dimension catalogue
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DimensionCatalog:
    """Valid values for every filterable dimension, loaded once at startup.

    Each mapping is ``display value -> surrogate key``. Filters arrive as human
    strings ("India", "Smart TV", "Paid Social") because that is what a shareable
    URL should contain, and the SQL binds integer keys because that is what the
    indexes are on. This structure is the translation layer, and it doubles as the
    allowlist.

    Attributes:
        countries: ISO code and country name, both mapped to ``country_id``.
        devices: Device name mapped to ``device_id``.
        device_platforms: Distinct platform names, e.g. ``iOS``.
        device_form_factors: Distinct form factors, e.g. ``phone``.
        channels: Channel name mapped to ``channel_id``.
        channel_groups: Distinct channel groups, e.g. ``Paid``.
        personas: Persona name mapped to ``persona_id``.
        genres: Genre name mapped to ``genre_id``.
        plans: Plan name mapped to ``plan_id``.
        plan_tiers: Distinct plan tiers, e.g. ``premium``.
        content_types: The ``core.content_type`` enum labels.
        loaded: Whether :meth:`load` has run successfully.
    """

    countries: dict[str, int] = field(default_factory=dict)
    devices: dict[str, int] = field(default_factory=dict)
    device_platforms: frozenset[str] = frozenset()
    device_form_factors: frozenset[str] = frozenset()
    channels: dict[str, int] = field(default_factory=dict)
    channel_groups: frozenset[str] = frozenset()
    personas: dict[str, int] = field(default_factory=dict)
    genres: dict[str, int] = field(default_factory=dict)
    plans: dict[str, int] = field(default_factory=dict)
    plan_tiers: frozenset[str] = frozenset()
    content_types: frozenset[str] = frozenset()
    loaded: bool = False

    async def load(self, session: AsyncSession) -> None:
        """Populate the catalogue from the dimension tables.

        Args:
            session: A session to read through.
        """
        # Countries are keyed by both ISO code and full name so that
        # `?country=IN` and `?country=India` both work. A shareable URL benefits
        # from the short form; a hand-written one from the readable form.
        rows = (
            await session.execute(
                text("SELECT country_id, iso_code, name FROM core.countries")
            )
        ).all()
        self.countries = {}
        for country_id, iso_code, name in rows:
            self.countries[iso_code] = country_id
            self.countries[name] = country_id

        rows = (
            await session.execute(
                text("SELECT device_id, name, platform, form_factor FROM core.devices")
            )
        ).all()
        self.devices = {name: device_id for device_id, name, _, _ in rows}
        self.device_platforms = frozenset(platform for _, _, platform, _ in rows)
        self.device_form_factors = frozenset(form for _, _, _, form in rows)

        rows = (
            await session.execute(
                text("SELECT channel_id, name, channel_group FROM core.marketing_channels")
            )
        ).all()
        self.channels = {name: channel_id for channel_id, name, _ in rows}
        self.channel_groups = frozenset(group for _, _, group in rows)

        self.personas = {
            name: persona_id
            for persona_id, name in (
                await session.execute(text("SELECT persona_id, name FROM core.personas"))
            ).all()
        }

        self.genres = {
            name: genre_id
            for genre_id, name in (
                await session.execute(text("SELECT genre_id, name FROM core.genres"))
            ).all()
        }

        rows = (
            await session.execute(
                text("SELECT plan_id, name, tier FROM core.subscription_plans")
            )
        ).all()
        self.plans = {name: plan_id for plan_id, name, _ in rows}
        self.plan_tiers = frozenset(tier for _, _, tier in rows)

        # Read the enum labels from the catalogue rather than duplicating the
        # tuple from revision 0001. One source of truth, and it stays right if a
        # label is ever appended.
        self.content_types = frozenset(
            label
            for (label,) in (
                await session.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid "
                        "JOIN pg_namespace n ON n.oid = t.typnamespace "
                        "WHERE n.nspname = 'core' AND t.typname = 'content_type' "
                        "ORDER BY e.enumsortorder"
                    )
                )
            ).all()
        )

        self.loaded = True

        logger.info(
            "dimension_catalog_loaded",
            countries=len({v for v in self.countries.values()}),
            devices=len(self.devices),
            channels=len(self.channels),
            personas=len(self.personas),
            genres=len(self.genres),
            plans=len(self.plans),
            content_types=len(self.content_types),
        )

    # -- resolution helpers -------------------------------------------------

    def _resolve(
        self,
        dimension: str,
        values: list[str] | None,
        lookup: dict[str, int],
    ) -> list[int] | None:
        """Translate display values into surrogate keys.

        Args:
            dimension: Filter name, used in the error message.
            values: Requested display values, or ``None`` for no filter.
            lookup: The relevant display-to-key mapping.

        Returns:
            Surrogate keys, de-duplicated and sorted, or ``None`` when no filter
            was requested.

        Raises:
            UnknownDimensionValueError: If any value is not in the catalogue.
        """
        if not values:
            return None

        unknown = [value for value in values if value not in lookup]
        if unknown:
            raise UnknownDimensionValueError(
                dimension, unknown, allowed=sorted(set(lookup))
            )

        # Sorted so that two requests differing only in filter order produce the
        # same bound parameters and therefore the same cache key.
        return sorted({lookup[value] for value in values})

    def _validate_literals(
        self,
        dimension: str,
        values: list[str] | None,
        allowed: frozenset[str],
    ) -> list[str] | None:
        """Validate values that stay as strings in the query.

        Used for the dimensions with no surrogate key of their own — platform,
        form factor, channel group, plan tier, content type. They are still
        allowlisted, and still bound as parameters.

        Args:
            dimension: Filter name, used in the error message.
            values: Requested values, or ``None``.
            allowed: Permitted values.

        Returns:
            The values, de-duplicated and sorted, or ``None``.

        Raises:
            UnknownDimensionValueError: If any value is not permitted.
        """
        if not values:
            return None

        unknown = [value for value in values if value not in allowed]
        if unknown:
            raise UnknownDimensionValueError(dimension, unknown, allowed=sorted(allowed))

        return sorted(set(values))

    def resolve_countries(self, values: list[str] | None) -> list[int] | None:
        """Resolve country names or ISO codes to ``country_id`` values."""
        return self._resolve("country", values, self.countries)

    def resolve_devices(self, values: list[str] | None) -> list[int] | None:
        """Resolve device names to ``device_id`` values."""
        return self._resolve("device", values, self.devices)

    def resolve_channels(self, values: list[str] | None) -> list[int] | None:
        """Resolve channel names to ``channel_id`` values."""
        return self._resolve("channel", values, self.channels)

    def resolve_personas(self, values: list[str] | None) -> list[int] | None:
        """Resolve persona names to ``persona_id`` values."""
        return self._resolve("persona", values, self.personas)

    def resolve_genres(self, values: list[str] | None) -> list[int] | None:
        """Resolve genre names to ``genre_id`` values."""
        return self._resolve("genre", values, self.genres)

    def resolve_plans(self, values: list[str] | None) -> list[int] | None:
        """Resolve plan names to ``plan_id`` values."""
        return self._resolve("plan", values, self.plans)

    def validate_platforms(self, values: list[str] | None) -> list[str] | None:
        """Validate device platform names."""
        return self._validate_literals("platform", values, self.device_platforms)

    def validate_form_factors(self, values: list[str] | None) -> list[str] | None:
        """Validate device form factors."""
        return self._validate_literals("form_factor", values, self.device_form_factors)

    def validate_channel_groups(self, values: list[str] | None) -> list[str] | None:
        """Validate marketing channel groups."""
        return self._validate_literals("channel_group", values, self.channel_groups)

    def validate_plan_tiers(self, values: list[str] | None) -> list[str] | None:
        """Validate plan tiers."""
        return self._validate_literals("plan_tier", values, self.plan_tiers)

    def validate_content_types(self, values: list[str] | None) -> list[str] | None:
        """Validate content types against the ``core.content_type`` enum."""
        return self._validate_literals("content_type", values, self.content_types)

    def options(self) -> dict[str, list[str]]:
        """Return every filter's valid values, for the frontend's filter bar.

        The dashboard populates its multi-selects from this rather than hard-coding
        them, so a dimension row added by a migration appears in the UI without a
        frontend change.

        Returns:
            Mapping of filter name to its sorted valid values. Countries are
            reported by name only; ISO codes remain accepted but would double the
            list a user sees.
        """
        by_id: dict[int, str] = {}
        for label, country_id in self.countries.items():
            # Two entries per country; keep the longer, which is the name.
            if country_id not in by_id or len(label) > len(by_id[country_id]):
                by_id[country_id] = label

        return {
            "country": sorted(by_id.values()),
            "device": sorted(self.devices),
            "platform": sorted(self.device_platforms),
            "form_factor": sorted(self.device_form_factors),
            "channel": sorted(self.channels),
            "channel_group": sorted(self.channel_groups),
            "persona": sorted(self.personas),
            "genre": sorted(self.genres),
            "plan": sorted(self.plans),
            "plan_tier": sorted(self.plan_tiers),
            "content_type": sorted(self.content_types),
        }


#: Process-wide catalogue, populated by :func:`init_dimension_catalog`.
_catalog: Final[DimensionCatalog] = DimensionCatalog()


async def init_dimension_catalog() -> DimensionCatalog:
    """Load the dimension catalogue at startup.

    Called from the FastAPI lifespan after the engine is ready. A failure is
    logged but not raised: the service should still start and serve
    ``/health`` against an unmigrated database, and filter validation then
    rejects everything with a clear message rather than the process refusing to
    boot.

    Returns:
        The process-wide catalogue, loaded if the database was reachable.
    """
    factory = get_sessionmaker()
    try:
        async with factory() as session:
            await _catalog.load(session)
    except SQLAlchemyError as exc:
        logger.warning(
            "dimension_catalog_load_failed",
            error=str(exc),
            hint="Filters will reject all values until migrations have run.",
        )
    return _catalog


def get_dimension_catalog() -> DimensionCatalog:
    """Return the process-wide dimension catalogue.

    Returns:
        The catalogue. May be unloaded — check :attr:`DimensionCatalog.loaded`.
    """
    return _catalog


#: Annotated catalogue dependency.
CatalogDep = Annotated[DimensionCatalog, Depends(get_dimension_catalog)]


__all__ = [
    "CatalogDep",
    "DimensionCatalog",
    "SessionDep",
    "get_dimension_catalog",
    "get_session",
    "init_dimension_catalog",
]
