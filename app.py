from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

import streamlit as st

from backend import (
    MODEL_OPTIONS,
    create_llm_service,
    get_model_config,
    initialize_run,
    save_article,
    save_outline,
    save_selected_originality,
    step2_fetch_serp_and_filter,
    step2_generate_analysis,
    step3_generate_outline,
    step4_propose_originality,
    step5_generate_sections_and_assemble,
    step6_fact_check,
)


st.set_page_config(page_title="SEO Article Generator", page_icon="📝", layout="wide")

st.markdown(
    """
<style>
:root {
  --blue:#1267d6;
  --blue-dark:#0b4fa8;
  --blue-soft:#eef5ff;
  --line:#dce7f5;
  --text:#172033;
  --muted:#61758e;
  --done:#e6e9ee;
  --done-text:#5f6875;
}
html, body, [class*="css"], .stApp, .stApp * {
  font-size:12px !important;
}
h1, h2, h3, h4, h5, h6,
[data-testid="stMetricValue"], [data-testid="stMetricLabel"] {
  font-size:12px !important;
}
h1, h2, h3, h4, h5, h6 { font-weight:700 !important; }
.stApp { background:#ffffff; color:var(--text); }
.block-container { max-width:1380px; padding-top:1.2rem; padding-bottom:4rem; }
[data-testid="stSidebar"] { background:#f7faff; border-right:1px solid var(--line); }
[data-testid="stExpander"] {
  border:1px solid var(--line);
  border-radius:12px;
  background:#ffffff;
  margin-bottom:12px;
  overflow:hidden;
}
[data-testid="stExpander"] summary {
  background:#f8fbff;
  color:#123f73;
  font-weight:700;
  padding:12px 14px;
}
[data-testid="stExpander"] summary:hover { color:var(--blue); }
.stButton > button, .stDownloadButton > button {
  border-radius:8px;
  min-height:34px;
  font-weight:700;
}
.stButton > button[kind="primary"] {
  background:var(--blue);
  border:1px solid var(--blue);
  color:white;
}
.stButton > button[kind="primary"]:hover { background:var(--blue-dark); }
.stButton > button[kind="secondary"] {
  background:var(--done);
  border:1px solid #cdd3dc;
  color:var(--done-text);
}
.stButton > button[kind="secondary"]:hover {
  background:#dce0e6;
  border-color:#b9c0ca;
  color:#404854;
}
.section-note {
  background:var(--blue-soft);
  border-left:4px solid var(--blue);
  border-radius:8px;
  padding:10px 12px;
  margin-bottom:10px;
}
.status-row {
  display:flex;
  gap:8px;
  align-items:center;
  flex-wrap:wrap;
  margin:4px 0 14px;
}
.status-pill {
  border:1px solid var(--line);
  border-radius:999px;
  padding:5px 10px;
  color:var(--muted);
  background:#fff;
  font-weight:700;
}
.status-pill.done { color:#56606d; background:#e6e9ee; border-color:#cfd5dd; }
.status-pill.current { color:#fff; background:var(--blue); border-color:var(--blue); }
div[data-testid="stDataFrame"] {
  border:1px solid var(--line);
  border-radius:10px;
  overflow:hidden;
}
.serp-purpose { color:var(--muted); margin:-4px 0 10px; }
.small-muted { color:var(--muted); }
</style>
""",
    unsafe_allow_html=True,
)

# This workflow intentionally uses st.session_state only. Do not add Streamlit caches.
STAGES = ["Setup", "SERP Research", "Outline", "Originality", "Article Generation", "Fact Check"]
DEFAULTS: Dict[str, Any] = {
    "serp_data": None,
    "serp_analysis": "",
    "outline": "",
    "originality_proposals": None,
    "selected_originality": None,
    "originality_choice": None,
    "article": "",
    "fact_check": "",
    "run_dir": None,
    "active_keyword": "",
    "active_llm_choice": "",
    "active_search_signature": "",
    "active_content_signature": "",
    "target_keyword_input": "",
    "owned_site_url_input": "",
    "cta_url_input": "",
}
for state_key, default_value in DEFAULTS.items():
    st.session_state.setdefault(state_key, default_value)


