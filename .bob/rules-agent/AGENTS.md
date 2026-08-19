# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Critical Coding Rules (Non-Obvious)

- **Always use `success()` / `error()` from `app/utils/responses.py`** for every route response — never return `jsonify()` directly. The frontend `api.js` wrapper parses the `{ status, data }` / `{ status, error }` envelope shape exclusively.
- **Import `db` and `login_manager` from `app.extensions`** — never instantiate them in a route or model file. Circular import errors will result if you don't.
- **`create_app()` in `app/__init__.py` is the only place** Blueprints are registered and extensions are initialised. Do not call `db.init_app()` or `login_manager.init_app()` anywhere else.
- **Ownership check before every mutating route** — `abort(403)` if `resource.owner_id != current_user.id`. Use `abort()`, not a manual JSON response, so Flask-Login error handlers remain in control.
- **All POST/PUT bodies must be validated with a marshmallow schema** before any DB access. Validation errors return `error(message, 400)`.
- **`Order.item_type`** is `"release"` or `"merch"` (string literal) + `item_id` (int). Do not add separate order tables per item type.
- **`Follow` has a `UNIQUE(fan_id, artist_id)` DB constraint** — catch `IntegrityError` in the follow route and return `409 Conflict`.
- **Every model must implement `to_dict()`** — routes must never access model columns directly when building response payloads.
- **All frontend HTTP calls go through `frontend/js/api.js`** — never call `fetch()` directly in a page script. `api.js` attaches `credentials: "include"` to every request.
- **Tests are required for all major changes** — run from `backend/` directory: `pytest tests/test_<module>.py::test_name -v` for a single test; `pytest --cov=app` for the full suite.
- **Migrations via Flask-Migrate only** — never call `db.create_all()` in production code paths.
- **flake8 must pass** before any commit — run `flake8 app` from `backend/`.
