# 架構與已知限制

給要修改或延伸本工具的開發者。

## 分層

```
MainView ─┬─ IoSlavesView ─▶ MainViewModel ─┐          (screens/ = View + binder；viewmodels/ 不 import tkinter)
          └─ AxisView ─────▶ AxisViewModel ─┤
                                            ▼
             AxisController ◀── CycleHandler ── EtherCatService ──▶ pysoem ──▶ SOEM ──▶ Npcap
             (motion/)                              (services/)
               │  AxisChannel
               ├─ EtherCatAxisChannel（PDO ↔ CiA 402，SDO 讀配置）
               └─ SimulatedAxisChannel ─ SimulatedCia402Drive（獨立實作的驅動器端）
```

| 元件 | 職責 | 不做的事 |
|---|---|---|
| `services/ethercat.py` `EtherCatService` | 擁有 `pysoem.Master`；連線 / 斷線；cyclic thread；每台 slave 的輸出入資料；ESM 狀態切換；SDO；DC SYNC0 與時間對齊；cycle handler | 不 import tkinter，可在 console / pytest 中使用 |
| `motion/controller.py` `AxisController` | CiA 402 master 端：狀態機、各模式 handshake、CSP/CSV 軌跡、寸動 dead-man | 不知道底下是真的驅動器還是模擬 |
| `motion/trajectory.py` | 線上梯形軌跡（位置或速度目標），每週期一步 | |
| `motion/channel_ethercat.py` | 以 SDO 讀 PDO 配置，bit 級讀寫 CiA 402 物件；建議 PDO 重新對應 | |
| `motion/sim_drive.py` | 獨立實作的軟體驅動器 | 不共用控制器邏輯（否則測不到 handshake 錯誤） |
| `viewmodels/` | `Observable` 狀態、`Command`、50 / 500 ms ticker、log | 不 import tkinter（`tools/check_view_rules.py` 檢查） |
| `screens/` | 純版面 View、binder、preview（截圖用的假資料情境） | 不做 I/O、不 sleep |
| `__main__.py` | DPI、Npcap DLL 路徑、主題、全域例外處理 | |

MVVM 依 `tkinter-mvvm-async` 規則（`observable.py`、`mvvm.py`、`theme.py` 來自其範本）。
主題色彩來自 `pysoem_tool/design/tokens.json`（High Performance HMI 灰階配色：正常狀態不搶眼，只有異常狀態用顏色）。

### pysoem 與 SoemWpfTool 的差異

| 項目 | SoemWpfTool（Soem.Net） | PySoemTool（pysoem 1.1.13） |
|---|---|---|
| 輸出入資料 | 擴充 `soem.dll` 取得每台 slave 的 IOmap offset | `slave.output`（可寫）/ `slave.input`：每台 slave 自己的資料區，不需要 offset |
| 預期 WKC | 擴充函式 | `master.expected_wkc` |
| DC SYNC0 | 擴充函式 | `slave.dc_sync(act, cycle_ns, shift_ns)`（沒有回傳值 → 寫入後讀 ESC 0x0981 確認） |
| DC 時間 | 擴充函式 | `master.dc_time` |
| ESC 暫存器 | 擴充函式 | `slave._fprd(addr, len)`（底線開頭的 API，升級 pysoem 時要重新確認） |
| SDO 失敗 | 只有 wkc | `SdoError` 帶 `abort_code`、`index`、`subindex`、`desc`；另有 `WkcError`、`MailboxError`、`PacketError` |
| 是否支援 DC | `SlaveInfo.SupportsDc` | 讀 ESC 0x0008 bit 2（pysoem 沒有公開 `hasdc`） |
| IO bit 數 | `Obits` / `Ibits` | `len(output) × 8`（pysoem 只給 byte 數；不足 8 bit 的 slave 會顯示 8） |

## 執行緒模型與 GIL

| 執行緒 | 做什麼 | 同步 |
|---|---|---|
| Tk 主執行緒 | 畫面；`UiDispatcher` 每 16 ms 把其他執行緒排入的更新套到 widget | 只有它碰 widget / `tk.Variable` |
| `EtherCAT cyclic`（THREAD_PRIORITY_HIGHEST） | 輸出 → `send_processdata` → `receive_processdata` → 輸入 → cycle handler → DC 對齊 | `_pd_lock` 保護輸出入資料；handler 在鎖內執行，必須不阻塞 |
| `Axis simulation` | 只有模擬軸時：1 ms 呼叫 `AxisController.on_cycle` | |
| `worker`（ThreadPoolExecutor） | 連線、狀態切換、SDO、軸參數寫入 | `_api_lock` 讓非週期性的 SOEM 呼叫不重疊 |
| `ui io tick` 50 ms / `ui state tick` 500 ms / `axis ui tick` 50 ms | 讀統計、輸入快照、slave 狀態、軸狀態，寫進 `Observable` | binder 以 `post_latest` 合併高頻更新 |