RESET_ORDER = ["serp", "analysis", "outline", "originality", "article", "fact"]
RESET_FIELDS: Dict[str, List[str]] = {
    "serp": ["serp_data"],
    "analysis": ["serp_analysis"],
    "outline": ["outline"],
    "originality": ["originality_proposals", "selected_originality", "originality_choice"],
    "article": ["article"],
    "fact": ["fact_check"],
}
RESET_WIDGETS: Dict[str, List[str]] = {
    "serp": [
        "outline_editor",
        "article_editor",
        "originality_choice_widget",
        "originality_additional_info_widget",
    ],
    "analysis": [
        "outline_editor",
        "article_editor",
        "originality_choice_widget",
        "originality_additional_info_widget",
    ],
    "outline": [
        "outline_editor",
        "article_editor",
        "originality_choice_widget",
        "originality_additional_info_widget",
    ],
    "originality": [
        "article_editor",
        "originality_choice_widget",
        "originality_additional_info_widget",
    ],
    "article": ["article_editor"],
    "fact": [],
}


def secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def reset_from(stage: str) -> None:
    start = RESET_ORDER.index(stage)
    for item in RESET_ORDER[start:]:
        for field in RESET_FIELDS[item]:
            st.session_state[field] = DEFAULTS[field]
    for widget_key in RESET_WIDGETS[stage]:
        st.session_state.pop(widget_key, None)


def reset_search_context() -> None:
    reset_from("serp")
    st.session_state.run_dir = None


def reset_all() -> None:
    for state_key, default_value in DEFAULTS.items():
        st.session_state[state_key] = default_value
    for widget_key in (
        "outline_editor",
        "article_editor",
        "originality_choice_widget",
        "originality_additional_info_widget",
    ):
        st.session_state.pop(widget_key, None)


def slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠ー]+", "-", value).strip("-")
    return slug[:32] or "keyword"


def ensure_run_dir(
    keyword: str,
    llm_choice: str,
    search_settings: Dict[str, Any],
    content_settings: Dict[str, Any],
) -> Path:
    if st.session_state.run_dir is None:
        run_id = "{0}-{1}".format(datetime.now().strftime("%Y%m%d-%H%M%S"), slugify(keyword))
        run_dir = Path(".seo") / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        st.session_state.run_dir = run_dir
    run_dir = Path(st.session_state.run_dir)
    initialize_run(
        run_dir,
        keyword=keyword,
        llm_choice=llm_choice,
        search_settings=search_settings,
        content_settings=content_settings,
    )
    return run_dir


def normalize_http_url(value: str) -> str:
    """Normalize a user-entered public HTTP(S) URL without fetching it."""
    raw = value.strip()
    if not raw or any(char.isspace() for char in raw):
        return ""
    candidate = raw if re.match(r"^https?://", raw, flags=re.IGNORECASE) else "https://" + raw
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return candidate


def action_button(label: str, key: str, completed: bool, **kwargs: Any) -> bool:
    """Completed actions stay usable but are displayed in gray."""
    return st.button(
        label,
        type="secondary" if completed else "primary",
        key=key,
        **kwargs,
    )


def rows_for(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "Rank": item.get("rank", ""),
            "Title": item.get("title", ""),
            "Snippet": item.get("snippet", ""),
            "Source": item.get("source", ""),
            "Date/Age": item.get("age", ""),
            "URL": item.get("url", ""),
        }
        for item in items
    ]


def originality_confirmation_is_current() -> bool:
    """Return True only when the visible choice and notes match the confirmed payload."""
    selected = st.session_state.get("selected_originality")
    proposals = st.session_state.get("originality_proposals") or []
    if not isinstance(selected, dict) or not proposals:
        return False

    raw_choice = st.session_state.get(
        "originality_choice_widget",
        st.session_state.get("originality_choice"),
    )
    try:
        selected_index = int(raw_choice)
    except (TypeError, ValueError):
        return False
    if selected_index < 0 or selected_index >= len(proposals):
        return False

    visible_proposal = proposals[selected_index]
    for field in ("title", "description", "placement"):
        if str(selected.get(field, "")).strip() != str(visible_proposal.get(field, "")).strip():
            return False

    visible_additional = str(
        st.session_state.get(
            "originality_additional_info_widget",
            selected.get("additional_information", ""),
        )
    ).strip()
    confirmed_additional = str(selected.get("additional_information", "")).strip()
    return visible_additional == confirmed_additional


