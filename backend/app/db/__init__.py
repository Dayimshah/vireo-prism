"""Database access: engine lifecycle, typed models, request-scoped dependencies.

The ORM models in :mod:`app.db.models` describe the schema for autogenerate,
tooling and tests. They are not the read path — analytical queries live in
``app/sql/queries/`` and execute through SQLAlchemy Core. See the module docstring
in :mod:`app.db.models` for why.
"""

from __future__ import annotations
