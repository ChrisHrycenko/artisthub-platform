# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Documentation Context (Non-Obvious)

- **`artisthub-plan.md` in the project root is the canonical reference** for architecture, DB schema, all API endpoints, and the phased implementation plan. It is the primary source of truth — not the README.
- **Flask is a pure JSON API** — it does not render any HTML. Jinja2 templates are not used. The frontend is entirely static files in `frontend/`.
- **Two separate auth flows** — Artist and Fan are different models with different `/api/auth/artist/*` and `/api/auth/fan/*` endpoints. There is no shared login or role selector.
- **Purchases are simulated** — the "Buy" button creates an `Order` row; there is no payment gateway. Do not suggest Stripe or similar unless explicitly asked.
- **`app/utils/responses.py`** is the single source of truth for the API response envelope shape (`{ status, data }` / `{ status, error }`). All frontend error handling assumes this shape.
- **`frontend/js/api.js`** is the only place `API_BASE_URL` is defined. All JS pages import from it.
- **Docker Compose is the canonical local run method** — `docker-compose up` from `docker/` starts nginx (frontend) + Flask (backend). nginx proxies `/api/*` to Flask on port 5000.
- **Future integrations (watsonx, OpenShift, Confluent)** are scoped in `artisthub-plan.md` Section 10. They are not in the MVP codebase — they would be added as `services/watsonx.py`, `services/events.py`, and `openshift/` manifests respectively.
