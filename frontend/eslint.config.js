import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

/**
 * ESLint 9 flat config.
 *
 * Type-aware linting is on (`recommendedTypeChecked`), which is slower than the
 * syntax-only preset and the reason this catches the mistakes worth catching here: a
 * floating promise in a fetch wrapper, or `??` applied to a value that is never null.
 *
 * `src/api/schema.d.ts` is generated and excluded — it is 3,000 lines of machine output
 * and linting it would only ever produce findings that cannot be fixed at the source.
 */
export default tseslint.config(
  {
    ignores: ['dist', 'node_modules', 'src/api/schema.d.ts', 'coverage'],
  },

  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,

  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        project: ['./tsconfig.app.json', './tsconfig.node.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // An unawaited fetch in this codebase means a request whose failure lands nowhere.
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',

      // The API returns `T | null` for every undefined figure. `||` would turn a
      // legitimate `0` into the fallback, which is exactly the null-means-zero bug this
      // whole layer exists to avoid.
      '@typescript-eslint/prefer-nullish-coalescing': 'error',

      // Allowed with a leading underscore, for the `_` in a destructure or an unused
      // render prop a Recharts signature requires.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],

      // Consistent `import type`, so a type-only import cannot survive into the bundle.
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
    },
  },

  // Config files run in Node, not the browser.
  {
    files: ['*.config.{ts,js}'],
    languageOptions: {
      globals: globals.node,
    },
  },

  /**
   * Turn type-aware rules off for plain-JS config files.
   *
   * `recommendedTypeChecked` and `stylisticTypeChecked` are spread unscoped above, so they
   * apply to every linted file — including `eslint.config.js` and `postcss.config.js`, which
   * are deliberately plain JS and therefore in neither tsconfig project. A rule such as
   * `await-thenable` needs type information, cannot get it for a file outside a project, and
   * fails the whole run rather than skipping the file:
   *
   *     Error while loading rule '@typescript-eslint/await-thenable': You have used a rule
   *     which requires type information, but don't have parserOptions set to generate type
   *     information for this file.
   *
   * `disableTypeChecked` is a single config object with no `files` key of its own, so the
   * scope has to be supplied here — spreading it bare would switch the type-aware rules off
   * for `src/` too, which is where they earn their cost.
   *
   * Adding these two files to `tsconfig.node.json` was the alternative and is the wrong
   * trade: `postcss.config.js` is plain JS on purpose, because postcss-load-config's
   * TypeScript support depends on an optional loader being installed.
   */
  {
    files: ['**/*.js'],
    ...tseslint.configs.disableTypeChecked,
  },
)
