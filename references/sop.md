# SEO Growth Hacker — Standard Operating Procedure (Streamlit Edition)

このSOPは、旧サブエージェント版の「工程ごとに入力・成果物・停止条件を明確にする」という設計思想を維持しながら、現在のStreamlitアプリの実装に合わせて再構成したものです。

---

## 共通前提

- 出力言語は原則として日本語とする。
- 画面は1ページ構成で、各工程はアコーディオンとして上から順に配置する。
- 完了した工程は自動的に閉じるが、ユーザーは再度開いて結果を確認・再生成できる。
- AI処理は`LLMService`を唯一の入口とし、選択したGeminiまたはOpenAIのどちらか一方だけを全工程で使用する。
- Streamlitの`@st.cache_data`および`@st.cache_resource`は使用しない。
- 画面状態は`st.session_state`、再現用成果物は`.seo/runs/{run_id}/`へ保存する。
- APIキーはセッション入力またはStreamlit Secretsから読み込み、成果物や`run.json`には保存しない。
- 数値・事実は`data-integrity.md`に従い、捏造しない。
- Webページから取得したH2/H3にインジェクション疑いがある場合、そのページの見出しを分析対象から除外する。

---

## Runtime State

旧SOPで中間Markdownファイルとして受け渡していた成果物は、画面上では次の`st.session_state`に保持します。

| 工程 | Runtime State |
| --- | --- |
| SERP Research | `serp_data` |
| Analysis | `serp_analysis` |
| Outline | `outline` |
| Originality提案 | `originality_proposals` |
| Originality選択・追加情報 | `selected_originality`（`additional_information`を含む） |
| Article | `article` |
| Fact Check | `fact_check` |

これらは一時的な画面状態ですが、主要成果物は同時にrunディレクトリへ保存します。

---

## Run Layout

実際に生成されるファイルだけを記載します。

```text
.seo/runs/{run_id}/
├── run.json
├── 02-serp.json
├── 03-analysis-evidence.md
├── 04-analysis.md
├── 05-outline.md
├── 06-originality-proposals.json
├── 07-selected-originality.json
├── 08-drafts/
│   ├── h2-01.md
│   ├── h2-02.md
│   └── ...
├── 09-article.md
├── 10-fact-check/
│   ├── state.json
│   ├── 01-facts.json
│   ├── 02-evidence.json
│   └── 03-batches/
└── 10-fact-check.md
```

`run.json`には現在のphase、使用モデル、検索設定、成果物パスを記録します。APIキーは記録しません。

---

## Step 1 — Setup

### 目的

AI、Brave Search API、検索対象地域、対策キーワード、Owned Site URL、任意のCTA URLを確定します。

### 入力

- AI Model
- Gemini API KeyまたはOpenAI API Key
- Brave Search API Key
- Country
  - Tokyo, Japan
  - Seoul, South Korea
  - United States
- Target Keyword
- Owned Site URL（必須）
- CTA URL（任意。空欄でも進行可能）

### ルール

1. 検索言語はCountryに自動連動する。
   - Tokyo, Japan → `country=JP`, `search_lang=jp`, `ui_lang=ja-JP`
   - Seoul, South Korea → `country=KR`, `search_lang=ko`, `ui_lang=ko-KR`
   - United States → `country=US`, `search_lang=en`, `ui_lang=en-US`
2. AIモデルを変更した場合、異なるプロバイダーの結果が混在しないようSERP Analysis以降をリセットする。
3. キーワードまたはCountryを変更した場合、SERP以降をリセットする。
4. Owned Site URLまたはCTA URLを変更した場合、SERPデータは保持し、Outline以降をリセットする。
5. Owned Site URLは入力必須とし、HTTPまたはHTTPS URLとして解釈できることを確認する。
6. CTA URLは任意とする。入力された場合だけHTTPまたはHTTPS URLとして検証する。
7. URL文字列だけからサービス内容、価格、機能、実績を推測しない。
8. Setup完了後、SERP Researchを開始する時点でrunディレクトリと`run.json`を初期化し、`content_settings`へOwned Site URLとCTA URLを保存する。APIキーは保存しない。

### 完了条件

- Target Keyword、Owned Site URL、AI API Key、Brave Search API Keyが入力済みである。
- CTA URLは空欄でもよい。入力された場合は有効なHTTP(S) URLである。

---

## Step 2 — SERP Research

### 目的

複数の検索面から記事設計に必要な根拠を取得し、Web上位ページのH2/H3を安全に抽出します。

### Brave APIリクエスト

