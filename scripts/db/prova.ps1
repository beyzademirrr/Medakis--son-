# Izole prova kumesi: gercek medakis-postgres'e ve medakis_pgdata'ya dokunmadan ayni imajla ayri bir
# PostgreSQL kurar, provision.sql + seed_membership.ps1 (yonetici adimi) uygular, rollerin parolalarini kimlik
# deposundan SCRAM dogrulayicisi olarak verir ve test paketini bu kumeye karsi calistirir.
#
#   powershell -ExecutionPolicy Bypass -File scripts\db\prova.ps1 -Islem kur
#   powershell -ExecutionPolicy Bypass -File scripts\db\prova.ps1 -Islem test
#   powershell -ExecutionPolicy Bypass -File scripts\db\prova.ps1 -Islem kaldir
#
# Konteyner: medakis-prova-postgres, volume: medakis_prova_pgdata, port: 127.0.0.1:5433.
# "test": dort pytest grubunu calistirir (varsayilan, postgres, prova_rol, lifecycle); herhangi biri
# basarisizsa betik 1 ile biter. prova_rol (rol uyeligi degistiren testler) yalniz burada, bu betigin
# dogruladigi prova konteynerinin degismez kimligi ve PostgreSQL system_identifier degeri verilerek
# etkinlesir (MEDAKIS_PROVA_KONTEYNER_ID / MEDAKIS_PROVA_SISTEM_KIMLIGI).
# -Hedef: yalniz cikis kodu dogrulamasi icin; tum paket yerine verilen pytest hedefini her grupta calistirir.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('kur', 'test', 'kaldir')]
    [string]$Islem,
    [string]$Hedef = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
function Fail($message) { Write-Host "HATA: $message" -ForegroundColor Red; exit 1 }

$ProvaVolume = 'medakis_prova_pgdata'
$ProvaContainer = 'medakis-prova-postgres'
$env:COMPOSE_PROJECT_NAME = 'medakis-prova'
$env:COMPOSE_FILE = 'compose.yaml;docker\prova\compose.prova.yaml'
$env:MEDAKIS_PG_PORT = '5433'
$env:PGPORT = '5433'
$env:MEDAKIS_PG_CONTAINER = $ProvaContainer
$env:MEDAKIS_PG_VOLUME = $ProvaVolume

# Guvenlik: bu betik yalniz prova adlariyla calisir.
$resolved = docker compose config --format json | ConvertFrom-Json
if ($resolved.name -ne 'medakis-prova' -or $resolved.services.postgres.container_name -ne $ProvaContainer -or
    $resolved.volumes.pgdata.name -ne $ProvaVolume) { Fail 'Compose yapilandirmasi prova degil; durduruldu.' }

$python = Join-Path $PWD '.venv\Scripts\python.exe'
function Psql($db, $sql) { docker compose exec -T postgres psql -U postgres -d $db -v ON_ERROR_STOP=1 -Atc $sql }

# Dogrulanmis prova hedefi: Compose'un sectigi tek konteyner; ad, proje etiketi ve tek volume prova
# kumesininki olmali. Donen degerler degismez konteyner kimligi ve PostgreSQL system_identifier'dir.
function Get-ProvaTarget {
    $ids = @(docker compose ps -q postgres | Where-Object { $_ })
    if ($ids.Count -ne 1) { return $null }
    $info = (docker inspect $ids[0] | ConvertFrom-Json)[0]
    $volumes = @($info.Mounts | Where-Object { $_.Type -eq 'volume' } | ForEach-Object { $_.Name })
    if ($info.Name.TrimStart('/') -ne $ProvaContainer -or
        $info.Config.Labels.'com.docker.compose.project' -ne 'medakis-prova' -or
        $volumes.Count -ne 1 -or $volumes[0] -ne $ProvaVolume -or -not $info.State.Running) { return $null }
    $sysid = (docker exec $info.Id psql -U postgres -d postgres -Atc 'SELECT system_identifier FROM pg_control_system()' | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or ([string]$sysid).Trim() -notmatch '^[0-9]+$') { return $null }
    return @{ Id = $info.Id; Sysid = ([string]$sysid).Trim() }
}

