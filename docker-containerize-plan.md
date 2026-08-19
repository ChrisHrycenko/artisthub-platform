# Docker Containerization Plan

## Overview

The Docker infrastructure for ArtistHub is already written and committed:
- `docker/Dockerfile` — multi-stage, non-root `artisthub` user, gunicorn, curl healthcheck, EXPOSE 5000
- `docker/docker-compose.yml` — `backend` + `web` (nginx) services, `.env` injection, named volume, healthcheck
- `docker/nginx.conf` — serves `frontend/` static files, reverse-proxies `/api/*` to Flask
- `.dockerignore` — secrets, venv, tests, db excluded
- `README.md` Section 11 — Docker instructions (Option A: `docker run`, Option B: `docker-compose`)
- `backend/app/routes/health.py` — `GET /api/health` endpoint checks app + DB

**Goal:** Build the image locally, run the container, verify `GET /api/health` returns 200, fix any failures, and ensure README.md has complete and accurate Docker instructions.

**Scope:** No new Docker files need to be created. Work is: build → run → test → fix failures → README accuracy pass.

---

## Known risks to address during build/run

1. **`ProductionConfig.__init__` is never called** — `create_app()` calls `flask_app.config.from_object(cfg_class)` (passing the class, not an instance), so the `RuntimeError` guard for a missing `SECRET_KEY` never fires. The app will start silently with the insecure placeholder. This is not a blocking issue for the build/run test, but should be fixed.

2. **`.env` file must exist** before running — docker-compose requires it. If absent, the `env_file` directive causes a startup error. The `.env` file (with a real `SECRET_KEY`) must be created from `.env.example` before building.

3. **`db.create_all()` is called inside `create_app()`** in an `app_context` — this means the first gunicorn worker to start will create the SQLite tables in `/app/instance/artisthub.db`. This works with the named volume mount, but if the volume doesn't pre-exist, the directory must be writable by the `artisthub` user. The Dockerfile already creates `/app/instance` and `chown`s it.

4. **CORS in production** — `ProductionConfig` inherits `CORS_ORIGINS` from `BaseConfig` which doesn't define it, so `flask_app.config.get("CORS_ORIGINS", [])` returns `[]`. The nginx proxy in docker-compose forwards all API requests from the browser via the same origin, so this is not a problem in the composed stack — but it means direct API calls from outside the container will fail CORS. Acceptable for MVP.

5. **gunicorn module path** — CMD is `gunicorn ... run:app`. The WORKDIR is `/app` and `COPY backend/ .` copies `run.py` to `/app/run.py`. This is correct.

---

## Sub-Tasks

---

### Sub-Task 1 — Fix `ProductionConfig` instantiation bug

**Status:** [ ] pending

**Intent**
`ProductionConfig.__init__` is an instance method that enforces a real `SECRET_KEY`, but `create_app()` loads config via `from_object(cfg_class)` — passing the class itself, not an instance. The guard never fires. Fix `create_app()` to instantiate `ProductionConfig` (and all config classes) so `__init__` runs.

**Expected Outcomes**
- `ProductionConfig()` is instantiated before being passed to `from_object()`
- Starting the container without a real `SECRET_KEY` raises a `RuntimeError` at startup
- All existing tests still pass (they use `TestingConfig` which has no special `__init__`)

**Todo List**
1. In `backend/app/__init__.py`, change `cfg_class = config_map.get(env, config_map["default"])` to retrieve the class, then instantiate it: `cfg = cfg_class()` and pass `cfg` to `flask_app.config.from_object(cfg)`
2. Verify `DevelopmentConfig` and `TestingConfig` have no `__init__` — they inherit from `BaseConfig` which also has none — so instantiation is safe for all three
3. Run `cd backend && pytest --cov=app -q` to confirm no regressions

**Relevant Context**
- [`backend/app/__init__.py`](backend/app/__init__.py:65-67) — `cfg_class` lookup and `from_object` call
- [`backend/app/config.py`](backend/app/config.py:96-103) — `ProductionConfig.__init__` guard

---

### Sub-Task 2 — Ensure `.env` is ready for the build

**Status:** [ ] pending

**Intent**
The docker-compose `env_file` directive requires a `.env` file at the repo root. If it does not exist, compose will error before a single container starts. A real `SECRET_KEY` must be present since `FLASK_ENV=production` is enforced in compose.

**Expected Outcomes**
- `.env` exists at the repo root with a cryptographically random `SECRET_KEY`
- `FLASK_ENV` is set to `production` (or omitted — docker-compose overrides it anyway)
- No credentials are committed to git (`.env` is in `.gitignore`)

**Todo List**
1. Check whether `.env` exists at the repo root
2. If absent, create it from `.env.example`: `cp .env.example .env`
3. Generate a real key: `python3 -c "import secrets; print(secrets.token_hex(32))"` and write it as `SECRET_KEY=<value>` in `.env`
4. Confirm `.env` is listed in `.gitignore`

