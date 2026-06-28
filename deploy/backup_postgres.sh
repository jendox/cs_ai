#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "Compose file not found: $SCRIPT_DIR/$COMPOSE_FILE" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $SCRIPT_DIR/$ENV_FILE" >&2
  exit 1
fi

while IFS='=' read -r key value; do
  case "$key" in
    POSTGRES_BACKUP_DIR|POSTGRES_BACKUP_RETENTION_DAYS|POSTGRES_BACKUP_SERVICE)
      value="${value%$'\r'}"
      value="${value%\"}"
      value="${value#\"}"
      value="${value%\'}"
      value="${value#\'}"
      if [[ -z "${!key:-}" ]]; then
        printf -v "$key" '%s' "$value"
      fi
      ;;
  esac
done < "$ENV_FILE"

SERVICE="${POSTGRES_BACKUP_SERVICE:-postgres}"
BACKUP_DIR="${POSTGRES_BACKUP_DIR:-$SCRIPT_DIR/backups/postgres}"
RETENTION_DAYS="${POSTGRES_BACKUP_RETENTION_DAYS:-14}"

if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  echo "POSTGRES_BACKUP_RETENTION_DAYS must be a non-negative integer" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$BACKUP_DIR/cs_postgres_${timestamp}.dump"
tmp_file="${backup_file}.tmp"

cleanup_tmp() {
  rm -f "$tmp_file"
}
trap cleanup_tmp EXIT

echo "Creating PostgreSQL backup: $backup_file"

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T "$SERVICE" \
  sh -ec 'pg_dump --format=custom --blobs --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "$tmp_file"

mv "$tmp_file" "$backup_file"
trap - EXIT

echo "Backup created: $backup_file"

if (( RETENTION_DAYS > 0 )); then
  echo "Removing PostgreSQL backups older than $RETENTION_DAYS days from $BACKUP_DIR"
  find "$BACKUP_DIR" -type f -name 'cs_postgres_*.dump' -mtime +"$RETENTION_DAYS" -print -delete
else
  echo "Backup retention cleanup is disabled because POSTGRES_BACKUP_RETENTION_DAYS=0"
fi
