"""GAS code.gs と揃えた定数（シート名・キー名）。"""

SHEET_HUB_TEST_LIST = "テスト一覧"
SHEET_ROSTER = "名簿"
SHEET_TEST_INFO = "テスト情報"
SHEET_ANSWER_FIELDS = "記述欄情報"
SHEET_POINTS = "配点情報"
SHEET_RESULTS = "採点結果"
SHEET_CRITERIA = "採点基準"
SHEET_SUMMARY = "考査総括"
SHEET_DOMAINS = "領域設定"
SHEET_IDENTITY_FIELDS = "本人確認欄情報"
SHEET_EXTERNAL_SCORES = "外部連携得点"
SHEET_OCR_REPLACEMENTS = "OCR置換ルール"
SHEET_DEEMED_SCORING = "みなし採点"
SHEET_DEEMED_DRAFT = "みなし採点下書き"
SHEET_OUTPUT_SLOTS = "出力欄設定"
SHEET_FEEDBACK_STYLE = "出力書式設定"

ORIGINAL_ARCHIVE_FOLDER_NAME = "元画像"
PROCESSED_ARCHIVE_FOLDER_NAME = "処理済み"
FEEDBACK_FOLDER_NAME = "個票"
ROOT_IMAGE_FOLDER_NAME = "採点システム画像"

TEST_INFO_KEYS = [
    "テスト名",
    "科目名",
    "実施日時",
    "作成日時",
    "模範解答画像FileID",
    "生徒回答フォルダID",
    "基準画像幅",
    "基準画像高さ",
    "ステータス",
    "現在ステップ",
    "最終保存日時",
    "選択名簿名",
    "IDマーク欄使用",
    "未受験者",
]

HUB_TEST_LIST_HEADERS = [
    "テスト名",
    "スプレッドシートID",
    "URL",
    "作成日",
    "ステータス",
    "現在ステップ",
    "最終保存日時",
]

ROSTER_HEADERS = [
    "名簿名",
    "ID",
    "年",
    "組",
    "番号",
    "氏名",
    "その他属性1",
    "その他属性2",
    "その他属性3",
]

CELL_PX = 20

STEPS = [
    {"id": 1, "label": "① テスト作成"},
    {"id": 2, "label": "② 回答欄設定"},
    {"id": 3, "label": "③ 生徒回答用紙取り込み"},
    {"id": 4, "label": "④ 配点決定"},
    {"id": 5, "label": "⑤ トリミング"},
    {"id": 6, "label": "（⑥ 薄字補正）"},
    {"id": 7, "label": "⑦ OCR実行"},
    {"id": 8, "label": "⑧ 採点基準"},
    {"id": 9, "label": "⑨ 採点実行"},
    {"id": 10, "label": "⑩ 領域設定"},
    {"id": 11, "label": "⑪ 合計・外部得点"},
    {"id": 12, "label": "⑫ 本人欄設定"},
    {"id": 13, "label": "⑬ ID・氏名照合"},
    {"id": 14, "label": "⑭ 個票・成績一覧出力"},
]

# 分岐後 — 手動採点ルート（⑦ の代替 + 空DBで開始）
MANUAL_BOOTSTRAP_STEP_ID = 15
MANUAL_GRADING_STEP_ID = 16

DESKTOP_READY_STEPS = {
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    MANUAL_BOOTSTRAP_STEP_ID,
    MANUAL_GRADING_STEP_ID,
}
