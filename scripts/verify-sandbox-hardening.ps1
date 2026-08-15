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
Assert-Contains $sandbox "name:\s+INTERNAL_API_TOKEN[\s\S]*key:\s+sandbox-token[\s\S]*name:\s+devpath-internal-auth" `
  "sandbox internal endpoints need a workload credential"
Assert-Contains $sandbox "kind:\s+Service[\s\S]*name:\s+devpath-sandbox-runner" `
  "dedicated runner service discovery must be rendered"
Assert-Contains $sandbox "kind:\s+NetworkPolicy[\s\S]*name:\s+devpath-sandbox-svc-ingress" `
  "sandbox ingress NetworkPolicy must be rendered"
Assert-Contains $sandbox "kind:\s+NetworkPolicy[\s\S]*name:\s+devpath-sandbox-runner-ingress" `
  "runner ingress NetworkPolicy must be rendered"

if ($sandbox -match "/var/run/docker\.sock" -or $sandbox -match "hostPath:") {
  throw "sandbox workload must not mount the host Docker socket or any hostPath"
}

Assert-Contains $ai "name:\s+INTERNAL_API_TOKEN[\s\S]*key:\s+sandbox-token" `
  "ai workload must receive the sandbox internal credential"
Assert-Contains $lcs "name:\s+INTERNAL_API_TOKEN[\s\S]*key:\s+sandbox-token" `
  "lcs workload must receive the sandbox internal credential"
Assert-Contains $migration 'name:\s+FLYWAY_POSTGRESQL_TRANSACTIONAL_LOCK[\s\S]*value:\s+"false"' `
  "migration Job must use a session-level Flyway lock for concurrent indexes"
Assert-Contains $migration "name:\s+sandbox-migration-preflight" `
  "migration Job must render the fail-closed Sandbox preflight"
Assert-Contains $migration "EXPECTED_ET8_SHARED_COMMIT[\s\S]*2b03c38934fdd19332da59107e4330a3af92d078" `
  "migration preflight must preserve the exact ET8 shared checkpoint"
Assert-Contains $migration "EXPECTED_SHARED_COMMIT[\s\S]*d3cf41faf21d00b815b398a7492af5506390151a" `
  "migration preflight must name the exact final shared lineage"
Assert-Contains $migration "ghcr\.io/devpathai/devpath-migration:d3cf41faf21d00b815b398a7492af5506390151a" `
  "migration Job image must be the exact final shared commit"
Assert-Contains $migration "postgres:17-alpine@sha256:979c4379dd698aba0b890599a6104e082035f98ef31d9b9291ec22f2b13059ca" `
  "migration preflight client image must be pinned by digest"
Assert-Contains $migration "ET8_FLYWAY_VERSION[\s\S]*202608161008" `
  "migration Job must retain the final ET8 Flyway checkpoint"
Assert-Contains $migration "TARGET_FLYWAY_VERSION[\s\S]*202608161011" `
  "migration Job must target the final shared Flyway version"
Assert-Contains $migration "MAINTENANCE_APPROVED[\s\S]*sandbox-migration-gate" `
  "migration preflight must require external maintenance approval"
Assert-Contains $migration "duplicate_active_users" `
  "migration preflight must reject duplicate active Sandbox rows"
Assert-Contains $migration "pg_total_relation_size" `
  "migration preflight must enforce an approved Sandbox table-size bound"
Assert-Contains $migration "pg_stat_activity" `
  "migration preflight must reject active database traffic"
Assert-Contains $migration "LOCK TABLE sandbox_sessions IN ACCESS EXCLUSIVE MODE NOWAIT" `
  "migration preflight must rehearse the historical DDL lock fail closed"

$runbook = Get-Content "apps/devpath-sandbox-svc/base/RUNBOOK.md" -Raw
Assert-Contains $runbook "V202608161008" `
  "Sandbox runbook must require the ET8 terminal-fence checkpoint"
Assert-Contains $runbook "V202608161011" `
  "Sandbox runbook must require the final shared migration"
Assert-Contains $runbook "2b03c38934fdd19332da59107e4330a3af92d078" `
  "Sandbox runbook must name the exact ET8 shared checkpoint"
Assert-Contains $runbook "d3cf41faf21d00b815b398a7492af5506390151a" `
  "Sandbox runbook must name the exact final shared lineage"
Assert-Contains $runbook "sandbox\.runs\.expired_active" `
  "Sandbox runbook must define the sustained expired-lease alert"

Write-Output "sandbox hardening manifests verified"
