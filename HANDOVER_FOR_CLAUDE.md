# Claude Code 引き継ぎ資料 - BicCamera/Najima/Yamada/Edion/Yodobashi アービトラージ

**作成日:** 2026-09-04
**リポジトリ:** https://github.com/harkingbee/biccamera-arbitrage
**Discord Webhook:** https://discord.com/api/webhooks/1543760445087949004/azEbkrLe4SMOiqiJsVOxKnMyGFKI4rZ0lQj7UdsqMrAsGnxTIrPLUzExZUbx29TcmJGh

---

## 1. プロジェクト概要

家電量販店6社（ビックカメラ/ヤマダデンキ/エディオン/ノジマ/ヨドバシカメラ/ケーズデンキ）の在庫処分品・夏物・スマホ関連商品を Yahoo!ショッピングAPI で取得し、**GitHubプログラム `take-kun/mercapi`（メルカリ）＋ Yahoo!オークション**で保守的中央値を算出して転売利益を判定するパイプライン。

**コア計算式:**
```
net_cost = (yahoo_price - COUPON_DISCOUNT) * (1 - point_rate - PAYPAY_RATE)
profit = conservative_median * 0.9 - SHIPPING - net_cost
is_profitable = profit>0 and margin>=MIN_MARGIN and total_count>=MIN_COUNT
conservative_median = min(mercapi_median, yahooAuction_median)
```

---

## 2. リポジトリ構成

```
biccamera-arbitrage/
├── yahoo_mercari_arbitrage/
│   ├── arbitrage.py                          # 元祖：Yahoo→Mercari 50件 (Selenium)
│   ├── arbitrage_github_integrated.py        # ★GitHub統合版メイン（6社×15カテゴリ、mercapi+YahooAuction）
│   ├── broad_arbitrage.py                    # 広域15カテゴリ版（テレビ/冷蔵庫等）
│   ├── smartphone_arbitrage.py               # スマホ8クエリ版
│   ├── clearance_arbitrage.py                # 在庫処分特化（priceLabel割引率+キーワード）
│   ├── multi_retailer_arbitrage.py           # 6社横断（ビック/ヤマダ/エディオン/ノジマ/ヨドバシ/ケーズ）
│   ├── nojima_arbitrage.py                   # ノジマオンライン3段階フォールバック
│   ├── arbitrage_accuracy_tfidf.py           # 精度向上：scikit-learn TF-IDF + ボラティリティ
│   ├── biccamera_scan.py / biccamera_summer_scan.py / etc. # 個別スキャン
│   ├── research/
│   │   ├── margin_analyzer.py                # atushi1841/yahoo-auctions-japan-scraper から移植
│   │   ├── research_locally.py
│   │   └── biccamera_watchlist.json          # カテゴリ別Watchlist
│   └── requirements.txt
├── results/
│   ├── target_products_20260831.csv          # ★ビックカメラ最終対象10件
│   ├── multi_target_deduped_20260902.csv     # 6社横断ユニーク3件
│   ├── smartphone_target_deduped_20260902.csv# スマホ1件
│   ├── clearance_target_20260904.csv         # 在庫処分2件
│   ├── github_integrated_*.csv               # GitHub統合版全量
│   ├── broad_*.csv / smartphone_*.csv / clearance_*.csv
│   └── accuracy_tfidf_*.csv
├── .github/workflows/
│   ├── arbitrage.yml                         # 毎日06:00 JST実行
│   └── accuracy.yml                          # 毎日07:00 精度モニタ
└── README.md
```

**ローカルパス（開発環境）:**
- `/Users/harkingbee/claud code/yahoo_mercari_arbitrage/` - スクリプト本体
- `/Users/harkingbee/opne code/project/results/` - 最新CSV出力先
- `/tmp/biccamera-arbitrage/` - GitHubリポジトリのローカルクローン

---

## 3. 重要ファイルと行番号

