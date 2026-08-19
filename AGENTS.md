# AGENTS.md

This file provides guidance to agents when working with code in this repository.

---

## Business Purpose

ArtistHub is a full-stack MVP platform for independent musicians. It provides:
- **Music discovery** — fans browse and purchase music releases
- **Artist social posts** — artists publish updates; fans read them
- **Fan engagement** — fans follow artists and track order history
- **Merchandise** — artists list products; fans simulate purchases (no real payment processor)

There are two distinct user roles: **Artist** (creates content) and **Fan** (consumes content). They are separate database models with separate registration and login pages. Purchases are simulated — a "Buy" action records an `Order` row; no payment gateway is integrated.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11 · Flask 3.x |
| Auth | Flask-Login (server-side sessions, session cookies) |
| ORM | Flask-SQLAlchemy (SQLAlchemy ORM — no raw SQL) |
| Validation | marshmallow (all POST/PUT bodies validated before DB access) |
| Password hashing | flask-bcrypt |
| CORS | Flask-CORS (restricted to frontend origin — never `*` in production) |
| Database | SQLite (`artisthub.db`) for MVP; swap to PostgreSQL via one config change |
| Frontend | Static SPA — plain HTML/CSS/JS (no framework, no Jinja2 templates) |
| Container | Docker + nginx (`docker-compose up` starts the full stack) |
| CI | GitHub Actions — lint + test on every push/PR to `main` |
| Linter | flake8 (PEP 8 compliance) |
| Test framework | pytest + pytest-cov |

---

## Project Structure

```
artisthub-platform/
├── backend/
│   ├── app/
│   │   ├── __init__.py        # create_app() factory — single entry point
│   │   ├── config.py          # DevelopmentConfig / TestingConfig / ProductionConfig
│   │   ├── extensions.py      # db and login_manager singletons (imported by models + routes)
│   │   ├── models/            # One file per model: artist, fan, release, post, merchandise, order, follow
│   │   ├── routes/            # One Blueprint per domain: auth, artists, releases, posts, merch, orders
│   │   └── utils/
│   │       └── responses.py   # success() / error() — MUST be used for every JSON response
│   ├── tests/                 # pytest tests; conftest.py provides all fixtures
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── run.py                 # Entry point: calls create_app()
├── frontend/
│   ├── js/
│   │   └── api.js             # Central fetch wrapper — ALL frontend HTTP calls go through here
│   └── css/
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── nginx.conf             # Serves frontend/; proxies /api/* to Flask on port 5000
└── .github/workflows/ci.yml
```

---

## Coding Standards

### Python (Backend)
- Follow **PEP 8** strictly; run `flake8` before committing
- Every module, class, and function must have a **docstring**
- Use **type hints** on all function signatures
- Use the **app factory pattern** — `create_app()` is the only place extensions are initialised and Blueprints are registered; never import `app` directly
- Import `db` and `login_manager` from `app.extensions`, never re-instantiate them
- Use `abort(403)` — not a manual JSON response — when ownership checks fail
- Never use raw SQL strings; always use SQLAlchemy ORM queries

### JavaScript (Frontend)
- All HTTP calls must go through `frontend/js/api.js` — never call `fetch()` directly in a page script
- `api.js` must attach `credentials: "include"` on every request (session cookie requirement)
- Add inline comments to any non-obvious async logic
- No external JS libraries or CDN imports in the MVP

### General
- No dead code, no commented-out blocks committed to `main`
- `.env` is never committed; `.env.example` with placeholder values is always kept up to date
- All secrets (especially `SECRET_KEY`) are loaded from environment variables only

---

## REST API Conventions

- All endpoints are prefixed `/api`
- All request and response bodies are `application/json`
- **Every** response must use the helpers from `app/utils/responses.py`:
  ```python
  # Success
  return success(data={"artist": artist.to_dict()}, status=200)

  # Error
  return error(message="You do not own this release.", status=403)
  ```
  This produces a consistent envelope:
  ```json
  { "status": "success", "data": { ... } }
  { "status": "error",   "error": "..." }
  ```
- Use standard HTTP verbs: `GET` (read), `POST` (create), `PUT` (update), `DELETE` (remove)
- Use standard HTTP status codes: `200`, `201`, `400`, `401`, `403`, `404`, `409`
- Auth-protected endpoints must check `current_user` via Flask-Login before any DB access
- **Ownership check pattern** — apply this before every mutating operation:
  ```python
  if resource.owner_id != current_user.id:
      abort(403)
  ```
- Paginate all list endpoints; accept `?page=` and `?per_page=` query parameters

---

## Database Conventions

- Use SQLAlchemy ORM for **all** database access — no raw SQL strings anywhere
- Every model lives in its own file under `backend/app/models/`
- All models must define a `to_dict()` method for serialisation — do not access model attributes directly in routes
- All tables use `id` (INTEGER, auto-increment PK), and `created_at` (DATETIME, default `datetime.utcnow`)
- Foreign keys follow the pattern `<table>_id` (e.g., `artist_id`, `fan_id`)
- The `Order` model uses a polymorphic `item_type` (`"release"` or `"merch"`) + `item_id` pattern — do not add separate order tables per item type
- The `Follow` table has a `UNIQUE(fan_id, artist_id)` constraint — enforce this at the model level and catch `IntegrityError` in the route to return `409`
- Database migrations use Flask-Migrate (Alembic); always generate a migration file for schema changes — never call `db.create_all()` in production code
- Switching from SQLite to PostgreSQL requires only changing `SQLALCHEMY_DATABASE_URI` in `config.py`

---

## Security Requirements

These are non-negotiable in every environment:

| Requirement | Implementation |
|---|---|
| Passwords | Hashed with `flask-bcrypt`; never stored or logged as plain text |
| Session cookie | `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"` |
| Secret key | `SECRET_KEY` loaded from env var only — never hardcoded |
| Ownership | Every mutating endpoint checks `current_user.id == resource.owner_id`; returns 403 on failure |
| Input validation | All `POST`/`PUT` bodies validated with a `marshmallow` schema before any DB write |
| CORS | `Flask-CORS` restricted to the known frontend origin — never `origins="*"` in production |
| No raw SQL | SQLAlchemy ORM enforced everywhere to prevent SQL injection |
| Secrets in git | `.env` is in `.gitignore`; `.env.example` is committed with placeholder values only |

---

## Testing Requirements

- **All major changes must include tests.** New endpoints, new models, and new business logic all require corresponding pytest tests.
- Tests live in `backend/tests/`; one file per route module (e.g., `test_releases.py` for `routes/releases.py`)
- `conftest.py` provides all shared fixtures — do not duplicate fixture logic in individual test files:
  - In-memory SQLite test DB (`TESTING=True` config)
  - Pre-registered `artist` and `fan` fixtures
  - Authenticated `artist_client` and `fan_client` test clients
- Run the full suite: `cd backend && pytest --cov=app`
- Run a single test file: `cd backend && pytest tests/test_auth.py -v`
- Run a single test by name: `cd backend && pytest tests/test_auth.py::test_artist_login_success -v`
- CI runs `flake8 app` then `pytest --cov=app` on every push and PR to `main`

---

## Documentation Requirements

- **All major changes must include documentation updates.**
- Every new Python module, class, and function must have a docstring before merging
- Any new environment variable must be added to `.env.example` with a descriptive comment
- Any new API endpoint must be documented in `artisthub-plan.md` Section 4
- `README.md` must stay current with setup steps, environment variables, and Docker instructions
- Non-obvious JavaScript logic must have inline comments
