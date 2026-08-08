"""Seeder entrypoint: ``python -m seeder``.

Orchestrates the whole generation run and is the only module that both generates
and writes.

Load order, and why it is what it is
------------------------------------
Foreign keys force part of this, and memory forces the rest.

``core.events`` references ``core.sessions`` references ``core.users``, so users
must land before sessions before events. But three ``core.users`` columns —
``is_premium``, ``last_seen_at``, ``churned_at`` — are only known *after* the
timeline walk has run. And at the medium profile the walk produces about 3.4
million event rows, which cannot all be held in memory as Python objects.

The resolution is to process users in chunks. For each chunk the walk runs, then
that chunk's users, sessions, events and subscriptions are copied in dependency
order, then the objects are dropped. Peak memory stays bounded by one chunk
regardless of profile, and no table is ever written before its parent.

A single connection can only have one ``COPY`` in progress at a time, which is why
the four tables are written sequentially per chunk rather than streamed in
parallel.

Transactionality
----------------
The entire load is one transaction. A failure at row three million leaves the
database exactly as it was, rather than half-populated in a way that looks like
real data.

Usage::

    python -m seeder                            # medium profile, from .env
    python -m seeder --profile small --truncate
    python -m seeder --seed 42 --validate
    python -m seeder --profile large --no-refresh
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time as dtime, timedelta
import random
import sys
import time
from typing import TYPE_CHECKING, Final

from app.core.config import SeedProfile, get_settings
from app.core.logging import configure_logging, get_logger, silence_loggers
from seeder import config, loaders
from seeder.catalog import build_catalog, curated_title_count
from seeder.generators.events import ContentSelector
from seeder.generators.experiments import EffectResolver, assign_users, build_experiments
from seeder.generators.sessions import build_intensity_tables, simulate_user
from seeder.generators.users import generate_users, user_row

if TYPE_CHECKING:
    import psycopg

logger = get_logger(__name__)

#: Users simulated per chunk before their rows are flushed. 2,000 users is roughly
#: 34,000 sessions and 270,000 events at the medium profile — a few hundred
#: megabytes of Python objects, which is comfortable on any development machine.
CHUNK_USERS: Final[int] = 2_000


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list. Defaults to ``sys.argv[1:]``.

    Returns:
        The parsed namespace.
    """
    parser = argparse.ArgumentParser(
        prog="python -m seeder",
        description="Generate the synthetic Vireo dataset and load it into PostgreSQL.",
        epilog=(
            "Row counts and runtimes per profile are documented in "
            "seeder/config.py. The same --seed always produces an identical dataset."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=sorted(config.PROFILES),
        default=None,
        help="dataset size (default: PRISM_SEED__PROFILE, normally 'medium')",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="random seed (default: PRISM_SEED__RANDOM_SEED). Fixes the dataset exactly.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="empty the seeded tables first. Dimension tables are never touched.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="assert journey invariants on sampled sessions (slower)",
    )
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="skip the analytics materialized view refresh",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="write docs/data_quality_report.html after loading",
    )
    parser.add_argument(
        "--window-end",
        type=lambda value: date.fromisoformat(value),
        default=None,
        help="last day of the simulation window as YYYY-MM-DD (default: today)",
    )
    return parser.parse_args(argv)


def _resolve_window(args: argparse.Namespace) -> tuple[date, date, datetime]:
    """Determine the simulation window.

    Args:
        args: Parsed arguments.

    Returns:
        ``(window_start, window_end, window_end_utc)``. The UTC timestamp is the
        ceiling for every generated event, keeping the
        ``ck_events_no_future_time`` constraint satisfied by construction.
    """
    settings = get_settings()
    window_end = args.window_end or settings.seed.window_end or datetime.now(UTC).date()
    window_start, window_end = config.window_bounds(window_end, settings.seed.window_months)

    # End of the final day, minus a margin. The margin matters: `now()` advances
    # while the load runs, and an event stamped at the exact current instant would
    # fail the CHECK by the time PostgreSQL evaluates it.
    window_end_utc = datetime.combine(window_end, dtime(hour=23, minute=30), tzinfo=UTC)
    now = datetime.now(UTC)
    if window_end_utc > now:
        window_end_utc = now - timedelta(minutes=5)

    return window_start, window_end, window_end_utc


def _load_chunk(
    conn: psycopg.Connection[object],
    *,
    users: list[object],
    sessions: list[object],
    events: list[object],
    subscriptions: list[object],
) -> None:
    """Copy one chunk's rows in foreign-key dependency order.

    Args:
        conn: The open connection.
        users: Rendered ``core.users`` rows.
        sessions: Rendered ``core.sessions`` rows.
        events: Rendered ``core.events`` rows.
        subscriptions: Rendered ``core.subscriptions`` rows.
    """
    with loaders.CopyLoader(conn, "core.users", loaders.USER_COLUMNS) as copy:
        copy.write_all(users)

    if sessions:
        with loaders.CopyLoader(conn, "core.sessions", loaders.SESSION_COLUMNS) as copy:
            copy.write_all(sessions)

    if events:
        with loaders.CopyLoader(conn, "core.events", loaders.EVENT_COLUMNS) as copy:
            copy.write_all(events)

    if subscriptions:
        with loaders.CopyLoader(
            conn, "core.subscriptions", loaders.SUBSCRIPTION_COLUMNS
        ) as copy:
            copy.write_all(subscriptions)


