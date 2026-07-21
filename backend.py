import base64
import json
import re
import time
from pathlib import Path
from typing import Any
from collections import Counter

import httpx

from google import genai
from google.genai import types
from openai import OpenAI

import fetch_serp

MODEL_OPTIONS = {
    "Gemini 3.1 Flash-Lite Preview": {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite-preview",
    },
    "Gemini Flash Latest": {
        "provider": "gemini",
        "model": "gemini-flash-latest",
    },
    "OpenAI GPT-5 mini": {
        "provider": "openai",
        "model": "gpt-5-mini",
    },
}


def get_model_config(llm_choice: str) -> dict[str, str]:
    if llm_choice not in MODEL_OPTIONS:
        raise ValueError(f"未対応のモデルです: {llm_choice}")
    return MODEL_OPTIONS[llm_choice]


class LLMService:
    """選択された1つのAIプロバイダーだけを全AI工程で利用する共通サービス。"""

    def __init__(self, api_key: str, llm_choice: str):
        if not api_key:
            raise ValueError("AI API Keyを入力してください。")
        self.choice = llm_choice
        self.config = get_model_config(llm_choice)
        self.provider = self.config["provider"]
        self.model = self.config["model"]
        self.client = self._create_client(api_key)

    def _create_client(self, api_key: str):
        if self.provider == "gemini":
            return genai.Client(api_key=api_key)
        if self.provider == "openai":
            return OpenAI(api_key=api_key)
        raise ValueError(f"未対応のAIプロバイダーです: {self.provider}")

    def generate(
        self,
        system_prompt: str,
        user_prompt: str = "",
        *,
        use_web_search: bool = False,
    ) -> str:
        """Outline / Originality / Article / Fact Checkの全処理が必ずこの入口を通る。"""
        if self.provider == "gemini":
            gemini_config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7,
                tools=[types.Tool(google_search=types.GoogleSearch())]
                if use_web_search
                else None,
            )
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_prompt or system_prompt,
                config=gemini_config,
            )
            if not response.text:
                raise RuntimeError("Geminiからテキスト応答を取得できませんでした。")
            return response.text

        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=user_prompt or system_prompt,
            tools=[{"type": "web_search"}] if use_web_search else [],
        )
        if not response.output_text:
            raise RuntimeError("OpenAIからテキスト応答を取得できませんでした。")
        return response.output_text


def create_llm_service(api_key: str, llm_choice: str) -> LLMService:
    return LLMService(api_key=api_key, llm_choice=llm_choice)


def load_prompt_file(filename: str) -> str:
    candidates = [Path("references") / filename, Path(filename)]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


SERP_PROVIDER_OPTIONS = {
    "Brave Search API": "brave",
}

