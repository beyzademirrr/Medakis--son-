# MedAkis PostgreSQL first-time setup. Safe to run again (idempotent).
# Passwords are typed by the user, hidden, and are never written to a file.
#
# Usage (project root):
#   powershell -ExecutionPolicy Bypass -File scripts\db\setup.ps1

$ErrorActionPreference = 'Continue'
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

function Fail($message) { Write-Host "HATA: $message" -ForegroundColor Red; exit 1 }

function Read-Secret($prompt) {
    $secure = Read-Host $prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Docker motoru calismiyor. Docker Desktop uygulamasini acin.' }

# Initialized means PG_VERSION exists in the volume, not merely that the volume exists:
# an interrupted first attempt can leave an empty volume behind.
docker compose stop postgres *> $null
docker compose run --rm --no-deps -T --entrypoint test postgres -f /var/lib/postgresql/18/docker/PG_VERSION *> $null
$initialized = ($LASTEXITCODE -eq 0)

if ($initialized) {
    Write-Host 'medakis_pgdata zaten kurulu: yonetici parolasi sorulmayacak, veri korunacak.'
} else {
    Write-Host 'Ilk kurulum: PostgreSQL yonetici (postgres) parolasini belirleyin.'
    Write-Host 'Yazarken ekranda gorunmez. Dosyaya kaydedilmez; parola yoneticinizde saklayin.'
    while ($true) {
        $first = Read-Secret 'Yonetici parolasi (en az 12 karakter)'
        $second = Read-Secret 'Yonetici parolasi (tekrar)'
        if ($first.Length -lt 12) { Write-Host 'En az 12 karakter olmali.'; continue }
        if ($first -cne $second) { Write-Host 'Parolalar eslesmedi.'; continue }
        break
    }
    $env:MEDAKIS_PG_SUPERUSER_PASSWORD = $first
    $first = $null; $second = $null
}

docker compose up -d --wait
$upCode = $LASTEXITCODE
Remove-Item Env:\MEDAKIS_PG_SUPERUSER_PASSWORD -ErrorAction SilentlyContinue
if ($upCode -ne 0) { Fail 'PostgreSQL baslatilamadi. Kontrol: docker compose logs postgres' }

# Remove the password from the container configuration (docker inspect). Data is unaffected.
if (-not $initialized) {
    docker compose up -d --wait --force-recreate
    if ($LASTEXITCODE -ne 0) { Fail 'Konteyner yeniden olusturulamadi.' }
}

Write-Host 'Roller ve veritabanlari hazirlaniyor...'
Get-Content -Raw 'docker\postgres\provision.sql' |
    docker compose exec -T postgres psql -q -U postgres -d postgres -v ON_ERROR_STOP=1 -f -
if ($LASTEXITCODE -ne 0) { Fail 'provision.sql basarisiz oldu.' }

# Rol uyeligi (medakis_owner -> medakis_seed) burada KURULMAZ: ayri, acik yonetici adimidir.
#   powershell -ExecutionPolicy Bypass -File scripts\db\seed_membership.ps1 -Onayla

foreach ($role in 'medakis_owner', 'medakis_app', 'medakis_rag', 'medakis_seed') {
    while ($true) {
        $state = docker compose exec -T postgres psql -U postgres -d postgres -Atc `
            "SELECT rolpassword IS NOT NULL FROM pg_authid WHERE rolname = '$role'"
        if ($state -eq 't') { Write-Host "$role parolasi tanimli."; break }
        Write-Host "$role icin parola belirleyin (gizli giris, sunucuya hash olarak gider):"
        docker compose exec -it postgres psql -U postgres -d postgres -c "\password $role"
    }
}

Write-Host ''
Write-Host 'Kurulum tamamlandi.' -ForegroundColor Green
docker compose ps
