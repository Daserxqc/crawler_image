# 本机一键：从云主机拉 to_local 并入库（改下面 $Vps / $RemoteRoot）
param(
  [string]$Vps = "user@your-vps",
  [string]$RemoteRoot = "/opt/crawler_image",
  [string]$Db = "output/tax_hr.db"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$LocalFrom = "output/cloud_sync/from_cloud"
New-Item -ItemType Directory -Force -Path $LocalFrom | Out-Null

Write-Host "Downloading from ${Vps}:${RemoteRoot}/output/cloud_sync/to_local ..."
scp -r "${Vps}:${RemoteRoot}/output/cloud_sync/to_local/." $LocalFrom
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

python scripts/local_pull_ingest.py --db $Db