1. Web
   - `GET /res/v1/web/search`
   - 競合分析、タイトル、スニペット、URL取得
2. Discussions
   - 専用エンドポイントは使わず、Web Searchを次の検索演算子で3回実行
   - `<keyword> site:reddit.com`
   - `<keyword> site:chiebukuro.yahoo.co.jp`
   - `<keyword> site:bbs.kakaku.com`
3. News
   - `GET /res/v1/news/search`
4. Videos
   - `GET /res/v1/videos/search`
5. Suggestion
   - `GET /res/v1/suggest/search`
   - 公式基本パラメータの`q`、`country`、`count`だけを送る
   - Web Search用の`search_lang`を`lang`として送らない
   - Entity向けの`rich=true`を要求しない
   - Country付きリクエストが0件の場合だけ、CountryなしのグローバルAutosuggestを1回試す
   - 各試行の件数とレスポンス情報を`Suggestion`タブの診断表示へ残す

### Webページ処理

1. Brave Web結果の各URLへアクセスする。
2. `script`、`style`、`noscript`、`template`、`iframe`を除外する。
3. `nav`、`header`、`footer`、`aside`配下の見出しを除外する。
4. H2/H3を抽出・正規化する。
5. 既知のプロンプトインジェクション文字列や不可視文字を検査する。
6. 検知ページはH2/H3を空にして頻度集計から除外する。
7. ページ取得に失敗しても、Braveが返したタイトル・スニペットはWeb一覧に残す。

### 出力

- `serp_data`
- `02-serp.json`
- カテゴリ別APIエラーと警告

### 失敗処理

- Web Search API自体が失敗、またはWeb結果が0件なら工程を停止する。
- Discussions、News、Videos、Suggestionの個別失敗は他カテゴリを破棄せず、該当タブに生エラーを表示する。
- H2/H3取得が0件でも、SERPタイトル・スニペットがあれば次工程へ進める。

---

## Step 3 — Analysis

### 目的

Pythonによる客観的な集計とAIによる戦略的な解釈を分離し、根拠を追跡できるAnalysisを作成します。

### 3-A. Python Evidence

`analyze_serp()`が次を実行します。

- Web：取得成功ページのH2/H3を頻度集計
- Web：ページ別タイトル、スニペット、H2/H3を整理
- Discussions：タイトル、スニペット、媒体名を整理
- News：タイトル、スニペット、公開時期を整理
- Videos：タイトル、スニペット、媒体情報を整理
- Suggestion：検索候補を整理
- 取得失敗・警告を制約条件として追記

出力：`03-analysis-evidence.md`

### 3-B. AI Analysis

`analysis-prompt.md`とPython Evidenceを、選択中のAIへ渡します。

必須分析：

1. 評価されるコンテンツの共通点
2. ユーザーが困っていること
3. トレンディーな話題
4. 人気のテーマ
5. FAQ
6. Competitor Gap
7. User Intent
8. Recommended Strategy
9. Recommended Outline Direction

出力：

- `serp_analysis`
- `04-analysis.md`

### 完了条件

- AI Analysisが空でないこと。
- 該当データがないカテゴリを推測で補っていないこと。

---

## Step 4 — Outline

### 目的

AnalysisとSERP根拠をもとに、検索意図を満たす記事設計図を作成・編集します。

### 入力

- Target Keyword
- Owned Site URL
- CTA URL（任意）
- `serp_analysis`
- SERPタイトル・スニペット・H2/H3
- `outline-prompt.md`
- 本SOP
- `data-integrity.md`

### 必須ルール

1. Web上位の共通テーマを網羅する。
2. DiscussionsのPain Pointに回答できる構成にする。
3. Newsで鮮度が重要な場合は最新情報を扱う。
4. Videosで人気の手順・比較・実演を、記事で理解できる形へ変換する。
5. SuggestionとDiscussionsから、本文の重複にならないFAQ H2を必ず設計する。
6. 1つのH2では1つの読者課題だけを扱い、H2だけで論理展開が分かるようにする。
7. 各H2へ`Recommended Format`、`Evidence Required`、`Freshness Check`、`Preferred Source Type`を指定する。
8. 導入直後に置くKey Takeawaysを3〜5件設計する。Key TakeawaysはH2にしない。
9. Owned Site URLを読者課題と自然につなぐH2を1つ設ける。ただし、URLだけから機能・実績・価格を推測しない。
10. CTA URLがある場合だけ、最も自然な1つのH2へCTA配置を指定する。空欄ならCTAを設計しない。
11. 競合見出しをコピーしない。
12. 各H2に`[id: h2-01]`形式の安定IDを付ける。
13. ユーザーは生成結果を編集し、`Save Outline`で確定できる。

