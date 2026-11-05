#!/bin/sh
# M8-CP2: production start for ONE hosted web service (one process, one Uvicorn worker, one mounted disk).
# Nothing here deploys anything; the host runs this as its start command. Secrets come only from the host's private
# environment (never from a file in the repository): HALSA_OWNER_PASSWORD_HASH, HALSA_FINANCE_PASSWORD_HASH,
# HALSA_VIEWER_PASSWORD_HASH. See docs/m8_cp2_hosting.md.
set -eu

# every persistent file lives under this mounted directory (services/paths.py); the app refuses to start otherwise
: "${HALSA_DATA_ROOT:?HALSA_DATA_ROOT must name the mounted persistent directory (absolute path)}"

export STOCKAGENT_NO_DOTENV=1                 # no .env file on the host: configuration = the host's environment
export ENABLE_AI="${ENABLE_AI:-false}"        # AI stays off at launch (explicit opt-in later)
export AI_PROVIDER="${AI_PROVIDER:-disabled}"

# The host's TLS proxy forwards plain HTTP; Uvicorn trusts X-Forwarded-Proto / -For only from FORWARDED_ALLOW_IPS.
# Unset → 127.0.0.1 only → behind a proxy every password login is refused (https_required): fail closed, never
# a password over an unverified scheme.
exec python -m uvicorn app:app \
    --host 0.0.0.0 --port "${PORT:-10000}" \
    --workers 1 \
    --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
    --no-server-header --timeout-graceful-shutdown 20
