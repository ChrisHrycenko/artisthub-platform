# openshift/secrets/README.md
#
# This directory is for REAL secret manifests during local or staging work.
#
# ============================================================
# THIS DIRECTORY IS .gitignored. NEVER commit files here.
# ============================================================
#
# Usage:
#   cp ../base/secrets.example.yaml ./artisthub-secrets.yaml
#   # Edit artisthub-secrets.yaml — fill in real base64-encoded values
#   kubectl apply -f artisthub-secrets.yaml -n artisthub
#   # After applying, remove the file from disk:
#   rm artisthub-secrets.yaml
#
# For production, use one of:
#   1. HashiCorp Vault Agent Injector
#   2. External Secrets Operator (ESO)
#   3. Sealed Secrets (kubeseal)
#
# See openshift/README.md for the full secrets strategy.