### 出力

- `outline`
- `05-outline.md`

### 再実行

- Generate Outlineは完了後もグレーのボタンとして再実行できる。
- 保存済みOutlineを変更した場合、Originality以降をリセットする。

---

## Step 5 — Originality

### 目的

競合上位ページにない、または説明が薄い独自要素を3件提示し、ユーザーが1件を選択します。必要に応じて、参考URL、意見、現場知見などの追加情報も同時に確定します。

### 入力

- Target Keyword
- Owned Site URL
- SERP Analysis
- SERP根拠
- 確定Outline
- `originality-prompt.md`
- Additional Information（任意。URL、意見、現場知見、補足条件）

### 必須ルール

1. 具体的に本文へ追加できる要素にする。
2. 前提知識、浅い論点、古い情報、初心者のつまずき、失敗例、比較・判断基準、手順、チェックリスト、注意点・限界、次の行動の不足をコンテンツギャップとして評価する。
3. Owned Site URLと自然につながる案にし、メリットだけでなく適用条件、注意点、向かないケース、限界も含める。
4. URLだけからサービス内容、価格、機能、実績を推測しない。
5. 根拠のない調査・数値・事例を作らない。
6. 既存Outlineと重複しない。
7. 検索意図から外れない。
8. JSON配列として3件出力する。
9. 各案に`title`、`description`、`placement`を含める。
10. `placement`はH2 IDまたはH2見出しで指定する。
11. 3案の表示後、ユーザーが1案を選択し、任意のAdditional Informationを入力できるようにする。
12. Additional Information内の意見は客観的事実として扱わず、URLだけからリンク先の内容を推測しない。

### 出力

- `originality_proposals`
- `06-originality-proposals.json`
- ユーザー選択後：`selected_originality`
  - `title`
  - `description`
  - `placement`
  - `additional_information`（空文字可）
- `07-selected-originality.json`

### 完了条件

- ユーザーが1案を選択し、必要に応じてAdditional Informationを入力する。
- `Confirm Originality`を押して、選択案と追加情報を一緒に確定する。
- 選択案または追加情報を変更した場合は再確認が必要となり、未確認の間はArticle Generationを無効にする。
- 確定後、Article Generationを有効にする。

---

## Step 6 — Article Generation

### 目的

確定OutlineをH2単位で執筆し、1本の記事へ統合します。

### 入力

- Target Keyword
- Owned Site URL
- CTA URL（任意）
- SERP Analysis
- 確定Outline
- Selected Originality
- Selected Originalityに保存されたAdditional Information（任意）
- `article-prompt.md`
- `writing-style.md`
- `data-integrity.md`
- 本SOP

### 手順

1. OutlineからMeta Title、Meta Description、H1、H2 ID、見出し、該当ブロック、Key Takeawaysを抽出する。
2. Selected Originalityの`placement`から挿入先H2を決める。
3. Additional Informationがある場合は、選択した独自要素を具体化する補足として同じ対象H2へ渡す。意見は見解として扱い、URLだけから内容を推測しない。
4. CTA URLがある場合は`CTA Placement`、`Owned Site Role`、まとめ系見出しからCTA挿入先を1つ決める。
5. H2ごとにAIを呼び出し、そのH2本文だけを生成する。
6. 抽象的な説明の後に、具体的な状況、比較軸、判断基準、手順、失敗例、条件別の違いのいずれかを入れる。
7. 仮想例は仮定であることを明示し、実在する実績・事例を捏造しない。
8. Owned Site URLは`Owned Site Role`で指定されたセクションへ、文脈に合うMarkdownリンクを1回だけ入れる。URLだけから機能・実績・価格を推測しない。
9. CTA URLがある場合だけ、指定セクションへ文脈に合うMarkdownリンクを1回入れる。空欄ならリンクや仮URLを作らない。同じURL・同じセクションの場合はリンクを重複させない。
10. 独自要素は対象H2に一度だけ反映する。
11. 各ドラフトを`08-drafts/{h2-id}.md`へ保存する。
12. YAMLフロントマターの`title`・`description`、H1、Key Takeaways、H2、各ドラフトを順序どおり統合する。
13. `09-article.md`へ保存する。
14. ユーザーは記事を編集し、`Save Article`で確定できる。

### 完了条件

