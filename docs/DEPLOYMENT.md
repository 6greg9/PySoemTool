# 部署與搬移到其他 Windows 電腦

假設目標電腦（現場機台）**沒有網路**。所有需要下載的東西都在有網路的電腦上先準備好。

- [1. 目標電腦需要什麼](#1-目標電腦需要什麼)
- [2. 選擇部署方式](#2-選擇部署方式)
- [3. 方式 A：PyInstaller 打包（建議）](#3-方式-apyinstaller-打包建議)
- [4. 方式 B：Python + wheel 離線安裝](#4-方式-bpython--wheel-離線安裝)
- [5. 安裝 Npcap 與 VC++](#5-安裝-npcap-與-vc)
- [6. 環境檢查腳本](#6-環境檢查腳本)
- [7. 授權與再散布](#7-授權與再散布)
- [8. 搬移時常見的差異](#8-搬移時常見的差異)

部署後無法執行的排查請見 [TROUBLESHOOTING.md 的「部署與啟動」](TROUBLESHOOTING.md#a-部署與啟動)。

---

## 1. 目標電腦需要什麼

| 項目 | 為什麼需要 | 離線安裝檔 | 備註 |
|---|---|---|---|
| Windows 10 / 11 **x64** | pysoem 只提供 `win_amd64` wheel | — | 32 位元與 ARM64 不支援 |
| **Npcap** | SOEM 透過 Npcap 收送原始乙太網 frame | `npcap-x.xx.exe` | 需系統管理員安裝。**免費版不可再散布**，見 §7 |
| **Visual C++ 2015–2022 Redistributable** (x64) | Python 與 pysoem 的原生模組連結 `VCRUNTIME140.dll` | `vc_redist.x64.exe` | 很多電腦已裝過，但不能假設 |
| Python 3.12 x64（含 tcl/tk） | **只有方式 B 需要** | `python-3.12.x-amd64.exe` | python.org 的完整安裝檔可離線安裝 |
| 有線乙太網卡 | 接 EtherCAT 網路 | — | 見 [HARDWARE.md](HARDWARE.md) |

## 2. 選擇部署方式

| | A. PyInstaller 打包資料夾 | B. Python + wheel |
|---|---|---|
| 目標電腦要裝 Python | **不用** | 要（3.12 x64） |
| 帶過去的東西 | `PySoemTool\`（exe + `_internal\`，約 35 MB） | 原始碼資料夾 + `offline\wheels\` + Python 安裝檔 |
| 可在現場改程式 / 跑 pytest | 不行 | 可以 |
| 適合 | 一般現場使用 | 開發、除錯、需要修改程式 |

兩種方式目標電腦都**仍需要 Npcap 與 VC++ Redistributable**。

## 3. 方式 A：PyInstaller 打包（建議）

**在有網路的開發電腦上建置：**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\pyinstaller --noconfirm PySoemTool.spec
```

輸出在 `dist\PySoemTool\`：`PySoemTool.exe` 與 `_internal\`（Python runtime、pysoem 原生模組、Tcl/Tk、`design\tokens.json`）。
`PySoemTool.spec` 的設計：

- 入口是 `launcher.py`（`pysoem_tool/__main__.py` 使用相對 import，不能直接當打包入口）。
- **不包含 Npcap**：程式啟動時以 `os.add_dll_directory()` 加入 `System32\Npcap`，打包版同樣有效。
- log 與 `error.log` 寫在 exe 所在資料夾（程式資料夾）。

再把 `README.md`、`docs\`、`tools\check_prerequisites.*` 複製到 `dist\PySoemTool\` 旁邊或裡面，離線電腦上也查得到文件與檢查工具：

```
PySoemTool-offline/
├── PySoemTool/              ← dist\PySoemTool 整個資料夾
│   ├── PySoemTool.exe
│   ├── _internal/ …
│   ├── README.md、docs/ …
│   └── tools/check_prerequisites.cmd / .ps1
└── prerequisites/
    └── vc_redist.x64.exe     ← Npcap 由各台電腦自行取得安裝（§7）
```

**驗證打包結果（不需要硬體）：**`PySoemTool.exe --smoke` 會啟動 GUI、連接模擬軸並啟用、確認 pysoem 可載入後自動結束，
結果寫在 `tool-yyyyMMdd.log` 的 `[smoke]` 行，結束代碼 0 = OK。

## 4. 方式 B：Python + wheel 離線安裝

**在有網路的電腦上準備：**

```powershell
# 下載 requirements.txt 中鎖定版本的 wheel（指定目標平台，與本機 Python 無關）
py -3.12 -m pip download -d offline\wheels -r requirements.txt --only-binary=:all: --python-version 3.12 --platform win_amd64
```

另外下載 `python-3.12.x-amd64.exe`（python.org，Windows installer 64-bit）。
需要在現場跑測試時，再把 `requirements-dev.txt` 也下載進 `offline\wheels`。

**在目標電腦上：**

1. 安裝 Python 3.12（勾選 **tcl/tk and IDLE** 與 **pip**；「Add to PATH」可不勾）。
2. 複製整個 PySoemTool 資料夾（含 `offline\wheels\`）到使用者可寫入的位置，例如 `C:\Tools\PySoemTool\`。
3. 建立虛擬環境並離線安裝：

   ```powershell
   cd C:\Tools\PySoemTool
   py -3.12 -m venv .venv
   .venv\Scripts\python -m pip install --no-index --find-links offline\wheels -r requirements.txt
   ```

   一定要加 `--no-index`，否則 pip 會先嘗試連 PyPI，逾時後才失敗。
   `.venv` 綁定建立它的 Python 路徑，**不要把 `.venv` 從別台電腦複製過來**，在目標電腦上重建。
4. 執行 `tools\check_prerequisites.cmd`，處理所有 FAIL。
5. 啟動：`.venv\Scripts\python -m pysoem_tool`（可做成捷徑；改用 `pythonw.exe` 則不會出現主控台視窗）。

> 建 venv 時若出現 `ensurepip` 失敗，代表那個 Python 沒附 pip（某些可攜版或工具管理的 Python），改用 python.org 的完整安裝檔。

## 5. 安裝 Npcap 與 VC++

需要一次系統管理員權限；之後執行工具通常不需要。

1. **VC++ Redistributable**：執行 `vc_redist.x64.exe`。顯示已安裝較新版本時直接關閉。
2. **Npcap**：執行 Npcap 安裝檔，選項：

   | 選項 | 建議 | 說明 |
   |---|---|---|
   | Restrict Npcap driver's access to Administrators only | **不勾** | 勾了之後本工具必須「以系統管理員身分執行」，否則網卡清單是空的 |
   | Support raw 802.11 traffic | 不勾 | 與 EtherCAT 無關 |
   | Install Npcap in WinPcap API-compatible Mode | 都可以 | 本工具在 import pysoem 前自行加入 `System32\Npcap`，勾不勾都能用 |

3. 依 [HARDWARE.md](HARDWARE.md) 調整接 EtherCAT 的網卡設定（檢查腳本會列出建議關閉的項目）。

從網路下載的 zip 解壓縮後，檔案可能帶有「來自網際網路」標記：

```powershell
Get-ChildItem .\PySoemTool-offline -Recurse | Unblock-File
```

## 6. 環境檢查腳本

`tools\check_prerequisites.cmd`（或 `.ps1`）不修改任何設定、不連網，檢查：

- 64 位元 Windows、目前是否為系統管理員
- 程式檔案：打包版（`PySoemTool.exe`、`_internal\`、pysoem 原生模組、tokens.json）或原始碼版（`pysoem_tool\`、requirements.txt）
- 原始碼版：`.venv` 的 Python 版本與位元數、tkinter、**實際 import pysoem**（會先加入 Npcap DLL 路徑）
- `VCRUNTIME140.dll`
- Npcap 驅動服務、DLL、「僅限系統管理員」、WinPcap 相容模式
- 程式資料夾是否可寫入（log 檔）
- 實體有線網卡、NPF 名稱，以及會影響 EtherCAT 時序的進階設定

結果分為 `[OK]` / `[WARN]` / `[FAIL]` / `[INFO]`，**有 FAIL 時結束代碼為 1**。腳本本身只需要 Windows PowerShell 5.1，不需要 Python。

```powershell
# 雙擊 tools\check_prerequisites.cmd，或：
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\check_prerequisites.ps1
# 檢查其他資料夾中的程式：
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\check_prerequisites.ps1 -AppDir D:\PySoemTool
```

`.cmd` 結束時會 `pause`；自動化時先設定環境變數 `NOPAUSE=1`，結束代碼會傳回。
若群組原則完全禁止 PowerShell 腳本，依 [TROUBLESHOOTING.md §G](TROUBLESHOOTING.md#g-沒有-powershell-時的手動檢查) 手動檢查。

## 7. 授權與再散布

- **SOEM** 與 **pysoem** 為 **GPLv3**（SOEM 另有商業授權）。本工具也是 GPLv3。把本工具（含 PyInstaller 打包版）提供給公司以外的人，
  等同散布 GPLv3 軟體，需要一併提供原始碼並遵守 GPLv3。僅在公司內部使用時的認定，請洽法務。
- **Npcap** 免費版的授權**限制再散布**：不能把 Npcap 安裝檔包進自己的離線包或安裝程式對外發送，也不能靜默安裝；
  這些需要 Npcap OEM 授權。請讓各台電腦自行取得並安裝 Npcap，並以 Npcap 官網的授權條款為準。
- **Python**（PSF License）、**Tcl/Tk**（BSD 類）可隨程式散布；PyInstaller bootloader 的授權允許散布打包結果。
- **VC++ Redistributable** 可依 Microsoft 授權隨應用程式散布。

## 8. 搬移時常見的差異

| 項目 | 說明 |
|---|---|
| 網卡名稱 | Windows 的連線名稱與 Npcap 的 `\Device\NPF_{GUID}` 每台都不同，重灌驅動或換插槽也會改變。工具每次啟動都重新列舉，不會記住上次的選擇 |
| 網卡晶片 | 進階設定名稱因廠商而異；檢查腳本以標準 keyword（`*InterruptModeration`、`*EEE`…）判斷 |
| Python / VC++ 是否已安裝 | 不要假設；用檢查腳本確認 |
| 系統管理員權限 | 安裝需要；執行通常不需要（除非 Npcap 選了僅限系統管理員） |
| 防毒 / EDR / 應用程式控制 | 可能封鎖 Npcap 驅動、未簽章的 exe、或從 USB 執行程式；需要 IT 加入白名單 |
| 電源計畫 | 筆電省電模式會增加通訊抖動；測試時接電源並用「平衡」或「高效能」 |

工具本身**沒有設定檔**、不寫登錄檔；搬移只需複製資料夾。log 檔寫在程式資料夾，不需要一起搬。
