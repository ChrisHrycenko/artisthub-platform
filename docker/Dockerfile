# syntax=docker/dockerfile:1
#
# docker/Dockerfile
#
# Builds the ArtistHub Flask backend image.
#
# Phase 8B — OpenShift arbitrary-UID hardening
# =============================================
# OpenShift's default restricted SCC assigns an arbitrary UID (e.g.
# 1000680000) from the project's UID range at pod creation time.  The
# assigned UID is always a member of the root group (GID 0).
#
# This image is hardened to work under any arbitrary UID:
#   - Application files are owned by root:root (UID 0, GID 0) and are
#     world-readable.
#   - Directories that require runtime writes (/app/instance, /tmp) are
#     group-writable (GID 0) so any UID assigned by OpenShift can write.
#   - The `artisthub` named user is removed; we use the GID 0 pattern
#     recommended by Red Hat:
#       chgrp -R 0 /app && chmod -R g=u /app
#   - No fixed numeric UID is required.  The container works as UID 1001
#     for local Docker testing and as any arbitrary UID on OpenShift.
#
# Usage (from repo root):
#   docker build -f docker/Dockerfile -t artisthub-backend .
#
# Local non-root test (approximates OpenShift arbitrary-UID behaviour):
#   docker run --user 185432:0 --env-file .env -p 5000:5000 artisthub-backend
#
# Environment variables are NEVER baked into this image.
#
# Phase 8A note: when DATABASE_URL=postgresql://..., the app factory skips
# db.create_all().  Run `flask db upgrade` (the db-migrate service) before
# starting this image against PostgreSQL.
#
# KAFKA_SCHEMAS_DIR is set to /app/kafka/schemas.  Avro schema files are
# readable by all UIDs because GID 0 permissions cover group-read access.

# ------------------------------------------------------------------ #
# Stage 1 — dependency install                                        #
# ------------------------------------------------------------------ #
FROM python:3.11-slim AS deps

WORKDIR /build

# Install curl for healthcheck; clean apt lists to keep layer small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------ #
# Stage 2 — final runtime image                                       #
# ------------------------------------------------------------------ #
FROM python:3.11-slim AS runtime

# Install curl (needed for the HEALTHCHECK command).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------ #
# OpenShift arbitrary-UID hardening                                   #
# No named user is created.  The GID 0 (root group) pattern is used  #
# instead, which OpenShift requires for arbitrary-UID compatibility.  #
#                                                                     #
# Steps:                                                              #
#   1. WORKDIR sets /app owned by root:root (UID 0 / GID 0).         #
#   2. Application files are COPYd (owned root:root by default).     #
#   3. chgrp -R 0 /app  — assigns root group to all files.           #
#   4. chmod -R g=u /app — group permissions mirror user permissions. #
#   5. /app/instance is additionally made group-writable so SQLite    #
#      writes succeed under any arbitrary UID (GID 0 member).         #
#   6. USER 1001 — a non-root numeric UID with no home directory,     #
#      no /etc/passwd entry required.  Represents the typical local   #
#      Docker test UID; OpenShift overrides this with its own UID     #
#      at runtime while keeping GID 0.                                #
# ------------------------------------------------------------------ #
WORKDIR /app

# Copy installed packages from the deps stage.
COPY --from=deps /usr/local/lib/python3.11/site-packages \
                 /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin/gunicorn /usr/local/bin/gunicorn

# Copy application source.
COPY backend/ .

# Copy Avro schemas — readable by all UIDs after chgrp/chmod below.
COPY kafka/schemas/ /app/kafka/schemas/

# Tell avro_utils.py where to find the schema files inside the container.
ENV KAFKA_SCHEMAS_DIR=/app/kafka/schemas

# Create the SQLite instance directory (used when DATABASE_URL=sqlite://...).
# Group-writable so any arbitrary UID assigned by OpenShift can create the DB.
RUN mkdir -p /app/instance

# Apply GID 0 pattern — the core of OpenShift arbitrary-UID support.
#   chgrp -R 0  : all files/dirs belong to GID 0 (root group)
#   chmod -R g=u : group permission bits mirror user permission bits
# After this, a process running as UID 185432 with GID 0 has the same
# filesystem access as the traditional "app user".
RUN chgrp -R 0 /app \
    && chmod -R g=u /app

# /tmp is provided by the kernel and is always writable by all UIDs.
# Worker heartbeat files (e.g. /tmp/relay_alive) are written there.
# No explicit action needed — /tmp is not inside /app.

# Switch to a non-root numeric UID.  1001 is used for local Docker testing.
# OpenShift will override this with an arbitrary UID from the project range
# while preserving GID 0, which gives access to the group-writable paths above.
USER 1001

# Document the port.
EXPOSE 5000

# HTTP liveness probe — used by Docker Compose and local health checks.
# OpenShift replaces this with httpGet probes in Deployment manifests (Phase 8C+).
# start-period=20s covers gunicorn startup + DB connection init.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# Gunicorn production server.
CMD ["gunicorn", \
     "-w", "2", \
     "-b", "0.0.0.0:5000", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "run:app"]
