# 問題排查

依症狀查找。每一項都假設**目標電腦可能沒有網路**，處理方式不依賴線上下載或線上說明。

**先做這兩件事**，大部分問題可以直接定位：

1. 執行 `tools\check_prerequisites.cmd`，處理所有 `[FAIL]`。
2. 看程式資料夾裡的 log：
   - `tool-yyyyMMdd.log`：操作紀錄、狀態切換步驟、cyclic 異常（`[cyclic]` 開頭）
   - `error.log`：程式內未處理的例外與完整堆疊

目錄：
[A. 部署與啟動](#a-部署與啟動) ·
[B. 連線與掃描](#b-連線與掃描) ·
[C. 狀態切換與 AL Status Code](#c-狀態切換與-al-status-code) ·
[D. 通訊品質](#d-通訊品質掉-frame週期抖動) ·
[E. IO 與 SDO](#e-io-與-sdo) ·
[F. 軸 (CiA 402)](#f-軸-cia-402) ·
[G. 沒有 PowerShell 時的手動檢查](#g-沒有-powershell-時的手動檢查)

---

## A. 部署與啟動

| 症狀 | 原因 | 處理 |
|---|---|---|
| 啟動時（或 log）出現「無法載入 pysoem：DLL load failed while importing pysoem…」 | 這個訊息**不一定是 Npcap 的問題**。可能：① 沒裝 Npcap ② 沒有 `VCRUNTIME140.dll`（沒裝 VC++ Redistributable）③ pysoem wheel 與 Python 版本不符 | 用檢查腳本逐項確認。本工具在 import pysoem 前會把 `System32\Npcap` 加入 DLL 搜尋路徑，所以 WinPcap 相容模式勾不勾都可以 |
| `No module named 'pysoem'` | 原始碼版沒有在 `.venv` 安裝套件，或用錯了 Python | `.venv\Scripts\python -m pip install --no-index --find-links offline\wheels -r requirements.txt`；用 `.venv\Scripts\python -m pysoem_tool` 啟動 |
| `No module named 'tkinter'` / `_tkinter` 載入失敗 | 安裝 Python 時沒勾 tcl/tk | 重新執行 Python 安裝檔 → Modify → 勾選「tcl/tk and IDLE」 |
| 建立 venv 時 `ensurepip` 失敗 | 使用的 Python 沒有附 pip（部分可攜版 / 管理工具安裝的 Python） | 改用 python.org 的完整安裝檔建立 venv（[DEPLOYMENT.md §4](DEPLOYMENT.md#4-方式-bpython--wheel-離線安裝)） |
| `pip install` 嘗試連網、逾時 | 沒有加 `--no-index` | 加上 `--no-index --find-links offline\wheels` |
| 雙擊 `PySoemTool.exe` 沒反應 | 應用程式控制（AppLocker / WDAC / 防毒）封鎖；檔案帶有「來自網際網路」標記；只複製了 exe | 整個資料夾（含 `_internal\`）一起複製；`Get-ChildItem <資料夾> -Recurse \| Unblock-File`；請 IT 將資料夾加入白名單 |
| 出現「Windows 已保護您的電腦」（SmartScreen） | exe 沒有數位簽章 | 「其他資訊」→「仍要執行」 |
| 網卡下拉選單是空的，log 顯示「找到 0 張網卡」 | Npcap 安裝時勾了「僅限系統管理員」；或 Npcap 驅動服務沒有執行 | 以系統管理員身分執行；或重新安裝 Npcap 並取消該選項；`sc query npcap` 確認 RUNNING |
| 網卡清單有東西，但找不到接 EtherCAT 的那張 | 網卡被停用、驅動未安裝，或 Npcap 安裝後才插上（USB 網卡） | 啟用網卡；按「重新整理」；仍沒有時重新安裝 Npcap |
| 程式資料夾裡沒有 log 檔 | 資料夾沒有寫入權限（例如放在 `C:\Program Files`） | 移到使用者可寫入的位置 |
| 跳出「未處理的例外」對話框 | 程式錯誤 | 對話框同時只會出現一個；完整內容在 `error.log`，回報問題時附上 |
| 想確認打包版本身是否正常（不接硬體） | | `PySoemTool.exe --smoke`：啟動、連接模擬軸並啟用、確認 pysoem 可載入後自動結束；結果寫在 `tool-yyyyMMdd.log` 的 `[smoke]` 行，結束代碼 0 = OK |

---

## B. 連線與掃描

| 症狀 | 原因 | 處理 |
|---|---|---|
| 「無法在 '\Device\NPF_{…}' 上開啟網卡」 | 選到的網卡已被停用、拔除，或驅動重新安裝後 GUID 改變 | 按「重新整理」重新選擇網卡 |
| 「沒有找到任何 EtherCAT slave」 | 選錯網卡；網路線未接或接在 slave 的 OUT 埠；slave 通訊電源沒開；PC 與 slave 之間有交換器 | 先看 slave 網口的 Link 燈；確認接的是第一台 slave 的 **IN**；確認選到的是那張有線網卡（不是 Wi-Fi / Hyper-V） |
| 掃到的 slave 比實際少 | 菊鏈中某處斷開：某台 slave 的 IN/OUT 接反、某段線不良、某台沒電 | 掃到的最後一台之後那一段就是問題點 |
| 掃到的 slave 順序與預期不同 | 列表順序是**實際接線順序**，不是站號 | 依接線順序對照；`Addr` 欄是 SOEM 依順序配置的位址 |
| 「process data (…) 超過 config_map 回報的 IOmap 大小」 | pysoem 回報的資料區與 IOmap 不一致（不應發生） | 回報 slave 型號與 IO 配置 |
| 連線後 Windows 顯示該網卡「未連線」或「未識別的網路」 | 正常：EtherCAT 網段沒有 DHCP / IP | 不用處理 |

---

## C. 狀態切換與 AL Status Code

工具的狀態切換規則：往下直接切；往上逐級切，從 INIT / PRE-OP 往上時自動重新設定（config_init、config_map、config_dc）。
每一步若 2 秒內沒到位，log 會顯示「要求 X 但只到 Y」與已執行的步驟。

| 症狀 | 原因 | 處理 |
|---|---|---|
| 切換失敗，slave 列整列變紅、狀態顯示「… + ERR」 | slave 拒絕轉換並設定了錯誤旗標 | 看該 slave 的 **AL Status** 欄，對照下表；排除原因後按「確認錯誤 (ACK)」再切換 |
| 在 OP 一段時間後自己掉到 SAFE-OP + ERR，AL `0x001B` | SyncManager watchdog 逾時：slave 太久沒收到 process data（程式卡住、PC 睡眠、連續掉 frame、網路線鬆脫） | 查 log 中同一時間的 `[cyclic]` 紀錄；處理 D 節的通訊品質問題；ACK 後重新切 OP |
| 伺服驅動器等裝置無法進 OP，AL `0x0030`、`0x0035`、`0x0036`、`0x002C` 之類的 DC 錯誤 | 該裝置要求 **DC 同步模式（SYNC0）**，或 SYNC0 週期與驅動器支援的週期不符 | 在 SAFE-OP 於「軸」分頁啟用 DC SYNC0 後再切 OP（[AXIS.md §5](AXIS.md#5-distributed-clockdc-sync0)）；確認 cyclic 週期是驅動器支援的值（常見 1 / 2 / 4 ms）。不需要同步時，也可在 PRE-OP 把 0x1C32:01 寫成 0（Free Run） |
| 從 INIT 往上切到 SAFE-OP / OP 時，log 顯示「重新設定時找到 N 個 slave，連線時是 M 個」 | 連線後 bus 有變動（拔插、斷電） | 斷線後重新連線 |

### 常見 AL Status Code

| Code | 意義 | 常見原因 |
|---|---|---|
| `0x0000` | 沒有錯誤 | |
| `0x0011` | Invalid requested state change | 跳級切換（例如 INIT 直接要求 OP）；本工具會自動逐級切換，手動寫入暫存器時才會遇到 |
| `0x0016` | Invalid mailbox configuration (PRE-OP) | mailbox SyncManager 設定與 slave 不符；重新連線 |
| `0x0017` | Invalid sync manager configuration | process data SyncManager 設定錯誤；常見於在 PRE-OP 改了 PDO mapping 後 |
| `0x001A` | Synchronization error | DC 同步失敗 |
| `0x001B` | Sync manager watchdog | 太久沒收到 process data，見上 |
| `0x001D` / `0x001E` | Invalid output / input configuration | PDO 長度與 slave 期望不符 |
| `0x0024` / `0x0025` | Invalid input / output mapping | PDO mapping（0x1600 / 0x1A00 系列）內容無效 |
| `0x0027` / `0x0028` | Freerun / Synchronization not supported | 模式設定與裝置能力不符 |
| `0x002C` | Fatal sync error | DC 同步訊號遺失 |
| `0x0030` | Invalid DC SYNC configuration | 裝置要求 SYNC0，見上 |
| `0x0035` / `0x0036` | Invalid DC sync cycle time / SYNC0 cycle time | 同上 |

廠商可能使用 `0x8000` 以上的自訂代碼，請查該 slave 的手冊。

---

## D. 通訊品質（掉 frame、週期抖動）

底部狀態列的意義：

| 欄位 | 意義 |
|---|---|
| `WKC 6/6` | 最近一次回應的 Working Counter / 預期值。預期值 = 有輸出的 slave × 2 + 有輸入的 slave × 1 |
| `週期 avg / max` | 實際 cyclic 週期的平均與最大值（µs） |
| `掉 frame` | 在接收逾時（2 ms）內完全沒收到回應的次數（WKC < 0） |
| `WKC 不足` | 有收到回應，但有 slave 沒處理資料的次數（只在 SAFE-OP 以上統計） |

燈號：綠色 = 最近一次 WKC 符合；紅色 = 不符合。「重設統計」會把最大週期與兩個錯誤計數歸零。

log 中的 `[cyclic]` 紀錄：週期超過設定值 3 倍、掉 frame、或 WKC 不足時記錄一筆。

```
[cyclic] cycle 22,309: WKC -1/6, 週期 2924 µs, send→recv 2036 µs
```

| 看到的情況 | 判讀 | 處理 |
|---|---|---|
| `WKC -1`，`send→recv` 約 2000 µs | 接收逾時：frame 沒回來，或回來得太晚 | 關閉網卡的 Interrupt Moderation、EEE、省電功能（[HARDWARE.md](HARDWARE.md#網卡進階設定)）；改用較長週期 |
| `send→recv` 正常（數百 µs），但週期很長 | 是 cyclic thread 被延後執行，不是網路問題 | 關閉其他高負載程式；筆電接電源；調整電源計畫 |
| 紀錄後面有「期間發生 GC」 | Python 的循環垃圾回收在這段期間執行過 | 偶發可忽略；若頻繁出現請回報（連線時已 `gc.freeze()`） |
| 長週期集中在拖曳視窗、切換分頁、按鈕操作時 | Tk 主執行緒大量重繪，搶 GIL | 預設 2 ms 週期通常不受影響；1 ms 時可接受偶發長週期 |
| `WKC` 為 0 ~ 預期值之間，且持續發生 | 有 slave 沒處理 process data：不在 SAFE-OP / OP、正在出錯、或線路中斷 | 看 slave 列表的狀態與 AL Status |
| 掉 frame 在特定時間集中出現 | 防毒掃描、Windows Update、排程工作 | 與 IT 確認現場機台的排程 |

一般 Windows PC 上出現**零星**長週期或掉 frame 是預期中的，1 ms 週期尤其明顯（見 [HARDWARE.md §3](HARDWARE.md#3-windows-與-python-的時間精度限制)）；
只要沒有連續到觸發 watchdog（AL `0x001B`），對 IO 測試沒有影響。

---

## E. IO 與 SDO

| 症狀 | 原因 | 處理 |
|---|---|---|
| 點了輸出按鈕，現場輸出沒動作 | slave 不在 OP（SAFE-OP 時輸出維持安全值）；slave 的 IO 電源沒接；輸出接線或負載問題 | 確認狀態欄為 `Op`；確認 IO 電源；看 slave 上的輸出 LED |
| 輸出按鈕切換、WKC 正常，但用 SDO 讀輸出物件（例如 0x7000）一直是 0 | 很多 slave 的韌體**不會**把 PDO 收到的輸出值同步到物件字典 | 不能用 SDO 讀回來驗證輸出；以 slave 的 LED 或量測為準。WKC 正常已代表 slave 收到了輸出資料 |
| 輸入燈號不變 | IO 電源沒接；感測器接線型式不符（NPN / PNP、sink / source） | 查 slave 手冊的接線圖；輸入面板右上的 HEX 是原始值，可對照 |
| IO 面板沒有任何 bit | 該 slave 沒有 process data（例如 coupler） | 正常 |
| SDO 失敗，訊息為「SDO abort 0x…（說明），slave #n 0xIIII:SS」 | slave 回覆了 abort，代碼見下表 | 依代碼處理 |
| SDO 失敗，訊息為「slave 沒有回應（WKC 0）」或 mailbox 錯誤 | 該 slave 不支援 CoE（沒有 mailbox）、不在 PRE-OP 以上、或線路問題 | 先讀 `0x1000:0`（Device type）確認 slave 支援 CoE |
| SDO 寫入成功但沒有效果 | 有些物件只在 PRE-OP 可寫，或要重新進 SAFE-OP 才生效（例如 PDO mapping） | 切到 PRE-OP 寫入，再切回 SAFE-OP / OP（工具會自動重新讀取 PDO mapping） |

常見 SDO abort code（CiA 301）：

| Code | 意義 |
|---|---|
| `0x05040000` | SDO 逾時 |
| `0x06010000` | 不支援此存取方式 |
| `0x06010001` / `0x06010002` | 物件只能寫 / **只能讀** |
| `0x06020000` | **物件不存在** |
| `0x06040041` | 物件不能映射到 PDO |
| `0x06040042` | PDO 長度超過上限 |
| `0x06070010` / `0x06070012` / `0x06070013` | 資料長度不符 / 太長 / 太短 |
| `0x06090011` | **subindex 不存在** |
| `0x06090030` / `0x06090031` / `0x06090032` | 值超出範圍 / 太大 / 太小 |
| `0x08000020` / `0x08000022` | 目前狀態下無法存取（例如 PDO mapping 要在 PRE-OP 才能改） |

SDO 輸入格式：Index 為 hex（如 `1018`），SubIndex 為十進位或 `0x` 開頭的 hex，寫入資料為 **little-endian** 的 hex bytes（例如 U16 值 0x1234 要輸入 `34 12`）。

---

## F. 軸 (CiA 402)

| 症狀 | 原因 | 處理 |
|---|---|---|
| 連接軸時「PDO 中沒有 0x6040、0x6041」 | 這台 slave 不是 CiA 402 驅動器，或它的 PDO 沒有包含 controlword / statusword | 確認是驅動器；在「軸來源」按「套用建議 PDO」，或用廠商工具設定 PDO |
| 連接軸時「SDO 讀到的 PDO 長度 … 和目前的 process data 不一致」 | PDO 剛被改過但 master 尚未重新讀取 | 切到 PRE-OP 再回 SAFE-OP（或斷線重連）後再連接 |
| 「套用建議 PDO」在某個 SDO 失敗 | 驅動器的 PDO 是固定的、需要 complete access，或該物件不能映射 | 改用驅動器廠商的工具設定 PDO；或保持原 PDO，只用 PP / PV / HM（參數會走 SDO） |
| 按啟用後停在 Switch on disabled / Ready to switch on | EtherCAT 不在 OP；驅動器主電源沒上；STO（安全扭力關閉）輸入沒解除；驅動器要求先在面板上 reset | 先把 EtherCAT 切到 OP；檢查主電源與 STO 配線；看狀態區的 statusword 與驅動器面板 |
| 啟用後馬上 Fault | 編碼器、馬達參數、相位或主電源問題 | 看「Error code (0x603F)」對照驅動器手冊 |
| 「等待驅動器切換到 …」一直不結束 | 驅動器不支援該模式（看 0x6502 Supported drive modes），或 0x6060 不在 PDO 且 SDO 寫入被拒 | 換成驅動器支援的模式；log 會顯示 SDO 失敗訊息 |
| PP 移動沒反應 | 0x607A 不在 PDO 也無法以 SDO 寫入；驅動器的軟體極限（0x607D）擋住；Halt 仍為 1 | 看 log；確認目標在極限內；按一次「移動」前不要按住停止 |
| CSP / CSV 時驅動器報 following error 或 sync error | 掉 frame、週期太短、沒有開 DC SYNC0，或加減速設太大 | 用 2 ms 以上週期、開 DC、降低速度與加減速；看狀態列的掉 frame 與 DC 相位偏差 |
| 狀態區顯示「通訊異常：已停止送出運動命令」 | 連續 20 個週期掉 frame 或 WKC 不足 | 處理 D 節的通訊品質問題；驅動器可能已因 watchdog 停止，恢復後需重新啟用 |
| 寸動放開後還繼續移動一小段 | 正常：以設定的加減速減速停止；dead-man 最長 0.4 秒後開始減速 | 降低寸動速度或加大加減速 |
| 原點復歸一直「原點復歸中」 | 驅動器在搜尋原點開關 / index，或方法與配線不符 | 確認方法號碼與感測器接線（見驅動器手冊），必要時按停止 |

## G. 沒有 PowerShell 時的手動檢查

若群組原則禁止執行 PowerShell 腳本，可在「命令提示字元」逐項確認：

```bat
:: 64 位元？應顯示 AMD64
echo %PROCESSOR_ARCHITECTURE%

:: VC++ Runtime
dir %SystemRoot%\System32\vcruntime140.dll

:: Npcap 驅動與 DLL
sc query npcap
dir %SystemRoot%\System32\Npcap\wpcap.dll %SystemRoot%\System32\Npcap\Packet.dll

:: Npcap 是否僅限系統管理員（AdminOnly 為 0x1 表示是）
reg query HKLM\SYSTEM\CurrentControlSet\Services\npcap\Parameters /v AdminOnly

:: 打包版
dir PySoemTool.exe _internal

:: 原始碼版：Python、tkinter 與 pysoem
.venv\Scripts\python -c "import sys,tkinter;print(sys.version, tkinter.TkVersion)"
.venv\Scripts\python -c "import os;os.add_dll_directory(os.path.join(os.environ['SystemRoot'],'System32','Npcap'));import pysoem;print('pysoem OK')"
```

網卡進階設定則在「裝置管理員 → 網路介面卡 → 內容 → 進階」逐項查看。
