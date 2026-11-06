# 硬體與網路設定

EtherCAT 在 Windows 上透過 Npcap 收送原始乙太網 frame，通訊品質主要取決於**網卡、驅動設定與 Windows 排程**。
本文件說明怎麼挑網卡、要調哪些設定、怎麼接線，以及 Windows 本身的限制。

診斷步驟見 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)；設定項目可用 `tools\check_prerequisites.cmd` 檢查。

---

## 1. 網卡

### 選擇

| 類型 | 適用性 | 說明 |
|---|---|---|
| PCIe / 主機板內建有線網卡（Intel、Realtek 等） | ✅ 建議 | Intel 網卡在 SOEM 社群中延遲通常較穩定；Realtek 也能用，但更依賴下面的設定 |
| USB 轉乙太網卡 | ⚠️ 可用於測試 | USB 輪詢會增加延遲與抖動；可以掃描、讀 SDO、慢速 IO，不適合短週期 |
| Wi-Fi | ❌ | 無法收送 EtherCAT frame |
| 虛擬網卡（Hyper-V、VPN、WAN Miniport、Loopback） | ❌ | 會出現在網卡清單中，但沒有實體連線 |
| 筆電 Docking station 的網口 | ⚠️ | 多半是 USB 網卡，同上 |

### 專用網卡

**建議用一張專用網卡接 EtherCAT，不要與公司網路共用。** EtherCAT frame 不是 IP 封包，
共用時 Windows 的 IP 流量（DHCP、ARP、IPv6 探測、更新）會被送進 EtherCAT 網段，slave 會忽略它們，
但會佔用頻寬、增加延遲，也可能被公司網路設備視為異常流量。

專用網卡上不需要設定 IP，也不需要 DHCP；Windows 顯示「未識別的網路」或「未連線」都可能是正常的。
是否能通訊，以工具能不能掃到 slave 為準。

### 網卡進階設定

在「裝置管理員 → 網路介面卡 → 該網卡 → 內容 → 進階」調整。名稱因廠商與驅動版本而異，
下表列出 Windows 標準 keyword（`Get-NetAdapterAdvancedProperty` 的 `RegistryKeyword` 欄）與常見顯示名稱：

| Keyword | 常見顯示名稱 | 建議 | 原因 |
|---|---|---|---|
| `*InterruptModeration` | Interrupt Moderation、中斷調節、中斷調整合適化 | **Disabled** | 網卡會累積封包再一次通知 CPU，直接造成收包延遲與週期抖動，是 Windows 上掉 frame 最常見的原因 |
| `*EEE` | Energy Efficient Ethernet、節能乙太網路 | **Disabled** | 閒置時進入低功耗，喚醒需要時間；部分 slave 與 EEE 搭配不良會讓 link 反覆重新協商 |
| `AdvancedEEE`、`EnableGreenEthernet`、`PowerSavingMode` | Advanced EEE、Green Ethernet、環保節能、省電模式（Realtek 常見） | **Disabled** | 同上 |
| `*FlowControl` | Flow Control、流量控制 | **Disabled** | EtherCAT 不使用 pause frame |
| `*SpeedDuplex` | Speed & Duplex、連線速度與雙工模式 | Auto 或 100 Mbps Full | EtherCAT 為 100 Mbps 全雙工；Auto 正常即可，遇到 link 不穩再固定 |
| `*ReceiveBuffers` | Receive Buffers、接收緩衝區 | 預設 | 一般不需調整 |

另外在同一個內容視窗的「**電源管理**」頁籤，取消「允許電腦關閉這個裝置以節省電源」。

用 PowerShell 查看（唯讀）：

```powershell
Get-NetAdapterAdvancedProperty -Name "<網卡名稱>" | Format-Table RegistryKeyword, DisplayName, DisplayValue
```

修改網卡設定需要系統管理員權限，套用時該網卡會短暫斷線。

### 不需要、也不建議做的事

- **不要**為了 EtherCAT 去停用網卡上的 TCP/IPv4 等協定繫結，除非確定該網卡只接 EtherCAT；這不會改善延遲，而且容易在搬移後忘記恢復。
- **不要**啟用 Jumbo Frame；EtherCAT frame 最大 1514 bytes。

---

## 2. 接線與拓樸

- EtherCAT 是**線性菊鏈**：PC 網卡 → 第一台 slave 的 **IN** 埠 → 該台 **OUT** 埠 → 下一台 IN → …
  IN / OUT 接反時，後面的 slave 會掃不到，或順序錯亂。
