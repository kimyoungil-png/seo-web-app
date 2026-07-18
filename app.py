from datetime import datetime
from pathlib import Path

import streamlit as st

from backend import (
    MODEL_OPTIONS,
    analyze_serp,
    create_llm_service,
    get_model_config,
    step2_fetch_serp_and_filter,
    step3_generate_outline,
    step4_propose_originality,
    step5_generate_sections_and_assemble,
    step6_fact_check,
)

st.set_page_config(page_title="SEO Article Generator", page_icon="📝", layout="wide")

st.markdown(
    '''
<style>
:root {
  --blue:#1267d6;
  --blue-dark:#0b4fa8;
  --blue-soft:#eef5ff;
  --line:#dce7f5;
  --text:#172033;
  --muted:#61758e;
}
html, body, [class*="css"], .stApp, .stApp * { font-size:12px !important; }
.stApp { background:#ffffff; color:var(--text); }
.block-container { max-width:1280px; padding-top:1.2rem; padding-bottom:4rem; }
h1 { font-size:24px !important; color:#0b3d7a; margin-bottom:2px !important; }
h2 { font-size:17px !important; color:#124f9c; }
h3 { font-size:14px !important; color:#124f9c; }
p, label, span, div, button, input, textarea, select { font-size:12px !important; }
[data-testid="stSidebar"] { background:#f7faff; border-right:1px solid var(--line); }
[data-testid="stExpander"] { border:1px solid var(--line); border-radius:12px; background:#ffffff; margin-bottom:12px; overflow:hidden; }
[data-testid="stExpander"] summary { background:#f8fbff; color:#123f73; font-weight:700; padding:12px 14px; }
[data-testid="stExpander"] summary:hover { color:var(--blue); }
.stButton > button, .stDownloadButton > button { border-radius:8px; border:1px solid var(--blue); min-height:36px; font-weight:700; }
.stButton > button[kind="primary"] { background:var(--blue); color:white; }
.stButton > button[kind="primary"]:hover { background:var(--blue-dark); }
.section-note { background:var(--blue-soft); border-left:4px solid var(--blue); border-radius:8px; padding:10px 12px; margin-bottom:10px; }
.status-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:4px 0 14px; }
.status-pill { border:1px solid var(--line); border-radius:999px; padding:5px 10px; color:var(--muted); background:#fff; font-weight:700; }
.status-pill.done { color:#0b4fa8; background:#eef5ff; border-color:#bcd4f5; }
.status-pill.current { color:#fff; background:var(--blue); border-color:var(--blue); }
div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
</style>
''',
    unsafe_allow_html=True,
)

STAGES = ["Setup", "SERP Research", "Outline", "Originality", "Article Generation", "Fact Check"]
DEFAULTS = {
    "serp_data": None,
    "serp_analysis": None,
    "outline": "",
    "originality_proposals": None,
    "selected_originality": None,
    "originality_choice": None,
    "article": "",
    "fact_check": "",
    "run_dir": None,
    "active_keyword": "",
    "active_llm_choice": "",
}
for key, default in DEFAULTS.items():
    st.session_state.setdefault(key, default)


def secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def reset_from(stage: str) -> None:
    order = ["serp", "outline", "originality", "article", "fact"]
    mapping = {
        "serp": ["serp_data", "serp_analysis"],
        "outline": ["outline"],
        "originality": ["originality_proposals", "selected_originality", "originality_choice"],
        "article": ["article"],
        "fact": ["fact_check"],
    }
    for item in order[order.index(stage):]:
        for key in mapping[item]:
            st.session_state[key] = DEFAULTS[key]


def reset_all() -> None:
    for key, default in DEFAULTS.items():
        st.session_state[key] = default


def ensure_run_dir() -> Path:
    if st.session_state.run_dir is None:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = Path(".seo") / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        st.session_state.run_dir = run_dir
    return st.session_state.run_dir


st.title("SEO Article Generator")
st.caption("Complete each section from top to bottom. Finished sections close automatically and can be reopened at any time.")