st.title("SEO Article Generator")
st.caption("One-page workflow. Completed sections close automatically and can be reopened.")

with st.sidebar:
    st.subheader("AI Settings")
    llm_choice = st.selectbox("AI Model", list(MODEL_OPTIONS.keys()))
    model_config = get_model_config(llm_choice)
    st.caption("Model ID: `{0}` · This provider is used for every AI stage".format(model_config["model"]))
    if model_config["provider"] == "gemini":
        api_key = st.text_input(
            "Gemini API Key",
            value=secret("GEMINI_API_KEY"),
            type="password",
        )
    else:
        api_key = st.text_input(
            "OpenAI API Key",
            value=secret("OPENAI_API_KEY"),
            type="password",
        )

    st.divider()
    st.subheader("SERP API Settings")
    brave_api_key = st.text_input(
        "Brave Search API Key",
        value=secret("BRAVE_SEARCH_API_KEY"),
        type="password",
    )
    country_options: Dict[str, Dict[str, Any]] = {
        "Tokyo, Japan": {
            "country": "JP",
            "search_lang": "jp",
            "ui_lang": "ja-JP",
            "location": "Tokyo, Japan",
            "location_headers": {
                "X-Loc-City": "Tokyo",
                "X-Loc-Country": "JP",
                "X-Loc-Timezone": "Asia/Tokyo",
                "X-Loc-Lat": "35.6762",
                "X-Loc-Long": "139.6503",
            },
        },
        "Seoul, South Korea": {
            "country": "KR",
            "search_lang": "ko",
            "ui_lang": "ko-KR",
            "location": "Seoul, South Korea",
            "location_headers": {
                "X-Loc-City": "Seoul",
                "X-Loc-Country": "KR",
                "X-Loc-Timezone": "Asia/Seoul",
                "X-Loc-Lat": "37.5665",
                "X-Loc-Long": "126.9780",
            },
        },
        "United States": {
            "country": "US",
            "search_lang": "en",
            "ui_lang": "en-US",
            "location": "United States",
            "location_headers": {"X-Loc-Country": "US"},
        },
    }
    selected_country_label = st.selectbox("Country", list(country_options.keys()))
    country_config = country_options[selected_country_label]
    result_count = 8
    serp_credentials = {"api_key": brave_api_key, **country_config}
    st.caption("Language is linked automatically: `{0}`".format(country_config["search_lang"]))

    st.divider()
    if st.button("Reset All", width="stretch"):
        reset_all()
        st.rerun()

# Model changes reset all AI-derived outputs, including Analysis, so providers never mix.
if st.session_state.active_llm_choice and st.session_state.active_llm_choice != llm_choice:
    reset_search_context()
st.session_state.active_llm_choice = llm_choice

status_placeholder = st.empty()
_previous_owned = normalize_http_url(st.session_state.get("owned_site_url_input", ""))
_previous_cta_raw = st.session_state.get("cta_url_input", "").strip()
_previous_cta = normalize_http_url(_previous_cta_raw) if _previous_cta_raw else ""
_previous_setup_complete = bool(
    st.session_state.get("target_keyword_input", "").strip()
    and _previous_owned
    and (not _previous_cta_raw or _previous_cta)
    and api_key
    and brave_api_key
)

