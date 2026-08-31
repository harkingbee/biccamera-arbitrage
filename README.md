# BicCamera × Mercari/Yahoo!オークション 転売アービトラージ

GitHubプログラム統合版：Yahoo!ショッピング + メルカリ + ヤフオクの3市場を横断して在庫処分品の利益を自動検出

## GitHub活用

- **take-kun/mercapi** - メルカリAPIラッパー（Selenium不要）
- **34j/yahoo-shopping** - Yahoo!ショッピング非同期ラッパー
- **atushi1841/yahoo-auctions-japan-scraper** - ヤフオク落札相場のトークン正規化・SKU一致ロジック
- **GitHub Actions** - 毎日06:00 JST自動実行

## 使い方

```bash
pip install -r yahoo_mercari_arbitrage/requirements.txt
export YAHOO_APP_ID=your_app_id
python -m yahoo_mercari_arbitrage.arbitrage_github_integrated
# → results/github_integrated_YYYYMMDD.csv
```

## 成果物

- `results/github_integrated_20260830_2219.csv` - 最新30件中5件が利益あり（例：ヤマゼン RLX-MP023-C +918円）
- GitHub Actionsで毎日自動コミット＋Issue作成

## ワークフロー

`.github/workflows/arbitrage.yml` が毎日06:00 JSTに実行
