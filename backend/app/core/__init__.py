"""Cross-cutting infrastructure: configuration, logging, caching, errors, auth.

Nothing in this package imports from :mod:`app.services`, :mod:`app.repositories`
or :mod:`app.api`. The dependency arrow points one way, which is what keeps
``app.core`` importable from the seeder and from tests without dragging in the web
layer.
"""

from __future__ import annotations
