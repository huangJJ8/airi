import { defineConfig } from 'vitest/config'

// Kept separate from vite.config.ts: vitest 3 bundles its own vite/rollup
// typings which conflict with vite 8 (rolldown) plugin types.
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.spec.ts'],
  },
})
