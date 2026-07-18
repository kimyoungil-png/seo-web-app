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


def get_llm_client(api_key: str, llm_choice: str):
    config = get_model_config(llm_choice)
    if config["provider"] == "gemini":
        return genai.Client(api_key=api_key)
    return OpenAI(api_key=api_key)


def generate_text(
    client: Any,
    llm_choice: str,
    system_prompt: str,
    user_prompt: str = "",
    *,
    use_web_search: bool = False,
) -> str:
    config = get_model_config(llm_choice)
    model = config["model"]

    if config["provider"] == "gemini":
        gemini_config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
            tools=[types.Tool(google_search=types.GoogleSearch())]
            if use_web_search
            else None,
        )
        response = client.models.generate_content(
            model=model,
            contents=user_prompt or system_prompt,
            config=gemini_config,
        )
        if not response.text:
            raise RuntimeError("Geminiからテキスト応答を取得できませんでした。")
        return response.text

    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=user_prompt or system_prompt,
        tools=[{"type": "web_search"}] if use_web_search else [],
    )
    if not response.output_text:
        raise RuntimeError("OpenAIからテキスト応答を取得できませんでした。")
    return response.output_text


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


def _search_brave(
    keyword: str,
    api_key: str,
    top_n: int,
    *,
    country: str,
    search_lang: str,
    ui_lang: str,
) -> list[dict[str, Any]]:
    """Brave Web Search APIから通常のWeb検索結果を取得する。"""
    if not api_key:
        raise ValueError("Brave Search API Keyを入力してください。")

    url = "https://api.search.brave.com/res/v1/web/search"
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
    params = {
        "q": keyword,
        "country": country,
        "search_lang": search_lang,
        "ui_lang": ui_lang,
        "count": min(max(top_n, 1), 20),
        "offset": 0,
        "safesearch": "moderate",
        "spellcheck": "true",
        "text_decorations": "false",
        "result_filter": "web",
    }

    response = httpx.get(url, headers=headers, params=params, timeout=30.0)

    # Handle common error statuses with clearer messages
    if response.status_code == 401:
        raise RuntimeError("Brave Search APIキーが無効です。")
    if response.status_code == 403:
        raise RuntimeError("Brave Search APIの利用権限または契約プランを確認してください。")
    if response.status_code == 429:
        raise RuntimeError("Brave Search APIのレート制限に達しました。しばらく待って再実行してください。")

    # Special handling for 422 Unprocessable Entity: try a shorter ui_lang (e.g. 'ja' from 'ja-JP')
    if response.status_code == 422:
        try:
            short_ui = ui_lang.split("-")[0]
            if short_ui and short_ui != ui_lang:
                params["ui_lang"] = short_ui
                retry_resp = httpx.get(url, headers=headers, params=params, timeout=30.0)
                if retry_resp.status_code == 200:
                    data = retry_resp.json()
                    web_results = (data.get("web") or {}).get("results") or []
                    return [
                        {
                            "url": row.get("url", ""),
                            "title": row.get("title"),
                            "snippet": row.get("description", ""),
                        }
                        for row in web_results[:top_n]
                        if row.get("url")
                    ]
                # fall through to raise below
        except Exception:
            pass
        # include response body for debugging
        raise RuntimeError(
            f"Brave Search API returned 422 Unprocessable Entity. Response body: {response.text}"
        )

    # For other non-success statuses, raise generic
    response.raise_for_status()

    data = response.json()
    web_results = (data.get("web") or {}).get("results") or []
    return [
        {
            "url": row.get("url", ""),
            "title": row.get("title"),
            "snippet": row.get("description", ""),
        }
        for row in web_results[:top_n]
        if row.get("url")
    ]

