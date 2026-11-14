# AGENTS.md

Instructions for AI coding agents working in this repository.

## What this is

swarm-lib is a small Python library and CLI that uses the filesystem as the orchestrator for agentic workflows. Tasks are JSON files moved between `pending/`, `claimed/<worker>/`, `done/` and `failed/` with atomic `os.replace`. Run state lives in a `status.json` checkpoint guarded by `fcntl.flock`. A bash worker loop claims tasks and runs any handler executable. There are no runtime dependencies. Status: alpha, version 0.2.0 (see `pyproject.toml` and "Status & roadmap" in `README.md`). The last commit is from May 2026, so treat the project as paused. It is public and licensed AGPL-3.0-or-later.

## Layout

- `swarm_lib/claims.py`: queue primitives (enqueue, try_claim, complete, cross-filesystem check).
- `swarm_lib/status.py`: `status.json` checkpoint read, write and locking.
- `swarm_lib/orphan.py`: heartbeats and the reaper that returns stale claims to `pending/`.
- `swarm_lib/cli.py`: the `swarm-cli` entry point (enqueue, claim, complete, status-init, status-show, status-write, heartbeat, reap, ls).
- `swarm_lib/worker_loop.sh`: the generic worker loop, shipped as package data.
- `swarm_lib/_io.py`: shared file helpers.
- `tests/`: pytest suite, including multi-worker contention tests.
- `examples/`: shell examples (`seven-step-chain`, `hmd-triage`), each with its own README.
- `DESIGN.md` / `DESIGN.html`: the design document. Keep the two in step if you change one.
- `site/`: the Astro 4 + Tailwind documentation site, plus a Kubernetes manifest in `site/k8s/`.
- `.github/workflows/deploy-site.yml.disabled`: a disabled GitHub Pages workflow. It does not run.

## Commands

Python 3.11 or newer. Use a virtual environment inside the clone (`.venv/` is gitignored).

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q                # whole suite, about 1 second
.venv/bin/swarm-cli --help
bash -n swarm_lib/worker_loop.sh   # syntax check for the worker loop
```

There is no linter or type checker configured in the repo. Site commands (from `site/package.json`, not verified here):

```bash
cd site && npm install && npm run dev     # local dev server
cd site && npm run build                  # output in site/dist/
```

## Deployment

The library is not published to PyPI (planned for v1.0 per the README). It is installed from the clone with `pip install -e .`.

The site is built and served from the owner's homelab using `site/k8s/swarm-website.yaml`. The owner runs that. An agent does not build, deploy or apply manifests. A change to a running system is a reviewed script the owner runs.

## Rules for agents

- Ask before large changes. Explain the problem and the proposed fix first. Keep diffs small and surgical.
- Never commit secrets (keys, tokens, passwords, `.env` files). Secrets live in a password manager and are read at run time.
- Never delete this repository or rewrite published history.
- Do not change deployment, CI or infrastructure files (`site/k8s/`, `.github/`, `site/astro.config.mjs`) unless that is the task. Do not re-enable the disabled workflow on your own.
- Run `pytest` and `bash -n swarm_lib/worker_loop.sh` before saying a change is done. Say plainly what was not run.
- Commit messages: short, imperative, say what changed.
- Keep the core library dependency-free (`dependencies = []`). Ask before adding any runtime dependency.
- The correctness rests on POSIX atomic rename on one filesystem and on every `status.json` writer taking the `.status.lock` flock. Do not add a code path that writes `status.json` or moves task files without going through those helpers.
- If you change CLI flags or the `status.json` schema, update `README.md`, `DESIGN.md`, `DESIGN.html` and the site docs pages together, and keep `worker_loop.sh` in step with the CLI it calls.
- The README quotes a test count ("42 tests passing"). Update it if you add or remove tests.
- Handlers in examples must follow the handler hygiene rules in `DESIGN.md`: write only under `$SWARM_RUN_DIR`, treat `payload` as untrusted.

## Must not be committed or made public

This repository is public.

- No secrets, tokens or `.env` files.
- No personal details about the owner beyond what the repo already shows.
- No internal hostnames, IP addresses, tailnet names, machine usernames or paths under `/home` in new content. Describe infrastructure generically ("the homelab cluster", "the reverse proxy").
- No build output (`site/dist/`, `*.egg-info/`, `.venv/`, `node_modules/`). These are already gitignored.
