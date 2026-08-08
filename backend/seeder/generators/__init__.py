"""Row generators for each table the seeder owns.

Module boundaries follow the tables:

* :mod:`~seeder.generators.users` — the population and its attributes.
* :mod:`~seeder.generators.sessions` — the per-user timeline. Owns the day-by-day
  walk, and therefore owns churn.
* :mod:`~seeder.generators.events` — turns a planned session into event rows.
* :mod:`~seeder.generators.subscriptions` — the logistic conversion model and
  subscription lifecycle.
* :mod:`~seeder.generators.experiments` — A/B definitions, assignment and the
  application of a true effect.

Why the timeline is sequential
------------------------------
It would be faster to generate users, then all sessions, then all subscriptions in
three independent passes. It would also be wrong. Whether a user converts on day
40 depends on how much they watched in the preceding fourteen days; whether they
churn in month three depends on their activity in the preceding twenty-eight days.
Both are strictly backward-looking, so the simulation walks each user's life
forward in time and decides each outcome from information that already exists.

That is what "no lookahead" means in practice, and it is the property that makes
the churn scorecard and the conversion funnel honest rather than circular.
"""

from __future__ import annotations
