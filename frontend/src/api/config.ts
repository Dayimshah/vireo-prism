/**
 * Resolved build-time configuration.
 *
 * One module reads `import.meta.env` so the rest of the app imports plain constants.
 * That keeps the fallbacks in one place and makes the values mockable without touching
 * Vite's injection.
 */

/**
 * Strip trailing slashes from a base URL.
 *
 * Paths are written `/kpi/dau` throughout, so a base ending in `/` would produce
 * `//api/v1//kpi/dau`. Most servers tolerate that; this API's `strict_query` dependency
 * runs after routing, and a doubled slash is a different path that matches no route — so
 * the symptom would be a 404 on an endpoint that plainly exists.
 */
function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, '')
}

/**
 * Where the API lives.
 *
 * The fallback matches `.env.example` and is for `npm run dev` without an env file. It is
 * a real localhost URL rather than a relative path because there is no dev proxy — see
 * the note in `vite.config.ts`.
 */
export const API_BASE_URL = normalizeBaseUrl(
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1',
)

/** Product name. */
export const APP_NAME = import.meta.env.VITE_APP_NAME || 'Prism'

/** Organisation the dashboards describe. */
export const ORG_NAME = import.meta.env.VITE_ORG_NAME || 'Vireo'

/**
 * True in a development build.
 *
 * Used to decide whether to surface a correlation id and a stack in the error panel.
 * A reader of a deployed dashboard cannot act on either.
 */
export const IS_DEV = import.meta.env.DEV
