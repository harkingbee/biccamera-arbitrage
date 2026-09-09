"""
arbitrage_github_integrated の当日スキャン結果をGoogle Sheetsへ書き込み、
Discordへサマリー通知する。

必要な環境変数（未設定ならスキップし正常終了する。CIを壊さないためのガード）:
- GOOGLE_SERVICE_ACCOUNT_JSON: サービスアカウントのJSON鍵の中身（文字列そのまま）
- GOOGLE_SHEET_ID: 書き込み先スプレッドシートのID（URLの /d/ と /edit の間）
- DISCORD_WEBHOOK_URL: 通知先のDiscord Webhook URL

シート構成:
- 「最新結果」: 直近スキャンの全件を毎回上書き
- 「履歴」: 実行ごとに日時・対象件数・利益あり件数・TOP1商品を1行追記

依存: gspread, google-auth, requests
"""
import csv
import glob
import json
import os
from datetime import datetime, timezone

import requests

RESULTS_DIR = "results"
SHEET_LATEST = "最新結果"
SHEET_HISTORY = "履歴"

# 「最新結果」シートに出す列。生CSVの21列は情報が多すぎるため、
# 利益が出ているかどうかが一目でわかる最小限の列に絞る
DISPLAY_COLUMNS = [
    ("mark", "利益あり"),
    ("name", "商品名"),
    ("model", "型番"),
    ("yahoo_price", "仕入価格"),
    ("conservative_median", "参考売値"),
    ("profit_yen", "利益額"),
    ("profit_margin", "利益率%"),
    ("yahoo_url", "URL"),
]


def latest_csv():
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "github_integrated_2*.csv")))
    return files[-1] if files else None


def load_rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _is_profitable(r):
    return str(r.get("is_profitable", "")).strip().lower() == "true"


def _profit_yen(r, default=-999):
    try:
        return int(r.get("profit_yen") or default)
    except (TypeError, ValueError):
        return default


def get_sheets_client():
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_worksheet(sh, title, rows=200, cols=25):
    try:
        return sh.worksheet(title)
    except Exception:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def _row_for_display(r):
    line = []
    for key, _ in DISPLAY_COLUMNS:
        line.append("✅" if key == "mark" and _is_profitable(r) else r.get(key, ""))
    return line


def write_latest(sh, rows, scanned_at):
    ws = get_or_create_worksheet(sh, SHEET_LATEST)
    ws.clear()
    profitable = [r for r in rows if _is_profitable(r)]
    ws.append_row([f"利益あり: {len(profitable)}件 / 対象{len(rows)}件　（最終更新: {scanned_at}）"])
    ws.append_row([label for _, label in DISPLAY_COLUMNS])
    # 利益ありの商品を上に、利益額が大きい順に並べる
    ordered = sorted(rows, key=lambda r: (not _is_profitable(r), -_profit_yen(r)))
    if ordered:
        ws.append_rows([_row_for_display(r) for r in ordered], value_input_option="USER_ENTERED")


def append_history(sh, rows, scanned_at):
    ws = get_or_create_worksheet(sh, SHEET_HISTORY, rows=1000, cols=8)
    if not ws.get_all_values():
        ws.append_row(["日時", "対象件数", "利益あり件数", "TOP1商品名", "TOP1利益額", "TOP1利益率%"])
    profitable = [r for r in rows if _is_profitable(r)]
    top = sorted(profitable, key=_profit_yen, reverse=True)
    top1 = top[0] if top else None
    ws.append_row([
        scanned_at, len(rows), len(profitable),
        top1["name"][:60] if top1 else "",
        top1.get("profit_yen", "") if top1 else "",
        top1.get("profit_margin", "") if top1 else "",
    ])


def notify_discord(sheet_url, rows, scanned_at):
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    profitable = [r for r in rows if _is_profitable(r)]
    top5 = sorted(profitable, key=_profit_yen, reverse=True)[:5]
    lines = [
        f"**{i}. {r['name'][:40]}** ¥{_profit_yen(r, 0):,} ({r.get('profit_margin','')}%) {r.get('model','')}"
        for i, r in enumerate(top5, 1)
    ]
    embed = {
        "title": "ビックカメラ アービトラージ 日次スキャン結果",
        "url": sheet_url,
        "description": (
            f"対象: {len(rows)}件 / 利益あり: {len(profitable)}件\n\n"
            + ("\n".join(lines) if lines else "本日は利益ありなし")
        ),
        "color": 0x00C853 if profitable else 0x9E9E9E,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": [{"name": "スプレッドシート", "value": sheet_url, "inline": False}],
    }
    r = requests.post(webhook, json={"embeds": [embed]}, timeout=15)
    r.raise_for_status()


def main():
    missing = [k for k in ("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEET_ID", "DISCORD_WEBHOOK_URL") if not os.getenv(k)]
    if missing:
        print(f"環境変数未設定のためスキップ: {', '.join(missing)}")
        return

    csv_path = latest_csv()
    if not csv_path:
        print(f"対象CSVなし（{RESULTS_DIR}/github_integrated_*.csv）、スキップ")
        return

    rows = load_rows(csv_path)
    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    gc = get_sheets_client()
    sh = gc.open_by_key(sheet_id)
    write_latest(sh, rows, scanned_at)
    append_history(sh, rows, scanned_at)
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"

    notify_discord(sheet_url, rows, scanned_at)
    print(f"完了: {csv_path} -> Sheets({sheet_id}) -> Discord通知")


if __name__ == "__main__":
    main()