def run(args: argparse.Namespace) -> int:
    """Generate and load the dataset.

    Args:
        args: Parsed arguments.

    Returns:
        A process exit code.
    """
    settings = get_settings()

    profile_name = args.profile or settings.seed.profile.value
    profile = config.get_profile(profile_name)
    seed = args.seed if args.seed is not None else settings.seed.random_seed

    window_start, window_end, window_end_utc = _resolve_window(args)

    # One seeded generator for the whole run. Every draw in the simulation comes
    # from here, which is what makes a seed reproduce a dataset byte for byte.
    rng = random.Random(seed)

    print(f"\n  Vireo dataset — profile '{profile.name}', seed {seed}")
    print(f"  Window: {window_start} to {window_end} ({settings.seed.window_months} months)")
    print(
        f"  Expecting ~{profile.users:,} users, ~{profile.approx_sessions:,} sessions, "
        f"~{profile.approx_events:,} events"
    )
    print(f"  Estimated runtime: ~{profile.approx_runtime_seconds}s\n")

    started = time.perf_counter()
    # libpq_dsn, not sync_dsn: psycopg.connect rejects SQLAlchemy's `+psycopg`
    # dialect marker, which SQLAlchemy itself strips before reaching the driver.
    conn = loaders.connect(settings.db.libpq_dsn)

    try:
        dimensions = loaders.read_dimensions(conn)
        loaders.ensure_partitions(conn, window_start, window_end)

        if args.truncate:
            loaders.truncate(conn)

        # -------------------------------------------------------------------
        # Catalogue
        # -------------------------------------------------------------------
        print("  [1/6] catalogue", end="", flush=True)
        catalogue = build_catalog(
            rng,
            size=profile.titles,
            genre_ids=dimensions["genre_ids"],
            window_start=window_start,
            window_end=window_end,
        )
        with loaders.CopyLoader(conn, "core.content", loaders.CONTENT_COLUMNS) as copy:
            copy.write_all(loaders.content_row(row) for row in catalogue)
        print(
            f" — {len(catalogue)} titles "
            f"({curated_title_count()} hand-authored in the pool)"
        )

        # -------------------------------------------------------------------
        # Population. Cheap relative to the walk, so generated in full up front:
        # experiment assignment needs every user before any timeline runs.
        # -------------------------------------------------------------------
        print("  [2/6] population", end="", flush=True)
        specs = generate_users(
            rng,
            count=profile.users,
            window_start=window_start,
            window_end=window_end,
            country_ids=dimensions["country_ids"],
            country_tiers=dimensions["country_tiers"],
            device_ids=dimensions["device_ids"],
            channel_ids=dimensions["channel_ids"],
            persona_ids=dimensions["persona_ids"],
            persona_bases=dimensions["persona_bases"],
            genre_names=tuple(dimensions["genre_ids"]),
        )
        print(f" — {len(specs):,} users")

        # -------------------------------------------------------------------
        # Experiments
        # -------------------------------------------------------------------
        print("  [3/6] experiments", end="", flush=True)
        definitions = build_experiments(
            rng,
            count=profile.experiments,
            window_start=window_start,
            window_end=window_end,
        )
        assignments, assignment_lookup = assign_users(definitions, specs)
        effects = EffectResolver(definitions, assignment_lookup)
        null_tests = sum(1 for d in definitions if d.true_lift == 0.0)
        print(
            f" — {len(definitions)} tests, {len(assignments):,} assignments "
            f"({null_tests} deliberately null)"
        )

        # -------------------------------------------------------------------
        # Timeline walk. The expensive phase, chunked to bound memory.
        # -------------------------------------------------------------------
        print("  [4/6] simulating timelines")
        selector = ContentSelector(catalogue, dimensions["genre_names"])
        intensity_tables = build_intensity_tables(specs, window_start, window_end)
        availability: dict[int, list[object]] = {}
        genre_names = tuple(dimensions["genre_ids"])

        session_id = 1
        totals = {"sessions": 0, "events": 0, "subscriptions": 0, "churned": 0, "premium": 0}

        for offset in range(0, len(specs), CHUNK_USERS):
            chunk = specs[offset : offset + CHUNK_USERS]
            user_rows: list[object] = []
            session_rows: list[object] = []
            event_rows: list[object] = []
            subscription_rows: list[object] = []

            for spec in chunk:
                timeline, session_id = simulate_user(
                    rng,
                    spec,
                    window_start=window_start,
                    window_end=window_end,
                    window_end_utc=window_end_utc,
                    selector=selector,
                    intensity=intensity_tables[spec.country_name],
                    availability=availability,  # type: ignore[arg-type]
                    genre_names=genre_names,
                    device_form_factors=dimensions["device_form_factors"],
                    plan_ids=dimensions["plan_ids"],
                    plan_prices=dimensions["plan_prices"],
                    plan_names=dimensions["plan_names"],
                    effects=effects,
                    session_id_start=session_id,
                    validate=args.validate,
                )

                # The walk decides these three, so the user row can only be
                # rendered now — which is the whole reason for chunking.
                spec.is_premium = timeline.is_premium
                spec.last_seen_at = timeline.last_seen_at
                spec.churned_at = timeline.churned_at

                user_rows.append(user_row(spec))
                session_rows.extend(row.as_row() for row in timeline.sessions)
                event_rows.extend(row.as_row() for row in timeline.events)
                subscription_rows.extend(row.as_row() for row in timeline.subscriptions)

                totals["sessions"] += len(timeline.sessions)
                totals["events"] += len(timeline.events)
                totals["subscriptions"] += len(timeline.subscriptions)
                totals["churned"] += 1 if timeline.churned_at else 0
                totals["premium"] += 1 if timeline.is_premium else 0

            _load_chunk(
                conn,
                users=user_rows,
                sessions=session_rows,
                events=event_rows,
                subscriptions=subscription_rows,
            )

            done = min(offset + CHUNK_USERS, len(specs))
            elapsed = time.perf_counter() - started
            rate = done / elapsed if elapsed > 0 else 0
            remaining = (len(specs) - done) / rate if rate > 0 else 0
            print(
                f"        {done:>7,}/{len(specs):,} users  "
                f"{totals['events']:>10,} events  "
                f"{elapsed:>5.0f}s elapsed  ~{remaining:.0f}s left",
                flush=True,
            )

        # -------------------------------------------------------------------
        # Experiments load last: assignments reference users.
        # -------------------------------------------------------------------
        print("  [5/6] finalising", end="", flush=True)
        with loaders.CopyLoader(
            conn, "core.experiments", loaders.EXPERIMENT_COLUMNS
        ) as copy:
            copy.write_all(definition.as_row() for definition in definitions)

        with loaders.CopyLoader(
            conn, "core.experiment_assignments", loaders.ASSIGNMENT_COLUMNS
        ) as copy:
            copy.write_all(assignment.as_row() for assignment in assignments)

        loaders.reset_sequences(conn)
        conn.commit()
        print(" — committed")

        # ANALYZE and the MV refresh run after the commit: both want the
        # committed snapshot, and neither should be able to roll the load back.
        loaders.analyze(conn)
        conn.commit()

        if not args.no_refresh:
            print("  [6/6] refreshing analytics views", end="", flush=True)
            timings = loaders.refresh_analytics(conn)
            conn.commit()
            total_ms = sum(duration for _, duration in timings)
            print(f" — {len(timings)} views in {total_ms / 1000:.1f}s")
        else:
            print("  [6/6] skipped view refresh (--no-refresh)")
            print("        the API will return 503 until you run `make refresh`")

        # -------------------------------------------------------------------
        # Verification
        # -------------------------------------------------------------------
        counts = loaders.row_counts(conn)
        stray = loaders.default_partition_count(conn)
        elapsed = time.perf_counter() - started

        print(f"\n  Loaded in {elapsed:.0f}s\n")
        for table, count in counts.items():
            print(f"    {table:<24} {count:>12,}")

        churn_rate = totals["churned"] / max(len(specs), 1)
        premium_rate = totals["premium"] / max(len(specs), 1)
        print(
            f"\n    churned {churn_rate:.1%} of users, "
            f"{premium_rate:.1%} premium at window end"
        )

        if stray:
            # Surfaced rather than logged quietly: rows here mean a partition
            # boundary bug, and the tests assert this stays zero.
            print(
                f"\n  WARNING: {stray:,} events landed in core.events_default, "
                "meaning some event_time fell outside every declared monthly "
                "partition. Check the window bounds."
            )

        if args.report:
            print("\n  Writing data-quality report...")
            from seeder.report import write_report

            path = write_report(conn)
            print(f"  {path}")

        print()

    except Exception:
        conn.rollback()
        logger.exception("seed_failed")
        print("\n  Seed failed and was rolled back. The database is unchanged.\n")
        return 1
    finally:
        conn.close()

    return 0


def main(argv: list[str] | None = None) -> int:
    """Console entrypoint.

    Args:
        argv: Argument list. Defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code.
    """
    args = _parse_args(argv)

    configure_logging()
    # Faker and SQLAlchemy would otherwise interleave with the progress display.
    silence_loggers(["faker", "sqlalchemy.engine", "sqlalchemy.pool"])

    if args.profile is not None:
        # Keep the settings object consistent with the override, so anything
        # reading the profile later agrees with what actually ran.
        get_settings().seed.profile = SeedProfile(args.profile)

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