**Relevant Context**
- [`.env.example`](.env.example) — template
- [`.gitignore`](.gitignore) — must exclude `.env`
- [`docker/docker-compose.yml`](docker/docker-compose.yml:36-37) — `env_file: ../.env`

---

### Sub-Task 3 — Build the Docker image

**Status:** [ ] pending

**Intent**
Build the `artisthub-backend` image from the repo root using the multi-stage Dockerfile. Verify the build completes without errors.

**Expected Outcomes**
- `docker build -f docker/Dockerfile -t artisthub-backend .` exits with code 0
- Image appears in `docker images` with tag `artisthub-backend:latest`
- No secrets are baked into any layer (`.dockerignore` excludes `.env`)

**Todo List**
1. From the repo root, run: `docker build -f docker/Dockerfile -t artisthub-backend .`
2. Confirm exit code 0
3. Run `docker images artisthub-backend` to confirm image exists

**Relevant Context**
- [`docker/Dockerfile`](docker/Dockerfile) — multi-stage build, WORKDIR `/app`, CMD gunicorn
- [`.dockerignore`](.dockerignore) — `.env` excluded

---

### Sub-Task 4 — Run the container and test `/api/health`

**Status:** [ ] pending

**Intent**
Run the backend image standalone (without nginx) and confirm `GET /api/health` returns HTTP 200 with `{"status": "success", "data": {"app": "ArtistHub", "status": "ok", ...}}`.

**Expected Outcomes**
- Container starts without errors
- `curl -s http://localhost:5000/api/health` returns HTTP 200
- Response body matches the expected envelope
- Docker HEALTHCHECK transitions to `healthy` within 45 seconds

**Todo List**
1. Run: `docker run --rm --env-file .env -v ah-db:/app/instance -p 5000:5000 --name artisthub-test artisthub-backend`
2. Wait ~5 seconds for gunicorn to bind
3. Run: `curl -s http://localhost:5000/api/health | python3 -m json.tool`
4. Confirm HTTP 200 and correct JSON response
5. If failure: capture `docker logs artisthub-test` and diagnose
6. Stop: `docker stop artisthub-test`

**Relevant Context**
- [`backend/app/routes/health.py`](backend/app/routes/health.py:23) — health endpoint
- [`docker/Dockerfile`](docker/Dockerfile:82-83) — HEALTHCHECK definition

---

### Sub-Task 5 — Fix any failures found in Sub-Task 4

**Status:** [ ] pending

**Intent**
Diagnose and fix any errors discovered when running the container and testing `/api/health`. Common failure modes are documented in the "Known risks" section above.

**Expected Outcomes**
- All identified failures are resolved
- Container re-built and re-tested to confirm `GET /api/health` returns 200
- No new test regressions introduced

**Todo List**
1. Review `docker logs artisthub-test` for Python tracebacks or gunicorn errors
2. Apply targeted fixes (likely candidates: config instantiation, CORS origins, volume permissions)
3. Rebuild: `docker build -f docker/Dockerfile -t artisthub-backend .`
4. Re-run Sub-Task 4 validation
5. Run `cd backend && pytest --cov=app -q` to confirm no regressions

**Relevant Context**
- Known risks listed in the Overview section above

---

### Sub-Task 6 — Verify and complete README.md Docker instructions

**Status:** [ ] pending

**Intent**
`README.md` Section 11 already documents both `docker run` and `docker-compose` workflows. Review it for accuracy after any fixes made in Sub-Tasks 1–5, and add anything missing (e.g. a note about the `SECRET_KEY` enforcement, health endpoint response example).

**Expected Outcomes**
- Section 11 accurately reflects the working build and run commands
- The `SECRET_KEY` enforcement behaviour is documented
- Health endpoint example response is present
- No placeholder text remains

**Todo List**
1. Read `README.md` lines 554–654 (Section 11)
2. Verify all commands in Option A and Option B match what was used in Sub-Tasks 3–4
3. Add or correct any inaccurate steps
4. Add a note: running without a real `SECRET_KEY` in `.env` will raise a `RuntimeError` at startup (now that Sub-Task 1 fixes the instantiation bug)
5. Confirm the health response example JSON is accurate

**Relevant Context**
- [`README.md`](README.md:554) — Section 11 Docker
- [`backend/app/routes/health.py`](backend/app/routes/health.py:57-62) — response shape

---

## File Change Summary

| File | Change |
|---|---|
| `backend/app/__init__.py` | Instantiate config class before `from_object()` so `ProductionConfig.__init__` fires |
| `.env` | Create from `.env.example` with real `SECRET_KEY` (not committed) |
| `README.md` | Minor accuracy updates to Section 11 if needed |

No new files. No new Docker files. Dockerfile, docker-compose.yml, .dockerignore, nginx.conf are already correct and production-quality.
