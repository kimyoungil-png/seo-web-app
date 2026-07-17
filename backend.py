import json
import os
import re
import time
from pathlib import Path
import google.generativeai as genai
from openai import OpenAI
import fetch_serp

def get_llm_client(api_key: str, llm_choice: str):
    """選択されたAIに応じてクライアントを初期化"""
    if "Gemini" in llm_choice:
        genai.configure(api_key=api_key)
        # 404エラー対策として -latest を明記
        return genai.GenerativeModel('gemini-flash-latest')
    else:
        return OpenAI(api_key=api_key)

def generate_text(client, llm_choice: str, system_prompt: str, user_prompt: str = "") -> str:
    """GeminiとOpenAIで異なるAPIの叩き方を吸収する共通関数"""
    if "Gemini" in llm_choice:
        prompt = system_prompt
        if user_prompt:
            prompt += f"\n\n{user_prompt}"
        response = client.generate_content(prompt)
        return response.text
    else:
        messages = [{"role": "system", "content": system_prompt}]
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7
        )
        return response.choices[0].message.content

def load_prompt_file(filename: str) -> str:
    path = Path(f"references/{filename}")
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
        engine="http"
    )
    
    if status_code != 0 and not out_path.exists():
        raise Exception("SERPの取得に失敗しました。")
        
    with open(out_path, "r", encoding="utf-8") as f:
        serp_data = json.load(f)
        
    filtered_results = [
        r for r in serp_data.get("results", [])
        if r.get("blocked_count", 0) == 0 and not r.get("fetch_error", False)
    ]
    serp_data["results"] = filtered_results
    return serp_data

def step3_generate_outline(client, llm_choice: str, keyword: str, serp_data: dict, run_dir: Path) -> str:
    sop_rules = load_prompt_file("sop.md")
    
    system_prompt = f"""
    あなたはプロのSEOコンサルタントです。以下のSOP(Step 4)に従って構成案を作成してください。
    
    【SOPルール】
    {sop_rules}
    
    【対策キーワード】: {keyword}
    【競合SERPデータ】: {json.dumps(serp_data, ensure_ascii=False)}
    
    出力は必ずMarkdown形式とし、各H2には [id: h2-01] のようなIDを付与してください。
    """
    
    outline = generate_text(client, llm_choice, system_prompt)
    (run_dir / "04-outline.md").write_text(outline, encoding="utf-8")
    return outline

def step4_generate_sections_and_assemble(client, llm_choice: str, keyword: str, outline: str, run_dir: Path) -> str:
    style_rules = load_prompt_file("writing-style.md")
    data_rules = load_prompt_file("data-integrity.md")
    
    h2_matches = re.findall(r'## (.*?) \[id: (h2-\d+)\]', outline)
    if not h2_matches:
        h2_matches = [(line.replace('## ', ''), f"h2-{i:02d}") for i, line in enumerate(outline.split('\n'), 1) if line.startswith('## ')]

    drafts_dir = run_dir / "05-drafts"
    drafts_dir.mkdir(exist_ok=True)
    full_article = f"# {keyword} のSEO記事\n\n"
    
    for h2_title, h2_id in h2_matches:
        system_prompt = f"""
        あなたはプロのSEOライターです。以下の執筆規約に厳格に従い、指定されたH2セクション「{h2_title}」のみを執筆してください。
        他のH2見出しには絶対に触れないでください。
        
        【執筆スタイル規約】
        {style_rules}
        
        【データ整合性ルール】
        {data_rules}
        
        【全体構成案】
        {outline}
        """
        
        user_prompt = f"H2見出し「{h2_title}」のセクション本文を執筆してください。"
        
        section_content = generate_text(client, llm_choice, system_prompt, user_prompt)
        
        (drafts_dir / f"{h2_id}.md").write_text(section_content, encoding="utf-8")
        full_article += f"## {h2_title}\n{section_content}\n\n"
        
        # APIの無料枠制限（Rate Limit）対策
        time.sleep(3)
    
    (run_dir / "06-final.md").write_text(full_article, encoding="utf-8")
    return full_article