#!/usr/bin/env bash
set -euo pipefail

# Keep generated credential material inside the private scratch directory.
k() { sudo kubectl "$@"; }

namespace=devpath-staging
scratch="$(mktemp -d)"

cleanup() {
  k -n "$namespace" delete pod mission-spine-db-bootstrap \
    --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
  k -n "$namespace" delete secret mission-spine-db-admin mission-spine-db-bootstrap \
    --ignore-not-found=true >/dev/null 2>&1 || true
  rm -rf -- "$scratch"
}
trap cleanup EXIT
umask 077

k create namespace "$namespace" --dry-run=client -o yaml | k apply -f - >/dev/null

copy_secret_files() {
  local source_namespace="$1" source_name="$2" destination_name="$3"
  shift 3
  local directory="$scratch/$destination_name"
  local -a arguments=()
  mkdir -p "$directory"
  for key in "$@"; do
    k -n "$source_namespace" get secret "$source_name" \
      -o "jsonpath={.data.$key}" | base64 -d > "$directory/$key"
    arguments+=("--from-file=$key=$directory/$key")
  done
  k -n "$namespace" create secret generic "$destination_name" \
    "${arguments[@]}" --dry-run=client -o yaml | k apply -f - >/dev/null
}

copy_tls_secret() {
  local source_name="$1"
  local directory="$scratch/$source_name"
  mkdir -p "$directory"
  k -n devpath get secret "$source_name" -o jsonpath='{.data.tls\.crt}' \
    | base64 -d > "$directory/tls.crt"
  k -n devpath get secret "$source_name" -o jsonpath='{.data.tls\.key}' \
    | base64 -d > "$directory/tls.key"
  k -n "$namespace" create secret tls "$source_name" \
    --cert="$directory/tls.crt" --key="$directory/tls.key" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
}

copy_secret_files devpath platform-db mission-spine-db-admin db-url db-user db-password

platform_db_exists=false
if k -n "$namespace" get secret platform-db >/dev/null 2>&1; then
  platform_db_exists=true
  k -n "$namespace" get secret platform-db -o jsonpath='{.data.db-password}' \
    | base64 -d > "$scratch/staging-db-password"
else
  db_password="$(openssl rand -base64 36 | tr -d '\n')"
  printf '%s' "$db_password" > "$scratch/staging-db-password"
fi

k -n "$namespace" create secret generic mission-spine-db-bootstrap \
  --from-file=db-password="$scratch/staging-db-password" \
  --dry-run=client -o yaml | k apply -f - >/dev/null

k -n "$namespace" delete pod mission-spine-db-bootstrap \
  --ignore-not-found=true --wait=true >/dev/null 2>&1
k -n "$namespace" run mission-spine-db-bootstrap \
  --image=postgres:17-alpine@sha256:979c4379dd698aba0b890599a6104e082035f98ef31d9b9291ec22f2b13059ca \
  --restart=Never \
  --overrides='{
      "apiVersion":"v1",
      "spec":{
        "automountServiceAccountToken":false,
        "containers":[{
          "name":"mission-spine-db-bootstrap",
          "image":"postgres:17-alpine@sha256:979c4379dd698aba0b890599a6104e082035f98ef31d9b9291ec22f2b13059ca",
          "command":["sh","-c","sleep 600"],
          "env":[
            {"name":"DB_URL","valueFrom":{"secretKeyRef":{"name":"mission-spine-db-admin","key":"db-url"}}},
            {"name":"DB_USER","valueFrom":{"secretKeyRef":{"name":"mission-spine-db-admin","key":"db-user"}}},
            {"name":"DB_PASSWORD","valueFrom":{"secretKeyRef":{"name":"mission-spine-db-admin","key":"db-password"}}},
            {"name":"STAGING_DB_PASSWORD","valueFrom":{"secretKeyRef":{"name":"mission-spine-db-bootstrap","key":"db-password"}}}
          ],
          "securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}
        }]
      }
    }' >/dev/null
k -n "$namespace" wait --for=condition=Ready pod/mission-spine-db-bootstrap \
  --timeout=120s >/dev/null

k -n "$namespace" exec -i mission-spine-db-bootstrap -- sh -se <<'DBSCRIPT'
set -eu
jdbc="${DB_URL#jdbc:postgresql://}"
hostport="${jdbc%%/*}"
host="${hostport%%:*}"
port="${hostport##*:}"
if [ "$port" = "$hostport" ]; then port=5432; fi
export PGPASSWORD="$DB_PASSWORD"

revoke_stage_membership() {
  psql -v ON_ERROR_STOP=1 -h "$host" -p "$port" -U "$DB_USER" -d postgres <<'SQL' \
    >/dev/null 2>&1 || true
SELECT format('REVOKE devpath_staging FROM %I', current_user) \gexec
SQL
}
trap revoke_stage_membership EXIT

psql -v ON_ERROR_STOP=1 -h "$host" -p "$port" -U "$DB_USER" -d postgres \
  --set=stage_password="$STAGING_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE devpath_staging LOGIN PASSWORD %L', :'stage_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'devpath_staging') \gexec
SELECT format('ALTER ROLE devpath_staging LOGIN PASSWORD %L', :'stage_password') \gexec
SELECT format('GRANT devpath_staging TO %I', current_user) \gexec
SELECT 'CREATE DATABASE devpath_staging OWNER devpath_staging'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'devpath_staging') \gexec
SQL
psql -v ON_ERROR_STOP=1 -h "$host" -p "$port" -U "$DB_USER" -d devpath_staging \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'
DBSCRIPT