if ($Islem -eq 'kur') {
    docker volume inspect $ProvaVolume *> $null
    if ($LASTEXITCODE -eq 0) { Fail "$ProvaVolume zaten var; once -Islem kaldir." }
    # Rastgele, saklanmayan superuser parolasi: yalniz ilk baslatmada gerekir.
    $env:MEDAKIS_PG_SUPERUSER_PASSWORD = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    docker compose up -d --wait
    $code = $LASTEXITCODE
    Remove-Item Env:\MEDAKIS_PG_SUPERUSER_PASSWORD
    if ($code -ne 0) { Fail 'prova kumesi baslatilamadi.' }
    docker compose up -d --wait --force-recreate   # parola konteyner yapilandirmasindan cikar
    if ($LASTEXITCODE -ne 0) { Fail 'prova kumesi yeniden olusturulamadi.' }

    Get-Content -Raw 'docker\postgres\provision.sql' | docker compose exec -T postgres psql -q -U postgres -d postgres -v ON_ERROR_STOP=1 -f -
    if ($LASTEXITCODE -ne 0) { Fail 'provision.sql basarisiz.' }

    Write-Host '== Uyelik oncesi: owner seed rolune gecebilir mi? (beklenen: hayir)'
    docker compose exec -T postgres psql -U medakis_owner -d medakis_test -Atc 'SET ROLE medakis_seed' 2>&1 | Write-Host
    Write-Host '== Yonetici adimi seed_membership.ps1, sabit konteyner kimligine bagli (iki kez: idempotent olmali)'
    $target = Get-ProvaTarget
    if (-not $target) { Fail 'prova hedefi dogrulanamadi; uyelik adimi calistirilmadi.' }
    foreach ($i in 1, 2) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File 'scripts\db\seed_membership.ps1' -KonteynerKimligi $target.Id -BeklenenSistemKimligi $target.Sysid -Onayla
        if ($LASTEXITCODE -ne 0) { Fail "seed_membership.ps1 basarisiz ($i)." }
    }
    Write-Host '== Uyelik sonrasi'
    Psql 'postgres' "SELECT 'uyelik satiri: ' || count(*) || ', admin/inherit/set: ' || string_agg(admin_option || '/' || inherit_option || '/' || set_option, ',') FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member WHERE u.rolname LIKE 'medakis_%' OR r.rolname LIKE 'medakis_%'"
    Psql 'postgres' "SELECT 'owner->seed SET: ' || pg_has_role('medakis_owner','medakis_seed','SET') || ', USAGE(miras): ' || pg_has_role('medakis_owner','medakis_seed','USAGE') || ', owner->app SET: ' || pg_has_role('medakis_owner','medakis_app','SET') || ', seed->owner SET: ' || pg_has_role('medakis_seed','medakis_owner','SET')"
    Psql 'postgres' "SELECT 'owner superuser/createrole: ' || rolsuper || '/' || rolcreaterole FROM pg_roles WHERE rolname = 'medakis_owner'"
    docker compose exec -T postgres psql -U medakis_owner -d medakis_test -Atc "BEGIN; SET LOCAL ROLE medakis_seed; SELECT 'islem ici rol: ' || current_user; ROLLBACK;" 2>&1 | Write-Host
    docker compose exec -T postgres psql -U medakis_owner -d medakis_test -Atc 'SET ROLE medakis_app' 2>&1 | Write-Host

    & $python 'scripts\db\prova_passwords.py' | docker compose exec -T postgres psql -q -U postgres -d postgres -v ON_ERROR_STOP=1 -f -
    if ($LASTEXITCODE -ne 0) { Fail 'rol parolalari ayarlanamadi.' }
    & $python -m alembic -x db=medakis_dev upgrade 0003_seed_write_window  # gercek kume gibi: medakis_dev 0003'te kalir (D11)
    if ($LASTEXITCODE -ne 0) { Fail 'prova medakis_dev migration basarisiz.' }
    Write-Host 'Prova kumesi hazir.' -ForegroundColor Green
    exit 0
}

if ($Islem -eq 'test') {
    $codes = @()
    foreach ($marker in @('', 'postgres', 'prova_rol', 'postgres_lifecycle')) {
        $pyArgs = @('-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-rs')
        if ($marker) { $pyArgs += @('-m', $marker) }
        if ($Hedef) { $pyArgs += $Hedef }
        Remove-Item Env:\MEDAKIS_PROVA_KONTEYNER_ID, Env:\MEDAKIS_PROVA_SISTEM_KIMLIGI -ErrorAction SilentlyContinue
        if ($marker -eq 'prova_rol' -and -not $Hedef) {
            # Yalniz bu grupta ve hemen oncesinde dogrulanan hedef verilir (lifecycle konteyneri yeniden olusturur).
            $target = Get-ProvaTarget
            if (-not $target) { Write-Host 'HATA: prova hedefi dogrulanamadi; prova_rol grubu calistirilmadi.' -ForegroundColor Red; $codes += 1; continue }
            $env:MEDAKIS_PROVA_KONTEYNER_ID = $target.Id
            $env:MEDAKIS_PROVA_SISTEM_KIMLIGI = $target.Sysid
        }
        & $python @pyArgs
        $codes += $LASTEXITCODE
        Remove-Item Env:\MEDAKIS_PROVA_KONTEYNER_ID, Env:\MEDAKIS_PROVA_SISTEM_KIMLIGI -ErrorAction SilentlyContinue
    }
    Write-Host "Prova test cikis kodlari (varsayilan, postgres, prova_rol, lifecycle): $($codes -join ', ')"
    if ($codes | Where-Object { $_ -ne 0 }) { Write-Host 'HATA: en az bir test grubu basarisiz.' -ForegroundColor Red; exit 1 }
    exit 0
}

if ($Islem -eq 'kaldir') {
    docker compose down          # yalniz prova konteyneri ve agi (-v KULLANILMAZ)
    if ($ProvaVolume -eq 'medakis_pgdata') { Fail 'volume adi gercek volume ile ayni; silinmedi.' }
    docker volume rm $ProvaVolume
    docker volume inspect medakis_pgdata *> $null
    if ($LASTEXITCODE -ne 0) { Fail 'GERCEK medakis_pgdata bulunamadi!' }
    Write-Host 'Prova kumesi kaldirildi; medakis_pgdata yerinde.' -ForegroundColor Green
}
