<#
.SYNOPSIS
    檢查 PySoemTool 在這台電腦上執行所需的條件。完全離線、唯讀，不會修改任何設定。

.DESCRIPTION
    檢查項目：作業系統位元數、程式檔案（PyInstaller 打包版或 Python 原始碼版）、
    Python 3.12 / venv / pysoem / tkinter（原始碼版）、Visual C++ Runtime、
    Npcap（驅動、DLL、僅限管理員選項）、程式資料夾寫入權限、有線網卡與會影響 EtherCAT 時序的進階設定。

    結果分為 [OK] / [WARN] / [FAIL] / [INFO]。有任何 FAIL 時結束代碼為 1。
    不使用網路；會執行程式資料夾中的 python.exe 做 import 測試（不安裝、不修改任何東西）。

.PARAMETER AppDir
    程式資料夾（PySoemTool.exe 或 pysoem_tool\ 所在的資料夾）。預設為此腳本所在資料夾的上一層。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\check_prerequisites.ps1
.EXAMPLE
    .\tools\check_prerequisites.cmd -AppDir D:\PySoemTool
#>
[CmdletBinding()]
param(
    [string]$AppDir
)

$ErrorActionPreference = 'Continue'
$script:failCount = 0
$script:warnCount = 0

function Write-Result([string]$level, [string]$title, [string]$detail = '') {
    switch ($level) {
        'OK'   { $color = 'Green' }
        'WARN' { $color = 'Yellow'; $script:warnCount++ }
        'FAIL' { $color = 'Red'; $script:failCount++ }
        default { $color = 'Gray' }
    }
    Write-Host ("[{0,-4}] {1}" -f $level, $title) -ForegroundColor $color
    if ($detail) { $detail -split "`n" | ForEach-Object { Write-Host "       $_" } }
}

