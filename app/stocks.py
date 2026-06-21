"""対象銘柄ユニバース（日本株・米国株）。

Yahoo Finance では日本株は証券コードに `.T`（東証）を付けて指定する。
米国株はティッカーをそのまま（サフィックス無し）渡す。

- `STOCKS` / `BENCHMARK` … 既存のバックテスト・ペーパートレード・自動売買が参照する
  日本株ユニバース（後方互換のためそのまま維持）。
- `UNIVERSE` … 新しい調査（スクリーナー `/screen`）が使う日米統合ユニバース。
"""

# 対象期間（営業日数）。3日 / 1週間 / 2週間。スクリーナーと較正で共有する。
HORIZONS: tuple[int, ...] = (3, 5, 10)

# (証券コード, 表示名) の対応。yfinance には `<code>.T` を渡す。
STOCKS: dict[str, str] = {
    # 自動車・機械
    "7203.T": "トヨタ自動車",
    "7267.T": "ホンダ",
    "6902.T": "デンソー",
    "6301.T": "コマツ",
    "6273.T": "SMC",
    # 電機・精密
    "6758.T": "ソニーグループ",
    "6861.T": "キーエンス",
    "6981.T": "村田製作所",
    "6594.T": "ニデック",
    "7751.T": "キヤノン",
    # 半導体
    "8035.T": "東京エレクトロン",
    "6857.T": "アドバンテスト",
    "6146.T": "ディスコ",
    # 通信・IT・ネット
    "9984.T": "ソフトバンクグループ",
    "9432.T": "日本電信電話（NTT）",
    "9433.T": "KDDI",
    "4689.T": "LINEヤフー",
    "6098.T": "リクルートホールディングス",
    "4661.T": "オリエンタルランド",
    # 金融
    "8306.T": "三菱UFJフィナンシャル・グループ",
    "8316.T": "三井住友フィナンシャルグループ",
    "8411.T": "みずほフィナンシャルグループ",
    "8766.T": "東京海上ホールディングス",
    "8591.T": "オリックス",
    # 素材・化学・医薬
    "4063.T": "信越化学工業",
    "4502.T": "武田薬品工業",
    "4519.T": "中外製薬",
    "5401.T": "日本製鉄",
    "4901.T": "富士フイルム",
    # 商社・小売・消費
    "8058.T": "三菱商事",
    "8031.T": "三井物産",
    "9983.T": "ファーストリテイリング",
    "3382.T": "セブン&アイ・ホールディングス",
    "2914.T": "日本たばこ産業（JT）",
    # ゲーム・エンタメ
    "7974.T": "任天堂",
    "9766.T": "コナミグループ",
    "7832.T": "バンダイナムコHD",
    # その他主力
    "6367.T": "ダイキン工業",
    "9020.T": "JR東日本",
}

# 市場全体の地合い判定に使うベンチマーク指数（yfinance のシンボル）。
BENCHMARK = "^N225"  # 日経平均株価
BENCHMARK_NAME = "日経平均"


# ---------------------------------------------------------------------------
# 調査（スクリーナー /screen）用の日米統合ユニバース。
# 日本株は STOCKS を土台に主力を拡張、米国株は S&P100 相当の高流動性現物株。
# ---------------------------------------------------------------------------

