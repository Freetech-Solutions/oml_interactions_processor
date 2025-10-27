#!/usr/bin/env bash
set -euo pipefail

# ENTRYPOINT: prepare environment and drop privileges to run the worker as omnileads
OMNILEADS_USER="${OMNILEADS_USER:-omnileads}"
OMNILEADS_GROUP="${OMNILEADS_GROUP:-appgroup}"
OMNILEADS_UID="${OMNILEADS_UID:-}"
OMNILEADS_GID="${OMNILEADS_GID:-}"

# create group if missing
if ! getent group "${OMNILEADS_GROUP}" >/dev/null 2>&1; then
  addgroup --system "${OMNILEADS_GROUP}" || true
fi

# if OMNILEADS_GID specified, attempt to set group id
if [ -n "${OMNILEADS_GID}" ]; then
  if getent group "${OMNILEADS_GID}" >/dev/null 2>&1; then
    echo "GID ${OMNILEADS_GID} already exists on system, skipping groupmod."
  else
    groupmod -g "${OMNILEADS_GID}" "${OMNILEADS_GROUP}" 2>/dev/null || addgroup --system --gid "${OMNILEADS_GID}" "${OMNILEADS_GROUP}" || true
  fi
fi

# create user if missing
if ! id "${OMNILEADS_USER}" >/dev/null 2>&1; then
  adduser --system --ingroup "${OMNILEADS_GROUP}" --home "/home/${OMNILEADS_USER}" --no-create-home "${OMNILEADS_USER}" || true
fi

# if OMNILEADS_UID specified, try to set it (best-effort)
if [ -n "${OMNILEADS_UID}" ]; then
  EXISTING="$(getent passwd "${OMNILEADS_UID}" | cut -d: -f1 || true)"
  if [ -n "${EXISTING}" ] && [ "${EXISTING}" != "${OMNILEADS_USER}" ]; then
    echo "WARNING: UID ${OMNILEADS_UID} exists for ${EXISTING}; not changing UID for ${OMNILEADS_USER}."
  else
    usermod -u "${OMNILEADS_UID}" "${OMNILEADS_USER}" 2>/dev/null || true
  fi
fi

# ensure home & cache
mkdir -p "/home/${OMNILEADS_USER}/.cache"
chown -R "${OMNILEADS_USER}:${OMNILEADS_GROUP}" "/home/${OMNILEADS_USER}" || true

# Ensure HF_HOME exists and is writable by omnileads (best-effort)
mkdir -p "${HF_HOME}"
chown -R "${OMNILEADS_USER}:${OMNILEADS_GROUP}" "${HF_HOME}" || true

# Ensure app and venv are readable/writable where needed
chown -R "${OMNILEADS_USER}:${OMNILEADS_GROUP}" /opt/venv /app 2>/dev/null || true

# Ensure Asterisk monitor dir is accessible to omnileads via group appgroup
MONITOR_DIR="/var/spool/asterisk/monitor"

if [ -d "$MONITOR_DIR" ]; then
  # Change group to appgroup (no cambia owner)
  chown -R :"${OMNILEADS_GROUP:-appgroup}" "$MONITOR_DIR" 2>/dev/null || true
  # Give group traversal/read/execute on dirs and read on files
  find "$MONITOR_DIR" -type d -exec chmod g+rx {} + 2>/dev/null || true
  find "$MONITOR_DIR" -type f -exec chmod g+r {} + 2>/dev/null || true

  # If you prefer to allow 'other' traversal (more permissive), uncomment:
  # chmod o+rx "$MONITOR_DIR" 2>/dev/null || true
fi

# Execute the final command as the non-root user
if command -v gosu >/dev/null 2>&1; then
  exec gosu "${OMNILEADS_USER}" "$@"
else
  exec su -s /bin/sh -c "$*" "${OMNILEADS_USER}"
fi
