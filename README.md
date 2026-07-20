# SEO Article Generator

Streamlit app using Brave Search API and one selected AI provider.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## AI provider behavior

Select either Gemini or OpenAI in **AI Settings**. The selected provider and model are used consistently for every AI stage:

- Outline
- Originality
- Article Generation
- Fact Check

Changing the AI model clears downstream AI-generated outputs so providers are not mixed within one workflow.

## AI prompt files

The AI instructions for each generation stage are stored separately under `references/`.

- `originality-prompt.md`: Stage 4 Originality
- `article-prompt.md`: Stage 5 Article Generation
- `factcheck-prompt.md`: Stage 6 Fact Check

You can tune these stages without editing Python code. The application reads each file at runtime.

## State management and cache policy

This application does not use `@st.cache_data`, `@st.cache_resource`, `st.cache_data`, or `st.cache_resource`.

Workflow results are stored only in `st.session_state`:

- SERP results and analysis
- Outline and manual edits
- Originality proposals and selection
- Generated article and manual edits
- Fact-check report

Changing the keyword or AI model resets downstream results so that outputs from different settings are not mixed. The Streamlit toolbar is set to `minimal` in `.streamlit/config.toml`, reducing access to development-only cache controls while preserving full API error details.
