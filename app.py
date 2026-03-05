from __future__ import annotations

import argparse
import csv
import html
import io
import os
import sqlite3
import sys
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.parser import BytesParser
from email.policy import default
from urllib.parse import parse_qs, quote_plus, urlparse

DB_PATH = os.path.join(os.path.dirname(__file__), "stock_records.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_time TEXT,
                trade_time TEXT,
                settlement_date TEXT,
                side TEXT NOT NULL,
                market TEXT,
                stock_name TEXT NOT NULL,
                shares REAL NOT NULL,
                unit_price REAL NOT NULL,
                amount REAL NOT NULL,
                fee REAL DEFAULT 0,
                other_fee REAL DEFAULT 0,
                currency TEXT,
                source TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dividends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dividend_date TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                market TEXT,
                amount REAL NOT NULL,
                currency TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


def parse_datetime(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).isoformat(sep=" ")
        except ValueError:
            pass
    return text


def to_float(value: str | None) -> float:
    cleaned = (value or "").replace(",", "").strip()
    return float(cleaned) if cleaned else 0.0


def normalize_side(side: str) -> str:
    side = (side or "").strip()
    if side in {"買進", "buy", "BUY", "Buy"}:
        return "BUY"
    if side in {"賣出", "sell", "SELL", "Sell"}:
        return "SELL"
    return side.upper()




def get_uploaded_file_content(headers, body: bytes, field_name: str) -> bytes | None:
    content_type = headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        return None

    mime_blob = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8") + body

    msg = BytesParser(policy=default).parsebytes(mime_blob)
    if not msg.is_multipart():
        return None

    for part in msg.iter_parts():
        if part.get_param("name", header="content-disposition") == field_name:
            payload = part.get_payload(decode=True)
            return payload if payload is not None else b""
    return None

def import_csv(content: str) -> int:
    reader = csv.DictReader(io.StringIO(content))
    rows = [r for r in reader if r.get("股票名稱")]
    now = datetime.now().isoformat(sep=" ")
    with get_conn() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO trades (
                    order_time, trade_time, settlement_date, side, market, stock_name,
                    shares, unit_price, amount, fee, other_fee, currency, source, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parse_datetime(row.get("委託時間", "")),
                    parse_datetime(row.get("成交時間", "")),
                    parse_datetime(row.get("交割日期", "")),
                    normalize_side(row.get("買賣別", "")),
                    row.get("市場別", "").strip(),
                    row.get("股票名稱", "").strip(),
                    to_float(row.get("成交股數")),
                    to_float(row.get("成交單價")),
                    to_float(row.get("成交價金")),
                    to_float(row.get("手續費")),
                    to_float(row.get("其他費用")),
                    row.get("幣別", "").strip(),
                    row.get("來源別", "").strip(),
                    row.get("備註", "").strip(),
                    now,
                ),
            )
    return len(rows)


def query_data(year: str, date: str, stock_name: str):
    filters = []
    params = []
    if year:
        filters.append("strftime('%Y', COALESCE(trade_time, settlement_date, order_time)) = ?")
        params.append(year)
    if date:
        filters.append("date(COALESCE(trade_time, settlement_date, order_time)) = date(?)")
        params.append(date)
    if stock_name:
        filters.append("stock_name LIKE ?")
        params.append(f"%{stock_name}%")

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    with get_conn() as conn:
        trades = conn.execute(
            f"SELECT * FROM trades {where} ORDER BY COALESCE(trade_time, settlement_date, order_time), id", params
        ).fetchall()

    d_filters = []
    d_params = []
    if year:
        d_filters.append("strftime('%Y', dividend_date) = ?")
        d_params.append(year)
    if date:
        d_filters.append("date(dividend_date) = date(?)")
        d_params.append(date)
    if stock_name:
        d_filters.append("stock_name LIKE ?")
        d_params.append(f"%{stock_name}%")

    d_where = f"WHERE {' AND '.join(d_filters)}" if d_filters else ""
    with get_conn() as conn:
        dividends = conn.execute(
            f"SELECT * FROM dividends {d_where} ORDER BY dividend_date, id", d_params
        ).fetchall()

    return trades, dividends


