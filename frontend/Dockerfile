# ===========================================================================
# Prism web — multi-stage build
#
#   builder  installs dependencies, type-checks, and compiles the bundle
#   runtime  nginx serving the static output, no Node present
#
# The runtime stage carries no build toolchain and no source: a compiled SPA is a
# directory of files, and shipping the compiler that produced them adds attack
# surface for nothing.
# ===========================================================================

# --------------------------------------------------------------------------
# Stage 1 — builder
# --------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /build

# Manifests alone first. Source changes on every commit and dependencies rarely do, so
# this layer survives most rebuilds.
#
# `package-lock.json` is copied explicitly rather than by glob: `npm ci` requires it, and
# a glob that silently matched nothing would fall through to a confusing error inside npm
# instead of failing here.
COPY package.json package-lock.json ./

# `npm ci` rather than `npm install` — installs exactly the locked tree and fails if the
# lockfile disagrees with package.json, which is the behaviour a build should have.
# Dev dependencies are needed: TypeScript, Vite and the Tailwind toolchain all run here.
RUN npm ci --no-audit --no-fund

# Ordered least- to most-frequently-changed.
COPY tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY vite.config.ts tailwind.config.ts postcss.config.js ./
COPY index.html ./
COPY public ./public
COPY src ./src

# Vite inlines these into the bundle at build time, so they are build arguments and not
# runtime environment. Changing where the API lives means rebuilding this image —
# restarting the container cannot do it. Defaults match docker-compose.yml.
ARG VITE_API_BASE_URL=http://localhost:8000/api/v1
ARG VITE_APP_NAME=Prism
ARG VITE_ORG_NAME=Vireo

ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_APP_NAME=${VITE_APP_NAME} \
    VITE_ORG_NAME=${VITE_ORG_NAME} \
    NODE_ENV=production

# `npm run build` runs `tsc --build` before `vite build`, so a type error fails the image
# rather than shipping. Vite does not type-check on its own — it strips types and moves on.
RUN npm run build

# --------------------------------------------------------------------------
# Stage 2 — runtime
# --------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Replaced wholesale rather than added to conf.d: the default server block claims port 80
# and would shadow this one on the paths they both match.
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=builder /build/dist /usr/share/nginx/html

# The nginx master stays root so it can bind port 80, which is the port docker-compose.yml
# publishes and healthchecks; its workers drop to the unprivileged `nginx` user, which is
# what the official image's own entrypoint arranges. Running the master unprivileged would
# mean listening on 8080 and changing the compose contract, and that file is delivered.
EXPOSE 80

# wget is busybox's, present in alpine already. `--spider` issues a HEAD and discards the
# body, so the check does not transfer the bundle every 15 seconds.
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