with st.expander("1. Setup — 基本設定", expanded=not _previous_setup_complete):
    st.markdown(
        '<div class="section-note">AI、APIキー、検索対象国、対策キーワード、Owned Site URLを設定します。CTA URLは任意です。</div>',
        unsafe_allow_html=True,
    )
    keyword = st.text_input(
        "Target Keyword",
        placeholder="例：Galaxy AI 使い方",
        key="target_keyword_input",
    )
    url_col1, url_col2 = st.columns(2)
    with url_col1:
        owned_site_input = st.text_input(
            "Owned Site URL",
            placeholder="https://example.com/",
            key="owned_site_url_input",
            help="記事で自然に案内・訴求する自社サイトURLです。入力必須です。",
        )
    with url_col2:
        cta_input = st.text_input(
            "CTA URL (optional)",
            placeholder="https://example.com/contact/",
            key="cta_url_input",
            help="問い合わせ、資料請求、サービスページなどの遷移先です。空欄でも進めます。",
        )

    owned_site_url = normalize_http_url(owned_site_input)
    cta_raw = cta_input.strip()
    cta_url = normalize_http_url(cta_raw) if cta_raw else ""
    owned_invalid = bool(owned_site_input.strip() and not owned_site_url)
    cta_invalid = bool(cta_raw and not cta_url)

    col1, col2, col3 = st.columns(3)
    col1.metric("AI Model", llm_choice)
    col2.metric("Country", selected_country_label)
    col3.metric("Web Results", "8 fixed")

    missing: List[str] = []
    if not keyword.strip():
        missing.append("Target Keyword")
    if not owned_site_input.strip():
        missing.append("Owned Site URL")
    if not api_key:
        missing.append("AI API Key")
    if not brave_api_key:
        missing.append("Brave Search API Key")

    if owned_invalid:
        st.error("Owned Site URLはhttp://またはhttps://で解釈できるURLを入力してください。")
    if cta_invalid:
        st.error("CTA URLは空欄にするか、http://またはhttps://で解釈できるURLを入力してください。")
    if owned_site_url and owned_site_url != owned_site_input.strip():
        st.caption("Owned Site URL normalized: `{0}`".format(owned_site_url))
    if cta_url and cta_url != cta_raw:
        st.caption("CTA URL normalized: `{0}`".format(cta_url))

    setup_complete = bool(not missing and not owned_invalid and not cta_invalid)
    if missing:
        st.info("未入力: " + " / ".join(missing))
    elif setup_complete:
        if cta_url:
            st.success("Setup is complete. Owned SiteとCTAを記事設計に反映します。")
        else:
            st.success("Setup is complete. CTA URLなしで進めます。")

search_signature = "{0}|{1}|{2}".format(keyword.strip(), selected_country_label, result_count)
if st.session_state.active_search_signature and st.session_state.active_search_signature != search_signature:
    reset_search_context()
st.session_state.active_search_signature = search_signature
st.session_state.active_keyword = keyword

content_signature = "{0}|{1}".format(owned_site_url, cta_url)
if st.session_state.active_content_signature and st.session_state.active_content_signature != content_signature:
    reset_from("outline")
st.session_state.active_content_signature = content_signature

current_search_settings: Dict[str, Any] = {
    "country_label": selected_country_label,
    "country": country_config["country"],
    "search_lang": country_config["search_lang"],
    "ui_lang": country_config["ui_lang"],
    "location": country_config["location"],
    "top_n": result_count,
}
content_settings: Dict[str, Any] = {
    "owned_site_url": owned_site_url,
    "cta_url": cta_url,
}

serp_complete = bool(st.session_state.serp_data and st.session_state.serp_analysis)
states = [
    setup_complete,
    serp_complete,
    bool(st.session_state.outline),
    originality_confirmation_is_current(),
    bool(st.session_state.article),
    bool(st.session_state.fact_check),
]
current_idx = next((i for i, done in enumerate(states) if not done), len(states) - 1)
status_html = '<div class="status-row">'
for idx, (label, done) in enumerate(zip(STAGES, states)):
    css_class = "done" if done else ("current" if idx == current_idx else "")
    marker = "✓" if done else str(idx + 1)
    status_html += '<span class="status-pill {0}">{1} {2}</span>'.format(css_class, marker, label)
status_html += "</div>"
status_placeholder.markdown(status_html, unsafe_allow_html=True)

