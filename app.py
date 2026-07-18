from datetime import datetime
from pathlib import Path

import streamlit as st

from backend import (
    MODEL_OPTIONS,
    analyze_serp,
    get_llm_client,
    get_model_config,
    step2_fetch_serp_and_filter,
    step3_generate_outline,
    step4_propose_originality,
    step5_generate_sections_and_assemble,
    step6_fact_check,
)

st.set_page_config(page_title="SEO Article Generator", page_icon="📝", layout="wide")

st.markdown(
    """
<style>
:root { --blue:#1267d6; --blue-soft:#eef5ff; --line:#dce7f5; --text:#172033; }
html, body, [class*="css"] { font-size: 14px; }
.stApp { background:#ffffff; color:var(--text); }
.block-container { max-width:1280px; padding-top:1.4rem; padding-bottom:3rem; }
h1 { font-size:1.8rem !important; color:#0b3d7a; margin-bottom:.25rem !important; }
h2 { font-size:1.25rem !important; color:#124f9c; }
h3 { font-size:1.05rem !important; }
[data-testid="stSidebar"] { background:#f7faff; border-right:1px solid var(--line); }
[data-testid="stSidebar"] * { font-size:13px; }
.stButton > button, .stDownloadButton > button {
  border-radius:8px; border:1px solid var(--blue); font-weight:600;
}
.stButton > button[kind="primary"] { background:var(--blue); color:white; }
.step-card { border:1px solid var(--line); border-radius:12px; padding:16px 18px; background:white; margin:10px 0 16px; }
.step-active { border:2px solid var(--blue); box-shadow:0 4px 18px rgba(18,103,214,.08); }
.step-done { background:#f7fbff; }
.step-label { font-size:12px; color:#56708f; font-weight:700; letter-spacing:.02em; }
.step-title { font-size:18px; color:#123f73; font-weight:750; margin-top:2px; }
.summary-box { background:var(--blue-soft); border-left:4px solid var(--blue); border-radius:8px; padding:12px 14px; }
.small-note { font-size:12px; color:#61758e; }
div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
</style>
""",
    unsafe_allow_html=True,
)

STEPS = [
    "基本設定",
    "競合SERP",
    "構成案",
    "独自性",
    "記事生成",
    "ファクトチェック",
]

DEFAULTS = {
    "current_step": 1,
    "serp_data": None,
    "serp_analysis": None,
    "outline": "",
    "originality_proposals": None,
    "selected_originality": None,
    "article": "",
    "fact_check": "",
    "run_dir": None,
    "active_keyword": "",
}
for key, default in DEFAULTS.items():
    st.session_state.setdefault(key, default)


def secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def reset_outputs() -> None:
    for key in [
        "serp_data", "serp_analysis", "outline", "originality_proposals",
        "selected_originality", "article", "fact_check", "run_dir",
    ]:
        st.session_state[key] = DEFAULTS[key]


def goto(step: int) -> None:
    st.session_state.current_step = max(1, min(len(STEPS), step))


st.title("SEO Article Generator")
st.caption("SERP調査から構成、独自性、記事生成、ファクトチェックまでを段階別に進めます。")

progress = (st.session_state.current_step - 1) / (len(STEPS) - 1)
st.progress(progress)
step_cols = st.columns(len(STEPS))
for i, (col, label) in enumerate(zip(step_cols, STEPS), start=1):
    marker = "✓" if i < st.session_state.current_step else str(i)
    col.markdown(
        f"<div style='text-align:center;font-size:12px;font-weight:700;color:{'#1267d6' if i <= st.session_state.current_step else '#8ba0b8'}'>{marker}<br>{label}</div>",
        unsafe_allow_html=True,
    )

with st.sidebar:
    st.subheader("AI API設定")
    llm_choice = st.selectbox("使用するAI", list(MODEL_OPTIONS.keys()))
    model_config = get_model_config(llm_choice)
    st.caption(f"モデルID: `{model_config['model']}`")
    if model_config["provider"] == "gemini":
        api_key = st.text_input("Gemini API Key", value=secret("GEMINI_API_KEY"), type="password")
    else:
        api_key = st.text_input("OpenAI API Key", value=secret("OPENAI_API_KEY"), type="password")

    st.divider()
    st.subheader("SERP API設定")
    brave_api_key = st.text_input(
        "Brave Search API Key", value=secret("BRAVE_SEARCH_API_KEY"), type="password"
    )
    country_options = {
        "Tokyo, Japan": {"country": "JP", "search_lang": "jp", "ui_lang": "ja-JP"},
        "Seoul, South Korea": {"country": "KR", "search_lang": "ko", "ui_lang": "ko-KR"},
        "United States": {"country": "US", "search_lang": "en", "ui_lang": "en-US"},
    }
    selected_country_label = st.selectbox("検索対象国・地域", list(country_options.keys()))
    result_count = st.selectbox("取得件数", [5, 8, 10, 15, 20], index=1)
    country_config = country_options[selected_country_label]
    serp_credentials = {"api_key": brave_api_key, **country_config}
    st.caption(f"検索言語は自動連動: `{country_config['search_lang']}`")

    st.divider()
    if st.button("最初からやり直す", use_container_width=True):
        reset_outputs()
        goto(1)
        st.rerun()

