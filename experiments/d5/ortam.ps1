# D5 izole deney ortamı başlatıcısı. Her komuttan önce nokta-kaynak olarak çalıştırılır:  . .\experiments\d5\ortam.ps1
# - Bütün önbellek ve geçici yollar D:\medakis-deney\d5 altına alınır; biri C:'ye düşerse betik DURUR (fail-closed).
# - HF_TOKEN bu süreçte kaldırılır (modeller herkese açık; token gönderilmez ve kaydedilmez).
# - MedAkis .venv'i ve ana proje bağımlılıkları kullanılmaz.
$ErrorActionPreference = "Stop"
$D5 = "D:\medakis-deney\d5"
foreach ($d in "tmp", "pip-cache", "hf", "hf\hub", "torch", "cuda-cache", "sonuclar", "kurulum") {
    New-Item -ItemType Directory -Force (Join-Path $D5 $d) | Out-Null
}
$env:TMP = "$D5\tmp"; $env:TEMP = "$D5\tmp"; $env:TMPDIR = "$D5\tmp"
$env:PIP_CACHE_DIR = "$D5\pip-cache"; $env:HF_HOME = "$D5\hf"; $env:HF_HUB_CACHE = "$D5\hf\hub"
$env:TORCH_HOME = "$D5\torch"; $env:CUDA_CACHE_PATH = "$D5\cuda-cache"; $env:XDG_CACHE_HOME = "$D5\tmp"
$env:PYTHONNOUSERSITE = "1"; $env:PIP_REQUIRE_VIRTUALENV = "1"; $env:PIP_NO_INPUT = "1"
$env:HF_HUB_DISABLE_TELEMETRY = "1"; $env:HF_HUB_DISABLE_IMPLICIT_TOKEN = "1"; $env:TOKENIZERS_PARALLELISM = "false"
Remove-Item Env:HF_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:HUGGING_FACE_HUB_TOKEN -ErrorAction SilentlyContinue
foreach ($v in "TMP", "TEMP", "TMPDIR", "PIP_CACHE_DIR", "HF_HOME", "HF_HUB_CACHE", "TORCH_HOME", "CUDA_CACHE_PATH", "XDG_CACHE_HOME") {
    $val = (Get-Item "Env:$v").Value
    if (-not $val.StartsWith("D:\medakis-deney\")) { throw "C: geri dönüşü engellendi: $v=$val" }
}
if (Test-Path Env:HF_TOKEN) { throw "HF_TOKEN kaldırılamadı" }
$PY = "$D5\.venv\Scripts\python.exe"
