"""A/B tests: the catalogue of experiments, and per-variant outcome counts for one.

Two queries. :func:`list_experiments` reads definitions from ``core.experiments`` so a
caller can discover which experiments exist; :func:`get_variant_metrics` measures the
outcomes of one of them.

The catalogue is not decoration. Both of the other experiment endpoints are keyed by
slug, and without a listing a client had no way to obtain a slug it did not already
know — so the only alternatives were a hardcoded list in the frontend, which breaks
silently on the next reseed, or a text box a reader cannot fill in.

:func:`get_variant_metrics` deliberately stops short of drawing a conclusion. It returns
the raw pair every significance test needs — ``n`` and ``successes`` per variant — and
computes no p-value, no confidence interval and no verdict. Those belong in the
statistics layer, where they can be unit-tested against known inputs rather than
buried in SQL that is awkward to verify.

Results are computed, never stored. ``core.experiments`` holds only the test
definition; there is no results table to fall out of date with the event stream,
so a re-run always reflects current data.

The control arm is identified rather than inferred. ``is_control`` is returned as a
column and the ordering puts it first, so the service layer never has to guess
which variant is the baseline by name or by position.

What honest results look like here
---------------------------------
Two of the eight seeded experiments have a true lift of exactly zero, and one is
genuinely negative. That is intentional: a dataset in which every test wins is a
dataset that cannot demonstrate a significance test working. The correct output for
those experiments is "not significant", and the null results are as much a
verification of the pipeline as the positive ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


async def list_experiments(session: AsyncSession) -> list[dict[str, Any]]:
    """Return every experiment defined in the dataset, newest first.

    Takes no arguments beyond the session — no window, no filters. This reads
    definitions from ``core.experiments``, so a reporting window would select tests by
    a period they were not defined in, and the user-scope filters describe people
    rather than tests.

    Returns:
        One row per experiment with keys ``experiment_key``, ``experiment_name``,
        ``hypothesis``, ``primary_metric``, ``status``, ``started_on``, ``ended_on``,
        ``traffic_allocation``, ``variant_count``, ``enrolled_users`` and
        ``duration_days``. Running experiments sort first, then by start date
        descending.

        ``enrolled_users`` is for orientation, not arithmetic: it counts every
        assignment on record, while the per-variant endpoint recomputes its own ``n``
        per arm after applying ``observation_end``. The two disagree by design
        whenever an observation cut-off is in force.

        An empty list means the dataset holds no experiments at all, which is a valid
        state for a database that is migrated but not seeded.
    """
    return await fetch_all(session, "experiments/experiment_catalogue")


async def get_variant_metrics(
    session: AsyncSession,
    experiment_key: str,
    observation_end: date | None = None,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return per-variant outcome counts for one experiment.

    Takes no date range: an experiment's window is a property of the experiment
    itself, carried on ``core.experiments`` as ``started_on`` and ``ended_on``.
    ``observation_end`` only caps how far forward outcomes are counted, which
    matters for a still-running test.

    Args:
        session: A read-only session.
        experiment_key: The experiment's stable slug, e.g.
            ``"autoplay-preview-v2"``. Bound as a parameter, not interpolated.
        observation_end: Last date on which an outcome counts. ``None`` counts every
            outcome on record, which is correct for a completed experiment.
        filters: Optional user-scope filters. Applying these re-segments the
            experiment population and invalidates the original randomisation, so
            treat any filtered result as exploratory rather than as the test's
            outcome.

    Returns:
        One row per variant, control first, with keys ``experiment_key``,
        ``experiment_name``, ``primary_metric``, ``status``, ``started_on``,
        ``ended_on``, ``traffic_allocation``, ``variant``, ``is_control``, ``n``,
        ``successes`` and ``rate_pct``.

        An empty list means no experiment matched ``experiment_key`` — the caller
        should surface that as a 404 rather than as an experiment with no data.
    """
    return await fetch_all(
        session,
        "experiments/experiment_variant_metrics",
        {
            "experiment_key": experiment_key,
            "observation_end": observation_end,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = ["get_variant_metrics", "list_experiments"]
