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

$sandbox = Render-Kustomization "apps/devpath-sandbox-svc/base"
$ai = Render-Kustomization "apps/devpath-ai-svc/base"
$lcs = Render-Kustomization "apps/devpath-lcs-svc/base"
$migration = Render-Kustomization "apps/devpath-migration/base"

Assert-Contains $sandbox "terminationGracePeriodSeconds:\s+100" `
  "sandbox termination grace must exceed the 90 second application drain"
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

Write-Output "sandbox hardening manifests verified"
