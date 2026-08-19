# ArtistHub

> A full-stack event-driven REST API platform connecting independent musicians with their fans — built with Python Flask, Apache Kafka (Confluent-compatible), Avro-governed event contracts, containerised with Docker, and developed collaboratively with IBM Bob AI.

[![CI](https://github.com/ChrisHrycenko/artisthub-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/ChrisHrycenko/artisthub-platform/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-lightgrey)
![Coverage](https://img.shields.io/badge/coverage-86%25-brightgreen)
![Tests](https://img.shields.io/badge/tests-424%20passing-brightgreen)
![Kafka](https://img.shields.io/badge/Kafka-Confluent%20Compatible-orange)
![Avro](https://img.shields.io/badge/Avro-Schema%20Registry-blueviolet)
![Docker](https://img.shields.io/badge/Docker-ready-blue)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Business Problem](#2-business-problem)
3. [Target Users](#3-target-users)
4. [Feature Set](#4-feature-set)
5. [Screenshots](#5-screenshots)
6. [Architecture](#6-architecture)
7. [Technology Stack](#7-technology-stack)
8. [Database Model](#8-database-model)
9. [API Reference](#9-api-reference)
10. [Local Installation](#10-local-installation)
11. [Docker](#11-docker)
12. [Local Kafka Development Stack](#12-local-kafka-development-stack-phase-7a)
12b. [Avro Serialization and Schema Registry](#12b-avro-serialization-and-schema-registry-phase-7f)
13. [Testing](#13-testing)
14. [Security](#14-security)
15. [Roadmap](#15-roadmap)
16. [Enterprise Evolution Roadmap](#16-enterprise-evolution-roadmap)
17. [IBM Bob Development Methodology](#17-ibm-bob-development-methodology)
18. [Event-Driven Architecture Deep Dive](#18-event-driven-architecture-deep-dive)
19. [Architecture Interview Walkthrough](#19-architecture-interview-walkthrough)
20. [Contributing](#20-contributing)

---

## 1. Project Overview

ArtistHub is a **production-ready full-stack MVP** for independent musicians. It combines a Flask REST API backend with a 12-page vanilla HTML/CSS/JS frontend, containerised with Docker behind nginx, and validated by a 243-test pytest suite at 96% code coverage — all developed using IBM Bob as a collaborative AI engineering partner.

The project demonstrates a complete software engineering lifecycle: domain modelling, layered API design, marshmallow-validated request handling, Flask-Login session auth, a fully wired multi-page frontend SPA, multi-stage Docker packaging, GitHub Actions CI, and professional documentation.

---

## 2. Business Problem

Independent musicians face a fragmented landscape: profile management, release distribution, fan communication, and merchandise sales each require separate tools with no common API. Existing platforms are either too broad (generic social networks) or too narrow (pure streaming services).

ArtistHub solves this by providing a **single, unified REST API** that:

- Gives artists one place to publish releases, write posts, and list merchandise.
- Gives fans one interface to discover artists, follow their activity, and access their catalogue.
- Exposes a clean `/api/*` contract so any frontend — web, mobile, or third-party — can integrate without re-implementing business logic.

---

## 3. Target Users

| Role | Description | Key Actions |
|---|---|---|
| **Artist** | Independent musician with a public profile | Register, publish releases, write posts, list merch, view analytics |
| **Fan** | Music enthusiast browsing the platform | Register, follow artists, browse releases, read posts, view merch |

The two roles are **separate database models** with separate authentication flows. An artist session and a fan session cannot be confused — Flask-Login's `user_loader` uses a `"artist-<id>"` / `"fan-<id>"` prefix scheme to distinguish them.

---

## 4. Feature Set

### Artist Features
- **Profile management** — display name, bio, genre, location, profile image URL
- **Music releases** — create, update, delete releases; types: Single / EP / Album / Mixtape / Compilation / Live; optional streaming URL (links to Spotify, SoundCloud, Bandcamp, etc.)
- **Social posts** — publish short-form updates (up to 2 000 characters) with optional image links
- **Merchandise listings** — list products with price (`NUMERIC(10,2)`), description, image URL, and optional inventory tracking
- **Analytics** — follower count, release count, post count, merch count per artist

### Fan Features
- **Registration and authentication** — unique username + email, bcrypt-hashed passwords
- **Follow / unfollow** — subscribe to an artist's content; `UNIQUE(fan_id, artist_id)` constraint prevents duplicates, returns `409` on conflict
- **Discover artists** — paginated artist directory
- **Browse catalogue** — paginated release and merch listings with optional genre filter

### Platform Features
- **Consistent JSON envelope** — every response is `{ "status": "success", "data": {...} }` or `{ "status": "error", "error": "..." }`
- **Paginated list endpoints** — all collection endpoints accept `?page=` and `?per_page=` (capped at 50)
- **Health check endpoint** — `GET /api/health` probes app status and database connectivity; used by Docker and load balancers
- **Multi-stage Docker image** — non-root user, no secrets baked in, gunicorn production server, curl-based `HEALTHCHECK`
- **GitHub Actions CI** — lint (`flake8`) + test (`pytest --cov`) on every push and PR to `main`

---

## 5. Screenshots

> The frontend SPA (Phase 4) is complete. Replace the placeholders below with real screenshots by opening the app via `docker-compose up` and capturing each page.

| View | Preview |
|---|---|
| Artist Profile | `[ screenshot: artist-profile.png ]` |
| Artist Dashboard (releases + merch management) | `[ screenshot: artist-dashboard.png ]` |
| Music Releases Browse | `[ screenshot: releases-browse.png ]` |
| Fan Dashboard (following list + feed) | `[ screenshot: fan-dashboard.png ]` |
| Merch Listing | `[ screenshot: merch-listing.png ]` |

---

## 6. Architecture

```mermaid
flowchart TD
    %% ── Source Control ─────────────────────────────────────────────
    GH["☁ GitHub\nSource Control\n(git push / PR / CI)"]

    %% ── Packaging ───────────────────────────────────────────────────
    subgraph DOCKER["🐳  Docker"]
        direction TB

        subgraph NGINX_BOX["nginx container  (port 8080)"]
            NGINX["nginx\nStatic file server\n+ Reverse proxy"]
        end

        subgraph FLASK_BOX["Flask/gunicorn container  (port 5000)"]
            direction TB
            ROUTES["Flask REST API\n/api/*\n(8 Blueprints)"]

            subgraph SERVICES["Application Services"]
                AUTH["auth\n/api/auth/*"]
                ARTISTS["artists\n/api/artists/*"]
                RELEASES["releases\n/api/releases/*"]
                POSTS["posts\n/api/posts/*"]
                MERCH["merch\n/api/merch/*"]
                FANS["fans\n/api/fans/*"]
                FOLLOWS["follows\n/api/follows/*"]
                HEALTH["health\n/api/health"]
            end

            MARSHMALLOW["marshmallow\nInput Validation"]
            SQLALCHEMY["Flask-SQLAlchemy\nORM"]

            subgraph MODELS["Models"]
                direction LR
                ARTIST_M["Artist"]
                FAN_M["Fan"]
                RELEASE_M["MusicRelease"]
                POST_M["SocialPost"]
                MERCH_M["MerchProduct"]
                FOLLOW_M["Follow"]
            end
        end

        DB[("SQLite\nartisthub.db\n(named volume)")]
    end

    %% ── Clients ─────────────────────────────────────────────────────
    ARTIST_U(["🎸 Artist\n(browser)"])
    FAN_U(["🎧 Fan\n(browser)"])

    %% ── Connections ─────────────────────────────────────────────────
    GH -->|"build image\n(CI / docker build)"| DOCKER

    ARTIST_U -->|"HTTPS request"| NGINX
    FAN_U    -->|"HTTPS request"| NGINX

    NGINX -->|"static files\n(HTML/CSS/JS)"| ARTIST_U
    NGINX -->|"static files\n(HTML/CSS/JS)"| FAN_U
    NGINX -->|"proxy /api/*"| ROUTES

    ROUTES --> MARSHMALLOW
    MARSHMALLOW --> SERVICES
    SERVICES --> SQLALCHEMY
    SQLALCHEMY --> MODELS
    MODELS <-->|"ORM read/write"| DB

    ROUTES -->|"JSON response\n{ status, data }"| NGINX
```

### Request / Response Flow

Every browser request travels the following path:

1. **Browser → nginx** — nginx is the sole public entry point (port 8080). Static assets (`*.html`, `*.css`, `*.js`) are served directly from the `frontend/` volume; no Flask involvement.
2. **nginx → Flask** — Paths matching `/api/*` are proxied to the Flask/gunicorn container on the internal Docker network. nginx forwards `Host`, `X-Real-IP`, `X-Forwarded-For`, and the session `Cookie`.
3. **Flask routing** — gunicorn dispatches to the Blueprint that owns the matched route. Flask-Login validates the session cookie on protected routes and returns `401` for unauthenticated requests.
4. **marshmallow validation** — Every `POST`/`PUT` body is validated against a schema before any database access. Failures return `400` immediately.
5. **Business logic + ownership check** — Route handlers verify `resource.owner_id == current_user.id` before any mutation, calling `abort(403)` on failure.
6. **SQLAlchemy ORM → SQLite** — All DB access goes through the ORM; no raw SQL. The database file is stored in a named Docker volume (`ah-db`), persisting across restarts.
7. **Response envelope** — Every route returns via `success()` / `error()` from `app/utils/responses.py`, producing a consistent `{ "status", "data" | "error" }` envelope.
8. **Source control loop** — GitHub Actions runs `flake8` + `pytest --cov` on every push to `main`, gating merges on green CI.

---

## 7. Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| **Language** | Python 3.11 | Type hints, `match`, `str \| None` — modern, well-supported LTS |
| **Web framework** | Flask 3.0 | Lightweight, explicit routing, no magic; ideal for a JSON API |
| **WSGI server** | gunicorn 22 | Production-grade multi-worker server; never use `flask run` in production |
| **Authentication** | Flask-Login 0.6 | Session-cookie auth; `UserMixin`; role-differentiated `user_loader` |
| **Password hashing** | Flask-Bcrypt 1.0 | bcrypt cost factor; never store or log plain-text passwords |
| **ORM** | Flask-SQLAlchemy 3.1 | Declarative models; no raw SQL; one-line switch to PostgreSQL |
| **Migrations** | Flask-Migrate 4.0 (Alembic) | Version-controlled schema changes; `flask db upgrade` in production |
| **Validation** | marshmallow 3.21 | Schema-first validation; separates input parsing from business logic |
| **CORS** | Flask-CORS 4.0 | Origin-restricted; `credentials: "include"` for session cookies |
| **Database** | SQLite (dev/MVP) → PostgreSQL (prod) | Single `DATABASE_URL` env var swap; no code changes required |
| **Containerisation** | Docker + nginx 1.25 | Multi-stage image; non-root user; named volume for DB persistence |
| **CI** | GitHub Actions | `flake8` lint + `pytest --cov` on every push and PR to `main` |
| **Testing** | pytest 8.2 + pytest-cov | 243 tests, 96% coverage; in-memory SQLite for fast, isolated runs |
| **Linting** | flake8 7.1 | PEP 8 enforcement; zero warnings required before merge |

---

## 8. Database Model

```
artist
├── id                INTEGER  PK  auto-increment
├── email             VARCHAR(255)  UNIQUE  NOT NULL
├── password_hash     VARCHAR(255)  NOT NULL         ← bcrypt, never exposed in API
├── display_name      VARCHAR(100)  NOT NULL
├── bio               TEXT          nullable
├── profile_image_url VARCHAR(500)  nullable
├── genre             VARCHAR(100)  nullable
├── location          VARCHAR(100)  nullable
└── created_at        DATETIME      NOT NULL  default=utcnow

fan
├── id                INTEGER  PK  auto-increment
├── email             VARCHAR(255)  UNIQUE  NOT NULL
├── password_hash     VARCHAR(255)  NOT NULL         ← bcrypt, never exposed in API
├── username          VARCHAR(100)  UNIQUE  NOT NULL
└── created_at        DATETIME      NOT NULL  default=utcnow

music_release
├── id                INTEGER  PK  auto-increment
├── artist_id         INTEGER  FK → artist.id  ON DELETE CASCADE  indexed
├── title             VARCHAR(200)  NOT NULL
├── release_type      VARCHAR(50)   NOT NULL  default="Single"
│                     (Single | EP | Album | Mixtape | Compilation | Live)
├── genre             VARCHAR(100)  nullable
├── description       TEXT          nullable
├── artwork_url       VARCHAR(500)  nullable
├── streaming_url     VARCHAR(500)  nullable         ← external link only
├── release_date      DATE          nullable
└── created_at        DATETIME      NOT NULL  default=utcnow

social_post
├── id                INTEGER  PK  auto-increment
├── artist_id         INTEGER  FK → artist.id  ON DELETE CASCADE  indexed
├── body              VARCHAR(2000)  NOT NULL
├── image_url         VARCHAR(500)   nullable        ← external link only
└── created_at        DATETIME       NOT NULL  default=utcnow

merch_product
├── id                   INTEGER  PK  auto-increment
├── artist_id            INTEGER  FK → artist.id  ON DELETE CASCADE  indexed
├── product_name         VARCHAR(200)   NOT NULL
├── description          TEXT           nullable
├── price                NUMERIC(10,2)  NOT NULL     ← never Float; avoids rounding errors
├── image_url            VARCHAR(500)   nullable     ← external link only
├── inventory_quantity   INTEGER        nullable
│                        (NULL=unlimited, 0=out-of-stock, N=available units)
└── created_at           DATETIME       NOT NULL  default=utcnow

follow
├── id                INTEGER  PK  auto-increment
├── fan_id            INTEGER  FK → fan.id     ON DELETE CASCADE  indexed
├── artist_id         INTEGER  FK → artist.id  ON DELETE CASCADE  indexed
└── created_at        DATETIME  NOT NULL  default=utcnow
    UNIQUE(fan_id, artist_id)  ← DB-level constraint; IntegrityError → 409
```

**Key design decisions:**
- `Artist` and `Fan` are **separate tables** — no single shared user table. This avoids a `role` column and the nullable-field explosion that comes with it.
- All `artist_id` foreign keys have `ON DELETE CASCADE` — deleting an artist atomically removes their releases, posts, merch, and follows.
- `price` uses `NUMERIC(10,2)`, not `FLOAT` — floating-point arithmetic must never be applied to currency values.
- `inventory_quantity = NULL` means unlimited/not tracked; `0` means out of stock. This avoids a boolean + integer pair.
- Switching to **PostgreSQL** requires only changing `DATABASE_URL` in `.env` — no ORM code changes.

---

## 9. API Reference

All endpoints are prefixed `/api`. All request and response bodies are `application/json`. Every response uses the envelope:

```
Success → { "status": "success", "data": { ... } }
Error   → { "status": "error",   "error": "Human-readable message" }
```

### Authentication

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/artist/register` | Public | Register a new artist account |
| `POST` | `/api/auth/artist/login` | Public | Artist login — sets session cookie |
| `POST` | `/api/auth/fan/login` | Public | Fan login — sets session cookie |
| `POST` | `/api/auth/logout` | Required | Clear the current session |
| `GET` | `/api/auth/me` | Required | Return current user id + role |

### Artists

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/artists` | Public | Paginated artist directory |
| `GET` | `/api/artists/<id>` | Public | Single artist profile |
| `POST` | `/api/artists` | Artist | Create / complete own profile |
| `PUT` | `/api/artists/<id>` | Artist (owner) | Update own profile |
| `GET` | `/api/artists/<id>/releases` | Public | Artist's releases, paginated |
| `GET` | `/api/artists/<id>/posts` | Public | Artist's posts, paginated |
| `GET` | `/api/artists/<id>/merch` | Public | Artist's merch, paginated |
| `GET` | `/api/artists/<id>/analytics` | Public | Follower, release, post, merch counts |

### Releases

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/releases` | Public | All releases, paginated; `?genre=` filter |
| `GET` | `/api/releases/<id>` | Public | Single release |
| `POST` | `/api/releases` | Artist | Create a release |
| `PUT` | `/api/releases/<id>` | Artist (owner) | Update own release |
| `DELETE` | `/api/releases/<id>` | Artist (owner) | Delete own release |

### Posts

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/posts` | Public | Global post feed, newest-first, paginated |
| `GET` | `/api/posts/<id>` | Public | Single post |
| `POST` | `/api/posts` | Artist | Publish a post |
| `DELETE` | `/api/posts/<id>` | Artist (owner) | Delete own post |

### Merchandise

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/merch` | Public | All products, paginated |
| `GET` | `/api/merch/<id>` | Public | Single product |
| `POST` | `/api/merch` | Artist | Create a product listing |
| `PUT` | `/api/merch/<id>` | Artist (owner) | Update own listing |
| `DELETE` | `/api/merch/<id>` | Artist (owner) | Delete own listing |

### Fans

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/fans/register` | Public | Register a new fan account |
| `GET` | `/api/fans/<id>` | Public | Fan profile |

### Follows

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/follows` | Fan | Follow an artist |
| `DELETE` | `/api/follows/<artist_id>` | Fan | Unfollow an artist |
| `GET` | `/api/follows` | Fan | List artists the fan follows |

### Health

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/health` | Public | App + database liveness probe |

---

### Example: Register an Artist

```bash
curl -s -X POST http://localhost:5000/api/auth/artist/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "nova@example.com",
    "password": "securepass123",
    "display_name": "Nova Beats",
    "genre": "Electronic",
    "location": "Toronto, ON"
  }' | python3 -m json.tool
```

```json
{
  "status": "success",
  "data": {
    "artist": {
      "id": 1,
      "email": "nova@example.com",
      "display_name": "Nova Beats",
      "bio": null,
      "profile_image_url": null,
      "genre": "Electronic",
      "location": "Toronto, ON",
      "created_at": "2024-01-15T10:30:00",
      "role": "artist",
      "follower_count": 0
    }
  }
}
```

### Example: Publish a Release

```bash
curl -s -X POST http://localhost:5000/api/releases \
  -H "Content-Type: application/json" \
  -b "session=<cookie>" \
  -d '{
    "title": "Midnight Protocol",
    "release_type": "EP",
    "genre": "Electronic",
    "streaming_url": "https://soundcloud.com/novabeats/midnight-protocol"
  }' | python3 -m json.tool
```

```json
{
  "status": "success",
  "data": {
    "release": {
      "id": 1,
      "artist_id": 1,
      "title": "Midnight Protocol",
      "release_type": "EP",
      "genre": "Electronic",
      "description": null,
      "artwork_url": null,
      "streaming_url": "https://soundcloud.com/novabeats/midnight-protocol",
      "release_date": null,
      "created_at": "2024-01-15T10:35:00"
    }
  }
}
```

### Example: Fan Follows an Artist

```bash
curl -s -X POST http://localhost:5000/api/follows \
  -H "Content-Type: application/json" \
  -b "session=<fan-cookie>" \
  -d '{ "artist_id": 1 }' | python3 -m json.tool
```

```json
{
  "status": "success",
  "data": {
    "follow": {
      "id": 1,
      "fan_id": 7,
      "artist_id": 1,
      "created_at": "2024-01-15T11:00:00"
    }
  }
}
```

### Example: Health Check

```bash
curl -s http://localhost:5000/api/health | python3 -m json.tool
```

```json
{
  "status": "success",
  "data": {
    "app": "ArtistHub",
    "status": "ok",
    "environment": "development",
    "database": "ok"
  }
}
```

---

## 10. Local Installation

### Prerequisites

- Python 3.11+ — [python.org/downloads](https://www.python.org/downloads/)
- pip (bundled with Python 3.11+)
- git

```bash
python3 --version   # ≥ 3.11
pip3 --version
git --version
```

### Steps

**1. Clone the repository**

```bash
git clone https://github.com/ChrisHrycenko/artisthub-platform.git
cd artisthub-platform
```

**2. Create and activate a virtual environment**

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**3. Install dependencies**

```bash
cd backend
pip install -r requirements-dev.txt
# Includes pytest, pytest-cov, and flake8 on top of requirements.txt
```

**4. Configure environment variables**

```bash
# From the repo root
cp .env.example .env
```

Generate a secure `SECRET_KEY` and add it to `.env`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Minimum `.env` for local development:

```
SECRET_KEY=<paste-generated-key-here>
FLASK_ENV=development
```

**5. Start the development server**

```bash
# From backend/
python run.py
```

The API is now available at **http://127.0.0.1:5000**

**6. Verify the health endpoint**

```bash
curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool
```

**7. Open the frontend**

In development mode the Flask server also serves the `frontend/` directory as static files (the `static_folder` is resolved relative to `app/__init__.py`). Open your browser at:

```
http://127.0.0.1:5000/index.html
```

Or use the **Docker Option B** (see Section 11) to run the full nginx + backend stack and open `http://localhost:8080`.

---

### Frontend Structure

```
frontend/
├── index.html                  Home page — hero, feature cards
├── browse-artists.html         Paginated artist directory
├── browse-releases.html        Releases with genre filter
├── browse-posts.html           Global social feed
├── browse-merch.html           Merchandise catalogue
├── artist-profile.html         Public artist profile (releases, posts, merch)
├── artist-register.html        Artist registration form
├── artist-login.html           Artist sign-in form
├── artist-dashboard.html       Artist CMS — profile, posts, releases, merch
├── artist-analytics.html       Follower / content count analytics
├── fan-register.html           Fan registration form
├── fan-login.html              Fan sign-in form
├── fan-dashboard.html          Fan home — followed artists + post feed
├── css/
│   ├── reset.css               Minimal CSS reset
│   └── main.css                Design system (variables, layout, components)
└── js/
    ├── api.js                  Central fetch wrapper — all HTTP calls go here
    ├── auth.js                 Session probe + nav state; runs on every page
    ├── browse.js               Shared pagination + card rendering utilities
    ├── browse-artists.js       Artist directory logic
    ├── browse-releases.js      Release browse + genre filter
    ├── browse-posts.js         Social feed logic
    ├── browse-merch.js         Merch browse logic
    ├── artist-profile.js       Profile page + Follow/Unfollow button
    ├── artist-register.js      Registration form handler
    ├── artist-login.js         Login form handler
    ├── artist-dashboard.js     Dashboard CMS — profile, posts, releases, merch CRUD
    ├── artist-analytics.js     Analytics display
    ├── fan-register.js         Fan registration handler
    ├── fan-login.js            Fan login handler
    └── fan-dashboard.js        Fan dashboard — following list + feed
```

**Design principles:** No frameworks, no CDN imports, no build step. All HTTP calls go through `window.api` (api.js) with `credentials: "include"`. All user-supplied strings are passed through `escapeHtml()` before `innerHTML` insertion.

---

## 11. Docker

### Prerequisites

| Tool | Install |
|---|---|
| Docker Engine 24+ or Docker Desktop | [docs.docker.com/get-docker](https://docs.docker.com/get-docker/) |
| Docker Compose v2 (bundled with Docker Desktop) | Verify: `docker compose version` |

```bash
docker --version         # Docker version 24.x or higher
docker compose version   # Docker Compose version v2.x
```

---

### Docker files

| File | Purpose |
|---|---|
| `docker/Dockerfile` | Multi-stage build — deps layer cached separately from source; copies `kafka/schemas/` for Avro serialization |
| `docker/docker-compose.yml` | Full stack: `backend` (gunicorn, port 5000 internal) + `web` (nginx, port 8080) |
| `docker/nginx.conf` | Serves `frontend/` static files; reverse-proxies `/api/*` to Flask |
| `.dockerignore` | Excludes `.env`, `venv/`, `__pycache__`, `tests/`, `*.db`; excludes `kafka/` tooling but **allows `kafka/schemas/`** via negation rule |

### Image security properties

- **No secrets baked in** — `.dockerignore` blocks `.env`; credentials are injected at runtime via `--env-file`
- **Non-root process** — runs as the `artisthub` system user (no root inside the container)
- **Minimal surface** — only `curl` added to `python:3.11-slim`; no shell tooling, no package manager
- **Safe layer caching** — `requirements.txt` is copied before source; pip only re-runs on dependency changes
- **Targeted binary copy** — only `gunicorn` is copied from the deps stage, not the entire `/usr/local/bin`
- **Lean build context** — `kafka/` tooling, `frontend/`, `docker/`, `scripts/` are all excluded; `kafka/schemas/` is explicitly allowed to support Avro serialization
- **`KAFKA_SCHEMAS_DIR=/app/kafka/schemas`** — baked into the image via `ENV`; tells `avro_utils.py` where to find `.avsc` files so path-traversal from the module file (which would resolve incorrectly inside the container) is bypassed

---

### Option A — Backend only (`docker run`)

```bash
# 1. Prepare .env
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"
# paste output as SECRET_KEY in .env

# 2. Build (run from repo root — takes ~60 s on first build)
docker build -f docker/Dockerfile -t artisthub-backend .

# 3. Run detached (logs visible with: docker logs -f artisthub)
docker run -d \
  --env-file .env \
  -e FLASK_ENV=development \
  -v ah-db:/app/instance \
  -p 5000:5000 \
  --name artisthub \
  artisthub-backend

# 4. Wait for gunicorn to bind (~5 s), then test the health endpoint
sleep 5
curl -s http://localhost:5000/api/health | python3 -m json.tool
# Expected response:
# {
#   "data": { "app": "ArtistHub", "database": "ok", "environment": "development", "status": "ok" },
#   "status": "success"
# }

# 5. Test a public API route
curl -s "http://localhost:5000/api/artists?page=1&per_page=3" | python3 -m json.tool

# 6. Check the built-in Docker HEALTHCHECK status (healthy / starting)
docker inspect --format='{{.State.Health.Status}}' artisthub

# 7. Stop and remove the container
docker stop artisthub && docker rm artisthub
```

> **Note — `FLASK_ENV` and `SECRET_KEY`:**
> The `-e FLASK_ENV=development` flag above activates `DevelopmentConfig`, which accepts the
> default `SECRET_KEY` placeholder in `.env`. If you switch to `FLASK_ENV=production` you **must**
> generate a real key first (`python3 -c "import secrets; print(secrets.token_hex(32))"`) —
> `ProductionConfig` raises a `RuntimeError` at boot if the placeholder is detected.

| Flag | Purpose |
|---|---|
| `--env-file .env` | Injects `SECRET_KEY` and `DATABASE_URL` at runtime — never baked into the image |
| `-e FLASK_ENV=development` | Overrides `FLASK_ENV` from `.env`; use `production` with a real secret key |
| `-v ah-db:/app/instance` | Named volume persists the SQLite DB across restarts |
| `-p 5000:5000` | Maps host port 5000 → gunicorn inside the container |
| `-d` | Detached — frees your terminal; follow logs with `docker logs -f artisthub` |

---

### Option B — Full stack (`docker-compose`)

Starts Flask/gunicorn **and** nginx, with nginx serving both the static frontend and the `/api/*` proxy.

> **Prerequisite:** The `.env` file is **mandatory**. `docker-compose.yml` enforces `FLASK_ENV=production`, which activates `ProductionConfig`. That config raises a `RuntimeError` at startup if `SECRET_KEY` is absent or still the default placeholder.

```bash
# 1. Prepare .env (mandatory — see above)
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"
# paste as SECRET_KEY in .env

# 2. Start
docker-compose -f docker/docker-compose.yml up --build

# 3. Verify
curl -s http://localhost:8080/api/health | python3 -m json.tool
# Frontend: http://localhost:8080

# 4. Stop
docker-compose -f docker/docker-compose.yml down

# Remove DB volume too
docker-compose -f docker/docker-compose.yml down -v
```

### Useful commands

```bash
docker ps                              # running containers
docker logs -f artisthub               # tail logs
docker exec -it artisthub /bin/sh      # debug shell (non-root, no bash)
docker rmi artisthub-backend           # force full rebuild on next run
docker volume ls                       # list named volumes
docker inspect artisthub | grep Health # check HEALTHCHECK status
```

### Switching to PostgreSQL

No code changes needed. Add to `.env`:

```
DATABASE_URL=postgresql://user:password@host:5432/artisthub
```

---

## 12. Local Kafka Development Stack (Phase 7A)

> ⚠️ **Kafka event production from ArtistHub is NOT yet implemented.**
> Phase 7A provisions the local broker and topics only. The Flask application
> does not produce any events until Phase 7C (Outbox + producer integration).

This section documents the local event-streaming development environment added in
Phase 7A. It runs alongside the existing ArtistHub stack without modifying any
v0.1.0 files.

### What Phase 7A adds

| File | Purpose |
|---|---|
| `docker/docker-compose.kafka.yml` | Compose override — adds Redpanda, Console, and topic init |
| `docker/redpanda-console-config.yaml` | Redpanda Console configuration (broker + Schema Registry URLs) |
| `docker/.env.kafka.example` | Documents Kafka environment variables for local and cloud use |
| `scripts/validate-phase-7a.sh` | Automated validation script |

### Starting the full stack (ArtistHub + Kafka)

```bash
# From the repo root
docker-compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.kafka.yml \
  up --build
```

| Service | URL | Purpose |
|---|---|---|
| ArtistHub frontend + API | http://localhost:8080 | nginx (unchanged from v0.1.0) |
| Redpanda Console | http://localhost:8082 | Topic browser, consumer lag |
| Kafka broker (host) | localhost:9092 | `rpk` / Python client access |
| Schema Registry | http://localhost:8081 | Avro schema storage (Phase 7B+) |

### Starting Kafka infrastructure only (no Flask / nginx)

```bash
docker-compose -f docker/docker-compose.kafka.yml up
```

### Kafka topics provisioned

| Topic | Partitions | Retention | Events |
|---|---|---|---|
| `artisthub.social` | 6 | 7 days | follows, unfollows, posts |
| `artisthub.catalog` | 6 | 30 days | releases, merch |
| `artisthub.identity` | 3 | 90 days | artist registration, profile updates |
| `artisthub.deadletter` | 3 | 14 days | unprocessable consumer events |

### Inspecting topics (rpk CLI)

```bash
# List topics
docker exec artisthub-redpanda rpk topic list

# Describe a topic (partition count, offsets)
docker exec artisthub-redpanda rpk topic describe artisthub.social

# Produce a test message
echo "hello-kafka" | docker exec -i artisthub-redpanda \
  rpk topic produce artisthub.social --key test

# Consume messages (Ctrl+C to stop)
docker exec artisthub-redpanda \
  rpk topic consume artisthub.social --offset start
```

### Running the automated validation

```bash
# Start the stack first, then:
bash scripts/validate-phase-7a.sh
```

### Teardown

```bash
# Stop all services
docker-compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.kafka.yml \
  down

# Remove Redpanda data volume (resets topic data)
docker-compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.kafka.yml \
  down -v
```

---

## 12b. Avro Serialization and Schema Registry (Phase 7F)

ArtistHub now enforces event contracts at runtime. Every message published to Kafka is serialized using **Apache Avro** governed by **Confluent Schema Registry**. Consumers that receive an incompatible or malformed message cannot silently mishandle it — deserialization fails loudly before business logic is applied.

### Why governed contracts matter

Without a schema registry, any producer can publish structurally invalid JSON and consumers will silently skip or crash. With Avro + Schema Registry:
- **Producers** are rejected at publish time if the event does not match the registered schema.
- **Consumers** receive a typed record — missing required fields are a serialization error, not a runtime `KeyError`.
- **Schema evolution** is controlled: BACKWARD-compatible changes (adding optional fields with defaults) pass; breaking changes (removing required fields, changing types) are rejected by the registry.

### Confluent wire format

Every Kafka message value is prefixed with a 5-byte header:

```
Byte 0       : 0x00  (Confluent magic byte)
Bytes 1–4    : schema_id (big-endian int32, assigned by Schema Registry)
Bytes 5+     : Avro binary payload (schemaless — schema resolved by id)
```

This format is read natively by all Confluent clients and the Redpanda embedded Schema Registry.

### Subject naming — RecordNameStrategy

ArtistHub uses **RecordNameStrategy**, where the Schema Registry subject is derived from the Avro record name — not from the topic name:

```
subject = "<namespace>.<RecordName>"
```

All 12 Phase 7B schemas share the namespace `io.artisthub.events`:

| Event type | Record name | Subject |
|---|---|---|
| `fan.followed.artist` | `FanFollowedArtist` | `io.artisthub.events.FanFollowedArtist` |
| `fan.unfollowed.artist` | `FanUnfollowedArtist` | `io.artisthub.events.FanUnfollowedArtist` |
| `artist.post.created` | `ArtistPostCreated` | `io.artisthub.events.ArtistPostCreated` |
| `artist.post.deleted` | `ArtistPostDeleted` | `io.artisthub.events.ArtistPostDeleted` |
| `artist.release.created` | `ArtistReleaseCreated` | `io.artisthub.events.ArtistReleaseCreated` |
| `artist.release.updated` | `ArtistReleaseUpdated` | `io.artisthub.events.ArtistReleaseUpdated` |
| `artist.release.deleted` | `ArtistReleaseDeleted` | `io.artisthub.events.ArtistReleaseDeleted` |
| `artist.merch.created` | `ArtistMerchCreated` | `io.artisthub.events.ArtistMerchCreated` |
| `artist.merch.updated` | `ArtistMerchUpdated` | `io.artisthub.events.ArtistMerchUpdated` |
| `artist.merch.deleted` | `ArtistMerchDeleted` | `io.artisthub.events.ArtistMerchDeleted` |
| `artist.registered` | `ArtistRegistered` | `io.artisthub.events.ArtistRegistered` |
| `artist.profile.updated` | `ArtistProfileUpdated` | `io.artisthub.events.ArtistProfileUpdated` |

RecordNameStrategy was chosen over TopicNameStrategy because multiple event types share the same topic (e.g. `artisthub.catalog` carries releases, merch, and deletions). One subject per record name avoids subject collisions.

### BACKWARD compatibility mode

All 12 schemas are registered with **BACKWARD** compatibility:
- New schema versions may **add** fields only if they have a default value.
- New schema versions may **never** remove required fields or change field types incompatibly.
- Old consumers (unaware of the new field) still deserialize correctly because the new field defaults are applied by the reader schema.

**Verified schema evolution test (Phase 7F):**

| Change | Result |
|---|---|
| Add `source_device` (nullable, default null) to `FanFollowedArtist` | ✅ BACKWARD-compatible — old consumers read v2 data, new field defaults to null |
| Remove required field `follow_id` from `FanFollowedArtist` | ❌ REJECTED — old consumers cannot reconstruct missing field |

Both tests are exercised in `backend/tests/test_avro_utils.py::TestSchemaCompatibility`.

### Schema Registry configuration

| Environment variable | Description |
|---|---|
| `SCHEMA_REGISTRY_URL` | Registry base URL (default: `http://localhost:8081`) |
| `SCHEMA_REGISTRY_API_KEY` | Basic-auth username — Confluent Cloud only; leave blank locally |
| `SCHEMA_REGISTRY_API_SECRET` | Basic-auth password — Confluent Cloud only; leave blank locally |

For local development with the Redpanda stack, the embedded Schema Registry is available at `http://localhost:8081` with no authentication required.

### Registering schemas against a live registry

When the full Kafka stack is running:

```bash
# Start the full stack
docker-compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.kafka.yml \
  up --build

# Register all 12 schemas
cd kafka
python register_schemas.py

# Verify subjects visible in Schema Registry
curl -s http://localhost:8081/subjects | python3 -m json.tool
# Expected: 12 subjects matching io.artisthub.events.*

# Open Redpanda Console to browse schemas
# http://localhost:8082 → Schema Registry tab
```

### How the relay uses Avro

The outbox relay (`backend/app/services/outbox_relay.py`) now:
1. Reads the JSON event dict from the outbox `payload` column (unchanged DB schema).
2. Calls `KafkaProducerService.produce_avro(event_type, record, ...)`.
3. The producer service resolves the schema, registers it if needed, serializes to Confluent bytes.
4. Publishes the binary Avro message to Kafka.
5. Marks `published_at` only after the broker acknowledgement.

### How consumers use Avro

Both consumers (`analytics_consumer.py`, `notification_consumer.py`) call `parse_message()` which:
- Detects Confluent magic byte `0x00` → calls `avro_utils.decode()` → returns typed dict.
- Falls back to JSON for unit tests (no magic byte).

This means **all existing 377 tests continue to pass** — the test harness sends plain JSON which triggers the JSON fallback path, while production traffic uses the Avro path.

### Known limitations (Phase 7F)

1. **Deserialization failure before event_id extraction**: If a consumer receives a Confluent-framed message with an unknown `schema_id`, deserialization fails before the `event_id` can be read from the payload. The dead-letter record is published with `event_id=null`. This is documented in both consumer modules.
2. **Schema Registry unreachable on first produce**: If Schema Registry is down when the relay starts, `get_or_register_schema_id()` raises `requests.ConnectionError`. The outbox row records `last_error` and will be retried on the next relay cycle.
3. **No Avro for dead-letter**: Dead-letter messages are plain JSON (not Avro). This is by design — the dead-letter topic is consumed by monitoring tooling that should not depend on a domain schema.

---

## 13. Testing

All tests run from `backend/`. The test suite uses an in-memory SQLite database (`TestingConfig`) — no test ever touches the development database.

### Run the full suite

```bash
cd backend
pytest --cov=app -v
```

### Run a single module

```bash
pytest tests/test_releases.py -v
```

### Run a single test

```bash
pytest tests/test_follows.py::TestFollowArtist::test_duplicate_follow_returns_409 -v
```

### Run the linter

```bash
flake8 app   # must produce zero output
```

### Coverage summary

| Module | Coverage |
|---|---|
| `routes/` | 95–100% |
| `models/` | 93–95% |
| `utils/` | 100% |
| `schemas/` | 97–100% |
| **Total** | **96%** |

### CI

GitHub Actions runs `flake8` and `pytest --cov --cov-fail-under=80` on every push and pull request to `main`. A red CI gate blocks merging.

---

## 13. Security

| Concern | Implementation |
|---|---|
| **Password storage** | bcrypt via `flask-bcrypt`; `password_hash` is never returned in any API response or logged |
| **Session cookies** | `SESSION_COOKIE_HTTPONLY=True` (no JS access); `SESSION_COOKIE_SAMESITE="Lax"` (CSRF mitigation) |
| **Secret key** | `SECRET_KEY` loaded from environment variable only; `ProductionConfig.__init__()` raises `RuntimeError` if the default placeholder is detected at startup |
| **Input validation** | Every `POST`/`PUT` body validated by a marshmallow schema before any DB access; validation errors return `400` |
| **Ownership enforcement** | Every mutating route checks `resource.owner_id == current_user.id`; failure calls `abort(403)` (never a manual JSON response) |
| **SQL injection** | Flask-SQLAlchemy ORM used exclusively; no raw SQL strings anywhere in the codebase |
| **CORS** | `Flask-CORS` restricted to configured origins (`CORS_ORIGINS`); `supports_credentials=True` for session cookie passing |
| **Secrets in Docker** | `.env` is in `.dockerignore`; the image contains no credentials; secrets are injected at runtime via `--env-file` |
| **Non-root container** | The gunicorn process runs as the `artisthub` system user, not `root` |
| **Duplicate follow** | `UNIQUE(fan_id, artist_id)` DB constraint is the authoritative guard; `IntegrityError` is caught and surfaced as `409` |

---

## 15. Roadmap

| Phase | Description | Status |
|---|---|---|
| 1 | Project skeleton, app factory, auth models, `/api/health` | ✅ Complete |
| 2 | Artist CRUD — releases, posts, merch, analytics | ✅ Complete |
| 3 | Fan features — follows, simulated orders | ✅ Complete |
| 4 | Frontend SPA — 12 pages (HTML/CSS/JS, api.js wrapper, artist + fan flows) | ✅ Complete |
| 5 | Docker packaging + GitHub Actions CI | ✅ Complete |
| 6 | `v0.1.0` MVP tag, PostgreSQL migration guide | 🔜 Planned |
| 7A | Kafka infra — local Redpanda broker, 4 topics, Redpanda Console | ✅ Complete |
| 7B | Kafka event contracts — 12 Avro schemas, Schema Registry | 🔜 Awaiting approval |
| 7C | Kafka producer — Outbox Pattern, route instrumentation | 🔜 Awaiting approval |
| 7D–7H | Analytics consumer, notification consumer, Confluent Cloud | 🔜 Awaiting approval |
| 8 | Real payment integration (Stripe PaymentIntents) | 🔜 Future |
| 9 | OpenShift deployment + IBM watsonx anomaly detection | 🔜 Future |

---

## 16. Enterprise Evolution Roadmap

> ⚠️ **Future Architecture — partially in progress.** Phase 7A has provisioned a local Redpanda (Kafka-compatible) broker for development. The Flask application does not yet produce events — that is Phase 7C. OpenShift clusters and watsonx services are not implemented. This section describes the full enterprise target state and the rationale for the architectural decisions that make it achievable.

---

### Current State

ArtistHub today is a containerised Flask REST API: a single Docker container serving gunicorn behind nginx, with SQLite for persistence. The architecture is intentionally minimal and correct for proving the domain model and API contract at MVP stage.

Its constraints are equally clear: a single container cannot scale horizontally, SQLite is not suitable for high-concurrency writes, there is no event streaming, no ML-driven personalisation, and no AI governance. Each layer below addresses one of those constraints.

---

### Layer 1 — Container Orchestration: Red Hat OpenShift

**Red Hat OpenShift** is an enterprise Kubernetes distribution that adds integrated CI/CD (OpenShift Pipelines / Tekton), RBAC, built-in security policies, and a developer console on top of upstream Kubernetes. It is available on-premises, in IBM Cloud, AWS, Azure, and GCP — making it the natural foundation for hybrid-cloud deployments.

**Why it follows from the current design:** The existing `docker/Dockerfile` is already the unit of deployment. Moving to OpenShift means converting `docker-compose.yml` service definitions into Kubernetes `Deployment` and `Service` manifests. Nothing in the Flask application changes — `DATABASE_URL` and `SECRET_KEY` are already injected as environment variables; OpenShift Secrets replace the `.env` file.

**Capabilities unlocked:**

| Capability | OpenShift mechanism |
|---|---|
| Horizontal scaling | `HorizontalPodAutoscaler` on CPU or request latency |
| Zero-downtime deploys | Rolling update strategy on the backend `Deployment` |
| Automated TLS | cert-manager + Let's Encrypt; Ingress terminates HTTPS |
| Hybrid-cloud portability | Multi-cluster federation; workloads run identically on-prem and in IBM Cloud |
| Supply-chain security | OpenShift Pipelines builds and signs the image; only signed images are deployable |
| Secrets management | OpenShift Secrets or HashiCorp Vault; `.env` files are never used in production |
| Observability | Prometheus + Grafana built in; `GET /api/health` becomes the pod liveness probe |

**Migration from the current Dockerfile — no application code changes required:**

```
# Today
docker-compose -f docker/docker-compose.yml up --build

# On OpenShift
oc apply -f openshift/manifests/
oc start-build artisthub-backend
```

---

### Layer 2 — Event Streaming: Confluent / Apache Kafka

**Apache Kafka** is a distributed, durable event log. **Confluent** is the enterprise distribution, adding Schema Registry, managed connectors, and a cloud-hosted option (Confluent Cloud). Every meaningful user action on ArtistHub is an event. Today those actions produce only a database write. With Kafka, each action also emits an immutable event record that any number of downstream services can consume independently — without coupling to or querying the primary Flask application.

**Planned event topology:**

| Kafka topic | Producing route | Payload |
|---|---|---|
| `artisthub.follows` | `POST /api/follows` | `{ fan_id, artist_id, timestamp }` |
| `artisthub.releases.created` | `POST /api/releases` | `{ release_id, artist_id, genre, timestamp }` |
| `artisthub.posts.created` | `POST /api/posts` | `{ post_id, artist_id, timestamp }` |
| `artisthub.merch.purchased` | `POST /api/orders` (Phase 7) | `{ order_id, fan_id, artist_id, item_type, total_price, timestamp }` |
| `artisthub.plays` | Frontend beacon (Phase 4) | `{ fan_id, release_id, artist_id, duration_seconds, timestamp }` |

**Integration pattern:** Each producing route calls a thin `emit_event(topic, payload)` helper after the database commit. The helper is a fire-and-forget Kafka producer (e.g. `confluent-kafka-python`). If the broker is unavailable the event is dropped — the primary write has already succeeded and the HTTP response is unaffected.

**Planned consumers:**

- **Real-time analytics** — consumes `artisthub.follows` and `artisthub.plays` to maintain live counters for a streaming dashboard.
- **Recommendation engine** — builds a fan interest graph from play and follow events; feeds the watsonx.ai model described in Layer 3.
- **Notification service** — consumes `artisthub.releases.created`, resolves the artist's follower list, and dispatches release alerts.
- **Data warehouse sink** — Confluent JDBC sink connector streams all topics into IBM Db2 Warehouse or BigQuery for historical analytics and model training.

**Schema governance:** All event schemas are registered in Confluent Schema Registry (Avro or Protobuf). Producers cannot publish a schema-breaking event without a registry approval, preventing silent downstream breakage.

---

### Layer 3 — AI and Analytics: IBM watsonx.ai

**IBM watsonx.ai** is IBM's enterprise AI studio: foundation model access, fine-tuning pipelines, and model deployment on IBM Cloud and on-premises via OpenShift AI. ArtistHub accumulates rich behavioural data — play history, follow graphs, post engagement, genre preferences — that is currently unused beyond simple counts. watsonx.ai would transform that data into personalised, intelligent experiences.

**Planned capabilities:**

#### Artist Analytics and Trend Intelligence
A time-series model trained on `artisthub.plays` and `artisthub.follows` Kafka events would extend the existing `GET /api/artists/<id>/analytics` endpoint to return:
- Audience growth rate and trajectory forecasts
- Genre affinity scores across the fan base
- Optimal release timing recommendations based on historical engagement patterns
- Follower velocity anomaly detection (organic growth vs. coordinated bot activity)

The Flask route would call a watsonx.ai inference endpoint and pass the result through the existing `success()` envelope — no change to the API contract.

#### Fan Recommendations
A collaborative filtering model (or a fine-tuned Granite foundation model) trained on the fan interest graph would power two new endpoints:
- `GET /api/recommendations/releases` — personalised release feed ranked by predicted affinity
- `GET /api/recommendations/artists` — artist discovery ranked by similarity to followed artists

#### AI-Assisted Content
Foundation models accessed via watsonx.ai would provide optional creative assistance to artists:
- **Bio drafting** — artist provides bullet points; model returns a polished profile bio for review
- **Post drafting** — given a new release, the model suggests a social post announcing it
- **Release description generation** — model writes a description from title, genre, and tracklist

These would be thin `POST /api/ai/*` routes that proxy to the watsonx.ai prompt API. The artist always reviews and edits the output before it is published — the model assists, it does not publish autonomously.

---

### Layer 4 — Business Workflow Automation: IBM watsonx Orchestrate

**IBM watsonx Orchestrate** is an AI-powered automation platform that lets users define and run multi-step business workflows using natural language, connecting to enterprise systems through a library of pre-built and custom skills.

**Why it fits ArtistHub:** Independent artists are sole traders managing their own business operations alongside creative work. Orchestrate would reduce that overhead by automating repeatable workflows triggered by platform events.

**Planned workflows:**

| Workflow | Trigger | Automated steps |
|---|---|---|
| **Release campaign** | Artist publishes a release | Draft post via watsonx.ai → artist approves → publish to feed → email newsletter to followers → schedule follow-up post |
| **Merch restock alert** | `inventory_quantity` reaches 0 | Email artist → draft a "back in stock" post → log stockout event to the data warehouse |
| **Monthly analytics digest** | Scheduled (1st of month) | Fetch analytics from watsonx.ai → generate natural-language summary → email digest to artist |
| **Follower milestone** | Count crosses 1 000 / 10 000 | Notify artist → draft a thank-you post → log milestone event |

Orchestrate connects to ArtistHub via a custom skill backed by the existing REST API — calling `POST /api/posts`, `GET /api/artists/<id>/analytics`, and similar endpoints using the artist's session credentials. **No changes to the Flask application are required** to support Orchestrate integration.

---

### Layer 5 — AI Governance: IBM watsonx.governance

**IBM watsonx.governance** is an AI risk and compliance platform that monitors deployed models for bias, drift, explainability, and regulatory compliance. It integrates natively with watsonx.ai and with third-party ML platforms.

**Why governance is non-negotiable for ArtistHub:** Any recommendation model deployed in production carries risks that must be managed continuously, not just at training time:

- **Popularity bias** — a model trained on play counts will systematically under-recommend new or niche artists, reinforcing the success of already-popular acts and suppressing emerging voices. This directly contradicts ArtistHub's purpose.
- **Demographic fairness** — if training data over-represents certain genres or regions, the model may surface systematically worse recommendations for artists from under-represented backgrounds.
- **Silent model drift** — user behaviour changes over time; a model accurate at training time degrades without continuous monitoring.

**Planned governance controls:**

| Control | watsonx.governance mechanism |
|---|---|
| Bias detection | Fairness metrics computed per protected attribute (genre, artist location); alert when disparity exceeds threshold |
| Drift monitoring | Statistical tests on input feature distributions vs. training baseline; automated retraining trigger |
| Explainability | SHAP values surfaced alongside recommendations so artists understand why a fan was or was not recommended to them |
| Immutable audit trail | Every model version, evaluation result, and deployment decision logged for regulatory review |
| Human oversight gate | No model is promoted to production without a governance review approval — a human signs off on the evaluation report |

**Feedback loop:** Each inference call to watsonx.ai passes a `transaction_id`. watsonx.governance correlates that ID with the recommendation served, the user who received it, and any downstream action (did the fan follow the recommended artist?). This closes the evaluation loop for continuous model quality assessment.

---

### Architectural Evolution Summary

The table below maps every current constraint to its enterprise resolution. The right column represents the target state — not the current state.

| Dimension | Current MVP | Enterprise Target |
|---|---|---|
| Deployment | Docker Compose, single host | Red Hat OpenShift, multi-cluster hybrid cloud |
| Database | SQLite, file-based | PostgreSQL on OpenShift (HA, replicated) |
| TLS / ingress | nginx, HTTP only | OpenShift Ingress + cert-manager (HTTPS) |
| Event streaming | None | Confluent Kafka (plays, follows, purchases, posts) |
| Analytics | Static counts | IBM watsonx.ai (ML inference, trend forecasting) |
| Content AI | None | watsonx.ai Granite (bio/post/description drafting) |
| Artist workflows | Manual | watsonx Orchestrate (automated campaign pipelines) |
| Model oversight | None | watsonx.governance (bias, drift, explainability, audit) |

**The Flask REST API, the marshmallow validation layer, and the `success()` / `error()` response envelope are unchanged across this entire evolution.** The API contract established in the MVP is the stable interface that every future service — Kafka producers, watsonx inference consumers, Orchestrate skills — will depend on. Building that contract correctly at MVP stage is what makes the enterprise evolution tractable rather than a rewrite.

---

## 17. IBM Bob Development Methodology

ArtistHub was built using **IBM Bob** — IBM's AI software engineering assistant — as an active collaborator across the full development lifecycle. This section documents how Bob was used, what it produced, and how the developer maintained oversight and responsibility for every decision.

### What is IBM Bob?

IBM Bob is an AI-powered software engineering agent integrated into the development environment. It can read and write code, execute shell commands, run tests, analyse lint output, and engage in multi-turn technical conversations grounded in the actual codebase — not hypothetical or hallucinated code.

---

### How Bob Was Used in This Project

#### Architecture Planning

Bob was engaged at the project outset to reason through the domain model and API surface. Key decisions made collaboratively:

- **Dual-model authentication** — Bob proposed and justified the `Artist` / `Fan` separate table design over a single `User` table with a `role` column, arguing that the nullable-field explosion and role-checking complexity in a shared table outweighs the join simplicity.
- **Polymorphic Order model** — Bob designed the `item_type` (`"release"` | `"merch"`) + `item_id` pattern to avoid a separate order table per product type.
- **`UNIQUE(fan_id, artist_id)` constraint** — Bob identified that a DB-level constraint is more reliable than application-level duplicate checking, and recommended catching `IntegrityError` in the route to surface a clean `409`.

The developer reviewed each proposal, challenged assumptions (e.g. whether a shared user table would simplify the frontend), and approved the final design.

#### Code Generation

Bob generated the initial implementation of all models, routes, schemas, and utilities. Each generated file followed the project's established conventions:

- `success()` / `error()` response envelope from `app/utils/responses.py`
- `db` and `login_manager` imported from `app.extensions` (never re-instantiated)
- Blueprints registered exclusively in `create_app()`
- `@login_required` + ownership `abort(403)` pattern on every mutating route
- marshmallow schema validation before any DB access

The developer read every generated function, verified the logic matched the specification, and requested revisions where behaviour was incorrect or incomplete.

#### Test Generation

Bob generated the full pytest suite — 243 tests across 9 modules — including:

- Happy-path and error-path coverage for every endpoint
- Authentication and role enforcement tests (artist session on fan-only endpoint → `403`, etc.)
- DB constraint tests (`UNIQUE` follow → `409`, cascade delete, etc.)
- Pagination boundary tests (`per_page` capped at 50)

The developer ran each test module as it was generated, reviewed failures, and instructed Bob to fix root causes rather than adjust assertions to pass.

#### Documentation

Bob produced all inline docstrings (module, class, and function level), the `AGENTS.md` coding standards document, and this README. The developer reviewed documentation for technical accuracy and tone, and corrected several places where Bob's phrasing was imprecise about security constraints.

#### Code Review

Before each phase was finalised, Bob performed a structured code review of its own output — identifying issues such as:

- `static_folder="../../frontend"` resolving to a non-existent path inside the Docker container (fixed by adding a runtime `os.path.isdir` check)
- `COPY --from=deps /usr/local/bin` in the multi-stage Dockerfile overwriting the runtime stage's interpreter symlinks (fixed by copying only `gunicorn`)
- `start_period: 10s` in `docker-compose.yml` being shorter than the Dockerfile's `--start-period=15s` (corrected to `20s`)
- Docker Compose healthcheck using `CMD` instead of `CMD-SHELL` (prevents shell operators from being interpreted)

The developer approved each fix after verifying the reasoning independently.

#### Containerisation

Bob wrote the production Dockerfile, `.dockerignore`, and `docker-compose.yml`. The developer reviewed the security properties (non-root user, no secrets in layers, targeted `COPY` from deps stage) and the operational properties (volume mount, `HEALTHCHECK`, gunicorn flags) before approving.

---

### Developer Responsibility

Bob generated code; the developer owned it. Specifically:

- Every schema, model, route, and test was **read and understood** before being committed.
- Bob's architectural proposals were **challenged and debated** — the developer asked "why not X?" before accepting "use Y".
- All test failures were **diagnosed by the developer** before Bob was instructed to fix them — Bob was not permitted to simply adjust assertions to make tests pass.
- Security decisions (session cookie flags, ownership checks, secrets handling) were **independently verified** against Flask-Login and Flask documentation.
- The final commit history reflects **deliberate, reviewed increments** — not a single bulk dump of AI output.

This methodology demonstrates that AI-assisted development, practised with appropriate rigour, accelerates delivery without compromising code quality, security, or developer understanding.

---

## 18. Event-Driven Architecture Deep Dive

This section is intended for technical reviewers — Solutions Architects, Senior Engineers, and IBM/Confluent interviewers — who want to understand exactly why and how ArtistHub evolved from a synchronous REST API to an event-driven platform.

### Why Kafka was introduced

The initial REST architecture was fully functional but had three compounding problems as the platform grew:

1. **Tight coupling** — the HTTP response time depended on all downstream side effects (analytics updates, notification dispatch) completing synchronously. A slow analytics DB write blocked the API response.
2. **No fan-out** — when an artist published a release, there was no mechanism to notify thousands of followers without making the API do O(N) DB inserts per request.
3. **No audit trail** — deleted events were gone. There was no way to replay "what happened" to rebuild read models or debug consumer bugs.

Kafka solved all three: the API commits exactly one outbox row and returns immediately. All downstream effects happen asynchronously, independently, and durably.

### The Transactional Outbox Pattern

The most critical correctness guarantee in this architecture is the **Transactional Outbox Pattern**:

```
Business mutation  ─┐
                    ├─ SAME SQLAlchemy session ─► db.session.commit()
Outbox row          ─┘
```

When a fan follows an artist:
1. The `Follow` row and the `OutboxEvent` row are written in a **single atomic transaction**.
2. If the DB commit fails, neither row exists — no phantom outbox event.
3. If the relay crashes after publishing but before marking `published_at`, re-delivery is safe because consumers deduplicate on `event_id`.
4. If Kafka is completely down, the API continues working — outbox rows accumulate and publish when the broker returns.

**Key invariants:**
- `published_at IS NULL` = pending (relay will attempt delivery)
- `published_at IS NOT NULL` = delivered (relay will not retry)
- `last_error IS NOT NULL` = failed attempt (will be retried next poll)

### Topic Design

| Topic | Partitions | Retention | Events routed here |
|---|---|---|---|
| `artisthub.social` | 6 | 7 days | follow, unfollow, post created/deleted |
| `artisthub.catalog` | 6 | 30 days | release and merch create/update/delete |
| `artisthub.identity` | 3 | 90 days | artist registration, profile update (PII boundary) |
| `artisthub.deadletter` | 3 | 14 days | all consumer-rejected messages |

**Why 6 partitions for social/catalog?** Fan-out events dominate traffic. 6 partitions allow horizontal scaling to 6 parallel consumers per group without rebalancing.

**Why separate identity topic?** `artist.registered` carries an email address. Keeping PII in a separate topic enables independent access control, separate retention policy, and clear audit scope.

**Message key = artist_id** for all events. This routes all events for a given artist to the same partition, preserving per-artist ordering without global ordering overhead.

### Producer Architecture

```
HTTP Request
    │
    ▼
Flask route (routes/*.py)
    │  db.session.add(business_obj)
    │  db.session.add(OutboxEvent)   ← same transaction
    ▼
db.session.commit()
    │
    ▼
HTTP Response (returned immediately — Kafka not in the request path)
    │
    │  (async, separate process)
    ▼
outbox_relay.py polls event_outbox WHERE published_at IS NULL
    │
    ▼
KafkaProducerService.produce_avro()
    │  ├─ avro_utils.record_name_for_event_type()
    │  ├─ avro_utils.get_or_register_schema_id()  → Schema Registry HTTP
    │  ├─ avro_utils.encode()                      → Confluent wire bytes
    │  └─ confluent_kafka.Producer.produce()
    │
    ▼
Broker ACK (delivery callback)
    │
    ▼
OutboxEvent.published_at = now()   db.session.commit()
```

### Avro and Schema Registry

Every Kafka message is **Confluent Avro wire format**:
```
[0x00][schema_id: 4 bytes BE][Avro binary payload]
```

The `schema_id` is looked up from Schema Registry at first produce per event type, then cached for the process lifetime. Consumers use the same id to fetch the reader schema.

**RecordNameStrategy** is used for subject naming:
```
subject = "io.artisthub.events.<RecordName>"
```
This avoids subject collisions when multiple event types share a topic.

**BACKWARD compatibility** mode is enforced:
- Safe: add optional field with a default
- Breaking: remove required field, change field type → **rejected by the registry**

### Consumer Groups

| Consumer group | Subscribed topics | Business effect |
|---|---|---|
| `artisthub.analytics.v1` | social, catalog, identity | Increments per-artist counters in `analytics_state` |
| `artisthub.notifications.v1` | catalog only | Creates `notification` rows (work queue) per follower |

Both groups use **manual offset commits** (enable.auto.commit = false):
```
1. Consume message
2. Deserialize (Avro)
3. Validate envelope fields
4. DB deduplication check (ProcessedEvent table)
5. Apply business side effect
6. INSERT ProcessedEvent row
7. db.session.commit()     ← DB commits here
8. consumer.commit(msg)    ← Kafka offset commits here
```

If step 7 fails, step 8 never runs. On restart, the message is re-delivered. The ProcessedEvent check at step 4 prevents double-counting.

### Idempotency — Two Layers

**Layer 1 — ProcessedEvent table (application level)**
```
ProcessedEvent.event_id  (PRIMARY KEY)
```
Before applying any side effect, the consumer checks whether this `event_id` has already been processed. If it has, the message is skipped and the offset is committed.

**Layer 2 — Database constraints (storage level)**
```sql
-- analytics_state: artist_id is the PK — upsert is idempotent
-- notification: UNIQUE(event_id, fan_id) — prevents duplicate rows
```

Together these layers guarantee exactly-once business effect even if Kafka delivers the same message multiple times (at-least-once delivery).

### Dead-Letter Handling

Messages that cannot be processed are routed to `artisthub.deadletter`. Dead-letter candidates:
- **Malformed message**: missing Confluent magic byte, corrupt Avro binary, missing required envelope fields
- **Missing required payload field**: `artist_id` or `release_id` absent in a release event
- **DB retries exhausted**: 3 attempts with exponential backoff, all failing

Dead-letter records carry full metadata:
```json
{
  "dead_letter_at": "2026-08-19T12:00:00Z",
  "original_topic": "artisthub.catalog",
  "original_partition": 2,
  "original_offset": 1847,
  "event_id": "uuid-or-null",
  "failure_reason": "Avro deserialization error: unknown schema_id 999",
  "original_payload": "..raw bytes or JSON.."
}
```

**Dead-letter messages are plain JSON** — monitoring tooling should not depend on a domain Avro schema.

**Known limitation:** When the Confluent magic byte is present but the schema_id is unknown (e.g. Schema Registry unreachable), deserialization fails before the payload is readable. `event_id` is null in the dead-letter record. This is documented and logged at ERROR level.

### Local Docker Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Docker Compose (docker-compose.yml + docker-compose.kafka.yml)  │
│                                                                  │
│  ┌─────────────────┐   ┌────────────────────────────────────┐   │
│  │  nginx :8080    │   │  Redpanda (Kafka-compatible) :9092 │   │
│  │  ├ static html  │   │  Schema Registry          :8081    │   │
│  │  └ /api/* proxy │   │  Admin API                :9644    │   │
│  └────────┬────────┘   └────────────────────────────────────┘   │
│           │                         ▲  ▼                        │
│  ┌────────▼────────┐   ┌────────────┴──────────┐                │
│  │ Flask/gunicorn  │   │  outbox-relay          │                │
│  │ :5000 internal  │   │  analytics-consumer    │                │
│  │                 │   │  notification-consumer │                │
│  │  SQLite DB      ├──►│  (shared SQLite vol)   │                │
│  └─────────────────┘   └───────────────────────┘                │
│                                                                  │
│  ┌──────────────────┐                                            │
│  │ Redpanda Console │ :8082  (browser UI)                        │
│  └──────────────────┘                                            │
└──────────────────────────────────────────────────────────────────┘
```

All services share a named Docker volume `db-data` mounted at `/app/instance` so the relay and consumers can read the same SQLite database that Flask writes to.

### Future Confluent Cloud Migration Path

Switching from local Redpanda to Confluent Cloud requires **zero application code changes**. Only environment variables change:

| Variable | Local value | Confluent Cloud value |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `redpanda:29092` | `pkc-xxx.region.provider.confluent.cloud:9092` |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | `SASL_SSL` |
| `CONFLUENT_API_KEY` | *(blank)* | your API key |
| `CONFLUENT_API_SECRET` | *(blank)* | your API secret |
| `SCHEMA_REGISTRY_URL` | `http://redpanda:8081` | `https://psrc-xxx.region.provider.confluent.cloud` |
| `SCHEMA_REGISTRY_API_KEY` | *(blank)* | your SR key |
| `SCHEMA_REGISTRY_API_SECRET` | *(blank)* | your SR secret |

The Confluent wire format, subject naming, and BACKWARD compatibility mode are identical between local Redpanda and Confluent Cloud.

### What is currently implemented vs. future

| Feature | Status |
|---|---|
| REST API (all 6 domains) | ✅ Implemented and tested |
| Transactional Outbox | ✅ Implemented and tested |
| Kafka producer (Avro) | ✅ Implemented and tested |
| Analytics consumer | ✅ Implemented and tested |
| Notification consumer (work queue) | ✅ Implemented and tested |
| Schema Registry (local Redpanda) | ✅ Implemented; validated locally |
| Confluent Cloud migration | 🔲 Config-only change (all code ready) |
| Notification delivery worker | 🔲 Future (Phase 7H+) |
| Avro/Confluent Cloud CI validation | 🔲 Future (requires CI secrets) |
| Fan registration events | 🔲 Future (fan.registered event type) |

---

## 19. Architecture Interview Walkthrough

### 30-Second Business Explanation

ArtistHub is a platform for independent musicians. Artists create profiles, publish music releases and social posts, and list merchandise. Fans browse artists, follow the ones they like, and buy their releases and merch. When a fan follows an artist who then drops a new release, the fan gets notified. The platform is a clean domain problem: two user types, content creation, content consumption, and fan engagement — all the classic patterns in one codebase.

---

### 90-Second Solutions Sales Engineer Explanation

> *Suitable for a technical conversation with a non-engineering Confluent SE or IBM account team.*

"ArtistHub started as a standard Flask REST API — straightforward CRUD, session auth, SQLite. It worked fine. But the moment we asked 'what happens when an artist with 50,000 followers drops a new release?' we hit the wall. The HTTP handler would have to query all 50,000 followers, write 50,000 notification rows, and hold the database connection open for all of it — while the API caller is still waiting for a 200 OK.

So we introduced Kafka. Now when an artist publishes a release, the API does exactly two things: it writes the release row and it writes one outbox event, in the same database transaction. The HTTP response returns in milliseconds. The Kafka relay picks up the outbox row asynchronously and publishes an Avro-encoded message to the `artisthub.catalog` topic. Two consumer groups are listening — the analytics consumer updates the artist's release count, and the notification consumer fans out one `notification` row per follower.

We're using Confluent-compatible Avro with Schema Registry. Every event is governed by a schema. The producer cannot publish a malformed event — the schema enforces it. Consumers cannot silently mishandle a corrupt message — deserialization fails and routes to dead-letter. All 12 event types across social, catalog, and identity domains are registered subjects in Schema Registry with BACKWARD compatibility enforced.

The entire Kafka stack runs locally with Redpanda. One environment variable change — `KAFKA_BOOTSTRAP_SERVERS` — points it at Confluent Cloud. No code changes needed."

---

### 3-Minute Technical Architecture Explanation

> *Suitable for a technical architecture panel or senior engineer review.*

**Layer 1 — Request/Response (Flask REST API)**

The HTTP tier is a Flask application with 6 domain Blueprints: auth, artists, releases, posts, merch, follows. Every POST/PUT body is validated by a marshmallow schema before any database access. All responses follow a `{ status, data }` / `{ status, error }` envelope. Flask-Login manages session cookies with HttpOnly + SameSite=Lax. Ownership checks (`abort(403)`) guard every mutating route. The API is completely stateless with respect to Kafka — it never calls a producer directly.

**Layer 2 — Transactional Outbox**

The glue between the synchronous API and the asynchronous event pipeline is the `event_outbox` table. Every route that needs to emit a domain event calls a `build_*` function from `event_factory.py`. This function returns an `OutboxEvent` model instance that is added to the same SQLAlchemy session as the business object. `db.session.commit()` writes both atomically. If the commit fails, there is no orphan event. If the relay crashes after publishing, re-delivery is safe — consumers deduplicate on `event_id`.

**Layer 3 — Kafka Producer**

The `outbox_relay.py` process runs as a separate container. It polls the outbox for rows where `published_at IS NULL`, calls `KafkaProducerService.produce_avro()`, which:
1. Looks up the Avro schema for the event type using `RecordNameStrategy` — `subject = "io.artisthub.events.<RecordName>"`
2. Calls `avro_utils.get_or_register_schema_id()` — registers if new, cached per-process thereafter
3. Serializes to Confluent wire format: `[0x00][schema_id 4 bytes big-endian][Avro binary]`
4. Calls `confluent_kafka.Producer.produce()` with a delivery callback
5. On broker ACK, the callback sets `published_at = now()` in the same DB session

The producer uses `enable.idempotence=True`, `acks=all`, retries=5. All Kafka and Schema Registry credentials come from environment variables — nothing is hardcoded.

**Layer 4 — Consumers**

Two independent consumer processes subscribe to different topic sets:

*Analytics consumer* subscribes to all three topics. It applies counter deltas to `AnalyticsState` rows (one per artist) and serves the `/api/artists/<id>/analytics` endpoint in real time. `enable.auto.commit = False`. The DB commit and the Kafka offset commit happen in strict sequence — DB first, then Kafka. A crash between them causes re-delivery which the `ProcessedEvent` dedup table absorbs.

*Notification consumer* subscribes only to `artisthub.catalog`. On `artist.release.created`, it queries `Follow` for the artist's followers and inserts one `Notification` row per fan. A `UNIQUE(event_id, fan_id)` constraint on the `notification` table provides a second dedup layer. The notification rows are a durable work queue — the delivery worker (email/push) reads them in a future phase.

*parse_message()* in both consumers detects the Confluent magic byte `0x00` and routes to `avro_utils.decode()`, falling back to JSON for unit tests. This means all 424 existing tests pass against plain JSON without requiring a live registry.

**Idempotency chains**

At-least-once Kafka delivery means any message can arrive twice. Three independent guards prevent double-processing:
- `ProcessedEvent.event_id` (PRIMARY KEY) — checked before any side effect
- `OutboxEvent.event_id` (UNIQUE) — prevents duplicate relay publishing
- `Notification UNIQUE(event_id, fan_id)` — DB constraint as defence in depth

**Schema governance**

12 Avro schemas, one per event type, registered in Schema Registry with BACKWARD compatibility. Schema evolution rules: you may add optional fields with defaults (BACKWARD-compatible, validated in test suite). You may not remove required fields or change field types (breaking change, fastavro reader/writer schema test confirms rejection). All subjects follow RecordNameStrategy so TopicNameStrategy collisions are impossible.

---

## 20. Contributing

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Follow the conventions in [`AGENTS.md`](AGENTS.md)
3. Run `flake8 app consumers` and `pytest --cov=app --cov=consumers` from `backend/` — both must pass
4. Open a pull request to `main` — CI runs automatically

---

*ArtistHub — built with Python, Flask, Kafka, Avro, and IBM Bob.*