| ファイル | 行番号 | 役割 |
|---|---|---|
| `yahoo_mercari_arbitrage/arbitrage_github_integrated.py:96` | `await m.search(status=[STATUS_SOLD_OUT])` | **GitHub mercapi** 呼び出し |
| `yahoo_mercari_arbitrage/arbitrage_github_integrated.py:40` | `fetch_yahoo_seller()` | Yahoo Shopping API V3 `itemSearch` |
| `yahoo_mercari_arbitrage/research/margin_analyzer.py:61` | `_tokens() _same_sku()` | atushi1841の照合ロジック |
| `yahoo_mercari_arbitrage/arbitrage_accuracy_tfidf.py:12` | `TfidfVectorizer` | scikit-learn精度向上 |
| `yahoo_mercari_arbitrage/nojima_arbitrage.py:25` | 3段階フォールバック | ノジマAkamai回避 |
| `yahoo_mercari_arbitrage/clearance_arbitrage.py:38` | `CLEARANCE_KEYWORDS` + `priceLabel` | 在庫処分判定 |
| `.github/workflows/arbitrage.yml:1` | `cron: '0 21 * * *'` | 毎日06:00 JST |

---

## 4. CONFIG（全スクリプト共通）

```python
CONFIG={
  "YAHOO_APP_ID": "dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw", # base64 Client ID
  "YAHOO_POINT_RATE":0.10, # ビック10% / ノジマ8%
  "COUPON_DISCOUNT":1000,  # ビック1000 / ノジマ500
  "PAYPAY_RATE":0.01,
  "MIN_PROFIT_MARGIN":0.10, # 広域は0.12
  "MIN_COUNT":3,
  "MERCARI_FEE":0.10,
  "MIN_PRICE":2000, # 広域5000, スマホ8000, 在庫処分3000
}
SHIPPING_MAP={"扇風機本体":600,"除湿機":1200,"家庭用エアコン":3000}
```

**小売別 seller_id:**
- ビックカメラ: `y-sofmap`, `y-kojima`
- ヤマダデンキ: `yamada-denki` ✅ Yahooで16件ヒット
- エディオン: `edion-tsutayakaden` ✅ 2件
- ノジマ/ヨドバシ/ケーズ: `yahoo_proxy`（汎用Yahoo検索、直接はAkamaiブロック）

---

## 5. 実行方法

```bash
# 環境
pip install -r yahoo_mercari_arbitrage/requirements.txt
# mercapi, yahoo-shopping, scikit-learn, selenium, webdriver-manager が含まれる

export YAHOO_APP_ID=dmVyPTIwMjUwNyZpZD1IYm5kZzRhN0w3Jmhhc2g9TkRNeE1tSTFZMkZsTUdVeFkyWmtNZw

# メイン（6社×15カテゴリ）
python -m yahoo_mercari_arbitrage.arbitrage_github_integrated
# → results/github_integrated_YYYYMMDD.csv

# 広域
python -m yahoo_mercari_arbitrage.broad_arbitrage
# スマホ
python -m yahoo_mercari_arbitrage.smartphone_arbitrage
# 在庫処分特化
python -m yahoo_mercari_arbitrage.clearance_arbitrage
# ノジマ
python -m yahoo_mercari_arbitrage.nojima_arbitrage
# 精度TF-IDF
python -m yahoo_mercari_arbitrage.arbitrage_accuracy_tfidf
```

**注意:**
- Yahoo APIは `429` で10秒リトライ（全スクリプトで `time.sleep(10*attempt)` 実装済み）
- Mercariは `mercapi` でAPIレベル取得のためSelenium不要だが、Yahoo AuctionはSelenium（`span.merPrice` / `span[class*='price']`）を使用
- Playwrightでノジマ直取得を試みたが `Access Denied`（Akamai Reference #18.*）でブロック、現在はYahoo代理にフォールバック

---

## 6. GitHub活用状況

| GitHubプログラム | 用途 | 統合ファイル |
|---|---|---|
| `take-kun/mercapi` | メルカリ売切価格取得 | `arbitrage_github_integrated.py:96` |
| `atushi1841/yahoo-auctions-japan-scraper` | Yahoo落札相場のトークン正規化・SKU一致 | `research/margin_analyzer.py:61` |
| `34j/yahoo-shopping` | Yahoo Shopping非同期ラッパー | `clearance_arbitrage.py` で `priceLabel` 取得に利用 |
| `scikit-learn/scikit-learn` | TF-IDFでタイトル類似度0.35以上のみ合格 | `arbitrage_accuracy_tfidf.py:12` |
| `matsuno-delgit/japan-electronics-retail-fy2026` | 7社の決算在庫データ（未統合、`/tmp/japan-electronics-retail` にクローン済み） | 今後 `companies.json` で在庫圧迫小売を重み付け予定 |