if [ "$platform_db_exists" = false ]; then
  admin_url="$(k -n "$namespace" get secret mission-spine-db-admin \
    -o jsonpath='{.data.db-url}' | base64 -d)"
  printf '%s/devpath_staging' "${admin_url%/*}" > "$scratch/db-url"
  printf '%s' devpath_staging > "$scratch/db-user"
  cp "$scratch/staging-db-password" "$scratch/db-password"
  k -n "$namespace" create secret generic platform-db \
    --from-file=db-url="$scratch/db-url" \
    --from-file=db-user="$scratch/db-user" \
    --from-file=db-password="$scratch/db-password" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
fi

if ! k -n "$namespace" get secret mission-spine-release-control >/dev/null 2>&1; then
  openssl rand -hex 32 | tr -d '\n' > "$scratch/control-token"
  openssl rand -hex 32 | tr -d '\n' > "$scratch/internal-token"
  printf 'mission-spine-%s' "$(openssl rand -hex 12)" > "$scratch/oauth-client-id"
  openssl rand -base64 36 | tr -d '\n' > "$scratch/oauth-client-secret"
  k -n "$namespace" create secret generic mission-spine-release-control \
    --from-file=control-token="$scratch/control-token" \
    --from-file=internal-token="$scratch/internal-token" \
    --from-file=oauth-client-id="$scratch/oauth-client-id" \
    --from-file=oauth-client-secret="$scratch/oauth-client-secret" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
fi

if ! k -n "$namespace" get secret devpath-jwt >/dev/null 2>&1; then
  openssl rand -base64 48 | tr -d '\n' > "$scratch/jwt-secret"
  k -n "$namespace" create secret generic devpath-jwt \
    --from-file=jwt-secret="$scratch/jwt-secret" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
fi

if ! k -n "$namespace" get secret ai-claude >/dev/null 2>&1; then
  printf 'disabled-staging-mock' > "$scratch/anthropic-api-key"
  k -n "$namespace" create secret generic ai-claude \
    --from-file=anthropic-api-key="$scratch/anthropic-api-key" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
fi

if ! k -n "$namespace" get secret sandbox-runner-server-tls >/dev/null 2>&1; then
  certdir="$scratch/runner-certs"
  mkdir -p "$certdir"
  openssl genrsa -out "$certdir/ca-key.pem" 3072 >/dev/null 2>&1
  openssl req -x509 -new -nodes -key "$certdir/ca-key.pem" -sha256 -days 30 \
    -subj '/CN=mission-spine-staging-runner-ca' -out "$certdir/ca.pem" >/dev/null 2>&1
  openssl genrsa -out "$certdir/server-key.pem" 3072 >/dev/null 2>&1
  openssl req -new -key "$certdir/server-key.pem" \
    -subj '/CN=devpath-sandbox-runner.devpath-staging.svc' \
    -out "$certdir/server.csr" >/dev/null 2>&1
  printf '%s\n' \
    'subjectAltName=DNS:devpath-sandbox-runner,DNS:devpath-sandbox-runner.devpath-staging,DNS:devpath-sandbox-runner.devpath-staging.svc,DNS:devpath-sandbox-runner.devpath-staging.svc.cluster.local' \
    'extendedKeyUsage=serverAuth' > "$certdir/server.ext"
  openssl x509 -req -in "$certdir/server.csr" -CA "$certdir/ca.pem" \
    -CAkey "$certdir/ca-key.pem" -CAcreateserial -out "$certdir/server-cert.pem" \
    -days 30 -sha256 -extfile "$certdir/server.ext" >/dev/null 2>&1
  openssl genrsa -out "$certdir/key.pem" 3072 >/dev/null 2>&1
  openssl req -new -key "$certdir/key.pem" -subj '/CN=devpath-sandbox-svc-staging' \
    -out "$certdir/client.csr" >/dev/null 2>&1
  printf '%s\n' 'extendedKeyUsage=clientAuth' > "$certdir/client.ext"
  openssl x509 -req -in "$certdir/client.csr" -CA "$certdir/ca.pem" \
    -CAkey "$certdir/ca-key.pem" -CAcreateserial -out "$certdir/cert.pem" \
    -days 30 -sha256 -extfile "$certdir/client.ext" >/dev/null 2>&1
  k -n "$namespace" create secret generic sandbox-runner-server-tls \
    --from-file=ca.pem="$certdir/ca.pem" \
    --from-file=server-cert.pem="$certdir/server-cert.pem" \
    --from-file=server-key.pem="$certdir/server-key.pem" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n "$namespace" create secret generic sandbox-runner-mtls \
    --from-file=ca.pem="$certdir/ca.pem" \
    --from-file=cert.pem="$certdir/cert.pem" \
    --from-file=key.pem="$certdir/key.pem" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
fi

copy_tls_secret devpath-web-tls
copy_tls_secret devpath-gateway-tls

k -n "$namespace" get secret \
  platform-db mission-spine-release-control devpath-jwt ai-claude \
  sandbox-runner-server-tls sandbox-runner-mtls devpath-web-tls devpath-gateway-tls \
  -o custom-columns='NAME:.metadata.name,TYPE:.type' --no-headers