with st.sidebar:
    st.subheader("AI Settings")
    llm_choice = st.selectbox("AI Model", list(MODEL_OPTIONS.keys()))
    model_config = get_model_config(llm_choice)
    st.caption(f"Model ID: `{model_config['model']}` · All AI stages use this provider only")
    if model_config["provider"] == "gemini":
        api_key = st.text_input("Gemini API Key", value=secret("GEMINI_API_KEY"), type="password")
    else:
        api_key = st.text_input("OpenAI API Key", value=secret("OPENAI_API_KEY"), type="password")

    if st.session_state.active_llm_choice and st.session_state.active_llm_choice != llm_choice:
        reset_from("outline")
    st.session_state.active_llm_choice = llm_choice

    st.divider()
    st.subheader("SERP API Settings")
    brave_api_key = st.text_input("Brave Search API Key", value=secret("BRAVE_SEARCH_API_KEY"), type="password")
    country_options = {
        "Tokyo, Japan": {"country": "JP", "search_lang": "jp", "ui_lang": "ja-JP"},
        "Seoul, South Korea": {"country": "KR", "search_lang": "ko", "ui_lang": "ko-KR"},
        "United States": {"country": "US", "search_lang": "en", "ui_lang": "en-US"},
    }
    selected_country_label = st.selectbox("Country", list(country_options.keys()))
    result_count = st.selectbox("Result Count", [5, 8, 10, 15, 20], index=1)
    country_config = country_options[selected_country_label]
    serp_credentials = {"api_key": brave_api_key, **country_config}
    st.caption(f"Language is linked automatically: `{country_config['search_lang']}`")

    st.divider()
    if st.button("Reset All", use_container_width=True):
        reset_all()
        st.rerun()

keyword = st.text_input("Target Keyword", value=st.session_state.active_keyword, placeholder="例：Galaxy AI 使い方")
if keyword != st.session_state.active_keyword:
    reset_from("serp")
    st.session_state.active_keyword = keyword

states = [
    bool(keyword and api_key and brave_api_key),
    bool(st.session_state.serp_data),
    bool(st.session_state.outline),
    bool(st.session_state.selected_originality),
    bool(st.session_state.article),
    bool(st.session_state.fact_check),
]
current_idx = next((i for i, done in enumerate(states) if not done), len(states) - 1)
status_html = '<div class="status-row">'
for idx, (label, done) in enumerate(zip(STAGES, states)):
    klass = "done" if done else ("current" if idx == current_idx else "")
    marker = "✓" if done else str(idx + 1)
    status_html += f'<span class="status-pill {klass}">{marker} {label}</span>'
status_html += "</div>"
st.markdown(status_html, unsafe_allow_html=True)

