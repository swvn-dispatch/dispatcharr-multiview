import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// package.sh writes the resolved build version into plugin.json before
// running `npm run build`, so this always reflects the version being shipped.
const pluginJsonPath = fileURLToPath(new URL('../../plugin.json', import.meta.url));
const pluginVersion = JSON.parse(readFileSync(pluginJsonPath, 'utf-8')).version;

export default defineConfig({
  plugins: [react()],
  // Relative base so the build works when the Python server mounts it under
  // any runtime-configured dash_path, not just a fixed prefix baked in here.
  base: './',
  // Forces a single React/Mantine instance even when @swvn-dispatch/dispatch-ui-kit
  // is npm-linked from a local checkout (which has its own copies for its own build).
  resolve: {
    dedupe: ['react', 'react-dom', '@mantine/core', '@mantine/hooks', '@mantine/notifications'],
  },
  define: { __APP_VERSION__: JSON.stringify(pluginVersion) },
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    // Dev-only: the real server mounts the API under the configured
    // dash_path (default "/dash"), so rewrite proxied requests to match.
    // Adjust the rewrite prefix here if you're testing a non-default path.
    proxy: {
      '/api': {
        target: 'http://localhost:9292',
        rewrite: (path) => `/dash${path}`,
      },
    },
  },
});
