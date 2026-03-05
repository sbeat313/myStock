# myStock

一個簡易的本地股票交易管理工具（Python 內建 http.server + SQLite）。

## 功能
- 上傳 CSV 匯入交易紀錄（支援國內/海外，含題目提供之海外格式）
- 將交易資料寫入本地 SQLite (`stock_records.db`)
- 依年份、日期、股票名稱查詢
- 顯示累計盈虧（買進/賣出/費用/股息）
- 手動新增國內/海外股息

## 啟動方式

### Windows（建議）
1. 直接雙擊 `start.bat`
2. 或在 cmd 執行：
```bat
cd /d 專案路徑
py app.py --open-browser
```

### macOS / Linux
```bash
python3 app.py
```

啟動後會看到：
- `[啟動成功] 請開啟瀏覽器：...`
- `[停止服務] 在此視窗按 Ctrl + C`

## 常見問題
- 若顯示埠號被占用，改用：
```bash
python app.py --port 5001
```
