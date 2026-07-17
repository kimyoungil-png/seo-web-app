import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from backend import (
    MODEL_OPTIONS,
    get_llm_client,
    get_model_config,
    step2_fetch_serp_and_filter,
    step3_generate_outline,
    step4_propose_originality,
    step5_generate_sections_and_assemble,
    step6_fact_check,
)

st.set_page_config(page_title="SEO Article Generator", page_icon="📝", layout="wide")
st.title("SEO Article Generator")

for key, default in {
    "serp_data": None,
    "outline": "",
    "originality_proposals": None,
    "selected_originality": None,
    "article": "",
    "fact_check": "",
    "run_dir": None,
}.items():
    st.session_state.setdefault(key, default)

with st.sidebar:
    st.header("API設定")
    llm_choice = st.selectbox("使用するAI", list(MODEL_OPTIONS.keys()))
    model_config = get_model_config(llm_choice)
    st.caption(f"実際のモデルID: `{model_config['model']}`")

    if model_config["provider"] == "gemini":
        api_key = st.text_input("Gemini API Key", type="password")
    else:
        api_key = st.text_input("OpenAI API Key", type="password")

keyword = st.text_input("対策キーワード")

if keyword and api_key and st.session_state.run_dir is None:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(".seo") / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    st.session_state.run_dir = run_dir

st.header("1. 競合SERPの取得")
if st.button("競合SERPを取得", disabled=not (keyword and api_key)):
    try:
        with st.spinner("競合SERPを取得しています..."):
            st.session_state.serp_data = step2_fetch_serp_and_filter(
                keyword, st.session_state.run_dir.name, st.session_state.run_dir
            )
        st.success("競合SERPを取得しました。")
    except Exception as exc:
        st.error(f"SERP取得エラー: {exc}")

if st.session_state.serp_data:
    rows = []
    for result in st.session_state.serp_data.get("results", []):
        headings = result.get("headings", {})
        rows.append(
            {
                "順位": result.get("rank"),
                "タイトル": result.get("title"),
                "URL": result.get("url"),
                "H2数": len(headings.get("h2", [])),
                "H3数": len(headings.get("h3", [])),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    for result in st.session_state.serp_data.get("results", []):
        with st.expander(f"{result.get('rank')}位: {result.get('title') or result.get('url')}"):
            st.markdown(f"[{result.get('url')}]({result.get('url')})")
            st.write("H2", result.get("headings", {}).get("h2", []))
            st.write("H3", result.get("headings", {}).get("h3", []))

st.header("2. 構成案の確認・編集")
if st.button(
    "構成案を生成",
    disabled=not (api_key and keyword and st.session_state.serp_data),
):
    try:
        client = get_llm_client(api_key, llm_choice)
        with st.spinner("構成案を生成しています..."):
            st.session_state.outline = step3_generate_outline(
                client,
                llm_choice,
                keyword,
                st.session_state.serp_data,
                st.session_state.run_dir,
            )
    except Exception as exc:
        st.error(f"構成案生成エラー: {exc}")

if st.session_state.outline:
    st.session_state.outline = st.text_area(
        "構成案は直接編集できます",
        value=st.session_state.outline,
        height=420,
    )

st.header("3. 独自性（オリジナル要素）の提案")
if st.button(
    "SERPにない独自要素を3件提案",
    disabled=not (api_key and st.session_state.outline and st.session_state.serp_data),
):
    try:
        client = get_llm_client(api_key, llm_choice)
        with st.spinner("競合との差別化案を考えています..."):
            st.session_state.originality_proposals = step4_propose_originality(
                client,
                llm_choice,
                keyword,
                st.session_state.serp_data,
                st.session_state.outline,
                st.session_state.run_dir,
            )
    except Exception as exc:
        st.error(f"独自性提案エラー: {exc}")

if st.session_state.originality_proposals:
    choices = {
        f"{i + 1}. {item.get('title', '提案')}": item
        for i, item in enumerate(st.session_state.originality_proposals)
    }
    for label, item in choices.items():
        with st.expander(label, expanded=True):
            st.write(item.get("description", ""))
            st.caption(f"推奨挿入箇所: {item.get('placement', '')}")
    selected_label = st.radio("記事に採用する独自要素", list(choices.keys()))
    st.session_state.selected_originality = choices[selected_label]

st.header("4. 記事の生成")
if st.button(
    "記事を生成",
    disabled=not (
        api_key
        and st.session_state.outline
        and st.session_state.selected_originality
    ),
):
    try:
        client = get_llm_client(api_key, llm_choice)
        with st.spinner("記事を生成しています..."):
            st.session_state.article = step5_generate_sections_and_assemble(
                client,
                llm_choice,
                keyword,
                st.session_state.outline,
                st.session_state.selected_originality,
                st.session_state.run_dir,
            )
    except Exception as exc:
        st.error(f"記事生成エラー: {exc}")

if st.session_state.article:
    st.session_state.article = st.text_area(
        "生成記事",
        value=st.session_state.article,
        height=600,
    )
    st.download_button(
        "記事をMarkdownでダウンロード",
        st.session_state.article,
        file_name="article.md",
        mime="text/markdown",
    )

st.header("5. ファクトチェック")
st.caption("Web検索を利用して、記事内の検証可能な主張を一覧化し、出典付きで判定します。")
if st.button(
    "記事をファクトチェック",
    disabled=not (api_key and st.session_state.article),
):
    try:
        client = get_llm_client(api_key, llm_choice)
        with st.spinner("記事全体を調査・検証しています..."):
            st.session_state.fact_check = step6_fact_check(
                client,
                llm_choice,
                st.session_state.article,
                st.session_state.run_dir,
            )
    except Exception as exc:
        st.error(f"ファクトチェックエラー: {exc}")

if st.session_state.fact_check:
    st.markdown(st.session_state.fact_check)
    st.download_button(
        "ファクトチェック結果をダウンロード",
        st.session_state.fact_check,
        file_name="fact-check.md",
        mime="text/markdown",
    )
