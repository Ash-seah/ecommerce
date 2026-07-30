[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"),
    [string]$Container,
    [string]$Superuser
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Environment file not found: $EnvFile"
}

$values = @{}
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
        continue
    }
    $key, $value = $trimmed.Split("=", 2)
    $values[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
}

if (-not $Container) {
    $Container = $values["POSTGRES_CONTAINER"]
}
if (-not $Container) {
    $Container = "terabit-fintech-postgres-1"
}
if (-not $Superuser) {
    $Superuser = $values["POSTGRES_SUPERUSER"]
}
if (-not $Superuser) {
    $Superuser = "postgres"
}

$ownerPassword = $values["ECOMMERCE_OWNER_PASSWORD"]
$readerPassword = $values["ECOMMERCE_READER_PASSWORD"]

# Hex-only credentials make psql variable assignment unambiguous and avoid shell evaluation.
if ($ownerPassword -notmatch "^[a-fA-F0-9]{64,}$") {
    throw "ECOMMERCE_OWNER_PASSWORD must be a generated hex secret of at least 32 bytes."
}
if ($readerPassword -notmatch "^[a-fA-F0-9]{64,}$") {
    throw "ECOMMERCE_READER_PASSWORD must be a generated hex secret of at least 32 bytes."
}
if ($ownerPassword -eq $readerPassword) {
    throw "Owner and reader passwords must be different."
}

$sqlPath = Join-Path $PSScriptRoot "bootstrap-shared-postgres.sql"
$preamble = "\set owner_password $ownerPassword`n\set reader_password $readerPassword`n"
$sql = $preamble + (Get-Content -LiteralPath $sqlPath -Raw)

# Secrets travel over stdin, not in the docker/psql command line.
$sql | docker exec -i $Container psql -X --username $Superuser --dbname postgres
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL bootstrap failed with exit code $LASTEXITCODE."
}
