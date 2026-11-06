# PySoemTool

以 [SOEM](https://github.com/OpenEtherCATsociety/SOEM)（Simple Open EtherCAT Master）為核心的 Windows EtherCAT 測試工具，
用 **Python 3.12 + [pysoem](https://github.com/bnjmnp/pysoem) + tkinter/ttk** 撰寫。功能對齊 C# 版 SoemWpfTool。

用途是**現場測試與除錯**：掃描 slave、切換 EtherCAT 狀態、看 IO、讀寫 SDO、觀察通訊品質，
以及測試 CiA 402 伺服驅動器（含不需硬體的模擬軸）。
它不是即時控制器，也沒有安全功能，不能用於生產用的運動控制或安全相關用途。

## 功能

| 功能 | 說明 |
|---|---|
| 網卡選擇 | 列出 Npcap 看得到的所有網卡；可設定 cyclic 週期（預設 2 ms） |
| Slave 掃描 | 名稱、狀態、AL Status Code、Vendor / Product / Revision、站號位址、IO bit 數、是否支援 DC；錯誤列標色 |
| 狀態控制 | INIT / PRE-OP / SAFE-OP / OP，依 EtherCAT 狀態機規則逐級切換；錯誤確認 (ACK) |
| IO 面板 | 選取的 slave 的輸出（點擊切換、全部 OFF）與輸入燈號，輸入同時顯示 HEX |
| SDO | 對選取的 slave 讀寫任意 index / subindex，自動解讀 U8 / U16 / U32 / 字串；失敗時顯示 **SDO abort code** |
| 通訊診斷 | WKC 實際 / 預期、平均 / 最大週期、cycles、掉 frame 與 WKC 不足分開計數、DC 相位偏差；異常週期寫進 log |
| **軸 (CiA 402)** | 啟用 / 停用 / 快速停止 / 錯誤重置；PP、PV、HM、CSP、CSV；放開即停的寸動；DC SYNC0；PDO 讀取與建議配置；狀態位元與趨勢圖；模擬軸。見 [docs/AXIS.md](docs/AXIS.md) |
| Log | 畫面保留最後 500 行，完整內容寫入程式資料夾的 `tool-yyyyMMdd.log`；未處理例外寫 `error.log` |

## 系統需求（摘要）

- Windows 10 / 11 **x64**
- [Npcap](https://npcap.com/)
- Microsoft Visual C++ 2015–2022 Redistributable（x64）
- 原始碼版：Python 3.12（64 位元，含 tcl/tk）＋ `pysoem==1.1.13`；**打包版（PyInstaller）不需要 Python**
- 一張接 EtherCAT 網路用的有線乙太網卡，建議專用

詳細安裝與搬移（含**沒有網路的電腦**）見 **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**。
任何電腦上都可以先執行 **`tools\check_prerequisites.cmd`**：不連網、不修改設定，逐項列出缺少的元件與建議調整的網卡設定。

## 快速開始（開發環境，使用 [uv](https://docs.astral.sh/uv/)）

```powershell
uv sync                     # 依 uv.lock 建立 .venv（Python 3.12 + 所有開發套件）
uv run python -m pysoem_tool
```

現場電腦不需要 uv：用打包版，或用 pip 離線安裝，見 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

1. 在上方下拉選單選擇接著 EtherCAT slave 的網卡，按 **連線**。完成後 slave 停在 **SAFE-OP**，cyclic 通訊開始。
2. 底部狀態列燈號為「WKC OK」、`WKC x/x` 兩邊相等，代表每台 slave 都有回應。
3. 按 **OP** 進入運作狀態。**此時輸出會真正驅動到現場設備**，操作前請確認輸出端安全。
4. 在 Slaves 列表點選一台 slave，下方 IO 面板顯示它的輸出與輸入；右側 SDO 區塊也針對這台操作。
5. 結束時按 **斷線**，所有 slave 會被要求回到 INIT。

## 測試

```powershell
uv run pytest -q                                                  # 不需要硬體
uv run python tools\check_view_rules.py pysoem_tool\screens --viewmodels pysoem_tool\viewmodels
uv run python tools\snap_view.py pysoem_tool.screens.main_window --sizes 1280x800,1024x768
```

## 文件

| 文件 | 內容 |
|---|---|
| [docs/AXIS.md](docs/AXIS.md) | 軸分頁：使用步驟、各運動模式、PDO 與 DC 設定、模擬軸、第一次接驅動器的建議程序 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 兩種部署方式（PyInstaller 打包 / Python + wheel 離線安裝）、檢查清單、授權注意事項 |
| [docs/HARDWARE.md](docs/HARDWARE.md) | 網卡選擇與設定、接線與拓樸、Windows 與 Python 的時間精度限制 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 依症狀查原因與處理方式，含常見 AL Status Code 與 SDO abort code |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 程式架構、執行緒與 GIL、狀態切換、DC 對齊、軸控制、測試清單、已知限制 |

## 專案結構

```
PySoemTool/
├── pysoem_tool/
│   ├── __main__.py            啟動：DPI、Npcap DLL 路徑、主題、例外處理（error.log、一次一個對話框）
│   ├── npcap.py               import pysoem 前加入 System32\Npcap
│   ├── services/ethercat.py   EtherCatService：cyclic thread、ESM、WKC 統計、SDO、DC
│   ├── motion/                cia402、trajectory、controller、sim_drive、channel、channel_ethercat
│   ├── viewmodels/            MVVM 的 ViewModel（不 import tkinter）
│   ├── screens/               tkinter 畫面（View + binder + preview）
│   ├── observable.py / mvvm.py / theme.py / design/tokens.json
├── tests/                     pytest：28 項模擬檢查、軌跡性質、PDO 通道
├── tools/                     check_prerequisites.(ps1|cmd)、check_view_rules.py、snap_view.py
├── docs/
├── launcher.py / PySoemTool.spec   PyInstaller 打包
├── pyproject.toml / uv.lock       開發環境（uv）
├── requirements.txt               現場 pip 離線安裝用，由 uv.lock 匯出
└── LICENSE (GPLv3)
```

## 授權

pysoem 與 SOEM 為 **GPLv3**（SOEM 另有商業授權），本專案也以 GPLv3 授權（[LICENSE](LICENSE)）。
Npcap 免費版不可再散布安裝檔。細節見 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#7-授權與再散布)。