keyword = st.text_input("対策キーワード", value=st.session_state.active_keyword)
if keyword != st.session_state.active_keyword:
    reset_outputs()
    st.session_state.active_keyword = keyword
    goto(1)

# STEP 1
if st.session_state.current_step == 1:
    st.markdown('<div class="step-card step-active"><div class="step-label">STEP 1</div><div class="step-title">基本設定</div></div>', unsafe_allow_html=True)
    st.write("サイドバーでAI、APIキー、検索対象国を設定してください。言語は国に合わせて自動設定されます。")
    c1, c2, c3 = st.columns(3)
    c1.metric("AI", llm_choice)
    c2.metric("検索地域", selected_country_label)
    c3.metric("SERP取得件数", result_count)
    ready = bool(keyword and api_key and brave_api_key)
    if not ready:
        st.info("対策キーワード、AI API Key、Brave Search API Keyを入力してください。")
    if st.button("SERP調査へ進む", type="primary", disabled=not ready):
        if st.session_state.run_dir is None:
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
            run_dir = Path(".seo") / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            st.session_state.run_dir = run_dir
        goto(2)
        st.rerun()

# STEP 2
elif st.session_state.current_step == 2:
    st.markdown('<div class="step-card step-active"><div class="step-label">STEP 2</div><div class="step-title">競合SERPの取得・分析</div></div>', unsafe_allow_html=True)
    if st.button("競合SERPを取得", type="primary", disabled=not keyword):
        try:
            with st.spinner("Brave Search APIからSERPを取得し、見出しを分析しています..."):
                st.session_state.serp_data = step2_fetch_serp_and_filter(
                    keyword,
                    st.session_state.run_dir.name,
                    st.session_state.run_dir,
                    provider="brave",
                    credentials=serp_credentials,
                    top_n=result_count,
                )
                st.session_state.serp_analysis = analyze_serp(st.session_state.serp_data)
            st.success(f"競合SERPを{len(st.session_state.serp_data.get('results', []))}件取得しました。")
        except Exception as exc:
            st.session_state.serp_data = None
            st.session_state.serp_analysis = None
            st.error(f"SERP取得エラー: {exc}")

    if st.session_state.serp_data:
        rows = []
        for result in st.session_state.serp_data.get("results", []):
            headings = result.get("headings", {})
            rows.append({
                "順位": result.get("rank"), "タイトル": result.get("title"),
                "URL": result.get("url"), "スニペット": result.get("snippet", ""),
                "H2数": len(headings.get("h2", [])), "H3数": len(headings.get("h3", [])),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
        with st.expander("SERP分析サマリー", expanded=True):
            st.markdown(st.session_state.serp_analysis or "")
        with st.expander("各ページのH2・H3を見る"):
            for result in st.session_state.serp_data.get("results", []):
                st.markdown(f"**{result.get('rank')}位：{result.get('title') or result.get('url')}**")
                st.write("H2:", result.get("headings", {}).get("h2", []))
                st.write("H3:", result.get("headings", {}).get("h3", []))
                st.divider()

    nav1, nav2 = st.columns([1, 1])
    if nav1.button("戻る", use_container_width=True):
        goto(1); st.rerun()
    if nav2.button("構成案へ進む", type="primary", use_container_width=True, disabled=not st.session_state.serp_data):
        goto(3); st.rerun()

# STEP 3
elif st.session_state.current_step == 3:
    st.markdown('<div class="step-card step-active"><div class="step-label">STEP 3</div><div class="step-title">構成案の確認・編集</div></div>', unsafe_allow_html=True)
    left, right = st.columns([0.42, 0.58], gap="large")
    with left:
        st.subheader("SERP分析")
        st.markdown('<div class="summary-box">構成生成時に、この分析結果とSERP生データの両方を参照します。</div>', unsafe_allow_html=True)
        st.markdown(st.session_state.serp_analysis or "分析結果がありません。")
    with right:
        st.subheader("構成案")
        if st.button("SERP分析を基に構成案を生成", type="primary", disabled=not api_key):
            try:
                client = get_llm_client(api_key, llm_choice)
                with st.spinner("SERP分析を参照して構成案を生成しています..."):
                    st.session_state.outline = step3_generate_outline(
                        client, llm_choice, keyword, st.session_state.serp_data,
                        st.session_state.run_dir, st.session_state.serp_analysis,
                    )
            except Exception as exc:
                st.error(f"構成案生成エラー: {exc}")
        if st.session_state.outline:
            st.session_state.outline = st.text_area(
                "構成案は直接編集できます", value=st.session_state.outline, height=520
            )

    nav1, nav2 = st.columns(2)
    if nav1.button("SERPへ戻る", use_container_width=True):
        goto(2); st.rerun()
    if nav2.button("独自性提案へ進む", type="primary", use_container_width=True, disabled=not st.session_state.outline):
        goto(4); st.rerun()

# STEP 4
elif st.session_state.current_step == 4:
    st.markdown('<div class="step-card step-active"><div class="step-label">STEP 4</div><div class="step-title">独自性（オリジナル要素）の選択</div></div>', unsafe_allow_html=True)
    if st.button("SERPにない独自要素を3件提案", type="primary", disabled=not api_key):
        try:
            client = get_llm_client(api_key, llm_choice)
            with st.spinner("競合との差別化案を生成しています..."):
                st.session_state.originality_proposals = step4_propose_originality(
                    client, llm_choice, keyword, st.session_state.serp_data,
                    st.session_state.outline, st.session_state.run_dir,
                    st.session_state.serp_analysis,
                )
        except Exception as exc:
            st.error(f"独自性提案エラー: {exc}")

    if st.session_state.originality_proposals:
        choices = {
            f"{i + 1}. {item.get('title', '提案')}": item
            for i, item in enumerate(st.session_state.originality_proposals)
        }
        cards = st.columns(3)
        for (label, item), col in zip(choices.items(), cards):
            with col:
                st.markdown('<div class="step-card step-done">', unsafe_allow_html=True)
                st.markdown(f"**{label}**")
                st.write(item.get("description", ""))
                st.caption(f"挿入箇所: {item.get('placement', '')}")
                st.markdown('</div>', unsafe_allow_html=True)
        selected_label = st.radio("記事に採用する独自要素", list(choices.keys()), horizontal=True)
        st.session_state.selected_originality = choices[selected_label]

    nav1, nav2 = st.columns(2)
    if nav1.button("構成案へ戻る", use_container_width=True):
        goto(3); st.rerun()
    if nav2.button("記事生成へ進む", type="primary", use_container_width=True, disabled=not st.session_state.selected_originality):
        goto(5); st.rerun()

# STEP 5
elif st.session_state.current_step == 5:
    st.markdown('<div class="step-card step-active"><div class="step-label">STEP 5</div><div class="step-title">記事の生成・編集</div></div>', unsafe_allow_html=True)
    if st.button("記事を生成", type="primary", disabled=not api_key):
        try:
            client = get_llm_client(api_key, llm_choice)
            with st.spinner("構成案、SERP分析、独自性を反映して記事を生成しています..."):
                st.session_state.article = step5_generate_sections_and_assemble(
                    client, llm_choice, keyword, st.session_state.outline,
                    st.session_state.selected_originality, st.session_state.run_dir,
                    st.session_state.serp_analysis,
                )
        except Exception as exc:
            st.error(f"記事生成エラー: {exc}")
    if st.session_state.article:
        st.session_state.article = st.text_area("生成記事", value=st.session_state.article, height=650)
        st.download_button("記事をMarkdownでダウンロード", st.session_state.article, file_name="article.md", mime="text/markdown")

    nav1, nav2 = st.columns(2)
    if nav1.button("独自性へ戻る", use_container_width=True):
        goto(4); st.rerun()
    if nav2.button("ファクトチェックへ進む", type="primary", use_container_width=True, disabled=not st.session_state.article):
        goto(6); st.rerun()

# STEP 6
else:
    st.markdown('<div class="step-card step-active"><div class="step-label">STEP 6</div><div class="step-title">ファクトチェック</div></div>', unsafe_allow_html=True)
    st.caption("references/factcheck-prompt.mdを読み込み、選択中のAIのWeb検索機能で記事を検証します。")
    if st.button("記事をファクトチェック", type="primary", disabled=not api_key):
        try:
            client = get_llm_client(api_key, llm_choice)
            with st.spinner("記事全体を調査・検証しています..."):
                st.session_state.fact_check = step6_fact_check(
                    client, llm_choice, st.session_state.article, st.session_state.run_dir
                )
        except Exception as exc:
            st.error(f"ファクトチェックエラー: {exc}")
    if st.session_state.fact_check:
        st.markdown(st.session_state.fact_check)
        st.download_button("ファクトチェック結果をダウンロード", st.session_state.fact_check, file_name="fact-check.md", mime="text/markdown")

    nav1, nav2 = st.columns(2)
    if nav1.button("記事へ戻る", use_container_width=True):
        goto(5); st.rerun()
    if nav2.button("最初から新しい記事を作る", use_container_width=True):
        reset_outputs(); st.session_state.active_keyword = ""; goto(1); st.rerun()
