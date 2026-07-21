from __future__ import annotations

import hashlib
import html
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


st.set_page_config(page_title="SEO Article Generator", page_icon="↗", layout="wide")

st.markdown(
    """
<style>
:root {
  --blue:#2458ff;
  --blue-dark:#173bc7;
  --blue-pale:#edf1ff;
  --ink:#0b0b0c;
  --ink-soft:#3d4047;
  --muted:#737987;
  --line:#d9dce3;
  --paper:#fbfbf8;
  --panel:#ffffff;
  --done:#e8e9ec;
  --done-text:#666b74;
}

html, body, [class*="css"], .stApp, .stApp * {
  font-family: Inter, "Helvetica Neue", Arial, "Noto Sans JP", sans-serif;
  font-size:12px !important;
}

.stApp {
  background:var(--paper);
  color:var(--ink);
}

.block-container {
  max-width:1240px;
  padding-top:1.15rem;
  padding-bottom:7rem;
}

header[data-testid="stHeader"] {
  background:rgba(251,251,248,.88);
  backdrop-filter:blur(12px);
  border-bottom:1px solid rgba(217,220,227,.72);
}

/* Hide the sidebar completely. Settings now live in Step 01. */
section[data-testid="stSidebar"] { display:none !important; }

.hero-shell {
  border-bottom:1px solid var(--line);
  padding:6px 0 34px;
  margin-bottom:6px;
}
.hero-brand {
  display:flex;
  align-items:center;
  gap:12px;
  font-weight:900;
  letter-spacing:-.04em;
  font-size:26px;
  line-height:1;
}
.hero-brand span { color:var(--blue); font-size:26px; }
.hero-eyebrow {
  color:var(--muted);
  letter-spacing:.18em;
  font-size:10px;
  margin-top:11px;
  text-transform:uppercase;
}
.hero-title {
  max-width:850px;
  margin:42px 0 12px;
  font-size:38px;
  line-height:1.12;
  letter-spacing:-.045em;
  font-weight:850;
}
.hero-title em {
  color:var(--blue);
  font-style:normal;
  font-size:inherit;
}
.hero-copy {
  max-width:710px;
  color:var(--ink-soft);
  font-size:13px;
  line-height:1.8;
}

.current-stage {
  display:grid;
  grid-template-columns:88px 1fr;
  gap:22px;
  align-items:start;
  border-left:4px solid var(--blue);
  padding:14px 0 14px 20px;
  margin:28px 0 22px;
}
.current-stage-number {
  color:var(--blue);
  font-weight:800;
  letter-spacing:.18em;
  font-size:11px;
  padding-top:4px;
}
.current-stage-title {
  font-size:25px;
  font-weight:820;
  letter-spacing:-.035em;
  line-height:1.2;
}
.current-stage-copy {
  color:var(--muted);
  margin-top:7px;
  line-height:1.7;
}

/* Flat editorial accordions, inspired by the supplied Ascent GEO page. */
[data-testid="stExpander"] {
  border:0;
  border-top:1px solid var(--line);
  border-bottom:1px solid var(--line);
  border-radius:0;
  background:transparent;
  margin:0;
  overflow:visible;
}
[data-testid="stExpander"] + [data-testid="stExpander"] {
  border-top:0;
}
[data-testid="stExpander"] summary {
  min-height:66px;
  background:transparent;
  color:var(--ink);
  font-weight:800;
  letter-spacing:-.01em;
  padding:18px 4px;
}
[data-testid="stExpander"] summary:hover { color:var(--blue); }
[data-testid="stExpander"] details > div {
  padding:6px 4px 28px;
}

.section-intro {
  border-left:4px solid var(--blue);
  padding:13px 0 13px 18px;
  margin:2px 0 24px;
}
.section-intro strong {
  display:block;
  color:var(--ink);
  font-size:15px;
  line-height:1.4;
  margin-bottom:7px;
}
.section-intro span {
  display:block;
  color:var(--muted);
  line-height:1.75;
}
.group-label {
  color:var(--blue);
  letter-spacing:.16em;
  font-size:10px;
  font-weight:800;
  margin:28px 0 8px;
  text-transform:uppercase;
}
.subsection-heading {
  margin:30px 0 8px;
  font-size:18px;
  font-weight:820;
  letter-spacing:-.025em;
}
.subsection-purpose {
  color:var(--muted);
  margin:0 0 13px;
  line-height:1.65;
}

[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
div[data-baseweb="select"] > div {
  background:var(--panel) !important;
  color:var(--ink) !important;
  border:1px solid #cfd3dc !important;
  border-radius:0 !important;
  box-shadow:none !important;
  min-height:44px;
}
[data-testid="stTextArea"] textarea { min-height:130px; }
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
div[data-baseweb="select"] > div:focus-within {
  border-color:var(--blue) !important;
  box-shadow:0 0 0 3px rgba(36,88,255,.10) !important;
}
[data-testid="stWidgetLabel"] p,
label[data-testid="stWidgetLabel"] p {
  color:var(--ink);
  font-weight:750;
  letter-spacing:.01em;
}

.stButton > button,
.stDownloadButton > button {
  border-radius:999px !important;
  min-height:46px;
  padding:0 26px;
  font-weight:800;
  letter-spacing:.02em;
  transition:transform .2s ease, box-shadow .2s ease, background .2s ease;
}
.stButton > button[kind="primary"] {
  background:var(--ink) !important;
  border:1px solid var(--ink) !important;
  color:#fff !important;
}
.stButton > button[kind="primary"]:not(:disabled)::after {
  content:"  →";
  color:#fff;
}
.stButton > button[kind="primary"]:not(:disabled) {
  animation:nextWave 2.7s ease-in-out infinite;
}
.stButton > button[kind="primary"]:not(:disabled):hover {
  background:var(--blue) !important;
  border-color:var(--blue) !important;
  transform:translateY(-2px);
}
.stButton > button[kind="secondary"],
.stDownloadButton > button {
  background:var(--done) !important;
  border:1px solid #d1d4da !important;
  color:var(--done-text) !important;
}
.stButton > button[kind="secondary"]:not(:disabled):hover,
.stDownloadButton > button:hover {
  background:#dedfe3 !important;
  color:var(--ink) !important;
}

.next-action {
  position:relative;
  overflow:hidden;
  background:#fff;
  border:1px solid var(--line);
  padding:15px 17px 15px 20px;
  margin:24px 0 10px;
}
.next-action::before {
  content:"";
  position:absolute;
  left:0;
  top:0;
  bottom:0;
  width:4px;
  background:var(--blue);
}
.next-action::after {
  content:"";
  position:absolute;
  left:-35%;
  right:-35%;
  bottom:0;
  height:2px;
  background:linear-gradient(90deg, transparent, var(--blue), transparent);
  animation:waveLine 2.8s linear infinite;
}
.next-action.done::before { background:#a7abb3; }
.next-action.done::after { display:none; }
.next-action-kicker {
  color:var(--blue);
  font-size:9px;
  font-weight:850;
  letter-spacing:.2em;
  text-transform:uppercase;
}
.next-action.done .next-action-kicker { color:#787d86; }
.next-action-title {
  color:var(--ink);
  font-weight:800;
  font-size:14px;
  margin-top:5px;
}
.next-action-detail {
  color:var(--muted);
  line-height:1.65;
  margin-top:5px;
}

.research-counts {
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin:14px 0 24px;
}
.research-count {
  border:1px solid var(--line);
  background:#fff;
  padding:8px 11px;
}
.research-count b { color:var(--blue); margin-left:5px; }

.option-card {
  border:1px solid var(--line);
  background:#fff;
  padding:16px 18px;
  margin:10px 0;
}
.option-index {
  color:var(--blue);
  font-size:10px;
  font-weight:850;
  letter-spacing:.18em;
}
.option-title {
  font-size:15px;
  font-weight:820;
  margin:5px 0 8px;
}
.option-body { color:var(--ink-soft); line-height:1.72; }
.option-meta { color:var(--muted); margin-top:9px; }

div[data-testid="stDataFrame"] {
  border:1px solid var(--line);
  border-radius:0;
  overflow:hidden;
  background:#fff;
}
.small-muted { color:var(--muted); }

@keyframes nextWave {
  0%,100% { transform:translate3d(0,0,0); box-shadow:0 8px 20px rgba(0,0,0,.09), 0 0 0 0 rgba(36,88,255,.10); }
  30% { transform:translate3d(0,-2px,0) rotate(-.18deg); box-shadow:0 12px 26px rgba(0,0,0,.12), 0 0 0 7px rgba(36,88,255,.07); }
  62% { transform:translate3d(0,1px,0) rotate(.14deg); box-shadow:0 8px 20px rgba(0,0,0,.09), 0 0 0 2px rgba(36,88,255,.03); }
}
@keyframes waveLine {
  0% { transform:translateX(-35%); }
  100% { transform:translateX(35%); }
}
@keyframes fieldWave {
  0%,100% { border-color:#cfd3dc; box-shadow:0 0 0 0 rgba(36,88,255,0); }
  50% { border-color:var(--blue); box-shadow:0 0 0 5px rgba(36,88,255,.08); }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation:none !important; transition:none !important; }
}
@media (max-width: 760px) {
  .block-container { padding-left:1rem; padding-right:1rem; }
  .hero-title { font-size:28px; }
  .current-stage { grid-template-columns:1fr; gap:5px; }
  .research-counts { display:block; }
  .research-count { margin-bottom:6px; }
}
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
    "outline_confirmed": "",
    "originality_proposals": None,
    "selected_originality": None,
    "originality_choice": None,
    "article": "",
    "article_confirmed": "",
    "fact_check": "",
    "run_dir": None,
    "active_keyword": "",
    "active_llm_choice": "",
    "active_search_signature": "",
    "active_content_signature": "",
    "active_api_signature": "",
    "setup_confirmed_signature": "",
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
    "outline": ["outline", "outline_confirmed"],
    "originality": ["originality_proposals", "selected_originality", "originality_choice"],
    "article": ["article", "article_confirmed"],
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
        "ai_model_widget",
        "gemini_api_key_widget",
        "openai_api_key_widget",
        "brave_api_key_widget",
        "country_widget",
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


def digest_secret(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def next_button(key: str, completed: bool = False, disabled: bool = False) -> bool:
    return st.button(
        "Next",
        type="secondary" if completed else "primary",
        key=key,
        disabled=disabled or completed,
        width="stretch",
    )


def render_next_action(title: str, detail: str = "", completed: bool = False) -> None:
    css_class = "next-action done" if completed else "next-action"
    kicker = "Stage complete" if completed else "Next action"
    st.markdown(
        "<div class='{0}'>"
        "<div class='next-action-kicker'>{1}</div>"
        "<div class='next-action-title'>{2}</div>"
        "<div class='next-action-detail'>{3}</div>"
        "</div>".format(css_class, kicker, title, detail),
        unsafe_allow_html=True,
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


def outline_confirmation_is_current() -> bool:
    outline = str(st.session_state.get("outline", ""))
    editor = str(st.session_state.get("outline_editor", outline))
    confirmed = str(st.session_state.get("outline_confirmed", ""))
    return bool(outline and editor == outline and confirmed == outline)


def article_confirmation_is_current() -> bool:
    article = str(st.session_state.get("article", ""))
    editor = str(st.session_state.get("article_editor", article))
    confirmed = str(st.session_state.get("article_confirmed", ""))
    return bool(article and editor == article and confirmed == article)


def originality_confirmation_is_current() -> bool:
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


def render_web_results(data: Dict[str, Any]) -> None:
    diagnostics = data.get("diagnostics", {})
    st.markdown("<div class='subsection-heading'>Web</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='subsection-purpose'>競合のタイトル、スニペット、取得可能なH2/H3を記事構成に利用します。</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "配信元が自動取得を拒否したページも、BraveのタイトルとスニペットはAnalysisへ使用します。"
    )

    web_rows: List[Dict[str, Any]] = []
    for result in data.get("web", []):
        headings = result.get("headings", {})
        access_status = result.get("access_status")
        if result.get("eligible_for_analysis"):
            status = "H2/H3 ready"
        elif result.get("blocked_count"):
            status = "Security filtered"
        elif access_status == "publisher_blocked":
            status = "Blocked by site ({0})".format(result.get("http_status") or "HTTP")
        elif access_status == "snippet_only_platform":
            status = "Snippet only (platform)"
        elif access_status == "no_article_headings":
            status = "No article headings"
        elif access_status == "timeout":
            status = "Timeout"
        elif result.get("fetch_error"):
            status = "Page fetch error"
        else:
            status = "Snippet only"
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
    st.caption(
        "Top H2/H3 ready: {0} / Analysis pool: {1} / Supplemental pages: {2} / Publisher blocked: {3}".format(
            diagnostics.get("web_top_heading_count", 0),
            diagnostics.get("web_heading_count", 0),
            diagnostics.get("web_supplement_count", 0),
            diagnostics.get("web_publisher_blocked_count", 0),
        )
    )

    with st.expander("View page headings", expanded=False):
        for result in data.get("web", []):
            st.markdown(
                "**{0}. {1}**".format(result.get("rank"), result.get("title") or result.get("url"))
            )
            h2_values = result.get("headings", {}).get("h2", [])
            h3_values = result.get("headings", {}).get("h3", [])
            if h2_values:
                st.markdown("**H2**\n" + "\n".join("- {0}".format(value) for value in h2_values))
            if h3_values:
                st.markdown("**H3**\n" + "\n".join("- {0}".format(value) for value in h3_values))
            if not h2_values and not h3_values:
                if result.get("access_status") == "publisher_blocked":
                    st.warning(
                        "The publisher returned HTTP {0}. Title and snippet are still used as evidence.".format(
                            result.get("http_status") or "error"
                        )
                    )
                elif result.get("access_status") == "snippet_only_platform":
                    st.info("Platform page: title and snippet are used instead of page headings.")
                else:
                    st.info("No usable H2/H3 headings were extracted.")
            if result.get("notes"):
                st.caption(" / ".join(result["notes"]))
            st.divider()

    supplemental = data.get("web_analysis_supplement", [])
    if supplemental:
        with st.expander("Supplemental H2/H3 sources", expanded=False):
            st.caption(
                "Lower-ranked editorial pages used to strengthen heading analysis when top results are inaccessible or non-article pages."
            )
            for result in supplemental:
                st.markdown(
                    "**{0}. [{1}]({2})**".format(
                        result.get("rank"), result.get("title") or result.get("url"), result.get("url")
                    )
                )
                h2_values = result.get("headings", {}).get("h2", [])
                h3_values = result.get("headings", {}).get("h3", [])
                st.caption("H2: {0} / H3: {1}".format(len(h2_values), len(h3_values)))
                st.divider()


def render_category_results(
    title: str,
    purpose: str,
    items: List[Dict[str, Any]],
    empty_message: str,
    error_message: str = "",
) -> None:
    st.markdown("<div class='subsection-heading'>{0}</div>".format(title), unsafe_allow_html=True)
    st.markdown("<div class='subsection-purpose'>{0}</div>".format(purpose), unsafe_allow_html=True)
    category_rows = rows_for(items)
    if category_rows:
        st.dataframe(category_rows, width="stretch", hide_index=True)
    else:
        st.info(empty_message)
    if error_message:
        st.error(error_message)


def render_suggestion_results(data: Dict[str, Any]) -> None:
    st.markdown("<div class='subsection-heading'>Suggestion</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='subsection-purpose'>検索候補を関連需要とFAQ候補へ変換するために利用します。</div>",
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
    warnings = data.get("warnings") or {}
    errors = data.get("errors") or {}
    if warnings.get("suggestion"):
        st.warning(warnings["suggestion"])
    if errors.get("suggestion"):
        st.error(errors["suggestion"])
    suggestion_meta = data.get("suggestion_meta") or {}
    attempts = suggestion_meta.get("attempts") or []
    if attempts:
        with st.expander("Autosuggest diagnostics", expanded=False):
            st.json(suggestion_meta)


st.markdown(
    """
<div class="hero-shell">
  <div class="hero-brand">SEO<span>FLOW</span></div>
  <div class="hero-eyebrow">Research / Structure / Originality / Writing / Verification</div>
  <div class="hero-title">One clear action at a time.<br><em>Build the article from top to bottom.</em></div>
  <div class="hero-copy">左メニューを廃止し、必要な入力と次の操作を処理順に配置しました。現在の入力欄と「Next」だけが控えめに動き、迷わず次工程へ進めます。</div>
</div>
""",
    unsafe_allow_html=True,
)
status_placeholder = st.empty()

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

# STEP 01 — all settings are intentionally in one vertical flow.
with st.expander(
    "01  SETUP — 基本設定",
    expanded=not bool(st.session_state.setup_confirmed_signature),
):
    st.markdown(
        "<div class='section-intro'><strong>検索と生成に必要な条件を、上から順に入力します。</strong>"
        "<span>CTA URLだけは任意です。その他の必須項目を入力し、最下部のNextを押してください。</span></div>",
        unsafe_allow_html=True,
    )

    st.markdown("<div class='group-label'>AI provider</div>", unsafe_allow_html=True)
    llm_choice = st.selectbox("AI Model", list(MODEL_OPTIONS.keys()), key="ai_model_widget")
    model_config = get_model_config(llm_choice)
    api_widget_key = "gemini_api_key_widget" if model_config["provider"] == "gemini" else "openai_api_key_widget"
    api_secret_name = "GEMINI_API_KEY" if model_config["provider"] == "gemini" else "OPENAI_API_KEY"
    if api_widget_key not in st.session_state:
        st.session_state[api_widget_key] = secret(api_secret_name)
    api_key = st.text_input(
        "Gemini API Key" if model_config["provider"] == "gemini" else "OpenAI API Key",
        type="password",
        key=api_widget_key,
    )
    st.caption("Model ID: `{0}` · This provider is used for every AI stage.".format(model_config["model"]))

    st.markdown("<div class='group-label'>Search research</div>", unsafe_allow_html=True)
    if "brave_api_key_widget" not in st.session_state:
        st.session_state.brave_api_key_widget = secret("BRAVE_SEARCH_API_KEY")
    brave_api_key = st.text_input("Brave Search API Key", type="password", key="brave_api_key_widget")
    selected_country_label = st.selectbox("Country", list(country_options.keys()), key="country_widget")
    country_config = country_options[selected_country_label]
    result_count = 8
    serp_credentials = {"api_key": brave_api_key, **country_config}
    st.caption("Search language is linked automatically: `{0}`".format(country_config["search_lang"]))

    st.markdown("<div class='group-label'>Article settings</div>", unsafe_allow_html=True)
    keyword = st.text_input(
        "Target Keyword",
        placeholder="例：Galaxy AI 使い方",
        key="target_keyword_input",
    )
    owned_site_input = st.text_input(
        "Owned Site URL",
        placeholder="https://example.com/",
        key="owned_site_url_input",
        help="記事で自然に案内・訴求する自社サイトURLです。入力必須です。",
    )
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

    setup_valid = bool(not missing and not owned_invalid and not cta_invalid)
    search_signature = "{0}|{1}|{2}".format(keyword.strip(), selected_country_label, result_count)
    content_signature = "{0}|{1}".format(owned_site_url, cta_url)
    api_signature = "{0}|{1}".format(digest_secret(api_key), digest_secret(brave_api_key))
    setup_signature = "{0}|{1}|{2}|{3}".format(
        llm_choice, search_signature, content_signature, api_signature
    )

    context_changed = False
    if st.session_state.active_llm_choice and st.session_state.active_llm_choice != llm_choice:
        reset_search_context()
        context_changed = True
    if st.session_state.active_search_signature and st.session_state.active_search_signature != search_signature:
        reset_search_context()
        context_changed = True
    if st.session_state.active_content_signature and st.session_state.active_content_signature != content_signature:
        reset_from("outline")
        context_changed = True
    if st.session_state.active_api_signature and st.session_state.active_api_signature != api_signature:
        reset_search_context()
        context_changed = True
    if context_changed:
        st.session_state.setup_confirmed_signature = ""

    st.session_state.active_llm_choice = llm_choice
    st.session_state.active_search_signature = search_signature
    st.session_state.active_content_signature = content_signature
    st.session_state.active_api_signature = api_signature
    st.session_state.active_keyword = keyword

    setup_complete = bool(
        setup_valid and st.session_state.setup_confirmed_signature == setup_signature
    )
    if missing:
        st.info("Required: " + " / ".join(missing))
    elif setup_valid and not setup_complete:
        st.success("All required fields are ready. Press Next to lock the setup.")
    elif setup_complete:
        st.success("Setup confirmed. The next section is ready below.")

    render_next_action(
        "Save these settings and continue to SERP Research",
        "CTA URL may remain empty. Changing a confirmed setting resets dependent results.",
        completed=setup_complete,
    )
    if next_button("setup_next", completed=setup_complete, disabled=not setup_valid):
        st.session_state.setup_confirmed_signature = setup_signature
        st.rerun()

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
    outline_confirmation_is_current(),
    originality_confirmation_is_current(),
    article_confirmation_is_current(),
    bool(st.session_state.fact_check),
]
current_idx = next((index for index, done in enumerate(states) if not done), len(states) - 1)
completed_count = sum(1 for done in states if done)
status_placeholder.markdown(
    "<div class='current-stage'>"
    "<div class='current-stage-number'>{0:02d} / 06</div>"
    "<div><div class='current-stage-title'>{1}</div>"
    "<div class='current-stage-copy'>{2} of 6 stages complete. Open completed sections whenever you need to review or edit them.</div></div>"
    "</div>".format(current_idx + 1, STAGES[current_idx], completed_count),
    unsafe_allow_html=True,
)

# Highlight the active input area. The motion is intentionally subtle and disabled by OS reduced-motion settings.
if current_idx == 0:
    st.markdown(
        "<style>"
        "[data-testid='stTextInput'] input, div[data-baseweb='select'] > div {animation:fieldWave 2.9s ease-in-out infinite;}"
        "</style>",
        unsafe_allow_html=True,
    )
elif current_idx in (2, 4):
    st.markdown(
        "<style>[data-testid='stTextArea'] textarea {animation:fieldWave 2.9s ease-in-out infinite;}</style>",
        unsafe_allow_html=True,
    )
elif current_idx == 3:
    st.markdown(
        "<style>"
        "[data-testid='stRadio'], [data-testid='stTextArea'] textarea {animation:fieldWave 2.9s ease-in-out infinite;}"
        "</style>",
        unsafe_allow_html=True,
    )

# STEP 02
with st.expander(
    "02  SERP RESEARCH — 検索結果の取得・AI Analysis",
    expanded=states[0] and not states[1],
):
    st.markdown(
        "<div class='section-intro'><strong>Brave Searchとページ見出しをまとめ、AI Analysisを作成します。</strong>"
        "<span>Web、Discussions、News、Videos、Suggestionを上から順番に確認できます。タブ切り替えはありません。</span></div>",
        unsafe_allow_html=True,
    )
    if not states[0]:
        st.info("Complete Setup first.")
    else:
        if not st.session_state.serp_data:
            render_next_action(
                "Run Brave SERP Research and AI Analysis",
                "Search results, accessible page headings and cross-source insights are created in one run.",
            )
            if next_button("serp_next"):
                reset_search_context()
                run_dir = ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings)
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
        elif not st.session_state.serp_analysis:
            render_next_action(
                "Generate AI Analysis from the saved SERP data",
                "The previously collected search evidence is reused.",
            )
            if next_button("analysis_next"):
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
        else:
            data = st.session_state.serp_data
            diagnostics = data.get("diagnostics", {})
            st.markdown(
                "<div class='research-counts'>"
                "<div class='research-count'>Web <b>{0}</b></div>"
                "<div class='research-count'>Discussions <b>{1}</b></div>"
                "<div class='research-count'>News <b>{2}</b></div>"
                "<div class='research-count'>Videos <b>{3}</b></div>"
                "<div class='research-count'>Suggestion <b>{4}</b></div>"
                "</div>".format(
                    diagnostics.get("web_count", 0),
                    diagnostics.get("discussions_count", 0),
                    diagnostics.get("news_count", 0),
                    diagnostics.get("videos_count", 0),
                    diagnostics.get("suggestion_count", 0),
                ),
                unsafe_allow_html=True,
            )

            render_web_results(data)
            st.divider()
            errors = data.get("errors") or {}
            render_category_results(
                "Discussions",
                "Reddit、Yahoo!知恵袋、価格.com掲示板からユーザーの本音とPain Pointを把握します。",
                data.get("discussions", []),
                "Discussion result was not returned for this query.",
                "\n\n".join(
                    message for error_key, message in errors.items() if error_key.startswith("discussions:")
                ),
            )
            st.divider()
            render_category_results(
                "News",
                "鮮度、更新性、最新情報、仕様変更を確認します。",
                data.get("news", []),
                "News result was not returned for this query.",
                errors.get("news", ""),
            )
            st.divider()
            render_category_results(
                "Videos",
                "手順、比較、レビュー、実演で人気のテーマを確認します。",
                data.get("videos", []),
                "Video result was not returned for this query.",
                errors.get("videos", ""),
            )
            st.divider()
            render_suggestion_results(data)
            st.divider()
            st.markdown("<div class='subsection-heading'>Analysis</div>", unsafe_allow_html=True)
            st.markdown(
                "<div class='subsection-purpose'>全ソースを、構成・独自性・記事生成へ渡す戦略インサイトに変換します。</div>",
                unsafe_allow_html=True,
            )
            st.markdown(st.session_state.serp_analysis)

            render_next_action(
                "SERP Research is complete",
                "Continue to Outline. The Outline section opens automatically below.",
                completed=True,
            )
            next_button("serp_completed_next", completed=True)
            if st.button("Re-run this step", key="rerun_serp", type="secondary"):
                reset_search_context()
                st.rerun()

# STEP 03
with st.expander(
    "03  OUTLINE — 構成案の生成・編集",
    expanded=states[1] and not states[2],
):
    st.markdown(
        "<div class='section-intro'><strong>Analysisを根拠に、読者課題ごとの構成を設計します。</strong>"
        "<span>生成後に内容を確認・編集し、もう一度Nextを押すと構成が確定します。</span></div>",
        unsafe_allow_html=True,
    )
    if not states[1]:
        st.info("Complete SERP Research first.")
    else:
        with st.expander("Analysis used for this outline", expanded=False):
            st.markdown(st.session_state.serp_analysis or "分析結果がありません。")

        if not st.session_state.outline:
            render_next_action(
                "Generate the outline",
                "Analysis, SERP evidence, Owned Site URL and optional CTA settings are included.",
            )
            if next_button("outline_generate_next"):
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
                        st.session_state.outline_confirmed = ""
                    st.rerun()
                except Exception as exc:
                    st.error("Outline generation error: {0}".format(exc))
        else:
            st.session_state.setdefault("outline_editor", st.session_state.outline)
            st.text_area("Outline", height=620, key="outline_editor")
            outline_current = outline_confirmation_is_current()
            if not outline_current:
                render_next_action(
                    "Save this outline and continue to Originality",
                    "Review the H2/H3 order, Key Takeaways, FAQ and evidence requirements before continuing.",
                )
                if next_button("outline_save_next", disabled=not st.session_state.outline_editor.strip()):
                    st.session_state.outline = st.session_state.outline_editor
                    save_outline(
                        ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                        st.session_state.outline,
                    )
                    st.session_state.outline_confirmed = st.session_state.outline
                    reset_from("originality")
                    st.rerun()
            else:
                render_next_action(
                    "Outline is confirmed",
                    "Continue to Originality. Editing this text reactivates the Next button.",
                    completed=True,
                )
                next_button("outline_completed_next", completed=True)
                if st.button("Regenerate outline", key="regenerate_outline", type="secondary"):
                    reset_from("outline")
                    st.rerun()

# STEP 04
with st.expander(
    "04  ORIGINALITY — 独自要素の提案・選択・追加情報",
    expanded=states[2] and not states[3],
):
    st.markdown(
        "<div class='section-intro'><strong>競合ギャップから3案を作り、1案と追加情報を確定します。</strong>"
        "<span>選択肢、参考URL、意見、現場の知見をArticle Generationへ引き継ぎます。</span></div>",
        unsafe_allow_html=True,
    )
    if not states[2]:
        st.info("Complete Outline first.")
    else:
        proposals = st.session_state.originality_proposals or []
        if not proposals:
            render_next_action(
                "Generate three originality ideas",
                "The ideas are based on content gaps, user pain points, trend signals and the Owned Site perspective.",
            )
            if next_button("originality_generate_next"):
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
        else:
            labels = [
                "{0}. {1}".format(index + 1, item.get("title", "Idea"))
                for index, item in enumerate(proposals)
            ]
            default_index = st.session_state.originality_choice or 0
            selected_index = st.radio(
                "Select one idea",
                options=list(range(len(labels))),
                index=min(default_index, len(labels) - 1),
                format_func=lambda index: labels[index],
                horizontal=False,
                key="originality_choice_widget",
            )

            for index, item in enumerate(proposals):
                selected_mark = " · SELECTED" if index == selected_index else ""
                st.markdown(
                    "<div class='option-card'>"
                    "<div class='option-index'>OPTION {0:02d}{1}</div>"
                    "<div class='option-title'>{2}</div>"
                    "<div class='option-body'>{3}</div>"
                    "<div class='option-meta'>Placement: {4}</div>"
                    "</div>".format(
                        index + 1,
                        selected_mark,
                        html.escape(str(item.get("title", "Idea"))),
                        html.escape(str(item.get("description", ""))),
                        html.escape(str(item.get("placement", ""))),
                    ),
                    unsafe_allow_html=True,
                )

            if "originality_additional_info_widget" not in st.session_state:
                st.session_state.originality_additional_info_widget = str(
                    (st.session_state.selected_originality or {}).get("additional_information", "")
                )
            additional_information = st.text_area(
                "Additional Information (URL or opinion) — Optional",
                key="originality_additional_info_widget",
                height=150,
                placeholder="参考URL、現場で感じている課題、記事に入れたい意見、独自データの要点など",
                help=(
                    "選択した独自性と一緒にArticle Generationへ渡します。"
                    "URLだけではリンク先の内容を推測しないため、反映したい要点も添えてください。"
                ),
            )
            confirmation_current = originality_confirmation_is_current()
            if not confirmation_current:
                render_next_action(
                    "Confirm the selected idea and additional information",
                    "The confirmed payload is used only in the most relevant article section.",
                )
                if next_button("originality_confirm_next"):
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
            else:
                render_next_action(
                    "Originality is confirmed",
                    "Continue to Article Generation. Changing the choice or notes reactivates Next.",
                    completed=True,
                )
                next_button("originality_completed_next", completed=True)
                if st.button("Regenerate ideas", key="regenerate_originality", type="secondary"):
                    reset_from("originality")
                    st.rerun()

# STEP 05
with st.expander(
    "05  ARTICLE GENERATION — 記事生成・編集",
    expanded=states[3] and not states[4],
):
    st.markdown(
        "<div class='section-intro'><strong>Outline、Analysis、独自性、追加情報から本文を生成します。</strong>"
        "<span>生成後に編集し、もう一度Nextを押すと記事が確定してFact Checkへ進みます。</span></div>",
        unsafe_allow_html=True,
    )
    if not states[3]:
        st.info("Confirm an Originality idea first.")
    else:
        if not st.session_state.article:
            render_next_action(
                "Generate the article",
                "The output includes title and description in YAML front matter, H1, Key Takeaways, article sections, FAQ and contextual CTA.",
            )
            if next_button("article_generate_next"):
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
                        st.session_state.article_confirmed = ""
                    st.rerun()
                except Exception as exc:
                    st.error("Article generation error: {0}".format(exc))
        else:
            st.caption("The article Markdown includes `title` and `description` in YAML front matter.")
            st.session_state.setdefault("article_editor", st.session_state.article)
            st.text_area("Article", height=760, key="article_editor")
            article_current = article_confirmation_is_current()
            if not article_current:
                render_next_action(
                    "Save this article and continue to Fact Check",
                    "Review the title, description, Key Takeaways, Owned Site references and CTA before continuing.",
                )
                if next_button("article_save_next", disabled=not st.session_state.article_editor.strip()):
                    st.session_state.article = st.session_state.article_editor
                    save_article(
                        ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                        st.session_state.article,
                    )
                    st.session_state.article_confirmed = st.session_state.article
                    reset_from("fact")
                    st.rerun()
            else:
                render_next_action(
                    "Article is confirmed",
                    "Continue to Fact Check. Editing the article reactivates Next.",
                    completed=True,
                )
                next_button("article_completed_next", completed=True)
                st.download_button(
                    "Download Markdown",
                    st.session_state.article,
                    file_name="article.md",
                    mime="text/markdown",
                )
                if st.button("Regenerate article", key="regenerate_article", type="secondary"):
                    reset_from("article")
                    st.rerun()

# STEP 06
with st.expander(
    "06  FACT CHECK — ファクトチェック",
    expanded=states[4] and not states[5],
):
    st.markdown(
        "<div class='section-intro'><strong>記事内の事実を抽出し、Brave Searchの証拠で検証します。</strong>"
        "<span>選択中のAIは5件ずつ判定し、途中経過を保存します。AI固有のWeb検索機能は使いません。</span></div>",
        unsafe_allow_html=True,
    )
    if not states[4]:
        st.info("Complete Article Generation first.")
    elif not st.session_state.fact_check:
        render_next_action(
            "Run the fact check",
            "Facts are extracted, Brave evidence is collected and verdicts are generated in resumable batches.",
        )
        if next_button("factcheck_next"):
            reset_from("fact")
            progress_bar = st.progress(0.0)
            progress_text = st.empty()

            def update_factcheck_progress(completed: int, total: int, message: str) -> None:
                denominator = max(total, 1)
                progress_bar.progress(min(max(completed / denominator, 0.0), 1.0))
                progress_text.caption(message)

            try:
                llm = create_llm_service(api_key, llm_choice)
                with st.spinner("事実抽出・Brave証拠収集・バッチ判定を実行しています..."):
                    st.session_state.fact_check = step6_fact_check(
                        llm,
                        st.session_state.article,
                        ensure_run_dir(keyword, llm_choice, current_search_settings, content_settings),
                        brave_api_key=brave_api_key,
                        country=country_config["country"],
                        search_lang=country_config["search_lang"],
                        ui_lang=country_config["ui_lang"],
                        location_headers=country_config.get("location_headers") or {},
                        batch_size=5,
                        progress_callback=update_factcheck_progress,
                    )
                progress_bar.progress(1.0)
                progress_text.caption("Fact Check complete")
                st.rerun()
            except Exception as exc:
                st.error("Fact Check error: {0}".format(exc))
    else:
        st.markdown(st.session_state.fact_check)
        st.download_button(
            "Download Fact Check",
            st.session_state.fact_check,
            file_name="fact-check.md",
            mime="text/markdown",
        )
        render_next_action(
            "All stages are complete",
            "Download the article and Fact Check report, or reopen any section above to review it.",
            completed=True,
        )
        next_button("factcheck_completed_next", completed=True)
        if st.button("Run Fact Check again", key="rerun_factcheck", type="secondary"):
            reset_from("fact")
            st.rerun()

st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
with st.expander("Workflow options", expanded=False):
    st.caption("Reset removes session-state results and starts a new run. API keys are not written to run files.")
    if st.button("Reset workflow", key="reset_workflow", type="secondary", width="stretch"):
        reset_all()
        st.rerun()
