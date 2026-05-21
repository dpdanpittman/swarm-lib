# swarm-lib site

Marketing / presentation site for `swarm-lib`.

## Stack

- Astro 4
- Tailwind 3
- Dark theme matching the design-doc HTML

## Local dev

```bash
cd site
npm install
npm run dev
```

Default dev URL: `http://localhost:4321/swarm/`

## Build

```bash
npm run build
# Output: site/dist/
```

## Deploy

TBD. Likely matches the `session-essence` pattern (k8s `zaphod` namespace + hostPort + Caddy reverse-proxy), serving at:

- Path-based: `https://mabus.ai/swarm/` (matches `mabus.ai/essence` precedent)
- Or subdomain: `https://swarm.mabus.ai/` (if subdomain preferred)

The `astro.config.mjs` is currently set up for path-based (`base: '/swarm'`). Change to `base: '/'` if going subdomain.

## Pages

- `src/pages/index.astro` — single-page landing, covers hero, three primitives, quickstart, design link, roadmap, footer.

Future pages (when v0.1 actually ships):
- `src/pages/docs/` — API reference, claim protocol, status.json schema, Tribunal port walkthrough
- `src/pages/examples/` — minimal worker examples
