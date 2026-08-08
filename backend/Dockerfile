# ===========================================================================
# Prism API — multi-stage build
#
#   builder  compiles wheels into a virtualenv (needs a toolchain)
#   runtime  copies only the finished venv (no compiler, non-root user)
#
# Splitting the stages keeps the shipped image free of gcc and header packages,
# which is both smaller and a smaller attack surface.
# ===========================================================================

# --------------------------------------------------------------------------
# Stage 1 — builder
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential is needed for any dependency without a manylinux wheel.
# libpq-dev is deliberately absent: psycopg[binary] bundles its own libpq.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the dependency manifest alone first. Application code changes on every
# commit; dependencies rarely do, so this layer stays cached across rebuilds.
COPY pyproject.toml ./

# Pins are extracted from pyproject rather than duplicated into a
# requirements.txt, so there is one source of truth for dependency versions.
# Written as `python -c` rather than a heredoc: heredocs inside a RUN with line
# continuations are BuildKit-frontend dependent, and a build that works on one
# machine and not another is worse than a slightly denser command.
RUN python -m pip install --upgrade pip setuptools wheel \
 && python -c "import tomllib, pathlib; d = tomllib.loads(pathlib.Path('pyproject.toml').read_text()); print('\n'.join(d['project']['dependencies'] + d['project']['optional-dependencies']['dev']))" > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt

# --------------------------------------------------------------------------
# Stage 2 — runtime
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=0 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=UTC

# curl is retained for container healthchecks and manual debugging only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 prism \
 && useradd --uid 10001 --gid prism --create-home --shell /usr/sbin/nologin prism

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Ordered least- to most-frequently-changed to maximise layer reuse.
COPY --chown=prism:prism alembic.ini ./
COPY --chown=prism:prism alembic ./alembic
COPY --chown=prism:prism pyproject.toml ruff.toml ./
COPY --chown=prism:prism app ./app
COPY --chown=prism:prism seeder ./seeder
COPY --chown=prism:prism tests ./tests

# Writable mount point for the generated Plotly data-quality report.
RUN mkdir -p /app/docs && chown prism:prism /app/docs

USER prism
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

# Overridden by docker-compose (which runs migrations first). This default makes
# `docker run` alone still produce a working API.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
