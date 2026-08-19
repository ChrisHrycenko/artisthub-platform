# ArtistHub MVP — Implementation Plan

## Top-Level Overview

ArtistHub is a full-stack MVP platform for independent musicians. It provides music discovery, social posts, fan engagement, and merchandise — all built on a clean, documented, interview-ready codebase.

**Stack:** Python Flask (backend API) · SQLite (database) · Flask-Login (sessions) · Static HTML/CSS/JS (frontend SPA) · Docker (containerisation)

**User Models:** Two separate models — `Artist` and `Fan` — with dedicated registration and login pages.

**Purchases:** Simulated only. A "Buy" button records an order in the database; no payment processor.

**Architecture Style:** Flask is a pure JSON REST API. The frontend is a static SPA (HTML/CSS/JS) that calls the API. Jinja2 templates are NOT used.

---

## 1. System Architecture

```
┌─────────────────────────┐        ┌─────────────────────────────┐
│   Static Frontend SPA   │◄──────►│   Flask REST API (JSON)     │
│  HTML / CSS / JS        │  HTTP  │  Python 3.11 · Flask 3.x    │
│  (served by nginx/cdn   │        │  Flask-Login · Flask-CORS   │
│   or Flask static/)     │        │  SQLAlchemy · SQLite         │
└─────────────────────────┘        └────────────┬────────────────┘
                                                │
                                         ┌──────▼──────┐
                                         │  SQLite DB  │
                                         │ artisthub.db│
                                         └─────────────┘
```

**Key principles:**
- Flask app is structured as a package with Blueprints (one per domain)
- All responses are `application/json`; errors follow a consistent `{ "error": "..." }` envelope
- CORS is enabled for the static frontend origin
- Flask-Login manages session cookies; endpoints check `current_user` before acting
- SQLAlchemy ORM is used for all DB access — no raw SQL strings

---

## 2. Directory Structure

```
artisthub-platform/
├── backend/
│   ├── app/
│   │   ├── __init__.py            # App factory (create_app)
│   │   ├── config.py              # Config classes (Dev, Test, Prod)
│   │   ├── extensions.py          # db, login_manager singletons
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── artist.py          # Artist model
│   │   │   ├── fan.py             # Fan model
│   │   │   ├── release.py         # MusicRelease model
│   │   │   ├── post.py            # SocialPost model
│   │   │   ├── merchandise.py     # MerchProduct model
│   │   │   ├── order.py           # Order model (purchases)
│   │   │   └── follow.py          # Follow model (fan → artist)
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py            # /api/auth/* (register, login, logout, me)
│   │   │   ├── artists.py         # /api/artists/*
│   │   │   ├── releases.py        # /api/releases/*
│   │   │   ├── posts.py           # /api/posts/*
│   │   │   ├── merch.py           # /api/merch/*
│   │   │   └── orders.py          # /api/orders/*
│   │   └── utils/
│   │       ├── __init__.py
│   │       └── responses.py       # success() / error() JSON helpers
│   ├── tests/
│   │   ├── conftest.py            # pytest fixtures (test app, test client, seeded db)
│   │   ├── test_auth.py
│   │   ├── test_artists.py
│   │   ├── test_releases.py
│   │   ├── test_posts.py
│   │   ├── test_merch.py
│   │   └── test_orders.py
│   ├── migrations/                # Flask-Migrate (Alembic) migrations
│   ├── requirements.txt
│   ├── requirements-dev.txt       # pytest, coverage, httpx
│   └── run.py                     # Entry point: create_app() + app.run()
├── frontend/
│   ├── index.html                 # Landing page / artist discovery
│   ├── artist-register.html
│   ├── artist-login.html
│   ├── artist-dashboard.html      # Manage releases, posts, merch
│   ├── artist-profile.html        # Public-facing artist page
│   ├── fan-register.html
│   ├── fan-login.html
│   ├── fan-dashboard.html         # Following feed, order history
│   ├── browse-releases.html
│   ├── browse-merch.html
│   ├── css/
│   │   ├── reset.css
│   │   └── main.css
│   └── js/
│       ├── api.js                 # Centralised fetch wrapper (base URL, error handling)
│       ├── auth.js                # Login / register / logout helpers
│       ├── artist-dashboard.js
│       ├── artist-profile.js
│       ├── fan-dashboard.js
│       ├── browse.js
│       └── merch.js
├── docker/
│   ├── Dockerfile                 # Backend image
│   ├── docker-compose.yml         # backend + static file server
│   └── nginx.conf                 # Serve frontend static files, proxy /api to Flask
├── .github/
│   └── workflows/
│       └── ci.yml                 # GitHub Actions: lint + test on push/PR
├── .env.example
├── .gitignore
└── README.md
```

