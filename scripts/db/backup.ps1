# Backs up one MedAkis database to backups\ (custom format, with a SHA-256 file).
# The dump is created inside the container and copied out with "docker compose cp":
# Windows PowerShell 5.1 ">" redirection would corrupt binary output.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\db\backup.ps1 -Database medakis_demo
#
# -BeklenenKimlik "<system_identifier>:<database oid>": the seed service passes the identity it
# read over its own connection. The script reads the identity of the Compose server before and
# after the dump and stops without keeping a file if they differ (another PostgreSQL instance).
#
# Every run gets a unique name (UTC time + random id), so concurrent backups never share a file.
# The dump must pass "pg_restore --list" inside the container before it is copied out. It is
# copied to "<name>.partial" first and renamed only after its SHA-256 file is written; a crash
# leaves at most a .partial file, which restore.ps1 refuses.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('medakis_dev', 'medakis_test', 'medakis_demo')]
    [string]$Database,
    [ValidatePattern('^[0-9]+:[0-9]+$')]
    [string]$BeklenenKimlik
)

$ErrorActionPreference = 'Continue'
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

function Fail($message, $code = 1) { Write-Host "HATA: $message" -ForegroundColor Red; exit $code }

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runId = [guid]::NewGuid().ToString('N').Substring(0, 12)
$name = "$Database-$stamp-$runId.dump"
$remote = "/tmp/backup-$runId.dump"
$partial = "backups\$name.partial"
New-Item -ItemType Directory -Force 'backups' | Out-Null
if ((Test-Path "backups\$name") -or (Test-Path $partial)) { Fail "Ayni adli yedek zaten var: $name" }

function Get-ServerIdentity {
    $sql = "SELECT (SELECT system_identifier FROM pg_control_system())::text || ':' || " +
           "(SELECT oid FROM pg_database WHERE datname = current_database())::text"
    $out = docker compose exec -T postgres psql -U postgres -d $Database -Atc $sql
    if ($LASTEXITCODE -ne 0 -or -not $out) { return '' }
    return ([string]($out | Select-Object -Last 1)).Trim()
}

if ($BeklenenKimlik) {
    $actual = Get-ServerIdentity
    if ($actual -ne $BeklenenKimlik) {
        Fail "yedeklenecek sunucu seed baglantisinin sunucusu degil (beklenen $BeklenenKimlik, bulunan $actual)." 2
    }
}

docker compose exec -T postgres pg_dump -U postgres -d $Database -Fc -f $remote
if ($LASTEXITCODE -ne 0) { docker compose exec -T postgres rm -f $remote; Fail 'pg_dump basarisiz.' }

# The archive must be readable before it is published.
docker compose exec -T postgres pg_restore --list $remote *> $null
if ($LASTEXITCODE -ne 0) { docker compose exec -T postgres rm -f $remote; Fail 'yedek okunamadi (pg_restore --list).' }

docker compose cp "postgres:$remote" $partial
$copyCode = $LASTEXITCODE
docker compose exec -T postgres rm -f $remote
if ($copyCode -ne 0) { Remove-Item $partial -ErrorAction SilentlyContinue; Fail 'yedek kopyalanamadi.' }

if ($BeklenenKimlik -and (Get-ServerIdentity) -ne $BeklenenKimlik) {
    Remove-Item $partial
    Fail 'yedek sirasinda sunucu degisti; yedek kullanilmadi.' 2
}

$hash = (Get-FileHash $partial -Algorithm SHA256).Hash.ToLower()
[IO.File]::WriteAllText("$PWD\backups\$name.sha256", "$hash  $name`n")
try {
    [IO.File]::Move("$PWD\$partial", "$PWD\backups\$name")  # hedef varsa hata verir; uzerine yazmaz
} catch {
    Remove-Item "backups\$name.sha256" -ErrorAction SilentlyContinue
    Fail "yedek yayimlanamadi: $($_.Exception.Message)"
}
Write-Host "Yedek: backups\$name"