def _extract_page_details(rank: int, item: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    """SERP APIの結果URLからtitle/H2/H3を取得し、既存の安全検査を適用する。"""
    url = item.get("url", "")
    result = {
        "rank": rank,
        "url": url,
        "title": item.get("title"),
        "snippet": item.get("snippet", ""),
        "headings": {"h2": [], "h3": []},
        "fetch_error": False,
        "blocked_count": 0,
        "notes": [],
    }
    if not url:
        result["fetch_error"] = True
        result["notes"].append("missing_url")
        return result

    try:
        title, h2, h3, notes = fetch_serp.fetch_page_headings(
            url=url,
            user_agent="ictGrowthHacker-SerpFetcher/1.0",
            timeout=timeout,
        )
        result["title"] = title or result["title"]
        result["headings"] = {"h2": h2, "h3": h3}
        result["notes"].extend(notes)
        payload_hits = fetch_serp.count_payload_hits(h2) + fetch_serp.count_payload_hits(h3)
        if payload_hits:
            result["blocked_count"] = payload_hits
            result["headings"] = {"h2": [], "h3": []}
            result["notes"].append("injection_suspected")
    except Exception as exc:
        result["fetch_error"] = True
        result["notes"].append(f"fetch_error:{type(exc).__name__}:{exc}")
    return result


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("results", "items", "data"):
            if isinstance(value.get(key), list):
                return value[key]
        return [value]
    return []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return re.sub(r"<[^>]+>", "", str(value)).strip()


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _normalize_result_items(section: Any, category: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(_as_list(section), 1):
        if not isinstance(raw, dict):
            raw = {"text": raw}
        title = _clean_text(_first_value(raw, "title", "question", "name", "query"))
        description = _clean_text(
            _first_value(raw, "description", "answer", "snippet", "text", "long_desc")
        )
        url = _clean_text(_first_value(raw, "url", "link", "source_url"))
        source = _clean_text(
            _first_value(raw, "source", "profile", "forum_name", "publisher", "site_name")
        )
        age = _clean_text(_first_value(raw, "age", "page_age", "published", "date"))
        item = {
            "rank": index,
            "category": category,
            "title": title,
            "url": url,
            "snippet": description,
            "source": source,
            "age": age,
            "raw": raw,
        }
        normalized.append(item)
    return normalized


def _normalize_entity_suggestions(section: Any) -> list[dict[str, Any]]:
    """Autosuggest rich=true の results をEntity候補として正規化する。"""
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(_as_list(section), 1):
        if not isinstance(raw, dict):
            raw = {"query": raw}
        normalized.append({
            "rank": index,
            "query": _clean_text(raw.get("query")),
            "is_entity": bool(raw.get("is_entity")),
            "title": _clean_text(_first_value(raw, "title", "query", "name")),
            "description": _clean_text(_first_value(raw, "description", "subtitle", "summary")),
            "image": _clean_text(_first_value(raw, "img", "image", "thumbnail")),
            "raw": raw,
        })
    # Entity判定された候補を先頭にする。判定フィールドがないプランでもrich情報を保持する。
    return sorted(normalized, key=lambda item: (not item["is_entity"], item["rank"]))


def _brave_get(
    endpoint: str,
    api_key: str,
    params: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Brave Search API Keyを入力してください。")
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/134.0.0.0 Safari/537.36"
        ),
    }
    url = f"https://api.search.brave.com{endpoint}"
    response = httpx.get(url, headers=headers, params=params, timeout=30.0)
    if response.status_code != 200:
        raise RuntimeError(f"Brave {label} API error {response.status_code}: {response.text}")
    return response.json()


def _base_search_params(
    keyword: str,
    top_n: int,
    *,
    country: str,
    search_lang: str,
    ui_lang: str,
) -> dict[str, Any]:
    return {
        "q": keyword,
        "country": country,
        "search_lang": search_lang,
        "ui_lang": ui_lang,
        "count": min(max(top_n, 1), 20),
        "offset": 0,
        "safesearch": "moderate",
        "spellcheck": "true",
        "text_decorations": "false",
        "extra_snippets": "true",
    }


def _search_brave(
    keyword: str,
    api_key: str,
    top_n: int,
    *,
    country: str,
    search_lang: str,
    ui_lang: str,
) -> dict[str, Any]:
    """Braveの専用エンドポイントとsite検索から用途別SERPを取得する。"""
    base = _base_search_params(
        keyword, top_n, country=country, search_lang=search_lang, ui_lang=ui_lang
    )

    # 1) Web: GET /res/v1/web/search
    web_raw = _brave_get(
        "/res/v1/web/search", api_key, base, label="Web Search"
    )
    web_items = _normalize_result_items(web_raw.get("web"), "web")[:top_n]

    # 2) Discussions: 専用endpointは使わず、Web Search + site: 演算子で3媒体を取得
    discussion_sites = [
        ("Reddit", "reddit.com"),
        ("Yahoo!知恵袋", "chiebukuro.yahoo.co.jp"),
        ("価格.com掲示板", "bbs.kakaku.com"),
    ]
    discussions: list[dict[str, Any]] = []
    discussion_raw: dict[str, Any] = {}
    per_site_count = min(max(top_n, 1), 10)
    for source_label, site in discussion_sites:
        params = _base_search_params(
            f"{keyword} site:{site}",
            per_site_count,
            country=country,
            search_lang=search_lang,
            ui_lang=ui_lang,
        )
        data = _brave_get(
            "/res/v1/web/search", api_key, params, label=f"Discussions ({source_label})"
        )
        discussion_raw[site] = data
        items = _normalize_result_items(data.get("web"), "discussions")
        for item in items:
            item["source"] = source_label
            item["site"] = site
            item["discussion_query"] = params["q"]
        discussions.extend(items)

    # 4) News: GET /res/v1/news/search
    news_raw = _brave_get(
        "/res/v1/news/search", api_key, base, label="News Search"
    )
    news_items = _normalize_result_items(news_raw.get("results"), "news")[:top_n]

    # 5) Videos: GET /res/v1/videos/search
    videos_raw = _brave_get(
        "/res/v1/videos/search", api_key, base, label="Videos Search"
    )
    video_items = _normalize_result_items(videos_raw.get("results"), "videos")[:top_n]

    # 6) Entity: GET /res/v1/suggest/search?q=...&rich=true
    suggest_params = {
        "q": keyword,
        "country": country,
        "count": min(max(top_n, 1), 10),
        "rich": "true",
    }
    suggest_raw = _brave_get(
        "/res/v1/suggest/search", api_key, suggest_params, label="Autosuggest"
    )
    entity_items = _normalize_entity_suggestions(suggest_raw.get("results"))

    return {
        "web": web_items,
        "discussions": discussions,
        "news": news_items,
        "videos": video_items,
        "entity": entity_items,
        "query": web_raw.get("query") or {},
        "raw_response": {
            "web": web_raw,
            "discussions": discussion_raw,
            "news": news_raw,
            "videos": videos_raw,
            "entity": suggest_raw,
        },
    }

def step2_fetch_serp_and_filter(
    keyword: str,
    run_id: str,
    run_dir: Path,
    *,
    provider: str,
    credentials: dict[str, str],
    top_n: int = 8,
) -> dict:
    """BraveのWeb・site検索Discussions・News・Videos・Entityを取得する。"""
    if provider != "brave":
        raise ValueError(f"未対応のSERPプロバイダーです: {provider}")

    brave_data = _search_brave(
        keyword,
        credentials.get("api_key", ""),
        top_n,
        country=credentials.get("country", "JP"),
        search_lang=credentials.get("search_lang", "jp"),
        ui_lang=credentials.get("ui_lang", "ja-JP"),
    )
    candidates = brave_data.get("web", [])
    if not candidates:
        raise RuntimeError("Brave Search APIからWeb検索結果を取得できませんでした。")

    raw_results = [_extract_page_details(rank, item) for rank, item in enumerate(candidates, 1)]
    valid_results = [
        result
        for result in raw_results
        if result.get("blocked_count", 0) == 0 and not result.get("fetch_error", False)
    ]

    serp_data = {
        "run_id": run_id,
        "keyword": keyword,
        "provider": provider,
        "search_settings": {
            "country": credentials.get("country", "JP"),
            "search_lang": credentials.get("search_lang", "jp"),
            "ui_lang": credentials.get("ui_lang", "ja-JP"),
        },
        "results": valid_results,
        "web": valid_results,
        "discussions": brave_data.get("discussions", []),
        "news": brave_data.get("news", []),
        "videos": brave_data.get("videos", []),
        "entity": brave_data.get("entity", {}),
        "query": brave_data.get("query", {}),
        "diagnostics": {
            "raw_web_count": len(raw_results),
            "valid_web_count": len(valid_results),
            "failed_web_count": sum(bool(r.get("fetch_error")) for r in raw_results),
            "blocked_web_count": sum(bool(r.get("blocked_count", 0)) for r in raw_results),
            "discussions_count": len(brave_data.get("discussions", [])),
            "news_count": len(brave_data.get("news", [])),
            "videos_count": len(brave_data.get("videos", [])),
            "entity_count": len(brave_data.get("entity", [])),
        },
    }
    (run_dir / "03-serp.json").write_text(
        json.dumps(serp_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not valid_results:
        raise RuntimeError(
            "Web順位URLは取得できましたが、本文の見出しを取得できるページがありませんでした。"
            "対象サイト側のアクセス制限を確認してください。"
        )
    return serp_data

def _category_lines(items: list[dict[str, Any]], limit: int = 12) -> list[str]:
    lines: list[str] = []
    for item in items[:limit]:
        title = item.get("title") or item.get("question") or "(タイトルなし)"
        snippet = item.get("snippet") or item.get("answer") or ""
        url = item.get("url") or ""
        lines.append(f"- {title}\n  - 概要: {snippet}\n  - URL: {url}")
    return lines or ["- 該当結果なし"]


def build_serp_summary(serp_data: dict) -> str:
    lines = ["# Brave SERP Research"]
    lines.extend(["", "## Web：競合分析・構成"])
    for result in serp_data.get("web", serp_data.get("results", [])):
        headings = result.get("headings", {})
        lines.append(
            "\n".join(
                [
                    f"順位: {result.get('rank')}",
                    f"タイトル: {result.get('title') or '(タイトルなし)'}",
                    f"URL: {result.get('url')}",
                    f"概要: {result.get('snippet', '')}",
                    f"H2: {json.dumps(headings.get('h2', []), ensure_ascii=False)}",
                    f"H3: {json.dumps(headings.get('h3', []), ensure_ascii=False)}",
                ]
            )
        )
    lines.extend(["", "## Discussions：ユーザーの本音・Pain Point"])
    lines.extend(_category_lines(serp_data.get("discussions", [])))
    lines.extend(["", "## News：鮮度・更新性・最新情報・変更点"])
    lines.extend(_category_lines(serp_data.get("news", [])))
    lines.extend(["", "## Videos：体験・理解促進・手順・比較・実演"])
    lines.extend(_category_lines(serp_data.get("videos", [])))
    entities = serp_data.get("entity") or []
    lines.extend(["", "## Entity：Autosuggest rich候補"])
    if entities:
        for item in entities[:10]:
            lines.extend([
                f"- 名称: {item.get('title') or item.get('query', '')}",
                f"  - Entity判定: {item.get('is_entity', False)}",
                f"  - 説明: {item.get('description', '')}",
            ])
    else:
        lines.append("- 該当結果なし")
    return "\n".join(lines)

def analyze_serp(serp_data: dict) -> str:
    """Braveの各SERPタイプをSEO記事制作の役割別に整理する。"""
    results = serp_data.get("web", serp_data.get("results", []))
    h2_counter: Counter[str] = Counter()
    h3_counter: Counter[str] = Counter()
    for result in results:
        headings = result.get("headings", {})
        for heading in headings.get("h2", []):
            normalized = re.sub(r"\s+", " ", str(heading)).strip()
            if normalized:
                h2_counter[normalized] += 1
        for heading in headings.get("h3", []):
            normalized = re.sub(r"\s+", " ", str(heading)).strip()
            if normalized:
                h3_counter[normalized] += 1

    lines = [
        f"### Web：競合分析・構成（取得 {len(results)}件）",
        "",
        "#### 頻出H2",
    ]
    lines.extend(
        [f"- {title}（{count}ページ）" for title, count in h2_counter.most_common(12)]
        or ["- 抽出できませんでした"]
    )
    lines.extend(["", "#### 頻出H3"])
    lines.extend(
        [f"- {title}（{count}ページ）" for title, count in h3_counter.most_common(15)]
        or ["- 抽出できませんでした"]
    )

    categories = [
        ("Discussions：ユーザーの本音・Pain Point", "discussions"),
        ("News：鮮度・更新性・最新情報・変更点", "news"),
        ("Videos：体験・理解促進・手順・比較・実演", "videos"),
    ]
    for heading, key in categories:
        items = serp_data.get(key, [])
        lines.extend(["", f"### {heading}（取得 {len(items)}件）"])
        if items:
            for item in items[:10]:
                title = item.get("title") or item.get("question") or "(タイトルなし)"
                snippet = item.get("snippet") or item.get("answer") or ""
                lines.append(f"- **{title}**：{snippet}")
        else:
            lines.append("- 該当結果なし")

    entities = serp_data.get("entity") or []
    lines.extend(["", f"### Entity：Autosuggest rich候補（取得 {len(entities)}件）"])
    if entities:
        for item in entities[:10]:
            label = item.get("title") or item.get("query") or "(名称なし)"
            desc = item.get("description") or ""
            flag = "Entity" if item.get("is_entity") else "Suggestion"
            lines.append(f"- **{label}**（{flag}）：{desc}")
    else:
        lines.append("- 該当結果なし")

    lines.extend([
        "",
        "### 構成作成時の判断基準",
        "- Webの共通論点を検索意図の中核として構成に反映する",
        "- Discussionsから悩み・不満・障壁・生の表現を抽出する",
        "- Newsから更新日、制度変更、製品変更など鮮度が必要な論点を確認する",
        "- Videosから手順、比較、実演、視覚説明が有効な箇所を特定する",
        "- Entityで名称、属性、関連概念の一貫性を確認する",
    ])
    return "\n".join(lines)

def step3_generate_outline(
    llm: LLMService,
    keyword: str,
    serp_data: dict,
    run_dir: Path,
    serp_analysis: str = "",
) -> str:
    sop_rules = load_prompt_file("sop.md")
    system_prompt = f"""
あなたはプロのSEOコンサルタントです。以下のSOPのStep 4に従って構成案を作成してください。

【SOPルール】
{sop_rules}

【対策キーワード】
{keyword}

【SERP横断分析】
{serp_analysis}

【競合SERP生データ】
{build_serp_summary(serp_data)}

出力はMarkdown形式とし、各H2には必ず [id: h2-01] の形式でIDを付与してください。
"""
    outline = llm.generate(system_prompt)
    (run_dir / "04-outline.md").write_text(outline, encoding="utf-8")
    return outline


def step4_propose_originality(
    llm: LLMService,
    keyword: str,
    serp_data: dict,
    outline: str,
    run_dir: Path,
    serp_analysis: str = "",
) -> list[dict[str, str]]:
    system_prompt = load_prompt_file("originality-prompt.md")
    if not system_prompt.strip():
        raise FileNotFoundError(
            "originality-prompt.mdが見つかりません。referencesに配置してください。"
        )
    user_prompt = f"""
対策キーワード: {keyword}

SERP横断分析:
{serp_analysis}

競合SERP生データ:
{build_serp_summary(serp_data)}

現在の構成案:
{outline}
"""
    raw = llm.generate(system_prompt, user_prompt)
    match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
    if not match:
        raise RuntimeError("独自性提案をJSONとして解析できませんでした。")
    proposals = json.loads(match.group(0))
    if not isinstance(proposals, list) or len(proposals) < 3:
        raise RuntimeError("独自性提案が3件生成されませんでした。")
    proposals = proposals[:3]
    (run_dir / "05-originality-proposals.json").write_text(
        json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return proposals


def step5_generate_sections_and_assemble(
    llm: LLMService,
    keyword: str,
    outline: str,
    originality: dict[str, str],
    run_dir: Path,
    serp_analysis: str = "",
) -> str:
    article_prompt = load_prompt_file("article-prompt.md")
    style_rules = load_prompt_file("writing-style.md")
    data_rules = load_prompt_file("data-integrity.md")
    if not article_prompt.strip():
        raise FileNotFoundError(
            "article-prompt.mdが見つかりません。referencesに配置してください。"
        )

    h2_matches = re.findall(r"##\s+(.*?)\s+\[id:\s*(h2-\d+)\]", outline)
    if not h2_matches:
        h2_matches = [
            (line.replace("## ", "").strip(), f"h2-{i:02d}")
            for i, line in enumerate(outline.splitlines(), 1)
            if line.startswith("## ")
        ]

    drafts_dir = run_dir / "06-drafts"
    drafts_dir.mkdir(exist_ok=True)
    full_article = f"# {keyword} のSEO記事\n\n"
    originality_text = json.dumps(originality, ensure_ascii=False)

    for h2_title, h2_id in h2_matches:
        system_prompt = f"""
【Article Generation専用指示】
{article_prompt}

【執筆スタイル規約】
{style_rules}

【データ整合性ルール】
{data_rules}

【SERP分析】
{serp_analysis}

【全体構成案】
{outline}

【選択された独自要素】
{originality_text}
"""
        user_prompt = f"H2見出し「{h2_title}」の本文のみを執筆してください。"
        section = llm.generate(system_prompt, user_prompt)
        (drafts_dir / f"{h2_id}.md").write_text(section, encoding="utf-8")
        full_article += f"## {h2_title}\n{section}\n\n"
        time.sleep(1)

    (run_dir / "07-final.md").write_text(full_article, encoding="utf-8")
    return full_article


def step6_fact_check(
    llm: LLMService,
    article: str,
    run_dir: Path,
) -> str:
    factcheck_prompt = load_prompt_file("factcheck-prompt.md")
    if not factcheck_prompt.strip():
        raise FileNotFoundError(
            "factcheck-prompt.mdが見つかりません。アプリのルートまたはreferencesに配置してください。"
        )
    report = llm.generate(
        factcheck_prompt,
        article,
        use_web_search=True,
    )
    (run_dir / "08-fact-check.md").write_text(report, encoding="utf-8")
    return report