with st.expander("1. Setup — 基本設定", expanded=not states[0]):
    st.markdown('<div class="section-note">AI、APIキー、検索対象国、対策キーワードを設定します。言語は国に合わせて自動連動します。</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("AI Model", llm_choice)
    c2.metric("Country", selected_country_label)
    c3.metric("SERP Results", result_count)
    missing = []
    if not keyword:
        missing.append("Target Keyword")
    if not api_key:
        missing.append("AI API Key")
    if not brave_api_key:
        missing.append("Brave Search API Key")
    if missing:
        st.info("未入力: " + " / ".join(missing))
    else:
        st.success("Setup is complete. Continue to SERP Research below.")

with st.expander("2. SERP Research — 競合SERPの取得・分析", expanded=states[0] and not states[1]):
    if not states[0]:
        st.info("Complete Setup first.")
    else:
        if st.button("Run SERP Research", type="primary", key="run_serp"):
            reset_from("serp")
            run_dir = ensure_run_dir()
            try:
                with st.spinner("Brave Search APIからSERPを取得し、各ページのH2・H3を分析しています..."):
                    st.session_state.serp_data = step2_fetch_serp_and_filter(
                        keyword, run_dir.name, run_dir, provider="brave", credentials=serp_credentials, top_n=result_count
                    )
                    st.session_state.serp_analysis = analyze_serp(st.session_state.serp_data)
                st.rerun()
            except Exception as exc:
                st.error(f"SERP Research error: {exc}")
        if st.session_state.serp_data:
            rows = []
            for result in st.session_state.serp_data.get("results", []):
                headings = result.get("headings", {})
                rows.append({
                    "Rank": result.get("rank"),
                    "Title": result.get("title"),
                    "URL": result.get("url"),
                    "Snippet": result.get("snippet", ""),
                    "H2": len(headings.get("h2", [])),
                    "H3": len(headings.get("h3", [])),
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.markdown(st.session_state.serp_analysis or "")
            with st.expander("View Page Headings", expanded=False):
                for result in st.session_state.serp_data.get("results", []):
                    st.markdown(f"**{result.get('rank')}. {result.get('title') or result.get('url')}**")
                    st.write("H2:", result.get("headings", {}).get("h2", []))
                    st.write("H3:", result.get("headings", {}).get("h3", []))
                    st.divider()

with st.expander("3. Outline — 構成案の生成・編集", expanded=states[1] and not states[2]):
    if not states[1]:
        st.info("Complete SERP Research first.")
    else:
        left, right = st.columns([0.4, 0.6], gap="large")
        with left:
            st.subheader("SERP Analysis")
            st.markdown(st.session_state.serp_analysis or "分析結果がありません。")
        with right:
            st.subheader("Outline Editor")
            if st.button("Generate Outline", type="primary", key="generate_outline"):
                reset_from("outline")
                try:
                    llm = create_llm_service(api_key, llm_choice)
                    with st.spinner("SERP分析とSERP生データを参照して構成案を生成しています..."):
                        st.session_state.outline = step3_generate_outline(
                            llm, keyword, st.session_state.serp_data, ensure_run_dir(), st.session_state.serp_analysis
                        )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Outline generation error: {exc}")
            if st.session_state.outline:
                edited_outline = st.text_area("Outline", value=st.session_state.outline, height=520, key="outline_editor")
                if edited_outline != st.session_state.outline:
                    st.session_state.outline = edited_outline
                    reset_from("originality")
                st.success("Outline is ready. Continue to Originality below.")

with st.expander("4. Originality — 独自要素の提案・選択", expanded=states[2] and not states[3]):
    if not states[2]:
        st.info("Complete Outline first.")
    else:
        if st.button("Generate 3 Originality Ideas", type="primary", key="generate_originality"):
            reset_from("originality")
            try:
                llm = create_llm_service(api_key, llm_choice)
                with st.spinner("SERPにない、または競合で薄い独自要素を生成しています..."):
                    st.session_state.originality_proposals = step4_propose_originality(
                        llm, keyword, st.session_state.serp_data, st.session_state.outline,
                        ensure_run_dir(), st.session_state.serp_analysis
                    )
                st.rerun()
            except Exception as exc:
                st.error(f"Originality generation error: {exc}")

        proposals = st.session_state.originality_proposals or []
        if proposals:
            labels = [f"{i + 1}. {item.get('title', 'Idea')}" for i, item in enumerate(proposals)]
            cards = st.columns(len(proposals))
            for idx, (item, col) in enumerate(zip(proposals, cards)):
                with col:
                    st.markdown(f"**{labels[idx]}**")
                    st.write(item.get("description", ""))
                    st.caption(f"Placement: {item.get('placement', '')}")
            selected_index = st.radio(
                "Select one idea",
                options=list(range(len(labels))),
                format_func=lambda i: labels[i],
                horizontal=True,
                key="originality_choice_widget",
            )
            if st.button("Confirm Originality", type="primary", key="confirm_originality"):
                st.session_state.originality_choice = selected_index
                st.session_state.selected_originality = proposals[selected_index]
                reset_from("article")
                st.rerun()
            if st.session_state.selected_originality:
                st.success(f"Selected: {st.session_state.selected_originality.get('title', 'Idea')}")

with st.expander("5. Article Generation — 記事生成・編集", expanded=states[3] and not states[4]):
    if not states[3]:
        st.info("Confirm an Originality idea first.")
    else:
        if st.button("Generate Article", type="primary", key="generate_article"):
            reset_from("article")
            try:
                llm = create_llm_service(api_key, llm_choice)
                with st.spinner("構成案、SERP分析、独自要素を反映して記事を生成しています..."):
                    st.session_state.article = step5_generate_sections_and_assemble(
                        llm, keyword, st.session_state.outline, st.session_state.selected_originality,
                        ensure_run_dir(), st.session_state.serp_analysis
                    )
                st.rerun()
            except Exception as exc:
                st.error(f"Article generation error: {exc}")
        if st.session_state.article:
            edited_article = st.text_area("Article", value=st.session_state.article, height=680, key="article_editor")
            if edited_article != st.session_state.article:
                st.session_state.article = edited_article
                reset_from("fact")
            st.download_button("Download Markdown", st.session_state.article, file_name="article.md", mime="text/markdown")

with st.expander("6. Fact Check — ファクトチェック", expanded=states[4] and not states[5]):
    if not states[4]:
        st.info("Complete Article Generation first.")
    else:
        st.caption("references/factcheck-prompt.mdを読み込み、選択中のAIのWeb検索機能で記事を検証します。")
        if st.button("Run Fact Check", type="primary", key="run_fact_check"):
            reset_from("fact")
            try:
                llm = create_llm_service(api_key, llm_choice)
                with st.spinner("記事全体を調査・検証しています..."):
                    st.session_state.fact_check = step6_fact_check(
                        llm, st.session_state.article, ensure_run_dir()
                    )
                st.rerun()
            except Exception as exc:
                st.error(f"Fact Check error: {exc}")
        if st.session_state.fact_check:
            st.markdown(st.session_state.fact_check)
            st.download_button(
                "Download Fact Check", st.session_state.fact_check, file_name="fact-check.md", mime="text/markdown"
            )
            st.success("All stages are complete.")