with st.expander(
    "2. SERP Research — 検索結果の取得・AI Analysis",
    expanded=states[0] and not states[1],
):
    if not states[0]:
        st.info("Complete Setup first.")
    else:
        stage_done = bool(st.session_state.serp_data and st.session_state.serp_analysis)
        if action_button("Run SERP Research & Analysis", "run_serp", stage_done):
            reset_search_context()
            run_dir = ensure_run_dir(
                keyword,
                llm_choice,
                current_search_settings,
                content_settings,
            )
            try:
                with st.spinner("Brave SERPを取得し、H2/H3を集計してAI Analysisを作成しています..."):
                    st.session_state.serp_data = step2_fetch_serp_and_filter(
                        keyword,
                        run_dir.name,
                        run_dir,
                        provider="brave",
                        credentials=serp_credentials,
                        top_n=result_count,
                    )
                    llm = create_llm_service(api_key, llm_choice)
                    st.session_state.serp_analysis = step2_generate_analysis(
                        llm,
                        keyword,
                        st.session_state.serp_data,
                        run_dir,
                    )
                st.rerun()
            except Exception as exc:
                st.error("SERP Research error: {0}".format(exc))

        if st.session_state.serp_data and not st.session_state.serp_analysis:
            if action_button("Run AI Analysis", "run_analysis_only", False):
                try:
                    llm = create_llm_service(api_key, llm_choice)
                    with st.spinner("取得済みSERPからAI Analysisを作成しています..."):
                        st.session_state.serp_analysis = step2_generate_analysis(
                            llm,
                            keyword,
                            st.session_state.serp_data,
                            ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                        )
                    st.rerun()
                except Exception as exc:
                    st.error("Analysis error: {0}".format(exc))

        if st.session_state.serp_data:
            data = st.session_state.serp_data
            diagnostics = data.get("diagnostics", {})
            metric_cols = st.columns(5)
            metric_cols[0].metric("Web", diagnostics.get("web_count", 0))
            metric_cols[1].metric("Discussions", diagnostics.get("discussions_count", 0))
            metric_cols[2].metric("News", diagnostics.get("news_count", 0))
            metric_cols[3].metric("Videos", diagnostics.get("videos_count", 0))
            metric_cols[4].metric("Suggestion", diagnostics.get("suggestion_count", 0))

            tabs = st.tabs(["Web", "Discussions", "News", "Videos", "Suggestion", "Analysis"])
            with tabs[0]:
                st.markdown(
                    "<div class='serp-purpose'>競合分析・記事構成に利用</div>",
                    unsafe_allow_html=True,
                )
                web_rows: List[Dict[str, Any]] = []
                for result in data.get("web", []):
                    headings = result.get("headings", {})
                    if result.get("eligible_for_analysis"):
                        status = "H2/H3 ready"
                    elif result.get("blocked_count"):
                        status = "Blocked"
                    elif result.get("fetch_error"):
                        status = "Page fetch error"
                    else:
                        status = "No headings"
                    web_rows.append(
                        {
                            "Rank": result.get("rank"),
                            "Title": result.get("title"),
                            "Snippet": result.get("snippet", ""),
                            "H2": len(headings.get("h2", [])),
                            "H3": len(headings.get("h3", [])),
                            "Status": status,
                            "URL": result.get("url"),
                        }
                    )
                st.dataframe(web_rows, width="stretch", hide_index=True)
                with st.expander("View Page Headings", expanded=False):
                    for result in data.get("web", []):
                        st.markdown(
                            "**{0}. {1}**".format(
                                result.get("rank"), result.get("title") or result.get("url")
                            )
                        )
                        st.write("H2:", result.get("headings", {}).get("h2", []))
                        st.write("H3:", result.get("headings", {}).get("h3", []))
                        if result.get("notes"):
                            st.caption(" / ".join(result["notes"]))
                        st.divider()

            errors = data.get("errors") or {}
            warnings = data.get("warnings") or {}
            with tabs[1]:
                st.markdown(
                    "<div class='serp-purpose'>ユーザーの本音・Pain Pointの抽出に利用</div>",
                    unsafe_allow_html=True,
                )
                discussion_rows = rows_for(data.get("discussions", []))
                if discussion_rows:
                    st.dataframe(discussion_rows, width="stretch", hide_index=True)
                else:
                    st.info("Discussion result was not returned for this query.")
                for error_key, message in errors.items():
                    if error_key.startswith("discussions:"):
                        st.error(message)

            with tabs[2]:
                st.markdown(
                    "<div class='serp-purpose'>鮮度・更新性・最新情報・変更点の確認に利用</div>",
                    unsafe_allow_html=True,
                )
                news_rows = rows_for(data.get("news", []))
                if news_rows:
                    st.dataframe(news_rows, width="stretch", hide_index=True)
                else:
                    st.info("News result was not returned for this query.")
                if errors.get("news"):
                    st.error(errors["news"])

            with tabs[3]:
                st.markdown(
                    "<div class='serp-purpose'>手順・比較・レビュー・実演テーマの把握に利用</div>",
                    unsafe_allow_html=True,
                )
                video_rows = rows_for(data.get("videos", []))
                if video_rows:
                    st.dataframe(video_rows, width="stretch", hide_index=True)
                else:
                    st.info("Video result was not returned for this query.")
                if errors.get("videos"):
                    st.error(errors["videos"])

            with tabs[4]:
                st.markdown(
                    "<div class='serp-purpose'>検索候補をFAQ候補へ変換するために利用</div>",
                    unsafe_allow_html=True,
                )
                suggestions = data.get("suggestion") or []
                if suggestions:
                    suggestion_rows = [
                        {
                            "Rank": item.get("rank", ""),
                            "Query": item.get("query", ""),
                            "Title": item.get("title", ""),
                            "Description": item.get("description", ""),
                            "Image": item.get("image", ""),
                        }
                        for item in suggestions
                    ]
                    st.dataframe(suggestion_rows, width="stretch", hide_index=True)
                else:
                    st.info("Suggestion result was not returned for this query.")
                if warnings.get("suggestion"):
                    st.warning(warnings["suggestion"])
                if errors.get("suggestion"):
                    st.error(errors["suggestion"])

            with tabs[5]:
                if st.session_state.serp_analysis:
                    st.markdown(st.session_state.serp_analysis)
                else:
                    st.info("AI Analysis has not completed yet.")

