# 軸分頁（CiA 402）

「軸 (CiA 402)」分頁用來測試符合 **CiA 402 / IEC 61800-7-201** 的伺服驅動器或步進驅動器：
啟用 / 停用、錯誤重置、五種運動模式、寸動、原點復歸，以及即時狀態與趨勢圖。
不需要硬體時可以用內建的**模擬軸**熟悉介面與狀態機。

> ⚠️ **安全**：這是測試工具，不是運動控制器。沒有軟體極限、沒有安全功能，不能取代緊急停止回路。
> 第一次接真實驅動器時，請**卸下負載或讓軸在安全行程內**，用低速、小行程開始。

- [1. 支援範圍](#1-支援範圍)
- [2. 使用步驟](#2-使用步驟)
- [3. 運動模式](#3-運動模式)
- [4. PDO 配置](#4-pdo-配置)
- [5. Distributed Clock（DC SYNC0）](#5-distributed-clockdc-sync0)
- [6. 模擬軸](#6-模擬軸)
- [7. 第一次接真實驅動器的建議程序](#7-第一次接真實驅動器的建議程序)

---

## 1. 支援範圍

| 項目 | 支援 |
|---|---|
| 驅動器 | 任何 CoE（CANopen over EtherCAT）+ CiA 402 的驅動器。PDO 中至少要有 controlword `0x6040` 與 statusword `0x6041` |
| 模式 | PP (1)、PV (3)、HM (6)、CSP (8)、CSV (9)。CST (10) 未提供 |
| 軸數 | 同時連接一個軸 |
| 單位 | 驅動器原生單位。位置通常是 encoder count，速度是 count/s；部分驅動器有自己的單位換算（factor group 0x608F 等），以手冊為準 |

## 2. 使用步驟

1. 在「IO / Slaves」分頁**連線 EtherCAT**。slave 會停在 SAFE-OP。
2. 切到「軸」分頁，在「軸來源」選擇驅動器所在的 slave，按 **連接軸**。
   工具會用 SDO 讀取該 slave 的 PDO 配置（0x1C12 / 0x1C13 → 0x16xx / 0x1Axx），在左側列出每個物件的位置。
   若 PDO 中沒有 0x6040 / 0x6041，會拒絕連接，見 [§4](#4-pdo-配置)。
3. 需要 CSP / CSV 時，在 OP 以下**設定 DC SYNC0**（[§5](#5-distributed-clockdc-sync0)）。
4. 回到「IO / Slaves」把 EtherCAT 切到 **OP**。
5. 在軸分頁按 **啟用 (Enable)**。工具依 CiA 402 狀態機依序送出 Shutdown → Switch on → Enable operation，
   狀態區顯示「Operation enabled」即完成。
6. 選擇運動模式，按 **切換**，等狀態區的「模式顯示 (0x6061)」變成相同模式，再下運動命令。
7. 結束時按 **停用**，再按 **分離**。斷開 EtherCAT 時軸會自動分離。

連接中的 slave，「IO / Slaves」分頁的輸出按鈕會變成唯讀，避免和軸控制互相覆蓋。

### 驅動器按鈕

| 按鈕 | 行為 |
|---|---|
| 啟用 | 依狀態機逐步進入 Operation enabled。進入的那一刻，CSP 目標位置設為實際位置、CSV 目標速度設為 0，不會跳動 |
| 停用 | 送 Shutdown（0x0006），驅動器回到 Ready to switch on，馬達失去扭力 |
| 快速停止 | 送 Quick stop（0x0002），驅動器以 0x6085 的減速度停止並進入 Quick stop active。再按「啟用」可重新啟用 |
| 錯誤重置 | 在 controlword bit 7 送出上升緣。驅動器錯誤後**不會自動重新啟用**，需要再按「啟用」 |
| 停止 | PP / PV / HM 送 Halt（bit 8）；CSP / CSV 由 PC 端以設定的減速度停到 0 |

## 3. 運動模式

| 模式 | 誰規劃軌跡 | 工具送出的東西 | 說明 |
|---|---|---|---|
| **PP** 位置 | 驅動器 | 0x607A 目標、0x6081/6083/6084 速度與加減速、bit 4 新目標上升緣 | 等驅動器回 set-point acknowledge（status bit 12）後放開 bit 4；到位看 bit 10。可選相對移動（bit 6） |
| **PV** 速度 | 驅動器 | 0x60FF 目標速度、0x6083/6084 | 停止用 Halt |
| **HM** 原點復歸 | 驅動器 | 0x6098 方法、0x6099:01/02 速度、0x609A 加速、0x607C 原點偏移（SDO），bit 4 開始 | 完成看 bit 12（attained）+ bit 10；錯誤看 bit 13。方法 35 / 37 以目前位置為原點，不會移動 |
| **CSP** 同步位置 | **PC** | 每個週期一個 0x607A | 工具內建梯形軌跡，保證不越過目標、加減速不超過設定值 |
| **CSV** 同步速度 | **PC** | 每個週期一個 0x60FF | 以設定加減速做斜坡 |

速度與加減速若不在 PDO 中，按下命令時會先用 SDO 寫入。

### 寸動（PV / CSP / CSV）

按住 **JOG −** / **JOG +** 時持續移動。這是**放開即停**的設計：按鈕每 0.1 秒更新一次命令，
0.4 秒沒有更新（放開、滑鼠移出、視窗失去焦點、程式卡住）軸就自動減速停止。

### CSP / CSV 與 Windows

CSP / CSV 的目標值由 PC 每個週期計算。工具以**名義週期**推進軌跡（驅動器每個 SYNC0 週期消耗一個目標值，
與 frame 實際送出的時間無關），所以 Windows 的抖動不會變成速度突波；但掉 frame 時那一個週期的目標會缺漏。建議：

- cyclic 週期 **2 ms 以上**，並啟用 DC SYNC0
- 依 [HARDWARE.md](HARDWARE.md) 調整網卡設定
- 狀態列的「掉 frame」持續增加時不要做 CSP

連續通訊異常（20 個週期）時，工具停止送出新的運動命令，目標保持在實際位置；之後由驅動器自己的 watchdog 處理。

## 4. PDO 配置

連接時讀到的 PDO 列在左側。工具使用的物件：

| 方向 | 物件 | 必要性 |
|---|---|---|
| RxPDO | 0x6040 Controlword | **必要** |
| RxPDO | 0x6060 Modes of operation | 沒有時改用 SDO 切換模式 |
| RxPDO | 0x607A Target position | PP / CSP 需要 |
| RxPDO | 0x60FF Target velocity | PV / CSV 需要 |
| RxPDO | 0x6081 / 0x6083 / 0x6084 | 選用，沒有時用 SDO |
| TxPDO | 0x6041 Statusword | **必要** |
| TxPDO | 0x6061 Modes display | 沒有時用 SDO 確認模式 |
| TxPDO | 0x6064 Position actual、0x606C Velocity actual | 強烈建議 |
| TxPDO | 0x603F Error code、0x6077 Torque actual、0x60F4 Following error | 選用 |

**套用建議 PDO**（「軸來源」區塊，選擇真實 slave 時出現）會：切到 PRE-OP → 把 0x1600 改成
`6040, 607A, 60FF, 6060`、0x1A00 改成 `6041, 6064, 606C, 603F, 6061` → 切回 SAFE-OP 讓 master 重新讀取配置。
只寫入驅動器 RAM，斷電後恢復原設定；PDO 固定或需要 complete access 的驅動器會拒絕，log 會指出是哪一個 SDO 失敗（含 SDO abort code，例如 0x06010002 = 唯讀、0x06040041 = 物件不能映射到 PDO）。
這時請改用驅動器廠商的工具設定 PDO。

## 5. Distributed Clock（DC SYNC0）

- 只能在 **OP 以下**設定，週期固定等於 cyclic 週期，可設定 shift。
- 啟用後工具也會嘗試把 0x1C32:01（同步模式）寫成 2（DC SYNC0），停用時寫回 0（Free Run）；驅動器不支援時忽略。
- 啟用期間，cyclic thread 會依 DC 時間調整送出時間，讓 frame 落在兩次 SYNC0 中間。底部狀態列會顯示「DC 相位偏差」，
  正常時應在週期的 ±1/4 內並逐漸收斂。
- 設定會在每次重新設定（從 INIT / PRE-OP 往上切）後自動重新套用，斷線時清除。

PP / PV / HM 通常 Free Run 即可。

## 6. 模擬軸

「模擬軸（不需硬體）」在本機以 1 ms 週期執行一個軟體驅動器，不經過 EtherCAT，也不需要連線。它獨立實作了驅動器端：

- CiA 402 狀態機、各模式的 handshake（set-point acknowledge、homing attained / error）
- PP / PV 的驅動器端軌跡，HM 方法 35 / 37 與「搜尋 20 000 單位後找到原點」的簡化方法
- CSP 目標跳動過大時報 **following error 0x8611** 並進入 Fault，與真實驅動器相同
- 「注入錯誤」按鈕：注入錯誤碼 0x2310（過電流）

用途是熟悉介面、確認操作流程。它不模擬馬達動態、負載或極限開關。

## 7. 第一次接真實驅動器的建議程序

1. 用驅動器廠商的工具確認馬達參數、極限、煞車設定正確，並先讓馬達空轉（不接負載）。
2. 本工具連線，看驅動器能否到 OP、WKC 是否正常。
3. 連接軸，確認 PDO 列表與 statusword 合理（通常停在 Switch on disabled，statusword 像 0x0240 或 0x0250）。
4. 用 **HM 方法 35**（以目前位置為原點，不移動）測試命令流程。
5. **PV**，低速（例如 1 轉/秒以下）測試啟動與停止，確認方向。
6. **PP**，小行程測試。
7. 需要時才試 CSP / CSV：先設 DC、2 ms 以上週期，低速小行程。
8. 每一步都確認「快速停止」與「停用」有效。
