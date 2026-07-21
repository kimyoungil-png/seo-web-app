# SEO Article Generator

Brave Search APIと、選択したGeminiまたはOpenAIのどちらか一方を使うStreamlitアプリです。

## Main workflow

全工程を1ページに縦並びで常時表示します。左サイドバー、SERPタブ、アコーディオンは使用しません。SetupからFact Checkまで上から下へ順番に確認・編集できます。

1. Setup
   - AI Model / AI API Key
   - Brave Search API Key / Country
   - Target Keyword
   - Owned Site URL（必須）
   - CTA URL（任意）
2. SERP Research
   - Web
   - Discussions
   - News
   - Videos
   - Suggestion
   - Analysis
3. Outline
4. Originality
   - 3つのAI提案から1案を選択
   - Additional Information（参考URL・意見など）を任意入力
5. Article Generation
6. Fact Check

Setup、SERP、Outline、Article、Fact Checkは、主要な`Next`を1回押すと生成と保存まで進みます。OutlineとArticleを後から編集した場合だけ、専用のApplyボタンで変更を反映します。Originalityは3案の生成後にユーザー選択が必要なため、選択確定時に`Next`を使用します。


## UI design

- 参考画像の白・黒・エレクトリックブルーを基調にした編集画面
- 機能入力は横並びにせず、原則として1項目ずつ縦方向に配置
- 現在の工程を上部に表示
- 各工程の次の操作を`Next action`として明示
- 完了ステージを緑色で強調し、ダウンロードボタンを緑色のアニメーションで表示
- 文字サイズは全体を12pxに統一し、余白・罫線・色・ウェイトで情報の階層を表現
- OSで「視差効果を減らす／アニメーションを減らす」が有効な場合、アニメーションを停止

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Secrets

`.streamlit/secrets.toml`またはStreamlit CloudのSecretsに、利用するキーを設定します。

```toml
GEMINI_API_KEY = "..."
OPENAI_API_KEY = "..."
BRAVE_SEARCH_API_KEY = "..."
```

GeminiとOpenAIのキーを両方設定する必要はありません。画面で使うAIのキーだけ設定してください。
APIキーはGitHubへコミットしないでください。


## Setup URLs

Setupには次のURL入力があります。

- `Owned Site URL`：必須。記事内で読者の課題と自然につなぐ案内先として利用します。
- `CTA URL`：任意。問い合わせ、資料請求、サービスページなどの遷移先です。空欄でも全工程を実行できます。

両方ともHTTPまたはHTTPS URLとして検証します。スキームを省略した場合は`https://`として正規化します。
Owned Site URLまたはCTA URLを変更すると、SERP ResearchとAnalysisは保持したままOutline以降をリセットします。
URL文字列だけから、サービス内容、価格、機能、実績は推測しません。

## Originality additional information

Originalityでは、AIが生成した3案から1案を選び、任意で`Additional Information (URL or opinion)`を入力できます。

- 参考URL
- 現場で感じている課題
- 記事へ反映したい意見や判断軸
- 独自データの要点
- 追加条件や注意事項

`Next`を押すと、選択案と追加情報をまとめて`07-selected-originality.json`へ保存します。Article Generationは、選択案の`placement`に対応するH2で両方を参照します。

追加情報は任意です。意見は意見として扱い、URLだけからリンク先の内容、機能、実績、価格などを推測しません。選択案または追加情報を変更した場合は、再度`Next`を押して確定するまでArticle Generationへ進めません。

## Article title and description

Article Generationの最終Markdownは、承認済みOutlineのMeta TitleとMeta Descriptionを使い、先頭に次の形式で出力します。

```yaml
---
title: "記事のMeta Title"
description: "記事のMeta Description"
---
```

その後にH1、Key Takeaways、H2本文が続きます。OutlineにMeta Titleがない旧データではH1をtitleとして使用し、Meta Descriptionがない場合はTarget Keywordから安全な説明文を補完します。

## AI models

画面に表示されるモデル名と、APIへ渡すモデルIDを一致させています。

- Gemini 3.1 Flash-Lite → `gemini-3.1-flash-lite`
- Gemini 3.5 Flash → `gemini-3.5-flash`
- OpenAI GPT-5 mini → `gpt-5-mini`

選択したプロバイダーは、Analysis、Outline、Originality、Article Generation、Fact Checkのすべてで統一して使用します。Fact CheckではAI固有のWeb検索機能を使わず、Brave Search APIが証拠収集を担当します。
AI Modelを変更すると、異なるプロバイダーの結果が混ざらないようSERP Research以降をリセットします。

## Brave endpoints

1回のSERP Researchで次を利用します。

- Web: `GET /res/v1/web/search`
- Discussions:
  - `<keyword> site:reddit.com`
  - `<keyword> site:chiebukuro.yahoo.co.jp`
  - `<keyword> site:bbs.kakaku.com`
  - いずれもWeb Search APIを利用
- News: `GET /res/v1/news/search`
- Videos: `GET /res/v1/videos/search`
- Suggestion: `GET /res/v1/suggest/search`

SuggestionはまずBrave Autosuggestを試します。

- `q`
- `country`
- `count`