with st.expander("3. Outline — 構成案の生成・編集", expanded=states[1] and not states[2]):
    if not states[1]:
        st.info("Complete SERP Research first.")
    else:
        st.subheader("SERP Analysis")
        st.markdown(st.session_state.serp_analysis or "分析結果がありません。")
        st.divider()
        st.subheader("Outline Editor")

        if action_button("Generate Outline", "generate_outline", states[2]):
            reset_from("outline")
            try:
                llm = create_llm_service(api_key, llm_choice)
                with st.spinner("AnalysisとSERP根拠を参照して構成案を生成しています..."):
                    generated_outline = step3_generate_outline(
                        llm,
                        keyword,
                        st.session_state.serp_data,
                        ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                        st.session_state.serp_analysis,
                        owned_site_url=owned_site_url,
                        cta_url=cta_url,
                    )
                    st.session_state.outline = generated_outline
                    st.session_state.outline_editor = generated_outline
                st.rerun()
            except Exception as exc:
                st.error("Outline generation error: {0}".format(exc))

        if st.session_state.outline:
            st.session_state.setdefault("outline_editor", st.session_state.outline)
            st.text_area("Outline", height=560, key="outline_editor")
            outline_changed = st.session_state.outline_editor != st.session_state.outline
            if action_button(
                "Save Outline",
                "save_outline",
                completed=not outline_changed,
                disabled=not outline_changed,
            ):
                st.session_state.outline = st.session_state.outline_editor
                save_outline(
                    ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                    st.session_state.outline,
                )
                reset_from("originality")
                st.rerun()
            st.success("Outline is ready. Continue to Originality below.")

