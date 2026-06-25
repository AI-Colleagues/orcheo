import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { fileURLToPath } from 'url'
import { readFileSync } from 'fs'

// ES Module equivalent of __dirname
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const packageJsonPath = path.resolve(__dirname, './package.json')
const packageJson = JSON.parse(readFileSync(packageJsonPath, 'utf-8')) as {
  version?: string
}
const studioVersion = packageJson.version ?? '0.0.0'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    __ORCHEO_STUDIO_VERSION__: JSON.stringify(studioVersion),
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@lib': path.resolve(__dirname, './src/lib'),
      '@design-system': path.resolve(__dirname, './src/design-system'),
      '@features': path.resolve(__dirname, './src/features'),
    }
  },
  // Fix for CJS/ESM compatibility issues with React 19
  // Force pre-bundling of all dependencies to handle CJS->ESM conversion
  optimizeDeps: {
    // Force re-optimization to ensure all deps are bundled
    force: true,
    // Only exclude test-related packages that shouldn't be in production
    exclude: ['vitest', '@testing-library/react', '@testing-library/jest-dom', '@testing-library/user-event'],
    // Explicitly include all known problematic CJS packages and their subpaths
    include: [
      // React ecosystem
      'react',
      'react-dom',
      'react-dom/client',
      'react/jsx-runtime',
      'react/jsx-dev-runtime',
      // use-sync-external-store (used by zustand, swr, @radix-ui)
      'use-sync-external-store',
      'use-sync-external-store/shim',
      'use-sync-external-store/shim/with-selector',
      'use-sync-external-store/shim/with-selector.js',
      // prop-types (used by many React libs)
      'prop-types',
      'react-is',
      // @rjsf dependencies
      'ajv',
      'ajv-formats',
      'jsonpointer',
      'json-schema-merge-allof',
      // lodash submodules used by @rjsf
      'lodash/get',
      'lodash/set',
      'lodash/has',
      'lodash/omit',
      'lodash/pick',
      'lodash/merge',
      'lodash/cloneDeep',
      'lodash/isEmpty',
      'lodash/isObject',
      'lodash/isString',
      'lodash/isNumber',
      'lodash/isNil',
      'lodash/isPlainObject',
      'lodash/forEach',
      'lodash/toPath',
      'lodash/uniqueId',
      'lodash/keys',
      'lodash/union',
      'lodash/uniq',
      'lodash/times',
      'lodash/reduce',
      'lodash/transform',
      'lodash/difference',
      'lodash/flattenDeep',
      'lodash/isEqualWith',
      'lodash/setWith',
      'lodash/pickBy',
      'lodash/unset',
      'lodash/defaultsDeep',
      'lodash/flatten',
      // Other CJS packages
      'dayjs',
      // zustand and related
      'zustand',
      'zustand/traditional',
      'zustand/middleware',
      // @braintree/sanitize-url (CJS transitive dep of mermaid)
      '@braintree/sanitize-url',
    ]
  },
  build: {
    commonjsOptions: {
      include: [/node_modules/],
      transformMixedEsModules: true,
    }
  },
  server: {
    port: 2026,
    hmr: process.env.VITE_DISABLE_HMR === 'true' ? false : undefined,
    allowedHosts: [
      'localhost',
      ...((process.env.VITE_ORCHEO_ALLOWED_HOSTS || '')
        .split(',')
        .map((h: string) => h.trim())
        .filter(Boolean))
    ]
  }
})
