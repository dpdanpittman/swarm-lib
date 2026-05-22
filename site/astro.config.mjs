import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

// Deployment target: swarm.mabus.ai via Caddy → k8s nginx pod (hostPort 3402)
// See site/k8s/swarm-website.yaml for the deployment manifest. mabus.ai/swarm/*
// 301-redirects to this subdomain via the mabus.ai Caddy block.
export default defineConfig({
  integrations: [tailwind()],
  site: 'https://swarm.mabus.ai',
});
