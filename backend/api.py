"""
Banker RSI Dashboard API
========================
Flask backend that queries the nasdaq_eod MySQL table and returns
buy signals based on three criteria:

  1. "immediate"  — banker_rsi > 0 today (just crossed above zero)
  2. "3day"       — banker_rsi == 0 for the 3 days prior, then > 0 today
  3. "5day"       — banker_rsi == 0 for the 5 days prior, then > 0 today

Setup:
    1. Copy .env.example to .env and fill in your DB credentials.
    2. pip install -r requirements.txt
    3. python api.py

Endpoints:
    GET /api/signals?days=10          — last N trading days of signal data
    GET /api/dates                    — list of available trade dates
    GET /api/signal-day?date=YYYY-MM-DD — signals for a specific date
    GET /api/health                   — DB connectivity check
"""

import os
from collections import defaultdict
from flask import Flask, jsonify, request
from flask_cors import CORS
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # allow React dev server to call this API

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 3306)),
    "database": os.getenv("DB_NAME", "nasdaq_eod"),
    "user":     os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "use_pure": True,
    "auth_plugin": "mysql_native_password",
}


def get_conn():
    return mysql.connector.connect(**DB_CONFIG)


def dict_cursor(conn):
    return conn.cursor(dictionary=True)


# ── helpers ──────────────────────────────────────────────────────────────────

def get_recent_dates(n: int) -> list:
    """Return the last N distinct trade dates (most recent first)."""
    conn = get_conn()
    cur = dict_cursor(conn)
    cur.execute(
        "SELECT DISTINCT trade_date FROM nasdaq_eod "
        "ORDER BY trade_date DESC LIMIT %s",
        (n,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [str(r["trade_date"]) for r in rows]


def compute_signals_for_date(trade_date: str) -> list:
    """
    For each ticker, look back up to 6 rows (including trade_date itself).
    Returns a list of signal dicts.
    """
    conn = get_conn()
    cur = dict_cursor(conn)

    query = """
        SELECT ticker, trade_date, banker_rsi, close
        FROM nasdaq_eod
        WHERE trade_date <= %s AND banker_rsi IS NOT NULL
        ORDER BY ticker, trade_date DESC
    """
    cur.execute(query, (trade_date,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # Group by ticker, keeping only the 7 most recent rows
    ticker_rows = defaultdict(list)
    for r in rows:
        t = r["ticker"]
        if len(ticker_rows[t]) < 7:
            ticker_rows[t].append(r)

    results = []
    for ticker, history in ticker_rows.items():
        # history[0] = trade_date (today), history[1..] = prior days
        if not history or str(history[0]["trade_date"]) != trade_date:
            continue

        today_rsi = float(history[0]["banker_rsi"])
        today_close = float(history[0]["close"])

        immediate = today_rsi > 0

        three_day = False
        if today_rsi > 0 and len(history) >= 4:
            prior_3 = [float(history[i]["banker_rsi"]) for i in range(1, 4)]
            three_day = all(v == 0 for v in prior_3)

        five_day = False
        if today_rsi > 0 and len(history) >= 6:
            prior_5 = [float(history[i]["banker_rsi"]) for i in range(1, 6)]
            five_day = all(v == 0 for v in prior_5)

        if immediate or three_day or five_day:
            results.append({
                "ticker":     ticker,
                "date":       trade_date,
                "banker_rsi": round(today_rsi, 4),
                "close":      round(today_close, 4),
                "immediate":  immediate,
                "three_day":  three_day,
                "five_day":   five_day,
                "prior_banker_rsi": [
                    {
                        "date":       str(history[i]["trade_date"]),
                        "banker_rsi": round(float(history[i]["banker_rsi"]), 4),
                    }
                    for i in range(1, min(6, len(history)))
                ],
            })

    def rank(r):
        if r["five_day"]:   return 0
        if r["three_day"]:  return 1
        return 2

    results.sort(key=lambda r: (rank(r), r["ticker"]))
    return results


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/api/dates")
def api_dates():
    try:
        dates = get_recent_dates(60)
        return jsonify({"dates": dates})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/signal-day")
def api_signal_day():
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "date param required (YYYY-MM-DD)"}), 400
    try:
        signals = compute_signals_for_date(date)
        return jsonify({"date": date, "signals": signals, "count": len(signals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/signals")
def api_signals():
    n = int(request.args.get("days", 7))
    try:
        dates = get_recent_dates(n)
        all_results = {d: compute_signals_for_date(d) for d in dates}
        return jsonify({"data": all_results, "dates": dates})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def health():
    try:
        conn = get_conn()
        cur = dict_cursor(conn)
        cur.execute("SELECT COUNT(*) as cnt FROM nasdaq_eod LIMIT 1")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({"status": "ok", "rows": row["cnt"]})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
