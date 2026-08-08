import path from 'node:path'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * Vite configuration.
 *
 * No dev proxy. The API sets `PRISM_API__CORS_ORIGINS` to this origin and exposes
 * `X-Cache` / `X-Request-ID` / `X-Response-Time-Ms` via `expose_headers`, so the browser
 * can read them cross-origin. A proxy would make dev same-origin and production
 * cross-origin — meaning a CORS mistake would only ever appear in the built image, which
 * is the one place it is expensive to find.
 */
export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },

  server: {
    port: 5173,
    // Bind on all interfaces so the port is reachable when this runs in a container.
    host: true,
    strictPort: true,
  },

  preview: {
    port: 5173,
    host: true,
    strictPort: true,
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
    // Raised from 500kB: recharts and its d3 dependencies land around 400kB gzipped and
    // the warning is noise rather than a finding. The split below is what actually helps.
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // Charts are the heaviest dependency and are not needed to render the shell,
        // the sidebar, or an error state. Splitting them lets the first paint skip the
        // charting library entirely.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
})
