# Explicit administrator step: medakis_owner -> medakis_seed role membership (SET only).
# Cluster-level change. setup.ps1 never runs this; it must be started on purpose with -Onayla.
#
# Usage (administrator, current Compose server):
#   powershell -ExecutionPolicy Bypass -File scripts\db\seed_membership.ps1 -Onayla
# Usage (bound to one verified container; used by the isolated rehearsal tests):
#   ... seed_membership.ps1 -KonteynerKimligi <docker id> -BeklenenSistemKimligi <system_identifier> -Onayla
#
# The SQL (docker\postgres\seed_membership.sql) is one DO statement executed with
# psql --single-transaction: an unexpected existing membership is refused before any change, and a
# failed verification rolls the grant back.
#
# With -KonteynerKimligi the target is that immutable Docker container: Compose is never consulted,
# so a changed Compose project or Docker context cannot redirect the command. The expected
# PostgreSQL system_identifier is set in the same transaction and checked inside the DO statement
# before any grant; a different PostgreSQL instance makes the whole transaction roll back.

param(
    [switch]$Onayla,
    [string]$KonteynerKimligi = '',
    [string]$BeklenenSistemKimligi = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
function Fail($message) { Write-Host "HATA: $message" -ForegroundColor Red; exit 1 }

$sql = Get-Content -Raw 'docker\postgres\seed_membership.sql'

if ($KonteynerKimligi -or $BeklenenSistemKimligi) {
    if ($KonteynerKimligi -notmatch '^[0-9a-f]{12,64}$') { Fail 'Gecersiz konteyner kimligi. Hicbir sey yapilmadi.' }
    if ($BeklenenSistemKimligi -notmatch '^[0-9]+$') { Fail 'Konteyner kimligiyle birlikte beklenen sistem kimligi zorunlu. Hicbir sey yapilmadi.' }
    $state = docker inspect --format '{{.State.Running}} {{.Name}}' $KonteynerKimligi 2>$null
    if ($LASTEXITCODE -ne 0 -or -not ([string]$state).StartsWith('true ')) {
        Fail 'Belirtilen konteyner calismiyor veya yok. Hicbir sey yapilmadi.'
    }
    Write-Host "Hedef konteyner (sabit kimlik): $KonteynerKimligi $(([string]$state).Split(' ')[1])"
    if (-not $Onayla) { Fail 'Kume duzeyinde rol degisikligi: devam icin -Onayla ekleyin. Hicbir sey yapilmadi.' }
    ("SET LOCAL medakis.beklenen_sistem = '$BeklenenSistemKimligi';`n" + $sql) |
        docker exec -i $KonteynerKimligi psql -U postgres -d postgres -v ON_ERROR_STOP=1 --single-transaction -q -f -
} else {
    $container = (docker compose ps --format '{{.Name}}' postgres | Select-Object -First 1)
    if (-not $container) { Fail 'Hedef PostgreSQL konteyneri calismiyor.' }
    Write-Host "Hedef konteyner: $container"
    if (-not $Onayla) { Fail 'Kume duzeyinde rol degisikligi: devam icin -Onayla ekleyin. Hicbir sey yapilmadi.' }
    $sql | docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 --single-transaction -q -f -
}
if ($LASTEXITCODE -ne 0) { Fail 'Rol uyeligi kurulmadi; mevcut durum degismedi.' }
Write-Host 'Rol uyeligi hazir (yalniz SET).' -ForegroundColor Green
