#!/bin/sh
# Initialise a fresh remote database before starting the application. Render
# may override Docker's CMD, but it still invokes this entrypoint first.
set -eu

echo "[entrypoint] running alembic upgrade head..."
alembic upgrade head
echo "[entrypoint] migrations complete."

if [ "${PRISM_SEED_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] PRISM_SEED_ON_START=true — seeding database (small profile)..."
  python -m seeder --profile small --truncate
  echo "[entrypoint] seeding complete."
fi

exec "$@"