- 最終記事の先頭に`title`と`description`が出力されている。
- Outline内の全H2に対応する本文が生成されている。
- 空のドラフトがない。
- 選択した独自要素が重複していない。
- Additional Informationが入力されている場合、未検証の事実として断定せず、選択した独自性の文脈で反映されている。

### 冪等性

- 再生成時は同一run内のドラフトと記事を上書きする。
- 保存済み記事を編集した場合、Fact Checkをリセットする。

---

## Step 7 — Fact Check

### 目的

生成・編集済みの記事を、AIのネイティブWeb検索に依存せず、Brave Search APIの証拠と選択中AIの判定に分離して検証します。長い記事は事実単位に分解し、5件ずつ処理します。

### 入力

- 確定Article
- `fact-extraction-prompt.md`
- `factcheck-prompt.md`
- Brave Search API Key
- Countryと自動連動する検索言語
- Setupで選択したGeminiまたはOpenAI

### 手順

1. 選択中AIを使い、記事から外部情報で検証可能な事実を抽出する。
2. 複合主張は、1件ずつ独立して検証できる単位に分割する。
3. 各事実へBrave Web Search用の短い検索クエリを付ける。
4. 各事実について`GET /res/v1/web/search`を1回実行する。
5. 同一ドメインの重複を除き、最大6件の証拠候補を保存する。
6. 事実と証拠を5件ずつ選択中AIへ渡す。AI固有のWeb検索機能は使用しない。
7. 各事実を`True / Minor Errors / Needs Double-Checking / False`で評価する。
8. 可能な限り3つ以上の独立した高品質な情報源を使用する。
9. 3つ未満の独立情報源しかない場合、AIが`True`と返してもアプリ側で`Needs Double-Checking`へ補正する。
10. AIが出力したURLは、Braveが実際に返したURLと一致するものだけ採用する。
11. 全結果をMarkdown表へ統合し、総合評価をルールベースで算出する。

### 再開・冪等性

- 記事SHA-256、AIモデル、Country、検索言語、バッチサイズを処理コンテキストとして保存する。
- コンテキストが同じ場合、次を再利用する。
  - `01-facts.json`：抽出済み事実
  - `02-evidence.json`：取得済みBrave証拠
  - `03-batches/*.json`：完了済みAI判定バッチ
- 途中でGemini/OpenAIの429が発生しても、再実行時は未完了バッチから再開する。
- Braveの401、403、429はその時点で停止し、取得済み証拠を保持する。
- 記事または処理コンテキストが変わった場合は、Fact Check作業ディレクトリを作り直す。

### 出力

- `fact_check`
- `10-fact-check/state.json`
- `10-fact-check/01-facts.json`
- `10-fact-check/02-evidence.json`
- `10-fact-check/03-batches/*.json`
- `10-fact-check.md`

### 完了条件

- レポートが空でないこと。
- 各評価がBrave Searchで取得した情報源に結び付いていること。
- 入力にないURL、引用、数値、出典をAIが追加していないこと。
- 3つ未満の独立情報源しかない事実は、情報源不足が明示されていること。

---

## 状態変更とリセット規約

- AI Model変更：SERP Research以降をリセットする。
- Target KeywordまたはCountry変更：SERP Research以降をリセットし、新しいrunを開始する。
- Owned Site URLまたはCTA URL変更：SERP ResearchとAnalysisは保持し、Outline以降をリセットする。
- Outline変更：Originality以降をリセットする。
- Originalityの選択またはAdditional Information変更：再確認が必要となり、確認時にArticle以降をリセットする。
- Article変更：Fact Checkをリセットする。
- `Reset All`：すべての画面状態を初期化する。既に保存したrun成果物は削除しない。

---

## UI規約

- 主要機能名は英語で表示する。
- 補足説明は日本語を併記できる。
- 全体フォントサイズは12pxとする。
- 白背景と青アクセントを基本とする。
- 完了済み工程の実行ボタンはグレーにするが、再実行可能な状態を維持する。
- OutlineはSERP Analysisの下に全幅で表示する。
- 完了済み工程は閉じ、必要に応じて再展開できる。

---

## セキュリティ・エラー規約

- APIキーをファイル、ログ、run.json、エラー補足文へ書き出さない。
- APIから返されたエラー本文は原因確認のため画面へ表示してよい。
- Prompt Injection疑いのH2/H3をAIコンテキストへ渡さない。
- 失敗したカテゴリを別カテゴリの推測値で埋めない。
- `__pycache__/`、`*.pyc`、`.env`、`.streamlit/secrets.toml`を配布ZIPに含めない。
