# Restores a backup into one MedAkis database. REPLACES that database's objects.
# Requires -Onayla. Checks the SHA-256 file and takes a safety backup first.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\db\restore.ps1 -Database medakis_test -File backups\x.dump -Onayla
#
# medakis_dev and medakis_demo restores are DISABLED (FAZ 1.2C closing decision). The refusal below
# is the first statement: no file check, no safety backup, no docker or database command runs.
# Re-enabling them is a separate infrastructure task: docs/gorevler/ALTYAPI_DEV_DEMO_GERI_YUKLEME.md

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('medakis_dev', 'medakis_test', 'medakis_demo')]
    [string]$Database,
    [Parameter(Mandatory = $true)]
    [string]$File,
    [switch]$Onayla,
    # Eski parametre; dev/demo geri yuklemesi devre disi oldugu icin kullanilmaz.
    [string]$DemoOnayi = ''
)

if ($Database -ne 'medakis_test') {
    Write-Host "HATA: $Database icin geri yukleme gecici olarak devre disi. Hicbir islem yapilmadi (yedek alinmadi, veritabanina baglanilmadi)." -ForegroundColor Red
    Write-Host 'Ayrinti: docs/gorevler/ALTYAPI_DEV_DEMO_GERI_YUKLEME.md'
    exit 3
}

$ErrorActionPreference = 'Continue'
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

function Fail($message) { Write-Host "HATA: $message" -ForegroundColor Red; exit 1 }

if (-not $Onayla) { Fail "$Database icerigi degisecek. Devam icin -Onayla ekleyin." }
if ($Database -eq 'medakis_demo' -and $DemoOnayi -cne 'medakis_demo') {
    Fail 'medakis_demo korumali: -DemoOnayi medakis_demo yazilmadan geri yukleme yapilmaz.'
}
if (-not (Test-Path $File -PathType Leaf)) { Fail "Yedek dosyasi bulunamadi: $File" }

$hashFile = "$File.sha256"
if (-not (Test-Path $hashFile -PathType Leaf)) { Fail "SHA-256 dosyasi bulunamadi: $hashFile" }
$expected = ((Get-Content $hashFile -Raw).Trim() -split '\s+')[0]
$actual = (Get-FileHash $File -Algorithm SHA256).Hash.ToLower()
if ($expected -ne $actual) { Fail 'SHA-256 eslesmedi: yedek bozuk olabilir.' }

# Islem baslamadan once: arsiv okunabilir mi, hedefe mi ait, medakis_test disinda eski surum veya
# medakis_seed icin genis yetki iceriyor mu (scripts\db\check_dump.py). Reddedilirse hicbir sey yapilmaz.
$python = Join-Path $PWD '.venv\Scripts\python.exe'
if (-not (Test-Path $python -PathType Leaf)) { Fail '.venv bulunamadi; yedek denetimi yapilamadi, geri yukleme yapilmadi.' }
& $python 'scripts\db\check_dump.py' --database $Database --file $File
if ($LASTEXITCODE -ne 0) { Fail 'Yedek denetimi reddetti; geri yukleme yapilmadi.' }

Write-Host 'Geri yuklemeden once guvenlik yedegi aliniyor...'
& powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\backup.ps1" -Database $Database
if ($LASTEXITCODE -ne 0) { Fail 'Guvenlik yedegi alinamadi; geri yukleme yapilmadi.' }

$remote = "/tmp/restore-$([guid]::NewGuid().ToString('N')).dump"
docker compose cp $File "postgres:$remote"
if ($LASTEXITCODE -ne 0) { Fail 'Yedek konteynere kopyalanamadi.' }

docker compose exec -T postgres pg_restore -U postgres -d $Database --clean --if-exists --single-transaction --exit-on-error $remote
$restoreCode = $LASTEXITCODE
docker compose exec -T postgres rm -f $remote
if ($restoreCode -ne 0) { Fail 'pg_restore basarisiz; islem geri alindi, veritabani degismedi.' }

Write-Host "Geri yukleme tamamlandi: $Database" -ForegroundColor Green