function Write-Section([string]$name) {
    Write-Host ''
    Write-Host "== $name ==" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- locate app
if (-not $AppDir) { $AppDir = Split-Path -Parent $PSScriptRoot }
$AppDir = [IO.Path]::GetFullPath($AppDir)
$sys32 = [Environment]::SystemDirectory

Write-Host "PySoemTool 環境檢查   $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "電腦: $env:COMPUTERNAME   程式資料夾: $AppDir"

# ---------------------------------------------------------------- OS
Write-Section '作業系統'
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$osText = if ($os) { "$($os.Caption) $($os.Version) build $($os.BuildNumber)" } else { [Environment]::OSVersion.VersionString }
if ([Environment]::Is64BitOperatingSystem) { Write-Result 'OK' '64 位元 Windows' $osText }
else { Write-Result 'FAIL' '需要 64 位元 Windows（pysoem 只提供 win_amd64 wheel）' $osText }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Result 'INFO' ('目前' + $(if ($isAdmin) { '以系統管理員身分' } else { '以一般使用者身分' }) + '執行此檢查')

# ---------------------------------------------------------------- app files
Write-Section '程式檔案'
$frozenExe = Join-Path $AppDir 'PySoemTool.exe'
$srcPkg = Join-Path $AppDir 'pysoem_tool\__main__.py'
$mode = $null
if (Test-Path $frozenExe) {
    $mode = 'frozen'
    Write-Result 'OK' 'PySoemTool.exe（PyInstaller 打包版，不需要另外安裝 Python）' $frozenExe
    $internal = Join-Path $AppDir '_internal'
    if (-not (Test-Path $internal)) {
        Write-Result 'FAIL' '缺少 _internal 資料夾' '打包資料夾必須整個複製，不能只拿 exe。'
    } else {
        $pyd = Get-ChildItem $internal -Recurse -Filter 'pysoem*.pyd' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pyd) { Write-Result 'OK' 'pysoem 原生模組' $pyd.FullName } else { Write-Result 'FAIL' '_internal 中找不到 pysoem 原生模組 (pysoem*.pyd)' '重新打包，或整個資料夾重新複製。' }
        if (-not (Get-ChildItem $internal -Recurse -Filter 'tokens.json' -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            Write-Result 'FAIL' '_internal 中找不到 design\tokens.json' '重新打包（PySoemTool.spec 會帶入此檔）。'
        }
    }
} elseif (Test-Path $srcPkg) {
    $mode = 'source'
    Write-Result 'OK' 'pysoem_tool 原始碼' (Split-Path $srcPkg)
    foreach ($f in 'pysoem_tool\design\tokens.json', 'pysoem_tool\services\ethercat.py', 'pysoem_tool\motion\controller.py', 'requirements.txt') {
        if (-not (Test-Path (Join-Path $AppDir $f))) { Write-Result 'FAIL' "缺少 $f" '程式資料夾必須整個複製。' }
    }
} else {
    Write-Result 'FAIL' '找不到 PySoemTool.exe 或 pysoem_tool\' '請用 -AppDir 指定程式資料夾，或確認整個資料夾都有複製過來。'
}

$blocked = Get-ChildItem $AppDir -File -Recurse -Depth 2 -ErrorAction SilentlyContinue |
    Where-Object { Get-Item $_.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue }
if ($blocked) {
    Write-Result 'WARN' "有 $(@($blocked).Count) 個檔案被標記為來自網際網路（可能被封鎖）" "可用：Get-ChildItem '$AppDir' -Recurse | Unblock-File"
}

# ---------------------------------------------------------------- Python (source mode)
if ($mode -eq 'source') {
    Write-Section 'Python 與套件（原始碼版）'
    $py = @((Join-Path $AppDir '.venv\Scripts\python.exe'), (Join-Path $AppDir 'python\python.exe')) |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $py) {
        Write-Result 'FAIL' '找不到 .venv\Scripts\python.exe' "依 docs\DEPLOYMENT.md 建立虛擬環境：`n  py -3.12 -m venv .venv`n  .venv\Scripts\python -m pip install --no-index --find-links offline\wheels -r requirements.txt"
    } else {
        $ver = & $py -I -c "import sys;print('%d.%d.%d' % sys.version_info[:3], sys.maxsize > 2**32)" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Result 'FAIL' "無法執行 $py" ("$ver`n虛擬環境指向的 Python 可能已被移除；請重新建立 .venv。")
        } else {
            $parts = "$ver".Trim().Split(' ')
            $v = [version]$parts[0]
            if ($parts[1] -ne 'True') { Write-Result 'FAIL' "Python $($parts[0]) 是 32 位元" '需要 64 位元 Python。' }
            elseif ($v.Major -eq 3 -and $v.Minor -ge 10 -and $v.Minor -le 13) {
                Write-Result 'OK' "Python $($parts[0]) (64 位元)" $py
            } else {
                Write-Result 'WARN' "Python $($parts[0])：未驗證的版本" '本工具以 Python 3.12 驗證；其他版本可能沒有 pysoem wheel。'
            }

            $tk = & $py -I -c "import tkinter;print(tkinter.TkVersion)" 2>&1
            if ($LASTEXITCODE -eq 0) { Write-Result 'OK' "tkinter (Tk $("$tk".Trim()))" }
            else { Write-Result 'FAIL' 'tkinter 無法載入' "安裝 Python 時要勾選 tcl/tk and IDLE。`n$tk" }

            $code = "import os,sys;d=os.path.join(os.environ.get('SystemRoot','C:\\Windows'),'System32','Npcap');" +
                    "os.path.isdir(d) and os.add_dll_directory(d);import pysoem;print(pysoem.__version__ if hasattr(pysoem,'__version__') else '?')"
            $ps = & $py -I -c $code 2>&1
            if ($LASTEXITCODE -eq 0) { Write-Result 'OK' "pysoem 可載入（版本 $("$ps".Trim()))" }
            else {
                $msg = ($ps | Out-String).Trim().Split("`n")[-1]
                if ($msg -match 'No module named') {
                    Write-Result 'FAIL' 'pysoem 未安裝' ".venv\Scripts\python -m pip install --no-index --find-links offline\wheels -r requirements.txt"
                } else {
                    Write-Result 'FAIL' 'pysoem 載入失敗' "$msg`n通常是缺 Npcap 或 Visual C++ Runtime（見下方檢查與 docs\TROUBLESHOOTING.md）。"
                }
            }
        }
    }
    $wheels = Join-Path $AppDir 'offline\wheels'
    if (Test-Path $wheels) {
        $n = @(Get-ChildItem $wheels -Filter *.whl).Count
        Write-Result 'INFO' "offline\wheels：$n 個 wheel（離線安裝用）"
    }
}

