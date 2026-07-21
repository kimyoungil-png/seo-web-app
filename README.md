# SEO Article Generator

Brave Search APIと、選択したGeminiまたはOpenAIのどちらか一方を使うStreamlitアプリです。

## Main workflow

全工程を1ページに縦並びで表示します。完了した工程は自動的に閉じますが、アコーディオンを開いて結果確認・再生成ができます。

1. Setup
   - Target Keyword
   - Owned Site URL（必須）
   - CTA URL（任意）
2. SERP Research
3. Outline
4. Originality
   - 3つのAI提案から1案を選択
   - Additional Information（参考URL・意見など）を任意入力
5. Article Generation
6. Fact Check

完了済み工程の実行ボタンはグレーになりますが、再実行可能です。

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

`Confirm Originality`を押すと、選択案と追加情報をまとめて`07-selected-originality.json`へ保存します。Article Generationは、選択案の`placement`に対応するH2で両方を参照します。

追加情報は任意です。意見は意見として扱い、URLだけからリンク先の内容、機能、実績、価格などを推測しません。選択案または追加情報を変更した場合は、再度`Confirm Originality`を押すまでArticle Generationへ進めません。

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

選択したプロバイダーは、Analysis、Outline、Originality、Article Generation、Fact Checkのすべてで統一して使用します。
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

Suggestionは最初に`rich=true`を試します。契約プランなどの理由で失敗した場合、通常Suggestionを表示するため`rich=false`へフォールバックします。

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
5. `factcheck-prompt.md` — 記事をWeb検索で検証

共通ルール:

- `sop.md` — 全体工程、状態、成果物、停止・再実行ルール
- `writing-style.md` — 文体、段落、見出し、表、文字数
- `data-integrity.md` — 数値・事実・欠損データの扱い

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