def calc_summary(trades, dividends):
    total_buy = total_sell = total_fees = realized = 0.0
    for t in trades:
        fee = float(t["fee"] + t["other_fee"])
        total_fees += fee
        amount = float(t["amount"])
        if t["side"] == "BUY":
            total_buy += amount
            realized -= amount + fee
        elif t["side"] == "SELL":
            total_sell += amount
            realized += amount - fee
    dividend_income = sum(float(d["amount"]) for d in dividends)
    return {
        "total_buy": total_buy,
        "total_sell": total_sell,
        "total_fees": total_fees,
        "dividend_income": dividend_income,
        "cumulative_pl": realized + dividend_income,
    }


def esc(s):
    return html.escape(str(s or ""))


def render_page(message: str, year: str, date: str, stock_name: str) -> bytes:
    trades, dividends = query_data(year, date, stock_name)
    summary = calc_summary(trades, dividends)

    trade_rows = "".join(
        f"<tr><td>{esc(t['trade_time'] or t['settlement_date'] or t['order_time'])}</td><td>{esc(t['side'])}</td>"
        f"<td>{esc(t['market'])}</td><td>{esc(t['stock_name'])}</td><td>{t['shares']:.2f}</td><td>{t['amount']:.2f}</td>"
        f"<td>{(t['fee'] + t['other_fee']):.2f}</td><td>{esc(t['currency'])}</td><td>{esc(t['source'])}</td></tr>"
        for t in trades
    )

    div_rows = "".join(
        f"<tr><td>{esc(d['dividend_date'])}</td><td>{esc(d['stock_name'])}</td><td>{esc(d['market'])}</td>"
        f"<td>{d['amount']:.2f}</td><td>{esc(d['currency'])}</td><td>{esc(d['note'])}</td></tr>"
        for d in dividends
    )

    flash = f'<div class="flash">{esc(message)}</div>' if message else ""
    html_doc = f"""
<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><title>股票交易紀錄管理</title>
<style>
body{{font-family:Arial,sans-serif;margin:20px;background:#f7f9fc}} .card{{background:#fff;padding:16px;border-radius:10px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
form{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}} input,button{{padding:8px;border:1px solid #ccc;border-radius:6px}} button{{background:#2563eb;color:#fff;border:none}}
table{{width:100%;border-collapse:collapse}} th,td{{border:1px solid #ddd;padding:8px;font-size:14px}} th{{background:#eff6ff}} .flash{{background:#dcfce7;padding:8px;border-radius:6px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}
.summary div{{border:1px solid #e2e8f0;background:#f8fafc;padding:10px;border-radius:8px}}
</style></head><body>
<h1>股票交易紀錄管理系統</h1>{flash}
<div class='card'><h2>1) 匯入交易 CSV</h2><form method='post' action='/upload' enctype='multipart/form-data'><input type='file' name='csv_file' accept='.csv' required><button>上傳並匯入</button></form></div>
<div class='card'><h2>2) 新增股息（手動）</h2><form method='post' action='/dividend'><input type='date' name='dividend_date' required><input name='stock_name' placeholder='股票名稱' required><input name='market' placeholder='市場別'><input type='number' step='0.01' name='amount' placeholder='股息金額' required><input name='currency' placeholder='幣別'><input name='note' placeholder='備註'><button>新增股息</button></form></div>
<div class='card'><h2>3) 查詢</h2><form method='get' action='/'><input type='number' name='year' placeholder='年份' value='{esc(year)}'><input type='date' name='date' value='{esc(date)}'><input name='stock_name' placeholder='股票名稱' value='{esc(stock_name)}'><button>查詢</button></form></div>
<div class='card'><h2>4) 累計盈虧摘要</h2><div class='summary'><div>總買進金額：{summary['total_buy']:.2f}</div><div>總賣出金額：{summary['total_sell']:.2f}</div><div>總交易費用：{summary['total_fees']:.2f}</div><div>總股息收入：{summary['dividend_income']:.2f}</div><div><b>累計盈虧：{summary['cumulative_pl']:.2f}</b></div></div></div>
<div class='card'><h2>交易紀錄（{len(trades)} 筆）</h2><table><thead><tr><th>成交時間</th><th>買賣別</th><th>市場別</th><th>股票名稱</th><th>股數</th><th>成交價金</th><th>費用</th><th>幣別</th><th>來源</th></tr></thead><tbody>{trade_rows}</tbody></table></div>
<div class='card'><h2>股息紀錄（{len(dividends)} 筆）</h2><table><thead><tr><th>日期</th><th>股票名稱</th><th>市場別</th><th>金額</th><th>幣別</th><th>備註</th></tr></thead><tbody>{div_rows}</tbody></table></div>
</body></html>
"""
    return html_doc.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        body = render_page(qs.get("msg", [""])[0], qs.get("year", [""])[0], qs.get("date", [""])[0], qs.get("stock_name", [""])[0])
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/upload":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            file_content = get_uploaded_file_content(self.headers, body, "csv_file")

            msg = "請先選擇 CSV 檔案。"
            if file_content is not None:
                try:
                    content = file_content.decode("utf-8-sig")
                    n = import_csv(content)
                    msg = f"成功匯入 {n} 筆交易資料。" if n else "CSV 沒有可匯入的交易資料。"
                except UnicodeDecodeError:
                    msg = "CSV 編碼格式錯誤，請使用 UTF-8 編碼。"

            self.send_response(303)
            self.send_header("Location", f"/?msg={quote_plus(msg)}")
            self.end_headers()
            return

        if self.path == "/dividend":
            length = int(self.headers.get("Content-Length", 0))
            payload = self.rfile.read(length).decode("utf-8")
            data = parse_qs(payload)
            dividend_date = data.get("dividend_date", [""])[0]
            stock_name = data.get("stock_name", [""])[0]
            market = data.get("market", [""])[0]
            amount = data.get("amount", [""])[0]
            currency = data.get("currency", [""])[0]
            note = data.get("note", [""])[0]

            msg = "股息日期、股票名稱與金額為必填。"
            if dividend_date and stock_name and amount:
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO dividends (dividend_date, stock_name, market, amount, currency, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (parse_datetime(dividend_date), stock_name, market, to_float(amount), currency, note, datetime.now().isoformat(sep=" ")),
                    )
                msg = "股息資料已新增。"

            self.send_response(303)
            self.send_header("Location", f"/?msg={quote_plus(msg)}")
            self.end_headers()
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        return


def run_server(host: str, port: int, open_browser: bool) -> int:
    init_db()
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"[錯誤] 無法啟動伺服器：{exc}")
        print("[提示] 可能是埠號被占用，可改用：python app.py --port 5001")
        return 1

    url = f"http://{host}:{port}" if host != "0.0.0.0" else f"http://127.0.0.1:{port}"
    print(f"[啟動成功] 請開啟瀏覽器：{url}")
    print("[停止服務] 在此視窗按 Ctrl + C")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[已停止] 伺服器已關閉。")
    finally:
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地股票交易紀錄管理系統")
    parser.add_argument("--host", default="127.0.0.1", help="監聽位址，預設 127.0.0.1")
    parser.add_argument("--port", type=int, default=5000, help="監聽埠號，預設 5000")
    parser.add_argument("--open-browser", action="store_true", help="啟動後自動開啟瀏覽器")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run_server(host=args.host, port=args.port, open_browser=args.open_browser))
