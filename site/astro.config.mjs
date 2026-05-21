import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

// Deployment target: mabus.ai/swarm/ via Caddy → k8s nginx pod (hostPort 3402)
// See site/k8s/swarm-website.yaml for the deployment manifest.
export default defineConfig({
  integrations: [tailwind()],
  site: 'https://mabus.ai',
  base: '/swarm',
});
