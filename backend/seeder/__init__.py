"""Synthetic data generation for the Vireo dataset.

This package manufactures the company whose behaviour Prism analyses: a catalogue,
a subscriber base, and eighteen months of their clickstream.

The generated data is not random. Each user is assigned one of eight behavioural
personas, and a persona determines how often that user opens the app, how long
they stay, what they search for, how much of a title they finish, and how likely
they are to churn. Subscription conversion is a logistic function of watch time,
completions, acquisition channel and country tier — declared as coefficients in
:mod:`seeder.config`.

That last point is the design intent. The analytics layer knows nothing about
those coefficients, yet the SQL in ``app/sql/queries/`` recovers them: paid social
really does convert worse than referral, Binge Watchers really do retain better
than Casual Viewers. The dashboard therefore shows findings rather than noise,
which is the difference between an analytics project and a chart gallery.

Reproducibility is a hard requirement. ``PRISM_SEED__RANDOM_SEED`` fixes every
draw, so the same seed yields an identical dataset on any machine — which is what
lets the numbers quoted in ``docs/`` and the committed screenshots stay true.

Usage::

    python -m seeder --profile medium --truncate
    python -m seeder.report          # writes docs/data_quality_report.html
"""

from __future__ import annotations