# 日本株ユニバース（既存 STOCKS ＋ 主力を追加して約110銘柄）。
JP_STOCKS: dict[str, str] = {
    **STOCKS,
    # 自動車・機械・重工
    "7201.T": "日産自動車",
    "7269.T": "スズキ",
    "7270.T": "SUBARU",
    "7011.T": "三菱重工業",
    "7012.T": "川崎重工業",
    "6326.T": "クボタ",
    "6305.T": "日立建機",
    "6471.T": "日本精工",
    "6503.T": "三菱電機",
    "6501.T": "日立製作所",
    "6502.T": "東芝",
    # 電機・精密・部品
    "6752.T": "パナソニック",
    "6701.T": "NEC",
    "6702.T": "富士通",
    "6753.T": "シャープ",
    "6762.T": "TDK",
    "6971.T": "京セラ",
    "6954.T": "ファナック",
    "6645.T": "オムロン",
    "7741.T": "HOYA",
    "7733.T": "オリンパス",
    "4543.T": "テルモ",
    # 半導体・半導体製造装置
    "8035.T": "東京エレクトロン",
    "6920.T": "レーザーテック",
    "6723.T": "ルネサスエレクトロニクス",
    "6963.T": "ローム",
    "3436.T": "SUMCO",
    "4062.T": "イビデン",
    # 通信・IT・ネット・ゲーム
    "4755.T": "楽天グループ",
    "4385.T": "メルカリ",
    "3659.T": "ネクソン",
    "2432.T": "ディー・エヌ・エー",
    "9434.T": "ソフトバンク",
    "4307.T": "野村総合研究所",
    "9613.T": "NTTデータグループ",
    "6178.T": "日本郵政",
    # 金融・証券・不動産
    "8604.T": "野村ホールディングス",
    "8628.T": "松井証券",
    "8801.T": "三井不動産",
    "8802.T": "三菱地所",
    "8830.T": "住友不動産",
    "8750.T": "第一生命ホールディングス",
    "8725.T": "MS&ADインシュアランス",
    # 素材・化学・医薬・食品
    "4005.T": "住友化学",
    "4188.T": "三菱ケミカルグループ",
    "4452.T": "花王",
    "4568.T": "第一三共",
    "4523.T": "エーザイ",
    "4503.T": "アステラス製薬",
    "2802.T": "味の素",
    "2503.T": "キリンホールディングス",
    "2502.T": "アサヒグループHD",
    "2801.T": "キッコーマン",
    "3407.T": "旭化成",
    "5108.T": "ブリヂストン",
    "5020.T": "ENEOSホールディングス",
    "5713.T": "住友金属鉱山",
    "5411.T": "JFEホールディングス",
    # 商社・小売・サービス・消費
    "8001.T": "伊藤忠商事",
    "8002.T": "丸紅",
    "8053.T": "住友商事",
    "3092.T": "ZOZO",
    "3088.T": "マツキヨココカラ&カンパニー",
    "8267.T": "イオン",
    "9843.T": "ニトリホールディングス",
    "2413.T": "エムスリー",
    "4684.T": "オービック",
    "6098.T": "リクルートホールディングス",
    # インフラ・運輸・電力・ガス
    "9022.T": "JR東海",
    "9201.T": "日本航空",
    "9202.T": "ANAホールディングス",
    "9501.T": "東京電力ホールディングス",
    "9503.T": "関西電力",
    "9531.T": "東京ガス",
    "9101.T": "日本郵船",
    "9104.T": "商船三井",
    "9064.T": "ヤマトホールディングス",
}