---

## 3. Database Schema

### `artist`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | auto-increment |
| email | TEXT UNIQUE NOT NULL | login credential |
| password_hash | TEXT NOT NULL | bcrypt |
| display_name | TEXT NOT NULL | public name |
| bio | TEXT | optional profile bio |
| profile_image_url | TEXT | avatar URL |
| genre | TEXT | primary genre tag |
| location | TEXT | optional city/country |
| created_at | DATETIME | default now() |

### `fan`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| email | TEXT UNIQUE NOT NULL | |
| password_hash | TEXT NOT NULL | |
| username | TEXT UNIQUE NOT NULL | |
| created_at | DATETIME | |

### `music_release`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| artist_id | INTEGER FK → artist.id | |
| title | TEXT NOT NULL | |
| artwork_url | TEXT | |
| genre | TEXT | |
| description | TEXT | |
| streaming_link | TEXT | |
| price | REAL | nullable = free |
| release_date | DATE | |
| created_at | DATETIME | |

### `social_post`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| artist_id | INTEGER FK → artist.id | |
| body | TEXT NOT NULL | post content |
| image_url | TEXT | optional attachment |
| created_at | DATETIME | |

### `merch_product`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| artist_id | INTEGER FK → artist.id | |
| name | TEXT NOT NULL | |
| description | TEXT | |
| price | REAL NOT NULL | |
| image_url | TEXT | |
| stock | INTEGER | NULL = unlimited |
| created_at | DATETIME | |

### `order`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| fan_id | INTEGER FK → fan.id | |
| item_type | TEXT | `"release"` or `"merch"` |
| item_id | INTEGER | FK to release or merch |
| quantity | INTEGER | default 1 |
| total_price | REAL | snapshot at purchase time |
| status | TEXT | `"pending"` / `"completed"` |
| created_at | DATETIME | |

### `follow`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| fan_id | INTEGER FK → fan.id | |
| artist_id | INTEGER FK → artist.id | |
| created_at | DATETIME | |
| UNIQUE | (fan_id, artist_id) | no duplicate follows |

---

## 4. REST API Endpoints

All endpoints are prefixed `/api`. All responses are JSON.
Auth-protected endpoints require an active Flask-Login session cookie.

### Auth — `/api/auth`
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/artist/register` | — | Register new artist |
| POST | `/api/auth/artist/login` | — | Artist login, sets session |
| POST | `/api/auth/fan/register` | — | Register new fan |
| POST | `/api/auth/fan/login` | — | Fan login, sets session |
| POST | `/api/auth/logout` | ✓ | Clear session |
| GET | `/api/auth/me` | ✓ | Return current user + role |

### Artists — `/api/artists`
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/artists` | — | List all artists (paginated) |
| GET | `/api/artists/:id` | — | Get artist profile |
| PUT | `/api/artists/:id` | ✓ Artist (own) | Update profile |
| GET | `/api/artists/:id/releases` | — | Artist's releases |
| GET | `/api/artists/:id/posts` | — | Artist's social posts |
| GET | `/api/artists/:id/merch` | — | Artist's merch |
| GET | `/api/artists/:id/followers` | ✓ Artist (own) | Follower count |

### Releases — `/api/releases`
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/releases` | ✓ Artist | Create release |
| GET | `/api/releases` | — | Browse all releases (paginated, filterable by genre) |
| GET | `/api/releases/:id` | — | Get release detail |
| PUT | `/api/releases/:id` | ✓ Artist (own) | Update release |
| DELETE | `/api/releases/:id` | ✓ Artist (own) | Delete release |

### Posts — `/api/posts`
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/posts` | ✓ Artist | Create post |
| GET | `/api/posts` | — | Browse all posts (paginated) |
| GET | `/api/posts/:id` | — | Get single post |
| DELETE | `/api/posts/:id` | ✓ Artist (own) | Delete post |

