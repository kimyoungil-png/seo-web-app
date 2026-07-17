import json
import re
import time
from pathlib import Path
from typing import Any

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


def step2_fetch_serp_and_filter(keyword: str, run_id: str, run_dir: Path) -> dict:
    out_path = run_dir / "03-serp.json"
    status_code = fetch_serp.run(
        keyword=keyword,
        top_n=8,
        out_path=out_path,
        user_agent="ictGrowthHacker-SerpFetcher/1.0",
        timeout=15.0,
        exclude_hosts=[],
        run_id=run_id,
        engine="http",
    )
    if status_code != 0 and not out_path.exists():
        raise RuntimeError("SERPの取得に失敗しました。")

    serp_data = json.loads(out_path.read_text(encoding="utf-8"))
    serp_data["results"] = [
        result
        for result in serp_data.get("results", [])
        if result.get("blocked_count", 0) == 0
        and not result.get("fetch_error", False)
    ]
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


def step3_generate_outline(
    client: Any,
    llm_choice: str,
    keyword: str,
    serp_data: dict,
    run_dir: Path,
) -> str:
    sop_rules = load_prompt_file("sop.md")
    system_prompt = f"""
あなたはプロのSEOコンサルタントです。以下のSOPのStep 4に従って構成案を作成してください。

【SOPルール】
{sop_rules}

【対策キーワード】
{keyword}

【競合SERPデータ】
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

競合SERP:
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


FACT_CHECK_SYSTEM_PROMPT = """
You are a meticulous fact checker. Fact check the supplied article in full and leave no stone unturned.

First, parse the article into individual verifiable factual claims. Do not treat opinions, recommendations, or clearly marked hypotheses as facts.

For every factual claim:
1. Conduct comprehensive web research.
2. Prefer primary sources, official documentation, government sources, peer-reviewed research, or highly reputable reporting.
3. Aim for at least three independent high-quality sources where available.
4. Classify the claim as True, False, Misleading, Unclear, or Time-sensitive.
5. Explain the conclusion briefly.
6. Include source title and URL for every source used.
7. Suggest corrected wording when the claim is false, misleading, unclear, or time-sensitive.

Return a Markdown table with these columns:
Claim | Verdict | Reasoning | Sources | Suggested correction

After the table, add a short section titled "Priority corrections" listing the statements that should be fixed before publication.
Do not invent sources or URLs. State clearly when sufficient evidence is unavailable.
"""


def step6_fact_check(
    client: Any,
    llm_choice: str,
    article: str,
    run_dir: Path,
) -> str:
    report = generate_text(
        client,
        llm_choice,
        FACT_CHECK_SYSTEM_PROMPT,
        article,
        use_web_search=True,
    )
    (run_dir / "08-fact-check.md").write_text(report, encoding="utf-8")
    return report
