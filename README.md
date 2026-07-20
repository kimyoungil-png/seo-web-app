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
- `faq`: question list and information needs
- `news`: freshness, changes, and recent developments
- `videos`: procedures, comparisons, demonstrations, and visual explanations
- `infobox`: normalized in the app as `entity`

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