with st.expander(
    "4. Originality — 独自要素の提案・選択・追加情報",
    expanded=states[2] and not states[3],
):
    if not states[2]:
        st.info("Complete Outline first.")
    else:
        if action_button(
            "Generate 3 Originality Ideas",
            "generate_originality",
            bool(st.session_state.originality_proposals),
        ):
            reset_from("originality")
            try:
                llm = create_llm_service(api_key, llm_choice)
                with st.spinner("競合で不足している独自要素を3件生成しています..."):
                    st.session_state.originality_proposals = step4_propose_originality(
                        llm,
                        keyword,
                        st.session_state.serp_data,
                        st.session_state.outline,
                        ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                        st.session_state.serp_analysis,
                        owned_site_url=owned_site_url,
                    )
                st.rerun()
            except Exception as exc:
                st.error("Originality generation error: {0}".format(exc))

        proposals = st.session_state.originality_proposals or []
        if proposals:
            labels = [
                "{0}. {1}".format(index + 1, item.get("title", "Idea"))
                for index, item in enumerate(proposals)
            ]
            cards = st.columns(len(proposals))
            for index, (item, column) in enumerate(zip(proposals, cards)):
                with column:
                    st.markdown("**{0}**".format(labels[index]))
                    st.write(item.get("description", ""))
                    st.caption("Placement: {0}".format(item.get("placement", "")))

            default_index = st.session_state.originality_choice or 0
            selected_index = st.radio(
                "Select one idea",
                options=list(range(len(labels))),
                index=min(default_index, len(labels) - 1),
                format_func=lambda i: labels[i],
                horizontal=True,
                key="originality_choice_widget",
            )

            if "originality_additional_info_widget" not in st.session_state:
                st.session_state.originality_additional_info_widget = str(
                    (st.session_state.selected_originality or {}).get(
                        "additional_information", ""
                    )
                )
            additional_information = st.text_area(
                "Additional Information (URL or opinion) — Optional",
                key="originality_additional_info_widget",
                height=140,
                placeholder=(
                    "例：参考URL、現場で感じている課題、記事に入れたい意見、"
                    "独自データの要点など"
                ),
                help=(
                    "選択した独自性と一緒にArticle Generationへ渡します。"
                    "URLだけではリンク先の内容を推測しないため、反映したい要点も添えてください。"
                ),
            )
            st.caption(
                "追加情報は任意です。意見は意見として扱い、URLだけから事実や実績を作りません。"
            )

            confirmation_current = originality_confirmation_is_current()
            if action_button(
                "Confirm Originality",
                "confirm_originality",
                confirmation_current,
            ):
                reset_from("article")
                st.session_state.originality_choice = selected_index
                selected_payload = dict(proposals[selected_index])
                selected_payload["additional_information"] = additional_information.strip()
                st.session_state.selected_originality = selected_payload
                save_selected_originality(
                    ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                    st.session_state.selected_originality,
                )
                st.rerun()
            if confirmation_current and st.session_state.selected_originality:
                st.success(
                    "Selected: {0}".format(
                        st.session_state.selected_originality.get("title", "Idea")
                    )
                )
                confirmed_notes = str(
                    st.session_state.selected_originality.get(
                        "additional_information", ""
                    )
                ).strip()
                if confirmed_notes:
                    st.caption("Additional Information has also been confirmed.")

with st.expander(
    "5. Article Generation — 記事生成・編集",
    expanded=states[3] and not states[4],
):
    if not states[3]:
        st.info("Confirm an Originality idea first.")
    else:
        if action_button("Generate Article", "generate_article", states[4]):
            reset_from("article")
            try:
                llm = create_llm_service(api_key, llm_choice)
                with st.spinner("Outline、Analysis、選択した独自性と追加情報を反映して記事を生成しています..."):
                    generated_article = step5_generate_sections_and_assemble(
                        llm,
                        keyword,
                        st.session_state.outline,
                        st.session_state.selected_originality,
                        ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                        st.session_state.serp_analysis,
                        owned_site_url=owned_site_url,
                        cta_url=cta_url,
                    )
                    st.session_state.article = generated_article
                    st.session_state.article_editor = generated_article
                st.rerun()
            except Exception as exc:
                st.error("Article generation error: {0}".format(exc))

        if st.session_state.article:
            st.caption("The article Markdown includes `title` and `description` in YAML front matter.")
            st.session_state.setdefault("article_editor", st.session_state.article)
            st.text_area("Article", height=680, key="article_editor")
            article_changed = st.session_state.article_editor != st.session_state.article
            if action_button(
                "Save Article",
                "save_article",
                completed=not article_changed,
                disabled=not article_changed,
            ):
                st.session_state.article = st.session_state.article_editor
                save_article(
                    ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                    st.session_state.article,
                )
                reset_from("fact")
                st.rerun()
            st.download_button(
                "Download Markdown",
                st.session_state.article,
                file_name="article.md",
                mime="text/markdown",
            )

with st.expander(
    "6. Fact Check — ファクトチェック",
    expanded=states[4] and not states[5],
):
    if not states[4]:
        st.info("Complete Article Generation first.")
    else:
        st.caption(
            "references/factcheck-prompt.mdを読み込み、選択中のAIのWeb検索機能で記事を検証します。"
        )
        if action_button("Run Fact Check", "run_fact_check", states[5]):
            reset_from("fact")
            try:
                llm = create_llm_service(api_key, llm_choice)
                with st.spinner("記事全体を調査・検証しています..."):
                    st.session_state.fact_check = step6_fact_check(
                        llm,
                        st.session_state.article,
                        ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                    )
                st.rerun()
            except Exception as exc:
                st.error("Fact Check error: {0}".format(exc))

        if st.session_state.fact_check:
            st.markdown(st.session_state.fact_check)
            st.download_button(
                "Download Fact Check",
                st.session_state.fact_check,
                file_name="fact-check.md",
                mime="text/markdown",
            )
            st.success("All stages are complete.")
