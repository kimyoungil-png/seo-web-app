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
