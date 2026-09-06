$ErrorActionPreference = "Stop"

function Render-Kustomization([string] $path) {
  $rendered = & kubectl kustomize $path
  if ($LASTEXITCODE -ne 0) {
    throw "kubectl kustomize failed for $path"
  }
  return ($rendered -join "`n")
}

function Assert-Contains([string] $text, [string] $pattern, [string] $message) {
  if ($text -notmatch $pattern) {
    throw $message
  }
}

function Read-Match([string] $text, [string] $pattern, [string] $message) {
  $matched = [regex]::Match($text, $pattern)
  if (-not $matched.Success) {
    throw $message
  }
  return $matched.Groups[1].Value
}

$sandbox = Render-Kustomization "apps/devpath-sandbox-svc/base"
$runner = Render-Kustomization "apps/devpath-sandbox-runner/base"
$ai = Render-Kustomization "apps/devpath-ai-svc/base"
$lcs = Render-Kustomization "apps/devpath-lcs-svc/base"
$migration = Render-Kustomization "apps/devpath-migration/base"

$podGraceMs = 1000 * [int](Read-Match $sandbox `
  "terminationGracePeriodSeconds:\s+(\d+)" `
  "sandbox termination grace is missing")
$activeCutoffMs = [int](Read-Match $sandbox `
  'name:\s+SANDBOX_ACTIVE_CUTOFF_MS[\s\S]*?value:\s+"(\d+)"' `
  "sandbox active cancellation cutoff is missing")
$executorDrainMs = [int](Read-Match $sandbox `
  'name:\s+SANDBOX_DRAIN_TIMEOUT_MS[\s\S]*?value:\s+"(\d+)"' `
  "sandbox executor drain timeout is missing")
$springShutdownSeconds = [int](Read-Match $sandbox `
  'name:\s+SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE[\s\S]*?value:\s+"?(\d+)s"?' `
  "Spring shutdown phase timeout is missing")
