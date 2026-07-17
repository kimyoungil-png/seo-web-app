import streamlit as st
import datetime
from pathlib import Path
import backend

import os
# --- クラウド環境用のPlaywright自動インストール ---
os.system("playwright install chromium")

st.set_page_config(page_title="SEO Growth Navigator", layout="wide")
st.title("🚀 SEO Growth Navigator (Web Edition)")

# --- 状態管理（Session State）の初期化 ---
if "step" not in st.session_state:
    st.session_state.step = 1
if "run_dir" not in st.session_state:
    st.session_state.run_dir = None
if "outline" not in st.session_state:
    st.session_state.outline = ""
if "serp_data" not in st.session_state:
    st.session_state.serp_data = None

# --- サイドバー：設定 ---
st.sidebar.header("⚙️ 設定")
# 💡 ここでAIを選択できるように変更しました！
llm_choice = st.sidebar.selectbox("🤖 使用するAIを選択", ["Gemini (1.5 Flash)", "OpenAI (GPT-4o)"])
api_key = st.sidebar.text_input("🔑 API Key", type="password", help="選択したAIのAPIキーを入力してください")

# ==========================================
# Step 1: キーワード入力
# ==========================================
st.header("1. 対策キーワードの入力")
keyword = st.text_input("キーワードを入力してください", placeholder="例：リモートワーク 課題")

if st.button("競合分析と構成案の生成を開始"):
    if not api_key or not keyword:
        st.error("APIキーとキーワードを入力してください。")
    else:
        st.session_state.step = 1
        
        with st.status("🔍 裏側で処理を実行中...", expanded=True) as status:
            # 選択したAIのクライアントを初期化
            client = backend.get_llm_client(api_key, llm_choice)
            
            run_id = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M')}-{keyword.replace(' ', '-')}"
            run_dir = Path(f".seo/runs/{run_id}")
            run_dir.mkdir(parents=True, exist_ok=True)
            st.session_state.run_dir = run_dir
            
            st.write("① 競合SERPを取得・サニタイズしています...")
            serp_data = backend.step2_fetch_serp_and_filter(keyword, run_id, run_dir)
            st.session_state.serp_data = serp_data
            
            st.write(f"② {llm_choice} が構成案を作成しています...")
            outline = backend.step3_generate_outline(client, llm_choice, keyword, serp_data, run_dir)
            st.session_state.outline = outline
            
            status.update(label="構成案の生成が完了しました！", state="complete", expanded=False)
            
        st.session_state.step = 2
        st.rerun()

# ==========================================
# Step 2: 構成案の編集・確定
# ==========================================
if st.session_state.step >= 2:
    st.divider()
    st.header("2. 構成案の確認・編集")
    st.info("💡 AIが競合を分析して作成した構成案です。必要に応じて手動で見出しや要点を加筆・修正してください。\n※ システムが識別するため、各H2の `[id: h2-01]` などのタグは消さないでください。")
    
    edited_outline = st.text_area("構成案 (Markdown)", value=st.session_state.outline, height=400)
    
    if st.button("✨ この構成で本文を執筆する"):
        st.session_state.outline = edited_outline
        (st.session_state.run_dir / "04-outline.md").write_text(edited_outline, encoding="utf-8")
        st.session_state.step = 3
        st.rerun()

# ==========================================
# Step 3: 本文執筆と最終出力
# ==========================================
if st.session_state.step == 3:
    st.divider()
    st.header("3. 記事の生成結果")
    
    with st.spinner(f"✍️ {llm_choice} が各H2セクションを執筆・統合しています...（数分かかる場合があります）"):
        client = backend.get_llm_client(api_key, llm_choice)
        final_article = backend.step4_generate_sections_and_assemble(
            client, llm_choice, keyword, st.session_state.outline, st.session_state.run_dir
        )
        
    st.success("🎉 記事の生成が完了しました！")
    
    st.download_button(
        label="📥 Markdownファイルとしてダウンロード",
        data=final_article,
        file_name=f"article_{keyword}.md",
        mime="text/markdown"
    )
    
    with st.expander("プレビューを表示", expanded=True):
        st.markdown(final_article)