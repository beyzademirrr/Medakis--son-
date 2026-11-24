<#
Kontrollü corpus: 13 GÜNCEL normal kaynak belgeyi SIRAYLA onaya sunar (yalnız proje sahibi çalıştırır).

    powershell -ExecutionPolicy Bypass -File scripts\rag_belge_onay_toplu.ps1

- Her belge için scripts\rag_belge_onay.py ayrı çalışır ve terminalde "ONAYLIYORUM <kaynak>@<sürüm>"
  ifadesinin ELLE yazılmasını ister. Bu betik hiçbir girdiyi kendisi vermez; onay mekanizmasını atlamaz.
- İfadeyi yazmazsan ya da farklı yazarsan o belge ATLANIR (onaylanmaz) ve sıradakine geçilir.
  Tamamen durmak için Ctrl+C.
- Listede yok: tarihsel SYN-YET-GECERLILIK@1.0 ve güvenlik fikstürleri (SYN-FKS-*). Onay komutu
  fikstürleri zaten reddeder.
- Daha önce onaylanmış belge tekrar onaylanamaz (komut "yalniz_taslak_onaylanir" ile reddeder).
#>

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Komut = Join-Path $Root 'scripts\rag_belge_onay.py'
$Aktor = 'Proje Sahibi'
$Gerekce = 'İçerik kodla karşılaştırıldı ve incelendi; corpus düzeltme raporu b94fe15'

$Belgeler = @(
    'SYN-PRC-DEVIR', 'SYN-PRC-ONAY', 'SYN-PRC-DISA',
    'SYN-ROL-HEMSIRE', 'SYN-ROL-SORUMLU', 'SYN-ROL-DESTEK',
    'SYN-YET-GECERLILIK|2.0', 'SYN-YET-SERVIS', 'SYN-YET-ACIK',
    'SYN-AKS-TAHMIN', 'SYN-AKS-KADRO', 'SYN-AKS-GOREV', 'SYN-AKS-KAYNAK'
)

if (-not (Test-Path $Python)) { throw "Python bulunamadı: $Python" }
Set-Location $Root
$Sonuc = @()
$i = 0
foreach ($b in $Belgeler) {
    $i++
    $parca = $b.Split('|')
    $kaynak = $parca[0]
    $surum = if ($parca.Count -gt 1) { $parca[1] } else { '1.0' }
    Write-Host ''
    Write-Host ("[{0}/{1}] {2}@{3}" -f $i, $Belgeler.Count, $kaynak, $surum) -ForegroundColor Cyan
    & $Python $Komut --kaynak $kaynak --surum $surum --karar onayla --aktor $Aktor --gerekce $Gerekce
    $durum = switch ($LASTEXITCODE) { 0 { 'ONAYLANDI' } 1 { 'ATLANDI (ifade eşleşmedi)' } default { 'REDDEDİLDİ' } }
    $Sonuc += [pscustomobject]@{ Belge = "$kaynak@$surum"; Durum = $durum }
}
Write-Host ''
Write-Host 'Özet:' -ForegroundColor Cyan
$Sonuc | Format-Table -AutoSize