# 米国株ユニバース（S&P100相当の高流動性現物株、約100銘柄）。
US_STOCKS: dict[str, str] = {
    # メガキャップ・テック
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet (A)",
    "GOOG": "Alphabet (C)",
    "META": "Meta Platforms",
    "TSLA": "Tesla",
    "AVGO": "Broadcom",
    "ORCL": "Oracle",
    "ADBE": "Adobe",
    "CRM": "Salesforce",
    "AMD": "AMD",
    "INTC": "Intel",
    "QCOM": "Qualcomm",
    "TXN": "Texas Instruments",
    "CSCO": "Cisco Systems",
    "IBM": "IBM",
    "NOW": "ServiceNow",
    "INTU": "Intuit",
    "AMAT": "Applied Materials",
    "MU": "Micron Technology",
    "PLTR": "Palantir",
    "UBER": "Uber",
    # 通信・メディア
    "NFLX": "Netflix",
    "DIS": "Walt Disney",
    "CMCSA": "Comcast",
    "T": "AT&T",
    "VZ": "Verizon",
    "TMUS": "T-Mobile US",
    # 金融
    "BRK-B": "Berkshire Hathaway (B)",
    "JPM": "JPMorgan Chase",
    "BAC": "Bank of America",
    "WFC": "Wells Fargo",
    "GS": "Goldman Sachs",
    "MS": "Morgan Stanley",
    "C": "Citigroup",
    "AXP": "American Express",
    "BLK": "BlackRock",
    "SCHW": "Charles Schwab",
    "V": "Visa",
    "MA": "Mastercard",
    "PYPL": "PayPal",
    # ヘルスケア
    "UNH": "UnitedHealth",
    "JNJ": "Johnson & Johnson",
    "LLY": "Eli Lilly",
    "ABBV": "AbbVie",
    "MRK": "Merck",
    "PFE": "Pfizer",
    "TMO": "Thermo Fisher",
    "ABT": "Abbott Laboratories",
    "DHR": "Danaher",
    "BMY": "Bristol-Myers Squibb",
    "AMGN": "Amgen",
    "GILD": "Gilead Sciences",
    "CVS": "CVS Health",
    "MDT": "Medtronic",
    # 一般消費財・小売
    "WMT": "Walmart",
    "COST": "Costco",
    "HD": "Home Depot",
    "LOW": "Lowe's",
    "NKE": "Nike",
    "MCD": "McDonald's",
    "SBUX": "Starbucks",
    "TGT": "Target",
    "BKNG": "Booking Holdings",
    "TJX": "TJX Companies",
    # 生活必需品
    "PG": "Procter & Gamble",
    "KO": "Coca-Cola",
    "PEP": "PepsiCo",
    "PM": "Philip Morris",
    "MO": "Altria",
    "MDLZ": "Mondelez",
    "CL": "Colgate-Palmolive",
    # エネルギー・素材
    "XOM": "Exxon Mobil",
    "CVX": "Chevron",
    "COP": "ConocoPhillips",
    "SLB": "Schlumberger",
    "LIN": "Linde",
    "FCX": "Freeport-McMoRan",
    # 資本財・運輸
    "BA": "Boeing",
    "CAT": "Caterpillar",
    "GE": "GE Aerospace",
    "HON": "Honeywell",
    "UPS": "United Parcel Service",
    "RTX": "RTX",
    "LMT": "Lockheed Martin",
    "DE": "Deere & Co",
    "UNP": "Union Pacific",
    "MMM": "3M",
    # その他主力
    "CRWD": "CrowdStrike",
    "PANW": "Palo Alto Networks",
    "SNOW": "Snowflake",
    "SHOP": "Shopify",
    "ABNB": "Airbnb",
    "COIN": "Coinbase",
    "MRVL": "Marvell Technology",
    "LRCX": "Lam Research",
    "KLAC": "KLA Corp",
    "ISRG": "Intuitive Surgical",
}

# 日米統合ユニバース（スクリーナーが対象とする全銘柄）。
UNIVERSE: dict[str, str] = {**JP_STOCKS, **US_STOCKS}

# 市場ごとの地合い判定ベンチマーク。
MARKET_BENCHMARK: dict[str, str] = {"JP": "^N225", "US": "^GSPC"}
MARKET_BENCHMARK_NAME: dict[str, str] = {"JP": "日経平均", "US": "S&P500"}


def market_of(ticker: str) -> str:
    """ティッカーの市場を返す。`.T` で終われば日本株(JP)、それ以外は米国株(US)。"""
    return "JP" if ticker.endswith(".T") else "US"


def benchmark_for(ticker: str) -> str:
    """その銘柄の相対強さ・地合い判定に使うベンチマーク指数シンボルを返す。"""
    return MARKET_BENCHMARK[market_of(ticker)]


def stock_name(ticker: str) -> str:
    """証券コードから表示名を返す。未知ならコードをそのまま返す。

    既存の日本株 STOCKS を優先しつつ、統合ユニバース UNIVERSE もカバーする。
    """
    return UNIVERSE.get(ticker, STOCKS.get(ticker, ticker))