Web Search用の`search_lang`を`lang`として送らず、Entity向けの`rich=true`も要求しません。Country付きリクエストが0件の場合はCountryなしで1回再試行します。Autosuggestが契約プランに含まれない場合や取得できない場合は、すでに取得済みのDiscussions、Web、Videos、Newsのタイトルから関連クエリ候補を作り、ワークフローを継続します。

Autosuggestの技術エラーは通常表示せず、SERPフォールバックを使ったことを分かりやすい警告で示します。必要な場合だけチェックボックスから技術診断を表示できます。

## Country and language

SERP設定画面ではBrave API KeyとCountryだけを設定します。言語は自動連動します。

- Tokyo, Japan → `JP / jp / ja-JP`
- Seoul, South Korea → `KR / ko / ko-KR`
- United States → `US / en / en-US`

Web結果件数は8件固定です。

## Analysis pipeline

Analysisは2段階です。

1. Pythonの`analyze_serp()`
   - WebページのH2/H3頻度集計
   - Discussions、News、Videos、Suggestionの入力整理
   - 取得失敗・警告の明示
2. `references/analysis-prompt.md`を使ったAI分析
   - ① 評価されるコンテンツの共通点
   - ② ユーザーが困っていること
   - ③ トレンディーな話題
   - ④ 人気のテーマ
   - ⑤ FAQ
   - Competitor Gap / User Intent / Recommended Strategy / Outline Direction

H2/H3抽出は`fetch_serp.py`が担当するため、AI Analysisへ移行しても失われません。

## Prompt files

処理順と役割は次のとおりです。

1. `analysis-prompt.md` — SERP根拠を戦略分析
2. `outline-prompt.md` — 1つのH2＝1つの読者課題、Key Takeaways、推奨表現形式、出典・鮮度、FAQ、Owned Site導線、任意CTAを設計
3. `originality-prompt.md` — コンテンツギャップを評価し、Owned Site視点のメリット・条件・注意点・限界を含む独自性を3案生成
4. `article-prompt.md` — 選択した独自性と任意のAdditional Informationを参照し、H2単位で本文を生成。抽象論の後に具体例を置き、Owned Siteと任意CTAを文脈に沿って反映
5. `fact-extraction-prompt.md` — 記事を独立して検証できる事実単位へ分解
6. `factcheck-prompt.md` — Brave Searchで収集した証拠を使い、5件ずつ評価

共通ルール:

- `sop.md` — 全体工程、状態、成果物、停止・再実行ルール
- `writing-style.md` — 文体、段落、見出し、表、文字数
- `data-integrity.md` — 数値・事実・欠損データの扱い


## Fact Check pipeline

Fact Checkは、長い記事を1回のWeb検索付きAIリクエストへ送らず、次の順序で処理します。

```text
Article
  ↓
選択中のAIで検証可能な事実を抽出
  ↓
各事実をBrave Web Searchで検索
  ↓
異なるドメインから最大6件の証拠候補を整理
  ↓
5件ずつ選択中のAIへ渡して判定
  ↓
True / Minor Errors / Needs Double-Checking / Falseの表を生成
```

AIのネイティブWeb検索機能は使用しません。抽出済み事実、取得済み証拠、完了済みバッチは`10-fact-check/`へ保存されます。503では一時的な混雑、429では利用上限であることを日本語で案内し、`Retry Fact Check`から続きの処理を再開できます。記事、AIモデル、国・言語、バッチサイズが変わった場合は、古いFact Check作業データを破棄して最初から処理します。

各事実につき3つ以上の独立した情報源を目標とします。3件未満の場合、AIが`True`と返してもアプリ側で`Needs Double-Checking`へ補正します。

## Runtime state and artifacts

画面状態は`st.session_state`だけで管理します。`@st.cache_data`と`@st.cache_resource`は使用しません。`run.json`の`content_settings`にはOwned Site URLとCTA URLを保存しますが、APIキーは保存しません。

各runの主要成果物は`.seo/runs/<run_id>/`へ保存されます。

```text
run.json
02-serp.json
03-analysis-evidence.md
04-analysis.md
05-outline.md
06-originality-proposals.json
07-selected-originality.json
08-drafts/
09-article.md
10-fact-check/
├── state.json
├── 01-facts.json
├── 02-evidence.json
└── 03-batches/
10-fact-check.md
```

旧SOPにあった`02-selection.md`など、現在のアプリが作成しないファイル名は削除済みです。

## Distribution ZIP

配布ZIPには次を含めません。

- `__pycache__/`
- `*.pyc`
- `.venv/`
- `.env*`
- `.streamlit/secrets.toml`
- `.seo/`

## Web page heading extraction and HTTP 403

Brave Search API returns the ranking, title, URL and snippets. H2/H3 extraction is a separate direct request from the Streamlit app to each publisher page.

The app now requests the normal desktop HTML variant with browser-compatible headers and the language that corresponds to the selected country. It does not bypass CAPTCHA, login walls or publisher access controls.

When a publisher returns HTTP 401, 403 or 429:

- The row is shown as `Blocked by site (...)` rather than a generic application error.
- Brave title, snippet and extra snippets remain available to Analysis.
- The page is excluded from H2/H3 frequency counts.
- Lower-ranked editorial results from the same Brave Web Search response may supplement the heading-analysis pool.

Social and video platform pages such as Instagram, X and YouTube are treated as `Snippet only (platform)` because their server-rendered HTML rarely contains useful article H2/H3 structures.
