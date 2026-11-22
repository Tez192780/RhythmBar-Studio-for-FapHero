# 源码异地快照：打包工作区（不含 .venv/.git/缓存）+ 一份 git bundle（可还原完整历史）。
#
# 用法（在项目任意位置，Windows PowerShell 5.1 即可）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File devtools\backup.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File devtools\backup.ps1 -Dest E:\bk -Keep 20
#
# 产物（同一时间戳配对，方便整对彻底删除）：
#   Beatbar-Maker-<时间戳>.zip     工作区源码快照，可直接解压查看/运行
#   Beatbar-Maker-<时间戳>.bundle  git 历史，`git clone <bundle> <dir>` 可完整还原
#
# 设计取舍：不备份 .venv（约 730MB，可从 requirements.txt 重建）与 .git（由 bundle 覆盖）。

[CmdletBinding()]
param(
    [string]$Dest = 'F:\DSH-Backups\Beatbar-Maker',
    [int]$Keep = 10
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$base = Join-Path $Dest "Beatbar-Maker-$stamp"

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# ---- 1. 工作区 zip（排除依赖与缓存）
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$skipDirs = @('.venv', '.git', '__pycache__', '.wheels', 'build', 'dist', '.idea', '.vscode')
$skipFile = @('*.pyc', '*.pyo', 'rbar_settings.json.tmp', '*.log')

$files = Get-ChildItem -LiteralPath $root -Recurse -File -Force | Where-Object {
    $rel = $_.FullName.Substring($root.Length + 1)
    $parts = $rel -split '[\\/]'
    $inSkip = $false
    foreach ($p in $parts[0..([Math]::Max(0, $parts.Count - 2))]) {
        if ($skipDirs -contains $p) { $inSkip = $true; break }
    }
    if ($inSkip) { return $false }
    foreach ($pat in $skipFile) { if ($_.Name -like $pat) { return $false } }
    return $true
}

$zipPath = "$base.zip"
$fs = [IO.File]::Create($zipPath)
$zip = New-Object IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($root.Length + 1).Replace('\', '/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $rel, 'Optimal') | Out-Null
    }
}
finally {
    $zip.Dispose()
    $fs.Dispose()
}

# ---- 2. git bundle（完整历史，含 tag）
$bundlePath = "$base.bundle"
$hasGit = Test-Path (Join-Path $root '.git')
if ($hasGit) {
    git -C $root bundle create $bundlePath --all 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warning 'git bundle 创建失败，本次只有 zip 快照' }
}

# ---- 3. 清理旧快照（按时间戳，成对保留 $Keep 份）
$all = Get-ChildItem -LiteralPath $Dest -File | Where-Object { $_.Name -match '^Beatbar-Maker-\d{8}-\d{6}\.(zip|bundle)$' }
$groups = $all | Group-Object { $_.Name.Substring(0, $_.Name.LastIndexOf('.')) } |
    Sort-Object Name -Descending
if ($groups.Count -gt $Keep) {
    foreach ($g in ($groups | Select-Object -Skip $Keep)) {
        foreach ($f in $g.Group) { Remove-Item -LiteralPath $f.FullName -Force }
        Write-Host ("  已清理旧快照 " + $g.Name)
    }
}

# ---- 4. 汇报
$zipMB = [Math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Write-Host ''
Write-Host "备份完成 -> $Dest"
Write-Host ("  {0}.zip    {1} MB  {2} 个文件" -f (Split-Path -Leaf $base), $zipMB, $files.Count)
if (Test-Path $bundlePath) {
    $nb = [Math]::Round((Get-Item $bundlePath).Length / 1KB, 1)
    Write-Host ("  {0}.bundle {1} KB  (git clone 可完整还原历史)" -f (Split-Path -Leaf $base), $nb)
}
Write-Host ("  当前保留 {0} 份（-Keep 调整）；恢复：Expand-Archive 或 git clone <bundle>" -f [Math]::Min($groups.Count, $Keep))
