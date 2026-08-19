# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Architectural Constraints (Non-Obvious)

- **App factory pattern is non-negotiable** — `create_app()` must remain the single entry point. Any new extension or Blueprint must be initialised/registered there. This is what makes `TestingConfig` (in-memory SQLite) work in CI without touching production config.
- **Blueprint-per-domain boundary is hard** — `auth`, `artists`, `releases`, `posts`, `merch`, `orders` are the six domains. Do not merge Blueprints or add cross-domain logic inside a route. Cross-domain coordination belongs in a service layer if needed.
- **`Order` polymorphism is intentionally simple** — `item_type` (`"release"` / `"merch"`) + `item_id` is sufficient for MVP. If order history requires joins, resolve at the Python level, not with DB-level polymorphic joins.
- **SQLite → PostgreSQL is a one-line config change** by design — `SQLALCHEMY_DATABASE_URI` in `config.py`. The ORM abstraction must not be broken by any raw SQL.
- **nginx proxies `/api/*` to Flask** — the frontend and backend share the same origin from the browser's perspective. CORS is still configured in Flask-CORS for direct local development (when Flask runs on a different port).
- **Two-user-model design is fixed** — `Artist` and `Fan` are separate tables with separate Flask-Login loaders. `current_user` can be either type; routes must check role before acting (artists cannot place orders; fans cannot create releases).
- **Future services are additive, not invasive** — `services/watsonx.py` and `services/events.py` are called from existing routes; they do not change endpoint contracts or DB schema. Plan new features as thin service modules, not route modifications.
- **OpenShift migration path** — Docker images from Phase 5 are OpenShift-compatible as-is. Add `openshift/` manifests as a new top-level directory; do not modify Docker files.
- **CI gate on `main`** — GitHub Actions must pass (`flake8` + `pytest --cov=app`) before any merge. Plan changes to account for test coverage.
