"""
指定したgithub_integrated_*.csvの is_profitable=True の行数を数えて標準出力に返す。
GitHub Actionsから「利益ありの時だけIssueを作る」判定に使う小さなユーティリティ。

使い方: python -m yahoo_mercari_arbitrage.count_profitable results/github_integrated_20260101_0600.csv
"""
import csv
import sys


def count_profitable(csv_path):
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return 0
    return sum(1 for r in rows if str(r.get("is_profitable", "")).strip().lower() == "true")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    print(count_profitable(path))
