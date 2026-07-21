# SEO Article Generator

Streamlit app using Brave Search API and one selected AI provider.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Workflow

All stages appear on one page. Completed stages are collapsed and can be reopened. A completed action button changes from blue to gray while remaining available for regeneration.

1. Setup
2. SERP Research
3. Outline
4. Originality
5. Article Generation
6. Fact Check

The Outline editor is shown below the SERP analysis at full page width.

## Brave SERP data

A single Brave Web Search request asks for all available result types:

- `web`: competitor analysis and outline planning
- `discussions`: user voice and pain points
- `news`: freshness, changes, and recent developments
- `videos`: procedures, comparisons, demonstrations, and visual explanations

Brave may omit a result type when it has no relevant data or when the subscribed plan does not include that response option.

## AI provider behavior

Select either Gemini or OpenAI in **AI Settings**. The selected provider and model are used consistently for every AI stage:

- Outline
- Originality
- Article Generation
- Fact Check

Changing the AI model clears downstream AI-generated outputs so providers are not mixed within one workflow.

## AI prompt files

- `references/originality-prompt.md`
- `references/article-prompt.md`
- `references/factcheck-prompt.md`
- `references/writing-style.md`
- `references/sop.md`
- `references/data-integrity.md`

## State management

The application does not use `@st.cache_data`, `@st.cache_resource`, `st.cache_data`, or `st.cache_resource`. Workflow results are stored in `st.session_state` only.

## Brave endpoints

One SERP Research run uses the following requests:

- Web: `GET /res/v1/web/search`
- Discussions: three Web Search requests using `site:reddit.com`, `site:chiebukuro.yahoo.co.jp`, and `site:bbs.kakaku.com`
- News: `GET /res/v1/news/search`
- Videos: `GET /res/v1/videos/search`
- Suggestion candidates: `GET /res/v1/suggest/search?rich=true`

FAQ retrieval has been removed. A full run therefore makes seven Brave API requests before fetching headings from individual Web result pages.

## Analysis処理

SERP Researchでは、次の2段階でAnalysisを作成します。

1. Pythonの`analyze_serp()`が、取得済みのH2/H3を頻度集計し、Discussions、News、Videos、SuggestionをAI入力用Markdownに整理します。
2. 選択中のGeminiまたはOpenAIが、`references/analysis-prompt.md`に従って以下を分析します。
   - ① 評価されるコンテンツの共通点
   - ② ユーザーが困っていること
   - ③ トレンディーな話題
   - ④ 人気のテーマ
   - ⑤ FAQ

実行結果は`.seo/runs/<run_id>/`内の以下に保存されます。

- `04-analysis-evidence.md`: Pythonで整理した根拠データ
- `05-serp-analysis.md`: AIによる最終Analysis
