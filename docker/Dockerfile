# syntax=docker/dockerfile:1
#
# docker/Dockerfile
#
# Builds the ArtistHub Flask backend image.
#
# Usage (from repo root):
#   docker build -f docker/Dockerfile -t artisthub-backend .
#
# Environment variables are NEVER baked into this image.
# Pass them at runtime via --env-file or -e flags:
#   docker run --env-file .env -p 5000:5000 artisthub-backend
#
# The SQLite database file is written to /app/instance at runtime.
# Mount a named volume there so data persists across container restarts:
#   docker run -v ah-db:/app/instance --env-file .env -p 5000:5000 artisthub-backend

# ------------------------------------------------------------------ #
# Stage 1 — dependency install                                        #
# Isolated so the pip layer is cached independently of source changes.#
# ------------------------------------------------------------------ #
FROM python:3.11-slim AS deps

WORKDIR /build

# Install curl in the same layer as pip so we can healthcheck later,
# and clean up apt lists to keep the layer small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only the requirements file first — Docker will cache this layer
# and only re-run pip install when requirements.txt changes.
COPY backend/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------ #
# Stage 2 — final runtime image                                       #
# ------------------------------------------------------------------ #
FROM python:3.11-slim AS runtime

# Install curl (needed for the HEALTHCHECK command below).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — running as root inside a container is a security risk.
RUN addgroup --system artisthub \
    && adduser --system --ingroup artisthub --no-create-home artisthub

WORKDIR /app

# Copy only the installed packages from the deps stage — not the entire
# /usr/local/bin directory, which would overwrite the runtime stage's own
# Python interpreter symlinks if the two stages ever resolve to different
# patch versions of the base image.
# Gunicorn and other console-scripts are installed as entry-point wrappers
# inside site-packages and are re-linked by copying the specific scripts.
COPY --from=deps /usr/local/lib/python3.11/site-packages \
                 /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin/gunicorn /usr/local/bin/gunicorn

# Copy application source (backend/ → /app/).
# .dockerignore keeps venv/, __pycache__, tests/, .env, and *.db out.
COPY backend/ .

# Copy Avro schemas so the relay and consumers can load them without a
# Schema Registry round-trip on startup. .dockerignore excludes kafka/
# in general but explicitly allows kafka/schemas/ via a negation rule.
COPY kafka/schemas/ /app/kafka/schemas/

# Tell avro_utils.py where to find the schema files inside the container.
# This overrides the local-dev path-traversal logic.
ENV KAFKA_SCHEMAS_DIR=/app/kafka/schemas

# Ensure the instance directory exists and is owned by the app user.
# The SQLite DB file is written here at runtime (via a named volume).
RUN mkdir -p /app/instance && chown -R artisthub:artisthub /app/instance

# Switch to non-root for all subsequent commands and the final process.
USER artisthub

# Document the port the container listens on.
EXPOSE 5000

# Lightweight liveness probe: curl the health endpoint.
# Requires curl (installed above). Starts after 15 s to give gunicorn
# time to initialise the DB and bind the socket.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# Gunicorn production server.
#   -w 2     — two worker processes (right-size for an MVP / single core).
#   -b       — bind to all interfaces so the host / nginx can reach it.
#   --timeout 60 — allow slow DB initialisation on first boot.
#   --access-logfile - — send access logs to stdout (Docker captures them).
#   --error-logfile -  — send error logs to stderr.
CMD ["gunicorn", \
     "-w", "2", \
     "-b", "0.0.0.0:5000", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "run:app"]