$springShutdownMs = 1000 * $springShutdownSeconds
if (-not ($activeCutoffMs -lt $executorDrainMs -and `
          $executorDrainMs -lt $springShutdownMs -and `
          $springShutdownMs -lt $podGraceMs)) {
  throw "shutdown budgets must satisfy active cutoff < executor < Spring < Pod"
}
if ($activeCutoffMs -ne 60000 -or $executorDrainMs -ne 75000 -or `
    $springShutdownMs -ne 90000 -or $podGraceMs -ne 120000) {
  throw "sandbox release budgets must be exactly 60s/75s/90s/120s"
}
Assert-Contains $sandbox "maxSurge:\s+0" `
  "sandbox rollout must never overlap old and new execution owners"
Assert-Contains $sandbox "maxUnavailable:\s+1" `
  "single-replica maxSurge=0 rollout must explicitly permit the transition"
Assert-Contains $sandbox "name:\s+DOCKER_HOST[\s\S]*value:\s+tcp://devpath-sandbox-runner:2376" `
  "sandbox must use the dedicated runner endpoint"
Assert-Contains $sandbox "name:\s+SANDBOX_RUNTIME[\s\S]*value:\s+runsc" `
  "sandbox production runtime must be runsc"
Assert-Contains $sandbox 'name:\s+SANDBOX_REQUIRE_ISOLATION[\s\S]*value:\s+"true"' `
  "sandbox must fail closed when isolation is unavailable"
Assert-Contains $sandbox "secretName:\s+sandbox-runner-mtls" `
  "sandbox must mount runner mTLS credentials"
Assert-Contains $sandbox "securityContext:[\s\S]*fsGroup:\s+101[\s\S]*fsGroupChangePolicy:\s+OnRootMismatch" `
  "sandbox pod must grant the immutable app group access to projected mTLS credentials"
Assert-Contains $sandbox "defaultMode:\s+288[\s\S]*secretName:\s+sandbox-runner-mtls" `
  "sandbox runner mTLS credentials must be group-readable but not world-readable"
Assert-Contains $sandbox "name:\s+INTERNAL_API_TOKEN[\s\S]*key:\s+sandbox-token[\s\S]*name:\s+devpath-internal-auth" `
  "sandbox internal endpoints need a workload credential"
Assert-Contains $sandbox "kind:\s+NetworkPolicy[\s\S]*name:\s+devpath-sandbox-svc-ingress" `
  "sandbox ingress NetworkPolicy must be rendered"
Assert-Contains $sandbox "kind:\s+NetworkPolicy[\s\S]*name:\s+devpath-sandbox-runner-ingress" `
  "runner ingress NetworkPolicy must be rendered"

if ($sandbox -match "/var/run/docker\.sock" -or $sandbox -match "hostPath:") {
  throw "sandbox workload must not mount the host Docker socket or any hostPath"
}

# 러너 앱(apps/devpath-sandbox-runner) — RUNBOOK 요건 1 의 실체.
Assert-Contains $runner "kind:\s+Service[\s\S]*name:\s+devpath-sandbox-runner" `
  "dedicated runner service discovery must be rendered by the runner app"
Assert-Contains $runner "storage\.googleapis\.com/gvisor/releases/release/20250820\.0/x86_64/runsc" `
  "runner must install a pinned gVisor release"
Assert-Contains $runner "d6c12a4cb4f714bfcba6fd6611ad4ca73fd88dce790a083d2ceda807cd7e074c0131d5dd2a3490399e8be91feed9afe450793e9708dacddc4afc99ee6e5c3d2e" `
  "runner must verify the pinned runsc sha512 before install"
Assert-Contains $runner "--add-runtime=runsc=/gvisor/runsc" `
  "runner engine must register the runsc runtime"
Assert-Contains $runner "--host=unix:///var/run/docker\.sock" `
  "runner must keep an internal-only Unix socket for preload and health probes"
Assert-Contains $runner "--tlsverify" `
  "runner API must verify client certificates"
Assert-Contains $runner "secretName:\s+sandbox-runner-server-tls" `
  "runner must serve the pinned mTLS server credentials"
if ($runner -match "2375") {
  throw "runner must expose only the mTLS 2376 listener (dind entrypoint injects 2375)"
}
if ($runner -match "dockerd-entrypoint") {
  throw "runner must exec dockerd directly; the dind entrypoint adds an unencrypted listener"
}
if ($runner -match "/var/run/docker\.sock.*hostPath" -or $runner -match "hostPath:") {
  throw "runner must not mount any hostPath"
}
Assert-Contains $runner "postStart:[\s\S]*docker pull" `
  "runner must preload runtime images before it becomes ready"
Assert-Contains $runner "postStart:[\s\S]*eclipse-temurin:21-jdk[\s\S]*node:20-alpine[\s\S]*python:3\.12-slim" `
  "runner preload must cover the exact Java, JavaScript, and Python runtime images"
Assert-Contains $runner "readinessProbe:[\s\S]*sandbox-runtimes-ready[\s\S]*docker image inspect" `
  "runner readiness must fail closed until every runtime image is locally available"
Assert-Contains $runner "livenessProbe:[\s\S]*docker info" `
  "runner liveness must verify the Docker daemon through its internal Unix socket"

Assert-Contains $ai "name:\s+INTERNAL_API_TOKEN[\s\S]*key:\s+sandbox-token" `
  "ai workload must receive the sandbox internal credential"
Assert-Contains $ai 'name:\s+LCS_URI[\s\S]*value:\s+"?http://devpath-lcs-svc\.devpath\.svc:8080"?' `
  "ai workload must use the in-cluster LCS service instead of localhost"
Assert-Contains $lcs "name:\s+INTERNAL_API_TOKEN[\s\S]*key:\s+sandbox-token" `
  "lcs workload must receive the sandbox internal credential"
Assert-Contains $migration 'name:\s+FLYWAY_POSTGRESQL_TRANSACTIONAL_LOCK[\s\S]*value:\s+"false"' `
  "migration Job must use a session-level Flyway lock for concurrent indexes"
Assert-Contains $migration "name:\s+sandbox-migration-preflight" `
  "migration Job must render the fail-closed Sandbox preflight"
Assert-Contains $migration "EXPECTED_ET8_SHARED_COMMIT[\s\S]*2b03c38934fdd19332da59107e4330a3af92d078" `
  "migration preflight must preserve the exact ET8 shared checkpoint"
Assert-Contains $migration "EXPECTED_SHARED_COMMIT[\s\S]*2fda29d38bc94345aa91bb6ea5823aef8125b0dc" `
  "migration preflight must name the exact final shared lineage"
Assert-Contains $migration "ghcr\.io/devpathai/devpath-migration:2fda29d38bc94345aa91bb6ea5823aef8125b0dc" `
  "migration Job image must be the exact final shared commit"
Assert-Contains $migration "filesystem:/flyway/sql,classpath:db/migration" `
  "migration Job must discover both SQL and nontransactional Java migrations"
Assert-Contains $migration "migration-runner\.jar" `
  "migration Job must fail closed when the Java migration runner is absent"
Assert-Contains $migration "postgres:17-alpine@sha256:979c4379dd698aba0b890599a6104e082035f98ef31d9b9291ec22f2b13059ca" `
  "migration preflight client image must be pinned by digest"
Assert-Contains $migration "ET8_FLYWAY_VERSION[\s\S]*202608161008" `
  "migration Job must retain the final ET8 Flyway checkpoint"
Assert-Contains $migration "TARGET_FLYWAY_VERSION[\s\S]*202609051004" `
  "migration Job must target the final shared Flyway version"
Assert-Contains $migration "MAINTENANCE_APPROVED[\s\S]*sandbox-migration-gate" `
  "migration preflight must require external maintenance approval"
Assert-Contains $migration "duplicate_active_users" `
  "migration preflight must reject duplicate active Sandbox rows"
Assert-Contains $migration "pg_total_relation_size" `
  "migration preflight must enforce approved table-size bounds"
Assert-Contains $migration "MAX_SUPPORT_REQUESTS_ROWS[\s\S]*max-support-requests-rows" `
  "migration preflight must require an approved support row bound"
Assert-Contains $migration "MAX_SUPPORT_REQUESTS_BYTES[\s\S]*max-support-requests-bytes" `
  "migration preflight must require an approved support size bound"
Assert-Contains $migration "support_requests_rows" `
  "migration preflight must measure support rows before V202609051001"
Assert-Contains $migration "pg_stat_activity" `
  "migration preflight must reject active database traffic"
Assert-Contains $migration "LOCK TABLE sandbox_sessions IN ACCESS EXCLUSIVE MODE NOWAIT" `
  "migration preflight must rehearse the historical DDL lock fail closed"
Assert-Contains $migration "LOCK TABLE support_requests IN ACCESS EXCLUSIVE MODE NOWAIT" `
  "migration preflight must rehearse the V202609051001 DDL lock fail closed"

$runbook = Get-Content "apps/devpath-sandbox-svc/base/RUNBOOK.md" -Raw
Assert-Contains $runbook "V202608161008" `
  "Sandbox runbook must require the ET8 terminal-fence checkpoint"
Assert-Contains $runbook "V202608201002" `
  "Sandbox runbook must require the final shared migration"
Assert-Contains $runbook "V202609051004" `
  "Sandbox runbook must require the mentor access migration target"
Assert-Contains $runbook "2b03c38934fdd19332da59107e4330a3af92d078" `
  "Sandbox runbook must name the exact ET8 shared checkpoint"
Assert-Contains $runbook "2fda29d38bc94345aa91bb6ea5823aef8125b0dc" `
  "Sandbox runbook must name the exact final shared lineage"
Assert-Contains $runbook "sandbox\.runs\.expired_active" `
  "Sandbox runbook must define the sustained expired-lease alert"

Write-Output "sandbox hardening manifests verified"