- 一般的**非管理型乙太網交換器不能放在 PC 與 slave 之間**（或 slave 之間）。EtherCAT frame 要依序經過每台 slave 再折返，交換器會破壞這個路徑。
- 使用 Cat5e 以上、雙絞的網路線；工業現場建議遮蔽線。每段線長上限 100 m。
- Slave 的**通訊電源與 IO 電源**常是分開的端子。通訊電源沒接，slave 掃不到；IO 電源沒接，slave 能進 OP 但輸出不會動、輸入讀不到。
- 多數 slave 的 IN / OUT 埠旁有 **Link/Activity 燈**，以及表示 EtherCAT 狀態的 **RUN 燈**（INIT 熄滅、PRE-OP 慢閃、SAFE-OP 單閃、OP 恆亮）、**ERR 燈**。現場排查先看這些燈，比看軟體快。

---

## 3. Windows 與 Python 的時間精度限制

Windows 不是即時作業系統，Python 另外有 GIL 與 GC。本工具已盡量降低抖動
（細節見 [ARCHITECTURE.md](ARCHITECTURE.md#執行緒模型與-gil)）：

- cyclic thread 設為 THREAD_PRIORITY_HIGHEST，連線期間 process 優先權 HIGH，`timeBeginPeriod(1)`
- 以 deadline 計時（`perf_counter_ns`）取代單純的 sleep；pysoem 的收送呼叫都釋放 GIL
- GIL 切換間隔縮短為 0.5 ms，連線時 `gc.freeze()`，異常週期會註明期間是否發生 GC
- 不使用會整段持有 GIL 的 `state_check`

但仍會受到驅動、其他程式、防毒、電源管理與 UI 負載影響。實務上：

| 週期 | 在一般 Windows PC 上 |
|---|---|
| 1 ms | 「盡力而為」：平均可達成，但最大週期常到數 ms，掉 frame / 長週期比 C# 版多 |
| **2 ms（預設）** – 10 ms | 較穩定，建議用於長時間測試與 CSP / CSV |
| < 1 ms | 不支援（設定值最小 1 ms） |

偶發的長週期或掉 frame 對 IO 測試通常沒有影響：slave 的 SyncManager watchdog 預設約 100 ms，
只有連續掉 frame 超過 watchdog 時間，slave 才會退出 OP（AL Status `0x001B`）。

**需要嚴格時序、DC 同步運動控制、或安全相關功能時，不要用 Windows 上的這個工具**，
應使用即時作業系統（如 Linux PREEMPT_RT）或專用的 EtherCAT 主站。

### 其他會影響的因素

- **筆電**：拔掉電源時 CPU 降頻、PCIe 與 USB 省電，抖動明顯變大。測試時接上電源。
- **電源計畫**：「省電」計畫會讓 CPU 降頻；用「平衡」或「高效能」。
- **防毒即時掃描、Windows Update、大量磁碟 IO**：會造成偶發延遲尖峰。
- **遠端桌面**：可以用，不影響 EtherCAT 通訊本身，但會增加 UI 負擔。
- **拖曳 / 縮放視窗、開著對話框**：tkinter 主執行緒會忙碌；cyclic thread 是獨立執行緒，影響很小，但大量重繪時最大週期可能變長。

---

## 4. 安全

- 按 **OP** 之後，輸出會真正驅動現場設備。操作前確認輸出端接的是什麼。
- 程式結束、當機或 PC 斷線時，slave 會在 watchdog 逾時後退出 OP；**輸出在這時會變成什麼值由 slave 決定**（多數是清為 0，部分可設定保持最後值），請查 slave 的手冊。
- 本工具沒有任何安全功能，不能取代緊急停止或安全回路。

### 伺服驅動器

- 驅動器的 **STO（Safe Torque Off）** 輸入、主電源與煞車配線必須先依手冊處理好；STO 未解除時驅動器無法啟用，這是正常的保護。
- 第一次測試時讓馬達**不接負載**，並在驅動器上設定軟體極限與電流限制。軸分頁沒有軟體極限。
- CSP / CSV 需要穩定的週期：建議 2 ms 以上並啟用 DC SYNC0（見 [AXIS.md](AXIS.md#5-distributed-clockdc-sync0)）。
  通訊品質不佳（掉 frame 持續增加）時只用 PP / PV / HM，這些模式由驅動器自己規劃軌跡，對 PC 的抖動不敏感。