# ---------------------------------------------------------------- VC++
Write-Section 'Visual C++ Runtime（Python 與 pysoem 需要 VCRUNTIME140.dll）'
$vcSys = Join-Path $sys32 'vcruntime140.dll'
$vcReg = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
$vcLocal = @((Join-Path $AppDir '_internal\vcruntime140.dll'), (Join-Path $AppDir 'vcruntime140.dll')) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (Test-Path $vcSys) {
    $ver = (Get-Item $vcSys).VersionInfo.FileVersion
    $regText = if ($vcReg -and $vcReg.Installed -eq 1) { "Redistributable $($vcReg.Version)" } else { '（登錄檔沒有 Redistributable 安裝紀錄，可能由其他程式帶入）' }
    Write-Result 'OK' 'vcruntime140.dll (System32)' "$ver  $regText"
} elseif ($vcLocal) {
    Write-Result 'WARN' 'System32 沒有 vcruntime140.dll，只有程式資料夾內的副本' "$vcLocal`n建議安裝 Microsoft Visual C++ 2015-2022 Redistributable (x64)。"
} else {
    Write-Result 'FAIL' '找不到 VCRUNTIME140.dll' '安裝 Microsoft Visual C++ 2015-2022 Redistributable (x64)：vc_redist.x64.exe（可離線安裝）。'
}

# ---------------------------------------------------------------- Npcap
Write-Section 'Npcap'
$npcapDir = Join-Path $sys32 'Npcap'
$npcapDlls = 'wpcap.dll', 'Packet.dll' | ForEach-Object { Join-Path $npcapDir $_ }
$svc = Get-Service npcap -ErrorAction SilentlyContinue
$params = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\npcap\Parameters' -ErrorAction SilentlyContinue

if (-not $svc) {
    if (Test-Path (Join-Path $sys32 'wpcap.dll')) {
        Write-Result 'FAIL' '沒有 Npcap 驅動，但 System32 有 wpcap.dll（可能是舊的 WinPcap 或其他軟體帶入）' '請安裝 Npcap；WinPcap 已停止維護。'
    } else {
        Write-Result 'FAIL' '未安裝 Npcap' 'Npcap 安裝檔本身可離線安裝（需要系統管理員權限）。免費版不可再散布，需由使用者自行取得。'
    }
} else {
    if ($svc.Status -eq 'Running') { Write-Result 'OK' "Npcap 驅動服務：$($svc.Status)" }
    else { Write-Result 'FAIL' "Npcap 驅動服務狀態：$($svc.Status)" '以系統管理員執行：sc.exe start npcap，或重新安裝 Npcap。' }

    $missing = $npcapDlls | Where-Object { -not (Test-Path $_) }
    if ($missing) { Write-Result 'FAIL' 'Npcap DLL 不完整' ('缺少：' + ($missing -join ', ')) }
    else { Write-Result 'OK' 'wpcap.dll / Packet.dll' $npcapDir }

    if ($params) {
        if ($params.WinPcapCompatible -eq 1) { Write-Result 'INFO' 'WinPcap API 相容模式：開啟（不影響本工具）' }
        else { Write-Result 'INFO' 'WinPcap API 相容模式：關閉（本工具在 import pysoem 前會加入 System32\Npcap，不影響）' }
        if ($params.AdminOnly -eq 1) {
            if ($isAdmin) { Write-Result 'WARN' 'Npcap 設為「僅限系統管理員」' '本工具必須以系統管理員身分執行，否則網卡清單會是空的。' }
            else { Write-Result 'FAIL' 'Npcap 設為「僅限系統管理員」，而目前不是系統管理員' "以系統管理員身分執行本工具，或重新安裝 Npcap 並取消勾選 Restrict Npcap driver's access to Administrators only。" }
        } else {
            Write-Result 'OK' 'Npcap 不限系統管理員，一般使用者即可執行'
        }
    }
}