cyclic thread **不拿** `_api_lock`：SOEM 允許 process data 與 mailbox / 狀態存取並行，SDO 逾時不會卡住 cyclic 通訊。
例外是重新設定：`config_map` 會重配 IOmap，所以先停 cyclic thread，完成後再啟動。

**GIL（以 pysoem 1.1.13 實測）**

| 呼叫 | 釋放 GIL？ | 本工具的處理 |
|---|---|---|
| `send_processdata` / `receive_processdata` | 只有 `release_gil=True`（keyword-only）時 | 一律傳 `release_gil=True` |
| `sdo_read` / `sdo_write` / `config_init` | 同上 | 一律傳 `release_gil=True` |
| `Master.state_check` / `CdefSlave.state_check` | **永遠不釋放**（等 2 秒就讓 cyclic thread 停 2 秒） | **不使用**；改用 `read_state()`（約 0.1 ms）＋ 10 ms sleep 輪詢 |
| `read_state`、`write_state`、`_fprd` | 不釋放，但都在 1 ms 內完成 | 可接受 |
| `config_map` / `config_dc` | 不釋放 | 只在 cyclic thread 停止時呼叫 |

其他降低抖動的做法（連線期間有效，斷線還原）：

- `sys.setswitchinterval(0.0005)`：預設 5 ms 的 GIL 切換間隔會讓忙碌的 UI 執行緒延後 cyclic thread 最多 5 ms。
- `gc.collect()` + `gc.freeze()`：長壽物件移出 GC 世代，縮短之後每次 GC 的時間。GC 保持啟用（避免記憶體無限成長）；
  每次 GC 都以 `gc.callbacks` 計數，異常週期的 log 會註明「期間發生 GC」。
- process 優先權 HIGH、`timeBeginPeriod(1)`（ctypes）。
- deadline 計時：`perf_counter_ns()`；剩餘 > 0.7 ms 時 `sleep(剩餘 − 0.5 ms)`（Python 3.11+ 在 Windows 使用高解析度 waitable timer），
  最後一段 `sleep(0)` 讓出 GIL 並輪詢。落後超過 10 個週期時重新同步。

## 狀態切換

`EtherCatService.change_state(target, allow_layout_change)`：

1. 有 slave 帶錯誤旗標時先寫入「目前狀態 | ACK」。
2. 讀所有 slave 的最低狀態。
3. 目標 ≤ 目前：直接寫入目標（ESM 允許任意往下切）。
4. 目標 > 目前，且目前 < SAFE-OP：重新設定（`config_init` → PRE-OP、`config_map` → SAFE-OP、`config_dc`、重新套用 DC SYNC0），
   slave 數量必須相同；IO 配置改變時只有 `allow_layout_change`（套用建議 PDO 流程）才接受，並通知 UI 重建 slave 列表。
5. 目標為 OP：在 cyclic 通訊持續下寫入 OP。

每一步輪詢最多 2 秒，失敗時的訊息包含沒到位的 slave、其 AL status 與已執行的步驟。
WKC：`< 0` = 掉 frame；`0 … 預期-1` = WKC 不足，只在預期所有 slave 在 SAFE-OP 以上時統計。

## DC 時間對齊

任何 slave 啟用 SYNC0 時，cyclic thread 每週期讀 `master.dc_time`（frame 經過參考時鐘的時間），
以 SOEM 範例 `ec_sync()` 的 PI 方式調整下一個 deadline，讓 frame 落在兩次 SYNC0 的中點，修正量限制在 ±1/4 週期。
狀態列顯示目前的相位偏差。

## 軸控制

每個週期 `AxisController.on_cycle`：讀入 → 解碼 CiA 402 狀態 → 取出 UI 請求 → controlword 基本命令 →
只有 Operation enabled **且** 0x6061 = 要求模式時才產生運動命令（否則 CSP 目標 = 實際位置、CSV 目標 = 0）→ 寫回。

