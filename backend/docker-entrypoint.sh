#!/bin/sh
# Initialise a fresh remote database before starting the application. Render
# may override Docker's CMD, but it still invokes this entrypoint first.
set -eu

alembic upgrade head

if [ "${PRISM_SEED_ON_START:-false}" = "true" ]; then
  python -m seeder --profile small --truncate
fi

exec "$@"
