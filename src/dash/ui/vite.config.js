import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// package.sh writes the resolved build version into plugin.json before
// running `npm run build`, so this always reflects the version being shipped.
const pluginJsonPath = fileURLToPath(new URL('../../plugin.json', import.meta.url));
const pluginVersion = JSON.parse(readFileSync(pluginJsonPath, 'utf-8')).version;

export default defineConfig({
  plugins: [react()],
  base: '/dash/',
  define: { __APP_VERSION__: JSON.stringify(pluginVersion) },
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/dash/api': 'http://localhost:9292',
    },
  },
});
