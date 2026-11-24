# MedAkis demo başlatıcı (Windows PowerShell). Proje kökünde:  .\baslat.ps1
#   -ModelHazirla : Random Forest iş yükü modellerini sentetik veriden yerel olarak eğitir (birkaç dakika; isteğe bağlı).
#   -Klasik       : Vardiya planı inceleme ekranını (klasik arayüz) açar.
param([switch]$ModelHazirla, [switch]$Klasik)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Sanal ortam oluşturuluyor (Python 3.14)..."
    py -3.14 -m venv .venv
}
& $py -c "import streamlit, ortools, sklearn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Bağımlılıklar kuruluyor (ilk kurulum birkaç dakika sürebilir)..."
    & $py -m pip install -r requirements.txt
}
if ($ModelHazirla -and -not (Test-Path "artifacts\forecasting\asama4_seed42")) {
    Write-Host "Random Forest modelleri sentetik veriden eğitiliyor..."
    & $py scripts\asama4_egit.py --cikti artifacts\forecasting\asama4_seed42
    & $py scripts\asama5_u1_kalibrasyon.py
}
$app = if ($Klasik) { @("app.py", "--", "--demo") } else { @("dashboard.py") }
Write-Host "MedAkis açılıyor: http://localhost:8501  (durdurmak için Ctrl+C)"
Start-Process "http://localhost:8501"
& $py -m streamlit run @app --server.headless true --server.port 8501