**GitHub Actions:**
- `arbitrage.yml` - 毎日06:00 JST、30件スキャン→CSVコミット→Issue作成（`peter-evans/create-issue-from-file`）
- `accuracy.yml` - 毎日07:00、TF-IDFスコアを `results/accuracy_*.csv` に記録
- Secrets: `YAHOO_APP_ID` は `gh secret set` で登録済み
- 実行履歴: https://github.com/harkingbee/biccamera-arbitrage/actions

---

## 7. 最新結果サマリー（2026-09-04時点）

| スキャン | 対象 | 利益あり | 代表商品 | CSV |
|---|---|---|---|---|
| ビックカメラ統合 | 30件 | **4件** | RLX-MP023-C +918円 (12件 high) | `results/github_integrated_20260901_2350.csv` |
| 6社横断 | 30件 | **3件ユニーク** | 2780ハンディファン +1247円 | `results/multi_target_deduped_20260902.csv` |
| スマホ | 30件 | **1件** | IPHONE11ケース +9065円 | `results/smartphone_target_deduped_20260902.csv` |
| 広域15カテゴリ | 30件 | **0件** | 汎用クエリでは型番ノイズで赤字 | `results/broad_20260902_2124.csv` |
| 在庫処分特化 | 131件→25件 | **2件** | EDBC-SVM121ヒーター +1655円 | `results/clearance_target_20260904.csv` |
| 最終対象リスト | - | **10件** | RLX-MP023-C等 | `results/target_products_20260831.csv` |

**最も信頼できるのは `RLX-MP023-C` と `YNG-LHF200-BE`（両サイト6-12件で中央値一致、TF-IDF 0.54-1.00 PASS）**

---

## 8. 残課題と次のステップ

1. **Yahoo `priceLabel` バッチ化** - 現状は1件ごとにAPIを叩くため429。在庫処分判定はタイトルキーワードに依存、割引率15%以上の自動検出は `priceLabel.discountedPrice` をバッチで取得する必要あり
2. **Mercariブロック対策** - 本日100件超で `しばらくお待ちください` が頻発。`undetected-chromedriver` または Bright Data Residential Proxy（`luminati-io/*`）への切替が必要
3. **ノジマ/ヨドバシ直取得** - Akamaiでブロック中。`BRIGHTDATA_PROXY` 環境変数を設定して `nojima_arbitrage.py:25` のProxy経由を有効化する必要あり
4. **直接スクレイピングのPlaywright移行** - Seleniumは検出されやすいため、 `microsoft/playwright` + `playwright-stealth` への移行を検討
5. **広域クエリの具体化** - 「テレビ 4K」等の汎用語は `2026` `1440ML` 等のノイズ型番を拾う。`KJ-75X80L` のようにSKUをWatchlist化すべき（`research/biccamera_watchlist.json` を15→30カテゴリに拡張）
6. **価格履歴の活用** - `results/` に日次CSVが蓄積されているが、ボラティリティ計算は未実装。`accuracy_tfidf.py` の `history` 辞書を活用して `stdev/mean >0.3` は除外するロジックを追加予定

---

## 9. Discord通知

- Webhook: `https://discord.com/api/webhooks/1543760445087949004/azEbkrLe4SMOiqiJsVOxKnMyGFKI4rZ0lQj7UdsqMrAsGnxTIrPLUzExZUbx29TcmJGh`
- 通知内容: 利益ありTOP5のEmbed + CSV添付（`target_products_*.csv` 等）
- テスト送信済み: 2026-09-01 13:30/13:39/14:59/15:00、2026-09-04 17:00（在庫処分）

---

## 10. Claude Codeへの指示

1. `gh repo clone harkingbee/biccamera-arbitrage` で最新を取得
2. `pip install -r yahoo_mercari_arbitrage/requirements.txt` で依存関係をインストール
3. 上記「実行方法」で任意のスキャンを実行し、結果を `results/` に保存
4. 残課題を `yahoo_mercari_arbitrage/` 内で修正し、GitHub Actionsが毎日自動実行されることを確認
5. 新しい小売（例：ジョーシン `joshin` ）を追加する場合は `RETAILERS` 辞書と `seller_id` を `multi_retailer_arbitrage.py:31` に追記

**参考ログ:** `logs/` 配下の `*_20260901_*.log` に全実行の詳細あり