# ---------------------------------------------------------------- write access
Write-Section '程式資料夾寫入權限（log 檔）'
$probe = Join-Path $AppDir (".write-test-{0}.tmp" -f [guid]::NewGuid().ToString('N'))
try {
    [IO.File]::WriteAllText($probe, 'x'); Remove-Item $probe -Force
    Write-Result 'OK' '可寫入，tool-yyyyMMdd.log 與 error.log 會存在程式資料夾'
} catch {
    Write-Result 'WARN' '程式資料夾無法寫入' '工具仍可執行，但不會產生 log 檔。避免放在 C:\Program Files，或給予使用者寫入權限。'
}

# ---------------------------------------------------------------- adapters
Write-Section '有線網卡與進階設定'
$wired = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.PhysicalMediaType -eq '802.3' -or $_.MediaType -eq '802.3' })
if ($wired.Count -eq 0) {
    Write-Result 'FAIL' '找不到實體有線網卡' 'EtherCAT 需要有線乙太網卡；Wi-Fi 與虛擬網卡（Hyper-V、VPN）無法使用。'
}
$recommendOff = [ordered]@{
    '*InterruptModeration' = '中斷調節 (Interrupt Moderation)：會延後收包，造成週期抖動與掉 frame'
    '*EEE'                 = 'Energy Efficient Ethernet：低功耗閒置會增加延遲，甚至讓 link 重新協商'
    'AdvancedEEE'          = 'Advanced EEE'
    'EnableGreenEthernet'  = 'Green Ethernet'
    'PowerSavingMode'      = '省電模式'
    '*FlowControl'         = '流量控制：EtherCAT 不使用 pause frame，建議關閉'
}
foreach ($nic in $wired) {
    $state = "$($nic.Status)" + $(if ($nic.Status -eq 'Up') { "，$($nic.LinkSpeed)" } else { '' })
    Write-Result 'INFO' "$($nic.Name)  —  $($nic.InterfaceDescription)" "狀態：$state   NPF 名稱：\Device\NPF_$($nic.InterfaceGuid)"
    $props = @(Get-NetAdapterAdvancedProperty -Name $nic.Name -ErrorAction SilentlyContinue)
    foreach ($kw in $recommendOff.Keys) {
        $p = $props | Where-Object { $_.RegistryKeyword -eq $kw } | Select-Object -First 1
        if (-not $p) { continue }
        if ("$($p.RegistryValue)" -eq '0') { Write-Result 'OK' "  $($p.DisplayName) = $($p.DisplayValue)" }
        else { Write-Result 'WARN' "  $($p.DisplayName) = $($p.DisplayValue)（建議關閉）" "  $($recommendOff[$kw])" }
    }
    $pm = Get-NetAdapterPowerManagement -Name $nic.Name -ErrorAction SilentlyContinue
    if ($pm -and "$($pm.AllowComputerToTurnOffDevice)" -eq 'Enabled') {
        Write-Result 'WARN' '  允許電腦關閉此裝置以節省電源 = 開啟（建議關閉）' '  裝置管理員 > 網卡內容 > 電源管理'
    }
}
if ($wired.Count -gt 0) {
    Write-Host ''
    Write-Host '  註：接 EtherCAT slave 的網卡沒有 IP 也正常；Windows 顯示「未連線」不一定代表 link 不通，以工具能否掃到 slave 為準。' -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- summary
Write-Host ''
if ($script:failCount -gt 0) {
    Write-Host "結果：$($script:failCount) 項 FAIL、$($script:warnCount) 項 WARN —— 請先處理 FAIL 項目。" -ForegroundColor Red
    exit 1
} elseif ($script:warnCount -gt 0) {
    Write-Host "結果：可以執行，但有 $($script:warnCount) 項 WARN 可能影響通訊品質。" -ForegroundColor Yellow
    exit 0
} else {
    Write-Host '結果：全部通過。' -ForegroundColor Green
    exit 0
}