def step2_fetch_serp_and_filter(
    keyword: str,
    run_id: str,
    run_dir: Path,
    *,
    provider: str,
    credentials: dict[str, str],
    top_n: int = 8,
) -> dict:
    """外部SERP APIで順位URLを取得し、各URLの見出しを安全に抽出する。"""
    if provider == "brave":
        candidates = _search_brave(
            keyword,
            credentials.get("api_key", ""),
            top_n,
            country=credentials.get("country", "JP"),
            search_lang=credentials.get("search_lang", "ja"),
            ui_lang=credentials.get("ui_lang", "ja-JP"),
        )
    else:
        raise ValueError(f"未対応のSERPプロバイダーです: {provider}")

    if not candidates:
        raise RuntimeError("SERP APIからオーガニック検索結果を取得できませんでした。")

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
            "search_lang": credentials.get("search_lang", "ja"),
            "ui_lang": credentials.get("ui_lang", "ja-JP"),
        },
        "results": valid_results,
        "diagnostics": {
            "raw_count": len(raw_results),
            "valid_count": len(valid_results),
            "failed_count": sum(bool(r.get("fetch_error")) for r in raw_results),
            "blocked_count": sum(bool(r.get("blocked_count", 0)) for r in raw_results),
        },
    }
    (run_dir / "03-serp.json").write_text(
        json.dumps(serp_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not valid_results:
        raise RuntimeError(
            "順位URLは取得できましたが、本文の見出しを取得できるページがありませんでした。"
            "対象サイト側のアクセス制限を確認してください。"
        )
    return serp_data


def build_serp_summary(serp_data: dict) -> str:
    lines = []
    for result in serp_data.get("results", []):
        headings = result.get("headings", {})
        lines.append(
            "\n".join(
                [
                    f"順位: {result.get('rank')}",
                    f"タイトル: {result.get('title') or '(タイトルなし)'}",
                    f"URL: {result.get('url')}",
                    f"H2: {json.dumps(headings.get('h2', []), ensure_ascii=False)}",
                    f"H3: {json.dumps(headings.get('h3', []), ensure_ascii=False)}",
                ]
            )
        )
    return "\n\n".join(lines)


def analyze_serp(serp_data: dict) -> str:
    """SERP全体の頻出見出しと検索意図の手掛かりを簡潔にまとめる。"""
    results = serp_data.get("results", [])
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

    lines = [f"### SERP分析（取得 {len(results)}件）", "", "#### 頻出H2"]
    if h2_counter:
        lines.extend([f"- {title}（{count}ページ）" for title, count in h2_counter.most_common(12)])
    else:
        lines.append("- 抽出できませんでした")
    lines.extend(["", "#### 頻出H3"])
    if h3_counter:
        lines.extend([f"- {title}（{count}ページ）" for title, count in h3_counter.most_common(15)])
    else:
        lines.append("- 抽出できませんでした")
    lines.extend([
        "",
        "#### 構成作成時の判断基準",
        "- 複数ページで共通する論点は検索意図の中核として優先する",
        "- 単一ページだけの見出しは必要性を検討し、網羅性のために機械的には追加しない",
        "- タイトル・スニペット・H2・H3を照合し、重複をまとめて自然な章立てにする",
        "- 競合で薄い論点は独自性提案の候補として扱う",
    ])
    return "\n".join(lines)


def step3_generate_outline(
    client: Any,
    llm_choice: str,
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
    outline = generate_text(client, llm_choice, system_prompt)
    (run_dir / "04-outline.md").write_text(outline, encoding="utf-8")
    return outline


def step4_propose_originality(
    client: Any,
    llm_choice: str,
    keyword: str,
    serp_data: dict,
    outline: str,
    run_dir: Path,
    serp_analysis: str = "",
) -> list[dict[str, str]]:
    system_prompt = """
あなたはSEO編集者です。競合SERPの見出しと現在の構成案を比較し、競合上位ページには含まれていない、または十分に扱われていない独自要素を3件だけ提案してください。

条件:
- 記事本文に実際に追加できる具体的な要素にする
- 単なる言い換えや一般論は禁止
- 根拠のない数値は提案しない
- 各案に title、description、placement の3項目を含める
- JSON配列だけを返す
"""
    user_prompt = f"""
対策キーワード: {keyword}

SERP横断分析:
{serp_analysis}

競合SERP生データ:
{build_serp_summary(serp_data)}

現在の構成案:
{outline}
"""
    raw = generate_text(client, llm_choice, system_prompt, user_prompt)
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
    client: Any,
    llm_choice: str,
    keyword: str,
    outline: str,
    originality: dict[str, str],
    run_dir: Path,
    serp_analysis: str = "",
) -> str:
    style_rules = load_prompt_file("writing-style.md")
    data_rules = load_prompt_file("data-integrity.md")

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
あなたはプロのSEOライターです。指定されたH2セクションだけを執筆してください。

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

独自要素は最も自然なH2に一度だけ盛り込み、他のセクションでは重複させないでください。
"""
        user_prompt = f"H2見出し「{h2_title}」の本文のみを執筆してください。"
        section = generate_text(client, llm_choice, system_prompt, user_prompt)
        (drafts_dir / f"{h2_id}.md").write_text(section, encoding="utf-8")
        full_article += f"## {h2_title}\n{section}\n\n"
        time.sleep(1)

    (run_dir / "07-final.md").write_text(full_article, encoding="utf-8")
    return full_article


def step6_fact_check(
    client: Any,
    llm_choice: str,
    article: str,
    run_dir: Path,
) -> str:
    factcheck_prompt = load_prompt_file("factcheck-prompt.md")
    if not factcheck_prompt.strip():
        raise FileNotFoundError(
            "factcheck-prompt.mdが見つかりません。アプリのルートまたはreferencesに配置してください。"
        )
    report = generate_text(
        client,
        llm_choice,
        factcheck_prompt,
        article,
        use_web_search=True,
    )
    (run_dir / "08-fact-check.md").write_text(report, encoding="utf-8")
    return report