### Merch — `/api/merch`
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/merch` | ✓ Artist | Create product |
| GET | `/api/merch` | — | Browse all merch (paginated) |
| GET | `/api/merch/:id` | — | Get product detail |
| PUT | `/api/merch/:id` | ✓ Artist (own) | Update product |
| DELETE | `/api/merch/:id` | ✓ Artist (own) | Delete product |

### Orders — `/api/orders`
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/orders` | ✓ Fan | Simulate purchase (release or merch) |
| GET | `/api/orders` | ✓ Fan | Fan's order history |

### Follows — `/api/follows`
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/follows` | ✓ Fan | Follow an artist |
| DELETE | `/api/follows/:artist_id` | ✓ Fan | Unfollow an artist |
| GET | `/api/follows` | ✓ Fan | List followed artists |

---

## 5. Frontend Pages

| File | Purpose | Key JS calls |
|---|---|---|
| `index.html` | Landing — browse artists, search by genre | `GET /api/artists` |
| `artist-register.html` | Artist signup form | `POST /api/auth/artist/register` |
| `artist-login.html` | Artist login form | `POST /api/auth/artist/login` |
| `artist-dashboard.html` | Manage releases, posts, merch; view follower count | All protected artist endpoints |
| `artist-profile.html` | Public artist page (releases, posts, merch) | `GET /api/artists/:id/*` |
| `fan-register.html` | Fan signup form | `POST /api/auth/fan/register` |
| `fan-login.html` | Fan login form | `POST /api/auth/fan/login` |
| `fan-dashboard.html` | Following feed, order history, follow/unfollow | `GET /api/follows`, `GET /api/orders` |
| `browse-releases.html` | Browse / filter all releases, "Buy" button | `GET /api/releases`, `POST /api/orders` |
| `browse-merch.html` | Browse all merch, "Buy" button | `GET /api/merch`, `POST /api/orders` |

**`js/api.js`** — central fetch wrapper:
- Stores `API_BASE_URL` in one place
- Attaches `credentials: "include"` to every request (for session cookies)
- Parses JSON and surfaces `{ error }` messages to the UI uniformly

---

## 6. Security Considerations

| Area | Approach |
|---|---|
| Passwords | Hashed with `bcrypt` via `flask-bcrypt`; never stored or logged in plain text |
| Session security | `SECRET_KEY` loaded from environment variable; `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"` |
| Ownership checks | Every mutating endpoint checks `current_user.id == resource.owner_id` before acting; returns 403 otherwise |
| Input validation | All POST/PUT request bodies validated with `marshmallow` schemas before touching the DB |
| CORS | `Flask-CORS` restricted to the frontend's origin only; not `*` in production |
| SQL injection | Fully mitigated by SQLAlchemy ORM — no raw string queries |
| `.env` secrets | `.env` file listed in `.gitignore`; `.env.example` committed with placeholder values |
| Rate limiting | Not in MVP scope; noted as a future addition |

---

## 7. Testing Strategy

**Framework:** `pytest` + Flask test client (no external server needed)

**Fixtures (`conftest.py`):**
- In-memory SQLite test database (`TESTING=True`)
- Pre-registered artist and fan fixtures
- Logged-in artist client and logged-in fan client

**Test coverage targets per module:**

| Module | What to test |
|---|---|
| `test_auth.py` | Register (valid, duplicate email, missing fields), login (valid, wrong password), logout, `/me` |
| `test_artists.py` | List, get profile, update own profile, cannot update another artist's profile |
| `test_releases.py` | CRUD by artist, cannot edit another artist's release, unauthenticated browse |
| `test_posts.py` | Create, list, delete own, cannot delete another's |
| `test_merch.py` | CRUD by artist, unauthenticated browse |
| `test_orders.py` | Fan can purchase release, fan can purchase merch, order recorded correctly, artist cannot order |

**CI:** GitHub Actions workflow runs `pytest --cov=app` on every push and pull request to `main`.

---

## 8. Phased Implementation Plan

### Phase 1 — Project Skeleton & Auth
- [x] done

**Intent:** Establish the repo, Flask app factory, database connection, and working auth for both user types before any feature work.

**Expected Outcomes:** Artist and Fan can register and log in. `/api/auth/me` returns the correct user. Tests pass.

**Todo List:**
1. Initialise git repo; create directory structure as specified in Section 2
2. Write `requirements.txt` (Flask, Flask-Login, Flask-Bcrypt, Flask-CORS, Flask-SQLAlchemy, marshmallow)
3. Write `requirements-dev.txt` (pytest, pytest-cov, coverage)
4. Write `app/__init__.py` with `create_app()` factory and config loading
5. Write `app/extensions.py` with `db` and `login_manager` singletons
6. Write `app/config.py` with `DevelopmentConfig` and `TestingConfig`
7. Write `Artist` and `Fan` models
8. Write `app/utils/responses.py` with `success()` and `error()` helpers
9. Write `routes/auth.py` Blueprint with all 6 auth endpoints
10. Write `tests/conftest.py` and `tests/test_auth.py`
11. Write `.env.example` and `.gitignore`
12. Confirm all auth tests pass

---

### Phase 2 — Core Artist Features
- [ ] pending

**Intent:** Allow artists to create and manage their releases, social posts, and merch products.

**Expected Outcomes:** All artist CRUD endpoints are operational. Ownership enforcement is in place.

**Todo List:**
1. Write `MusicRelease`, `SocialPost`, `MerchProduct` models
2. Write `routes/artists.py` Blueprint (profile GET/PUT, nested sub-resources)
3. Write `routes/releases.py` Blueprint (full CRUD)
4. Write `routes/posts.py` Blueprint (create, list, delete)
5. Write `routes/merch.py` Blueprint (full CRUD)
6. Add marshmallow validation schemas for each resource
7. Write tests for all new endpoints

---

### Phase 3 — Fan Features
- [ ] pending

**Intent:** Allow fans to follow artists, browse content, and simulate purchases.

**Expected Outcomes:** Follow/unfollow works. Orders are recorded. Fan dashboard data is served correctly.

**Todo List:**
1. Write `Follow` and `Order` models
2. Write `routes/orders.py` Blueprint (POST purchase, GET history)
3. Add follow/unfollow endpoints to `routes/artists.py` (or a dedicated `routes/follows.py`)
4. Write tests for orders and follows
5. Add follower count to artist profile endpoint response

---

### Phase 4 — Frontend SPA
- [ ] pending

**Intent:** Build all 10 HTML pages and the JS layer that calls the REST API.

**Expected Outcomes:** A working browser UI covering all user flows end-to-end.

**Todo List:**
1. Write `js/api.js` fetch wrapper
2. Write `js/auth.js` (register, login, logout, session check)
3. Build `artist-register.html` + `artist-login.html`
4. Build `fan-register.html` + `fan-login.html`
5. Build `index.html` (artist discovery, genre filter)
6. Build `artist-profile.html` (public view — releases, posts, merch)
7. Build `artist-dashboard.html` (CRUD panels for releases, posts, merch; follower count)
8. Build `fan-dashboard.html` (following list, order history, unfollow)
9. Build `browse-releases.html` (list with Buy button)
10. Build `browse-merch.html` (list with Buy button)
11. Write `css/reset.css` and `css/main.css`

---

### Phase 5 — Docker & CI
- [ ] pending

**Intent:** Containerise the application and wire up automated testing on GitHub Actions.

**Expected Outcomes:** `docker-compose up` starts the full stack. CI passes on every push to `main`.

**Todo List:**
1. Write `docker/Dockerfile` for the Flask backend
2. Write `docker/nginx.conf` (serve frontend static files; proxy `/api` to Flask)
3. Write `docker/docker-compose.yml` (backend + nginx services)
4. Write `.github/workflows/ci.yml` (install deps → lint with `flake8` → `pytest --cov`)
5. Write `README.md` with setup instructions, environment variables, and how to run locally and with Docker

---

### Phase 6 — Polish & Interview Readiness
- [ ] pending

**Intent:** Ensure the codebase is clean, documented, and defensible in a technical interview.

**Expected Outcomes:** Every module has docstrings. README is complete. Code follows PEP 8. No dead code.

**Todo List:**
1. Add module-level and function-level docstrings across all backend files
2. Add inline comments to non-obvious JS logic
3. Add `# type: ignore` or type hints to Python functions where useful
4. Run `flake8` and resolve all warnings
5. Final review of `.env.example`, `README.md`, and `docker-compose.yml`
6. Tag `v0.1.0` in git

---

## 9. Docker Strategy

**Goal:** One command (`docker-compose up`) starts the entire stack locally.

| Service | Image | Role |
|---|---|---|
| `backend` | Custom `Dockerfile` from `python:3.11-slim` | Runs `flask run` or `gunicorn` |
| `web` | `nginx:alpine` | Serves `frontend/` static files; proxies `/api/*` to `backend:5000` |

**`Dockerfile` approach:**
- Copy `requirements.txt`, `pip install` first (layer caching)
- Copy app source
- `CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "run:app"]`

**Volume:** SQLite `artisthub.db` is mounted as a named volume so data persists across container restarts.

**Environment:** `.env` file is passed to the backend container via `env_file:` in `docker-compose.yml`. Never baked into the image.

**Production note:** For a real deployment, swap SQLite for PostgreSQL and add a `db` service to `docker-compose.yml`. The SQLAlchemy ORM makes this a one-line config change.

---

## 10. Future Expansion Opportunities

### IBM watsonx
| Feature | How watsonx fits |
|---|---|
| AI-generated artist bios | `watsonx.ai` text generation via REST API — artist fills in keywords, watsonx writes the bio |
| Music recommendation engine | `watsonx.data` + collaborative filtering on listen/follow events |
| Sentiment analysis on posts | Classify fan engagement tone using `watsonx.ai` NLP models |
| Content moderation | Flag inappropriate posts/merch descriptions before publishing |

**Integration path:** Add a `services/watsonx.py` module that wraps the IBM watsonx REST API. Routes call this service as needed; it is entirely behind the existing endpoint contract, so no frontend changes are required.

### Red Hat OpenShift
| Feature | How OpenShift fits |
|---|---|
| Production container orchestration | Deploy the `docker-compose` services as OpenShift `Deployment` and `Service` objects |
| Horizontal scaling | Scale the Flask backend pods independently of the nginx frontend |
| CI/CD pipelines | OpenShift Pipelines (Tekton) replaces GitHub Actions for production build/deploy |
| Secrets management | OpenShift `Secrets` replace `.env` files; injected as environment variables |
| Persistent storage | Replace SQLite with a PostgreSQL `StatefulSet`; PersistentVolumeClaims handle storage |

**Migration path:** The Docker images built in Phase 5 are OpenShift-compatible with no changes. Add `openshift/` manifests (`Deployment`, `Service`, `Route`, `ConfigMap`, `Secret`) as a new directory.

### Confluent (Apache Kafka)
| Feature | How Confluent fits |
|---|---|
| Real-time follower notifications | Publish a `fan.followed` event; a notification consumer pushes to WebSocket or email |
| Purchase event streaming | Every simulated order publishes to a `orders.created` topic for analytics |
| Artist activity feed | Fan dashboard subscribes to events from followed artists in real time |
| Analytics pipeline | Stream listen/browse events into a data warehouse for artist engagement dashboards |

**Integration path:** Add a `services/events.py` module that wraps the `confluent-kafka` Python client. Each route that triggers a notable event (follow, order, post) calls `events.publish(topic, payload)`. A separate consumer service (new Docker service) handles downstream processing.

---

## Relevant Context

- All backend modules follow the **app factory pattern** — `create_app()` in `app/__init__.py` is the single source of truth for configuration and extension initialisation.
- **Blueprints** are registered in `create_app()`; each Blueprint owns its own URL prefix.
- **Ownership enforcement pattern** to reuse across all mutating endpoints: after fetching a resource, assert `resource.owner_id == current_user.id`, else `abort(403)`.
- **Response envelope pattern** from `utils/responses.py` — every endpoint returns `success(data, status)` or `error(message, status)` — keeps the frontend `api.js` wrapper simple.
- The **`item_type` / `item_id`** design in `Order` is a lightweight polymorphic reference. For the MVP it is sufficient. If order history needs joins, a view or Python-level resolution is used rather than a DB-level polymorphic join.
