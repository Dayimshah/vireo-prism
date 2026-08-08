import type { Config } from 'tailwindcss'
import animate from 'tailwindcss-animate'

/**
 * Tailwind configuration.
 *
 * Every colour is a CSS variable rather than a literal, so the light and dark palettes
 * live together in `src/index.css` and a component never branches on theme.
 *
 * Pinned to Tailwind 3.4 deliberately: v4 moves configuration into CSS and drops this
 * file, which the shadcn component idiom used throughout `components/ui` depends on.
 */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },

        // Semantics for a metric moving, not for a value being valid. `positive` means
        // the number went the direction the metric wants — which for churn is down, so
        // the mapping is the tile's job (`higher_is_better` comes from the API) and
        // never the colour's.
        positive: {
          DEFAULT: 'hsl(var(--positive))',
          foreground: 'hsl(var(--positive-foreground))',
          muted: 'hsl(var(--positive-muted))',
        },
        negative: {
          DEFAULT: 'hsl(var(--negative))',
          foreground: 'hsl(var(--negative-foreground))',
          muted: 'hsl(var(--negative-muted))',
        },

        // Neither direction: needs attention, is not a failure. Added for the API's
        // `degraded` health status, which returns 200 — a working service whose database is
        // migrated but unseeded. `negative` would read as an outage and `muted` would let
        // eleven empty dashboards look intentional, so neither existing token fitted.
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
          muted: 'hsl(var(--warning-muted))',
        },

        // Categorical series colours. Eight, because the widest categorical dimension in
        // the dataset is persona (8 values); channel has 12 and its charts rank and
        // truncate rather than plotting every one. Ordered for adjacent-hue separation,
        // so series 1 and 2 stay distinguishable in a stacked area.
        chart: {
          1: 'hsl(var(--chart-1))',
          2: 'hsl(var(--chart-2))',
          3: 'hsl(var(--chart-3))',
          4: 'hsl(var(--chart-4))',
          5: 'hsl(var(--chart-5))',
          6: 'hsl(var(--chart-6))',
          7: 'hsl(var(--chart-7))',
          8: 'hsl(var(--chart-8))',
        },

        // Heatmap ramp for the cohort matrices. A NULL cell is "not yet observable" and
        // must not read as a low value, so it is painted with `--heat-null` — a neutral
        // outside the ramp — rather than with step 0.
        heat: {
          0: 'hsl(var(--heat-0))',
          1: 'hsl(var(--heat-1))',
          2: 'hsl(var(--heat-2))',
          3: 'hsl(var(--heat-3))',
          4: 'hsl(var(--heat-4))',
          5: 'hsl(var(--heat-5))',
          null: 'hsl(var(--heat-null))',
        },
      },

      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },

      fontFamily: {
        sans: ['Inter var', 'Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        // Figures in tables and tiles are tabular so digits align down a column.
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },

      fontSize: {
        // One step below `text-xs`, for axis ticks and cache badges.
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },

      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
        // Used by the skeleton loader. A sweep rather than a pulse, so a loading chart
        // does not look like a chart with animated data.
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [animate],
} satisfies Config