- **CSP / CSV 以名義週期推進軌跡**，不用量到的時間（驅動器每個 SYNC0 消耗一個目標；用量測 dt 時 Windows 背靠背補償週期會把
  1 count 的捨入變成極大速度 → following error）。`test_25_irregular_dt…` 是這個 bug 的回歸測試。
- 軌跡產生器解 `|rem| − (v+v')/2·dt = v'²/(2·dec)` 求離散停止速度，不越過固定目標；目標被改近時越過再折返。
- 寸動 dead-man：0.4 秒沒更新就減速停止。原點復歸開始後至少維持 bit 4 50 ms 才判斷完成。
- Fault reset：20 週期的窗（前 10 bit 7 = 0、後 10 = 1）。驅動器 Fault 時清除啟用請求。
- 連續 20 個週期通訊異常：停止產生運動命令，目標維持實際位置。

## 測試（`tests/`，不需要硬體）

`test_simulation.py` 對應 SoemWpfTool 的 28 項模擬檢查：

| # | 項目 | # | 項目 |
|---|---|---|---|
| 01 | statusword 解碼 | 15 | HM 方法 35 |
| 02 | 初始 Switch on disabled | 16 | HM 搜尋型方法：bit 4 至少 50 ms |
| 03 | 啟用的狀態機順序 | 17 | HM 無效方法 → homing error |
| 04 | 進入 OP 時 CSP 目標 = 實際位置 | 18 | CSP 到達目標 |
| 05 | 停用 → Ready to switch on | 19 | CSP 不越過、加速度上限 |
| 06 | 快速停止與重新啟用 | 20 | CSV 斜坡與停止 |
| 07 | 驅動器錯誤清除啟用請求 | 21 | CSP 寸動 dead-man |
| 08 | Fault reset 上升緣、需重新啟用 | 22 | PV 寸動 dead-man |
| 09 | 0x6061 未切換前拒絕運動命令 | 23 | 持續更新的寸動不停止 |
| 10 | 移動中拒絕切換模式 | 24 | 通訊中斷 20 週期 → 停止命令 |
| 11 | PP 絕對移動 handshake | 25 | **不規則 dt 不造成 following error**（回歸） |
| 12 | PP 相對移動 | 26 | CSP 目標跳動 → Fault 0x8611 |
| 13 | PP Halt | 27 | 軌跡性質：固定目標不越過、限速限加速、一定到達（40 組隨機） |
| 14 | PV 速度與 Halt | 28 | 軌跡性質：隨機改目標時加速度 ≤ 2 倍、一定到達（40 組隨機） |

`test_channel.py`：bit 讀寫與符號延伸、PDO 探索、DIO 被拒（訊息指出 0x6040 / 0x6041）、長度不一致、建議 PDO 寫入順序與失敗步驟。

## 已知限制

| 限制 | 影響 | 可能的改善方向 |
|---|---|---|
| Windows 非即時，加上 Python GIL / GC | 週期抖動比 C# 版大；1 ms 週期為「盡力而為」，預設 2 ms | 嚴格時序請用 RT 系統 |
| 只能同時連接一個軸；沒有 CST | | `AxisController` 可多實例註冊 |
| 沒有軟體極限、沒有安全功能 | | |
| 單位為驅動器原生單位 | | |
| 使用 pysoem 的底線 API `_fprd` | 升級 pysoem 時需重新確認 | |
| IO bit 數以 byte 為單位 | 不足 8 bit 的 slave 顯示為 8 bit | 以 SDO 讀 PDO 長度 |
| 單一 group、單一網卡 | | |
| 網卡選擇不會記住 | | 以描述或 MAC 比對 |

## UI 框架：為什麼是 tkinter 而不是 PySide6

評估後維持 tkinter + ttk：

- **離線部署**：tkinter 隨 Python 安裝；PySide6 wheel 約 100 MB 以上，打包後 150 MB 以上（本工具打包版約 35 MB）。
- **授權**：PySide6 為 LGPLv3，散布打包版時要讓使用者能替換 Qt 函式庫；tkinter 沒有這個負擔。
- **效能不是瓶頸**：時序關鍵的部分在 cyclic thread；UI 只以 50 ms 輪詢快照，tkinter 足夠。

PySide6 會明顯比較好的地方（之後若要擴充可考慮）：多軸、長時間的高取樣率趨勢圖（pyqtgraph / QtCharts，含縮放與游標）、
大量 slave 的表格（虛擬化的 QTableView）、更完整的高 DPI 支援。ViewModel 不 import tkinter，換 UI 框架時可以沿用。
