"""Prism — the internal product analytics platform for Vireo.

Vireo is a fictional streaming service; Prism is the analytics platform its data
team built. Everything in this package reads from the ``core`` and ``analytics``
schemas described in ``docs/architecture.md``.

Layering, strictly enforced::

    api/v1/routers  →  services  →  repositories  →  sql/queries/*.sql

Routers validate and serialise. Services hold business logic and statistics.
Repositories own the query name and its bound parameters. The ``.sql`` files hold
every line of SQL in the project. A router never contains SQL; a repository never
contains arithmetic.
"""

from __future__ import annotations

__version__ = "1.0.0"
