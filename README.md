# myStock

一個簡易的本地股票交易管理工具（Python 內建 http.server + SQLite）。

## 功能
- 上傳 CSV 匯入交易紀錄（支援國內/海外，含題目提供之海外格式）
- 將交易資料寫入本地 SQLite (`stock_records.db`)
- 依年份、日期、股票名稱查詢
- 顯示累計盈虧（買進/賣出/費用/股息）
- 手動新增國內/海外股息

## 啟動
```bash
python3 -m venv .venv
source .venv/bin/activate
python app.py
```

瀏覽 `http://localhost:5000`。
