import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

// Deployment target: GitHub Pages at https://dpdanpittman.github.io/swarm-lib/
// When mabus.ai/swarm/ Caddy block lands, swap to:
//   site: 'https://mabus.ai', base: '/swarm'
export default defineConfig({
  integrations: [tailwind()],
  site: 'https://dpdanpittman.github.io',
  base: '/swarm-lib',
});
