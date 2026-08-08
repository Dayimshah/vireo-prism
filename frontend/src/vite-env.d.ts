/// <reference types="vite/client" />

/**
 * Typed build-time configuration.
 *
 * Vite replaces `import.meta.env.VITE_*` with string literals at build time, so these are
 * baked into the bundle and cannot be changed by restarting the container — which is why
 * `frontend/Dockerfile` takes them as `ARG` rather than reading them at runtime.
 *
 * All three are declared required. They have defaults in `docker-compose.yml` and
 * `.env.example`, and `src/api/config.ts` falls back for the dev server, so the failure
 * this guards against is a typo in the name rather than an absent value.
 */
interface ImportMetaEnv {
  /** Absolute base URL of the API, including the `/api/v1` prefix and no trailing slash. */
  readonly VITE_API_BASE_URL: string

  /** Product name shown in the sidebar and the document title. */
  readonly VITE_APP_NAME: string

  /** Organisation the analytics describe. Shown as the sidebar subtitle. */
  readonly VITE_ORG_NAME: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
