from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
from google import genai
from google.genai import types
from openai import OpenAI

import fetch_serp


MODEL_OPTIONS: Dict[str, Dict[str, str]] = {
    "Gemini 3.1 Flash-Lite": {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
    },
    "Gemini 3.5 Flash": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
    },
    "OpenAI GPT-5 mini": {
        "provider": "openai",
        "model": "gpt-5-mini",
    },
}


def get_model_config(llm_choice: str) -> Dict[str, str]:
    if llm_choice not in MODEL_OPTIONS:
        raise ValueError("未対応のモデルです: {0}".format(llm_choice))
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

    def _create_client(self, api_key: str) -> Any:
        if self.provider == "gemini":
            return genai.Client(api_key=api_key)
        if self.provider == "openai":
            return OpenAI(api_key=api_key)
        raise ValueError("未対応のAIプロバイダーです: {0}".format(self.provider))

    def generate(
        self,
        system_prompt: str,
        user_prompt: str = "",
        *,
        use_web_search: bool = False,
        temperature: float = 0.7,
    ) -> str:
        """Analysis / Outline / Originality / Article / Fact Checkの共通入口。"""
        if not system_prompt.strip():
            raise ValueError("System promptが空です。")

        input_text = user_prompt.strip() or "上記の指示に従って結果を出力してください。"

        if self.provider == "gemini":
            config_kwargs: Dict[str, Any] = {
                "system_instruction": system_prompt,
                "temperature": temperature,
            }
            if use_web_search:
                config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
            config = types.GenerateContentConfig(**config_kwargs)
            response = self.client.models.generate_content(
                model=self.model,
                contents=input_text,
                config=config,
            )
            text = getattr(response, "text", None)
            if not text:
                raise RuntimeError("Geminiからテキスト応答を取得できませんでした。")
            return str(text)

        request: Dict[str, Any] = {
            "model": self.model,
            "instructions": system_prompt,
            "input": input_text,
        }
        if use_web_search:
            request["tools"] = [{"type": "web_search"}]
        response = self.client.responses.create(**request)
        text = getattr(response, "output_text", None)
        if not text:
            raise RuntimeError("OpenAIからテキスト応答を取得できませんでした。")
        return str(text)


def create_llm_service(api_key: str, llm_choice: str) -> LLMService:
    return LLMService(api_key=api_key, llm_choice=llm_choice)


def load_prompt_file(filename: str) -> str:
    candidates = [Path("references") / filename, Path(filename)]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def load_sop_step(step_prefix: str) -> str:
    """Return only one current Step section so unrelated stage instructions do not conflict."""
    sop = load_prompt_file("sop.md")
    if not sop:
        return ""
    lines = sop.splitlines()
    start: Optional[int] = None
    for index, line in enumerate(lines):
        if line.startswith("## {0}".format(step_prefix)):
            start = index
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## Step "):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_manifest(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "run.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def update_run_manifest(run_dir: Path, phase: str, **updates: Any) -> Dict[str, Any]:
    """現在の実装で実際に生成する成果物だけをrun.jsonに記録する。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(run_dir)
    manifest.setdefault("run_id", run_dir.name)
    manifest.setdefault("started_at", _utc_now())
    manifest["phase"] = phase
    manifest["updated_at"] = _utc_now()
    manifest.update(updates)
    (run_dir / "run.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def initialize_run(
    run_dir: Path,
    *,
    keyword: str,
    llm_choice: str,
    search_settings: Dict[str, Any],
    content_settings: Optional[Dict[str, Any]] = None,
) -> None:
    """Create or refresh run context without discarding the current phase or artifacts."""
    existing = _read_manifest(run_dir)
    update_run_manifest(
        run_dir,
        str(existing.get("phase") or "setup-ready"),
        keyword=keyword,
        ai={
            "choice": llm_choice,
            "provider": get_model_config(llm_choice)["provider"],
            "model": get_model_config(llm_choice)["model"],
        },
        search_settings=search_settings,
        content_settings=content_settings or {},
        artifacts=existing.get("artifacts", {}),
    )


def save_outline(run_dir: Path, outline: str) -> None:
    path = run_dir / "05-outline.md"
    path.write_text(outline, encoding="utf-8")
    update_run_manifest(
        run_dir,
        "outline-ready",
        artifacts={**_read_manifest(run_dir).get("artifacts", {}), "outline": str(path)},
    )


def save_selected_originality(run_dir: Path, originality: Dict[str, str]) -> None:
    path = run_dir / "07-selected-originality.json"
    path.write_text(json.dumps(originality, ensure_ascii=False, indent=2), encoding="utf-8")
    update_run_manifest(
        run_dir,
        "originality-selected",
        selected_originality=originality,
        artifacts={
            **_read_manifest(run_dir).get("artifacts", {}),
            "selected_originality": str(path),
        },
    )


def save_article(run_dir: Path, article: str) -> None:
    path = run_dir / "09-article.md"
    path.write_text(article, encoding="utf-8")
    update_run_manifest(
        run_dir,
        "article-ready",
        artifacts={**_read_manifest(run_dir).get("artifacts", {}), "article": str(path)},
    )


# ---------------------------------------------------------------------------
# Page heading extraction policy
# ---------------------------------------------------------------------------

_PAGE_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_PAGE_ACCEPT_LANGUAGE: Dict[str, str] = {
    "JP": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "KR": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "US": "en-US,en;q=0.9",
}

# These platforms rarely expose useful article H2/H3 in server-rendered HTML.
# Keep their Brave title/snippet as evidence instead of making a needless page request.
_SNIPPET_ONLY_HOST_SUFFIXES: Tuple[str, ...] = (
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "tiktok.com",
)


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().lstrip("www.")


def _is_snippet_only_platform(url: str) -> bool:
    host = _hostname(url)
    return any(host == suffix or host.endswith("." + suffix) for suffix in _SNIPPET_ONLY_HOST_SUFFIXES)


def _accept_language_for_country(country: str) -> str:
    return _PAGE_ACCEPT_LANGUAGE.get(
        str(country).upper(),
        "en-US,en;q=0.9",
    )


# ---------------------------------------------------------------------------
# Brave Search API
# ---------------------------------------------------------------------------


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return re.sub(r"<[^>]+>", "", str(value)).strip()


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("results", "items", "data"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
        return [value]
    return []


def _first_value(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _source_name(raw: Dict[str, Any]) -> str:
    direct = _first_value(raw, "source", "forum_name", "publisher", "site_name")
    if direct:
        return _clean_text(direct)
    profile = raw.get("profile")
    if isinstance(profile, dict):
        return _clean_text(_first_value(profile, "long_name", "name", "url"))
    meta_url = raw.get("meta_url")
    if isinstance(meta_url, dict):
        return _clean_text(_first_value(meta_url, "hostname", "netloc", "path"))
    return ""


def _normalize_result_items(section: Any, category: str) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for index, raw_value in enumerate(_as_list(section), 1):
        raw: Dict[str, Any]
        if isinstance(raw_value, dict):
            raw = raw_value
        else:
            raw = {"text": raw_value}

        title = _clean_text(_first_value(raw, "title", "question", "name", "query"))
        description = _clean_text(
            _first_value(raw, "description", "answer", "snippet", "text", "long_desc")
        )
        url = _clean_text(_first_value(raw, "url", "link", "source_url"))
        age = _clean_text(
            _first_value(raw, "age", "page_age", "published", "date", "published_time")
        )
        thumbnail = _first_value(raw, "thumbnail", "image")
        if isinstance(thumbnail, dict):
            thumbnail = _first_value(thumbnail, "src", "url", "original")

        extra_snippets = [
            _clean_text(value)
            for value in _as_list(raw.get("extra_snippets"))
            if _clean_text(value)
        ]
        normalized.append(
            {
                "rank": index,
                "category": category,
                "title": title,
                "url": url,
                "snippet": description,
                "extra_snippets": extra_snippets,
                "source": _source_name(raw),
                "age": age,
                "thumbnail": _clean_text(thumbnail),
            }
        )
    return normalized


def _normalize_suggestions(section: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for index, raw_value in enumerate(_as_list(section), 1):
        raw = raw_value if isinstance(raw_value, dict) else {"query": raw_value}
        image = _first_value(raw, "img", "image", "thumbnail")
        if isinstance(image, dict):
            image = _first_value(image, "src", "url", "original")
        normalized.append(
            {
                "rank": index,
                "query": _clean_text(_first_value(raw, "query", "title", "name")),
                "title": _clean_text(_first_value(raw, "title", "query", "name")),
                "description": _clean_text(
                    _first_value(raw, "description", "subtitle", "summary")
                ),
                "image": _clean_text(image),
            }
        )
    return normalized


def _brave_get(
    endpoint: str,
    api_key: str,
    params: Dict[str, Any],
    *,
    label: str,
    location_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if not api_key:
        raise ValueError("Brave Search API Keyを入力してください。")

    headers: Dict[str, str] = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/134.0.0.0 Safari/537.36"
        ),
    }
    if location_headers:
        headers.update(location_headers)

    url = "https://api.search.brave.com{0}".format(endpoint)
    response = httpx.get(url, headers=headers, params=params, timeout=30.0)
    if response.status_code != 200:
        raise RuntimeError(
            "Brave {0} API error {1}: {2}".format(label, response.status_code, response.text)
        )
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Brave {0} API returned a non-object response.".format(label))
    return data


def _web_search_params(
    keyword: str,
    top_n: int,
    *,
    country: str,
    search_lang: str,
    ui_lang: str,
) -> Dict[str, Any]:
    return {
        "q": keyword,
        "country": country,
        "search_lang": search_lang,
        "ui_lang": ui_lang,
        "count": min(max(top_n, 1), 20),
        "offset": 0,
        "safesearch": "moderate",
        "spellcheck": True,
        "text_decorations": False,
        "extra_snippets": True,
        "operators": True,
    }


def _news_search_params(
    keyword: str,
    top_n: int,
    *,
    country: str,
    search_lang: str,
    ui_lang: str,
) -> Dict[str, Any]:
    return {
        "q": keyword,
        "country": country,
        "search_lang": search_lang,
        "ui_lang": ui_lang,
        "count": min(max(top_n, 1), 50),
        "offset": 0,
        "safesearch": "moderate",
        "spellcheck": True,
        "extra_snippets": True,
        "operators": True,
    }


def _video_search_params(
    keyword: str,
    top_n: int,
    *,
    country: str,
    search_lang: str,
    ui_lang: str,
) -> Dict[str, Any]:
    return {
        "q": keyword,
        "country": country,
        "search_lang": search_lang,
        "ui_lang": ui_lang,
        "count": min(max(top_n, 1), 50),
        "offset": 0,
        "safesearch": "moderate",
        "spellcheck": True,
        "operators": True,
    }


def _search_brave(
    keyword: str,
    api_key: str,
    top_n: int,
    *,
    country: str,
    search_lang: str,
    ui_lang: str,
    location_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """用途別エンドポイントを実行し、失敗した任意カテゴリだけエラーとして保持する。"""
    errors: Dict[str, str] = {}
    warnings: Dict[str, str] = {}

    # Request extra Web candidates in the same Brave API call. Top results are
    # still displayed unchanged, while lower-ranked editorial pages can supplement
    # H2/H3 analysis when official, social or protected pages cannot be fetched.
    web_candidate_count = min(max(top_n * 2, top_n + 4), 20)
    web_raw = _brave_get(
        "/res/v1/web/search",
        api_key,
        _web_search_params(
            keyword,
            web_candidate_count,
            country=country,
            search_lang=search_lang,
            ui_lang=ui_lang,
        ),
        label="Web Search",
        location_headers=location_headers,
    )
    web_candidates = _normalize_result_items(web_raw.get("web"), "web")[:web_candidate_count]
    web_items = web_candidates[:top_n]

    discussion_sites: Sequence[Tuple[str, str]] = (
        ("Reddit", "reddit.com"),
        ("Yahoo!知恵袋", "chiebukuro.yahoo.co.jp"),
        ("価格.com掲示板", "bbs.kakaku.com"),
    )
    discussions: List[Dict[str, Any]] = []
    per_site_count = min(max(top_n, 1), 10)
    for source_label, site in discussion_sites:
        try:
            query = "{0} site:{1}".format(keyword, site)
            data = _brave_get(
                "/res/v1/web/search",
                api_key,
                _web_search_params(
                    query,
                    per_site_count,
                    country=country,
                    search_lang=search_lang,
                    ui_lang=ui_lang,
                ),
                label="Discussions ({0})".format(source_label),
                location_headers=location_headers,
            )
            items = _normalize_result_items(data.get("web"), "discussions")
            for item in items:
                item["source"] = source_label
                item["site"] = site
                item["discussion_query"] = query
            discussions.extend(items)
        except Exception as exc:  # Category-level partial failure is displayed in the UI.
            errors["discussions:{0}".format(site)] = str(exc)

    news_items: List[Dict[str, Any]] = []
    try:
        news_raw = _brave_get(
            "/res/v1/news/search",
            api_key,
            _news_search_params(
                keyword,
                top_n,
                country=country,
                search_lang=search_lang,
                ui_lang=ui_lang,
            ),
            label="News Search",
            location_headers=location_headers,
        )
        news_items = _normalize_result_items(news_raw.get("results"), "news")[:top_n]
    except Exception as exc:
        errors["news"] = str(exc)

    video_items: List[Dict[str, Any]] = []
    try:
        videos_raw = _brave_get(
            "/res/v1/videos/search",
            api_key,
            _video_search_params(
                keyword,
                top_n,
                country=country,
                search_lang=search_lang,
                ui_lang=ui_lang,
            ),
            label="Videos Search",
            location_headers=location_headers,
        )
        video_items = _normalize_result_items(videos_raw.get("results"), "videos")[:top_n]
    except Exception as exc:
        errors["videos"] = str(exc)

    # Autosuggest is a separate Brave endpoint. Use only the parameters documented
    # for this endpoint: q, country and count. In particular, do not pass the Web
    # Search `search_lang` value as `lang`, and do not require rich suggestions.
    suggestion_items: List[Dict[str, Any]] = []
    suggestion_meta: Dict[str, Any] = {
        "requested_query": keyword.strip(),
        "requested_country": country,
        "attempts": [],
    }
    base_suggest_params: Dict[str, Any] = {
        "q": keyword.strip(),
        "country": country,
        "count": min(max(top_n, 1), 20),
    }
    try:
        suggest_raw = _brave_get(
            "/res/v1/suggest/search",
            api_key,
            base_suggest_params,
            label="Autosuggest",
        )
        suggestion_items = _normalize_suggestions(suggest_raw.get("results"))
        suggestion_meta["attempts"].append(
            {
                "mode": "country",
                "result_count": len(suggestion_items),
                "response_type": suggest_raw.get("type", ""),
                "response_query": suggest_raw.get("query") or {},
            }
        )

        # Some country-localized queries may legitimately return no suggestions.
        # Retry once without country rather than silently showing a permanent zero.
        if not suggestion_items:
            global_params = {
                "q": keyword.strip(),
                "count": min(max(top_n, 1), 20),
            }
            global_raw = _brave_get(
                "/res/v1/suggest/search",
                api_key,
                global_params,
                label="Autosuggest global fallback",
            )
            suggestion_items = _normalize_suggestions(global_raw.get("results"))
            suggestion_meta["attempts"].append(
                {
                    "mode": "global",
                    "result_count": len(suggestion_items),
                    "response_type": global_raw.get("type", ""),
                    "response_query": global_raw.get("query") or {},
                }
            )
            if suggestion_items:
                warnings["suggestion"] = (
                    "Country-specific Autosuggest returned 0 results, so a global "
                    "Autosuggest request was used."
                )
            else:
                warnings["suggestion"] = (
                    "Brave Autosuggest returned 0 results for both country-specific "
                    "and global requests."
                )
    except Exception as exc:
        errors["suggestion"] = str(exc)
        suggestion_meta["attempts"].append(
            {"mode": "error", "result_count": 0, "error": str(exc)}
        )

    return {
        "web": web_items,
        "web_candidates": web_candidates,
        "discussions": discussions,
        "news": news_items,
        "videos": video_items,
        "suggestion": suggestion_items,
        "suggestion_meta": suggestion_meta,
        "query": web_raw.get("query") or {},
        "errors": errors,
        "warnings": warnings,
    }


def _extract_page_details(
    rank: int,
    item: Dict[str, Any],
    timeout: float = 10.0,
    accept_language: str = "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
) -> Dict[str, Any]:
    """Web結果URLからH2/H3を取得し、インジェクション検査を適用する。

    配信元がアクセスを拒否した場合は無理に回避せず、Braveのタイトル・
    スニペットをAnalysis用の代替根拠として保持する。
    """
    url = item.get("url", "")
    result: Dict[str, Any] = {
        **item,
        "rank": rank,
        "url": url,
        "headings": {"h2": [], "h3": []},
        "fetch_error": False,
        "http_status": None,
        "access_status": "pending",
        "blocked_count": 0,
        "notes": [],
        "eligible_for_analysis": False,
        "snippet_evidence_available": bool(
            item.get("title") or item.get("snippet") or item.get("extra_snippets")
        ),
    }
    if not url:
        result["fetch_error"] = True
        result["access_status"] = "missing_url"
        result["notes"].append("missing_url")
        return result

    if _is_snippet_only_platform(url):
        result["access_status"] = "snippet_only_platform"
        result["notes"].append("snippet_only_platform")
        return result

    try:
        title, h2, h3, notes = fetch_serp.fetch_page_headings(
            url=url,
            user_agent=_PAGE_FETCH_USER_AGENT,
            timeout=timeout,
            accept_language=accept_language,
        )
        result["title"] = title or result.get("title")
        result["headings"] = {"h2": h2, "h3": h3}
        result["notes"].extend(notes)
        payload_hits = fetch_serp.count_payload_hits(h2) + fetch_serp.count_payload_hits(h3)
        if payload_hits:
            result["blocked_count"] = payload_hits
            result["headings"] = {"h2": [], "h3": []}
            result["access_status"] = "security_blocked"
            result["notes"].append("injection_suspected")
        elif h2 or h3:
            result["access_status"] = "headings_ready"
            result["eligible_for_analysis"] = True
        else:
            result["access_status"] = "no_article_headings"
            result["notes"].append("no_article_headings")
    except fetch_serp.PageFetchError as exc:
        result["fetch_error"] = True
        result["http_status"] = exc.status_code
        if exc.status_code in (401, 403, 429):
            result["access_status"] = "publisher_blocked"
            result["notes"].append(
                "publisher_blocked_http_{0}".format(exc.status_code)
            )
        else:
            result["access_status"] = "http_error"
            result["notes"].append("http_{0}".format(exc.status_code))
        if exc.detail:
            result["notes"].append(exc.detail)
    except httpx.TimeoutException:
        result["fetch_error"] = True
        result["access_status"] = "timeout"
        result["notes"].append("timeout")
    except Exception as exc:
        result["fetch_error"] = True
        result["access_status"] = "fetch_error"
        result["notes"].append(
            "fetch_error:{0}:{1}".format(type(exc).__name__, str(exc))
        )
    return result


def _extract_many_page_details(
    ranked_items: Sequence[Tuple[int, Dict[str, Any]]],
    *,
    accept_language: str,
    timeout: float = 10.0,
    max_workers: int = 4,
) -> List[Dict[str, Any]]:
    """複数ページを限定並列で取得し、元の順位順で返す。"""
    if not ranked_items:
        return []
    results: List[Dict[str, Any]] = []
    worker_count = min(max(max_workers, 1), len(ranked_items))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                _extract_page_details,
                rank,
                item,
                timeout,
                accept_language,
            ): rank
            for rank, item in ranked_items
        }
        for future in as_completed(future_map):
            rank = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "rank": rank,
                        "title": "",
                        "url": "",
                        "snippet": "",
                        "headings": {"h2": [], "h3": []},
                        "fetch_error": True,
                        "http_status": None,
                        "access_status": "worker_error",
                        "blocked_count": 0,
                        "notes": [
                            "worker_error:{0}:{1}".format(type(exc).__name__, str(exc))
                        ],
                        "eligible_for_analysis": False,
                        "snippet_evidence_available": False,
                    }
                )
    return sorted(results, key=lambda value: int(value.get("rank") or 0))


def step2_fetch_serp_and_filter(
    keyword: str,
    run_id: str,
    run_dir: Path,
    *,
    provider: str,
    credentials: Dict[str, Any],
    top_n: int = 8,
) -> Dict[str, Any]:
    """Brave Web / Discussions / News / Videos / Suggestionを取得する。"""
    if provider != "brave":
        raise ValueError("未対応のSERPプロバイダーです: {0}".format(provider))

    search_settings = {
        "country": credentials.get("country", "JP"),
        "search_lang": credentials.get("search_lang", "jp"),
        "ui_lang": credentials.get("ui_lang", "ja-JP"),
        "location": credentials.get("location", "Tokyo, Japan"),
        "top_n": top_n,
    }
    location_headers = credentials.get("location_headers") or {}

    brave_data = _search_brave(
        keyword,
        str(credentials.get("api_key", "")),
        top_n,
        country=str(search_settings["country"]),
        search_lang=str(search_settings["search_lang"]),
        ui_lang=str(search_settings["ui_lang"]),
        location_headers=location_headers,
    )
    all_candidates = brave_data.get("web_candidates") or brave_data.get("web", [])
    if not all_candidates:
        raise RuntimeError("Brave Search APIからWeb検索結果を取得できませんでした。")

    accept_language = _accept_language_for_country(str(search_settings["country"]))
    display_candidates = list(all_candidates[:top_n])
    display_ranked = [
        (int(item.get("rank") or index), item)
        for index, item in enumerate(display_candidates, start=1)
    ]
    web_results = _extract_many_page_details(
        display_ranked,
        accept_language=accept_language,
    )
    analysis_web = [
        result
        for result in web_results
        if result.get("eligible_for_analysis") and not result.get("blocked_count")
    ]

    # When top results are protected, social or non-article pages, use lower-ranked
    # editorial pages only as supplemental H2/H3 evidence. The visible top ranking
    # remains unchanged.
    analysis_target = min(max(top_n, 1), 6)
    supplemental_results: List[Dict[str, Any]] = []
    if len(analysis_web) < analysis_target:
        extra_candidates = list(all_candidates[top_n:])
        extra_ranked = [
            (int(item.get("rank") or (top_n + index)), item)
            for index, item in enumerate(extra_candidates, start=1)
        ]
        extracted_extras = _extract_many_page_details(
            extra_ranked,
            accept_language=accept_language,
            timeout=8.0,
        )
        for result in extracted_extras:
            if result.get("eligible_for_analysis") and not result.get("blocked_count"):
                supplemental_results.append(result)
                analysis_web.append(result)
                if len(analysis_web) >= analysis_target:
                    break

    serp_data: Dict[str, Any] = {
        "run_id": run_id,
        "keyword": keyword,
        "provider": provider,
        "search_settings": search_settings,
        "web": web_results,
        "web_analysis_supplement": supplemental_results,
        "results": analysis_web,
        "discussions": brave_data.get("discussions", []),
        "news": brave_data.get("news", []),
        "videos": brave_data.get("videos", []),
        "suggestion": brave_data.get("suggestion", []),
        "suggestion_meta": brave_data.get("suggestion_meta", {}),
        "query": brave_data.get("query", {}),
        "errors": brave_data.get("errors", {}),
        "warnings": brave_data.get("warnings", {}),
        "diagnostics": {
            "web_count": len(web_results),
            "web_heading_count": len(analysis_web),
            "web_top_heading_count": sum(bool(r.get("eligible_for_analysis")) for r in web_results),
            "web_supplement_count": len(supplemental_results),
            "web_fetch_error_count": sum(bool(r.get("fetch_error")) for r in web_results),
            "web_publisher_blocked_count": sum(
                r.get("access_status") == "publisher_blocked" for r in web_results
            ),
            "web_snippet_only_count": sum(
                r.get("access_status") == "snippet_only_platform" for r in web_results
            ),
            "web_blocked_count": sum(bool(r.get("blocked_count")) for r in web_results),
            "discussions_count": len(brave_data.get("discussions", [])),
            "news_count": len(brave_data.get("news", [])),
            "videos_count": len(brave_data.get("videos", [])),
            "suggestion_count": len(brave_data.get("suggestion", [])),
        },
    }

    path = run_dir / "02-serp.json"
    path.write_text(json.dumps(serp_data, ensure_ascii=False, indent=2), encoding="utf-8")
    update_run_manifest(
        run_dir,
        "serp-researched",
        search_settings=search_settings,
        serp=serp_data["diagnostics"],
        artifacts={**_read_manifest(run_dir).get("artifacts", {}), "serp": str(path)},
    )
    return serp_data


# ---------------------------------------------------------------------------
# Evidence preparation and AI analysis
# ---------------------------------------------------------------------------


def _category_lines(items: List[Dict[str, Any]], limit: int = 12) -> List[str]:
    lines: List[str] = []
    for item in items[:limit]:
        title = item.get("title") or "(タイトルなし)"
        snippet = item.get("snippet") or ""
        source = item.get("source") or ""
        url = item.get("url") or ""
        lines.append("- タイトル: {0}".format(title))
        if snippet:
            lines.append("  - スニペット: {0}".format(snippet))
        if source:
            lines.append("  - 出典: {0}".format(source))
        if url:
            lines.append("  - URL: {0}".format(url))
    return lines or ["- 該当結果なし"]


def build_serp_summary(serp_data: Dict[str, Any]) -> str:
    lines: List[str] = ["# Brave SERP Research", "", "## Web：競合分析・構成"]
    for result in serp_data.get("web", []):
        headings = result.get("headings", {})
        lines.extend(
            [
                "",
                "### {0}. {1}".format(
                    result.get("rank"), result.get("title") or "(タイトルなし)"
                ),
                "- URL: {0}".format(result.get("url", "")),
                "- 概要: {0}".format(result.get("snippet", "")),
                "- H2: {0}".format(json.dumps(headings.get("h2", []), ensure_ascii=False)),
                "- H3: {0}".format(json.dumps(headings.get("h3", []), ensure_ascii=False)),
                "- 取得状態: {0}".format(
                    "利用可"
                    if result.get("eligible_for_analysis")
                    else "; ".join(result.get("notes", [])) or "見出しなし"
                ),
            ]
        )

    lines.extend(["", "## Discussions：ユーザーの本音・Pain Point"])
    lines.extend(_category_lines(serp_data.get("discussions", [])))
    lines.extend(["", "## News：鮮度・更新性・最新情報・変更点"])
    lines.extend(_category_lines(serp_data.get("news", [])))
    lines.extend(["", "## Videos：体験・理解促進・手順・比較・実演"])
    lines.extend(_category_lines(serp_data.get("videos", [])))
    lines.extend(["", "## Suggestion：検索候補"])
    suggestions = serp_data.get("suggestion", [])
    if suggestions:
        for item in suggestions[:20]:
            lines.append("- {0}".format(item.get("query") or item.get("title") or ""))
            if item.get("description"):
                lines.append("  - 説明: {0}".format(item["description"]))
    else:
        lines.append("- 該当結果なし")
    return "\n".join(lines)


def analyze_serp(serp_data: Dict[str, Any]) -> str:
    """AI分析前の根拠データをPythonで集計・Markdown化する。"""
    web_results = serp_data.get("web", [])
    analysis_results = serp_data.get("results") or [
        result for result in web_results if result.get("eligible_for_analysis")
    ]
    supplemental_results = serp_data.get("web_analysis_supplement", [])
    h2_counter: Counter[str] = Counter()
    h3_counter: Counter[str] = Counter()

    for result in analysis_results:
        if not result.get("eligible_for_analysis"):
            continue
        headings = result.get("headings", {})
        for heading in headings.get("h2", []):
            normalized = re.sub(r"\s+", " ", str(heading)).strip()
            if normalized:
                h2_counter[normalized] += 1
        for heading in headings.get("h3", []):
            normalized = re.sub(r"\s+", " ", str(heading)).strip()
            if normalized:
                h3_counter[normalized] += 1

    lines: List[str] = [
        "# SERP Analysis Evidence",
        "",
        "## Web evidence",
        "",
        "- Web結果件数: {0}".format(len(web_results)),
        "- 上位結果でのH2/H3取得成功件数: {0}".format(
            sum(bool(r.get("eligible_for_analysis")) for r in web_results)
        ),
        "- 補完ページを含む分析対象件数: {0}".format(len(analysis_results)),
        "- HTTP 403等で配信元に拒否された上位ページ: {0}".format(
            sum(r.get("access_status") == "publisher_blocked" for r in web_results)
        ),
        "",
        "### 頻出H2",
    ]
    lines.extend(
        ["- {0}（{1}ページ）".format(title, count) for title, count in h2_counter.most_common(25)]
        or ["- 抽出できませんでした"]
    )
    lines.extend(["", "### 頻出H3"])
    lines.extend(
        ["- {0}（{1}ページ）".format(title, count) for title, count in h3_counter.most_common(30)]
        or ["- 抽出できませんでした"]
    )

    lines.extend(["", "### ページ別タイトル・スニペット・見出し"])
    for result in web_results[:20]:
        lines.append(
            "- {0}. {1}".format(result.get("rank"), result.get("title") or "(タイトルなし)")
        )
        lines.append("  - スニペット: {0}".format(result.get("snippet", "")))
        if result.get("eligible_for_analysis"):
            headings = result.get("headings", {})
            lines.append(
                "  - H2: {0}".format(json.dumps(headings.get("h2", []), ensure_ascii=False))
            )
            lines.append(
                "  - H3: {0}".format(json.dumps(headings.get("h3", []), ensure_ascii=False))
            )
        else:
            lines.append(
                "  - 見出し取得状態: {0}".format(
                    "; ".join(result.get("notes", [])) or "取得なし"
                )
            )

    if supplemental_results:
        lines.extend(["", "### H2/H3分析の補完ページ"])
        for result in supplemental_results:
            headings = result.get("headings", {})
            lines.append(
                "- {0}. {1}".format(
                    result.get("rank"), result.get("title") or "(タイトルなし)"
                )
            )
            lines.append("  - URL: {0}".format(result.get("url", "")))
            lines.append(
                "  - H2: {0}".format(json.dumps(headings.get("h2", []), ensure_ascii=False))
            )
            lines.append(
                "  - H3: {0}".format(json.dumps(headings.get("h3", []), ensure_ascii=False))
            )

    for heading, key in (
        ("Discussions evidence", "discussions"),
        ("News evidence", "news"),
        ("Videos evidence", "videos"),
    ):
        items = serp_data.get(key, [])
        lines.extend(["", "## {0}（取得 {1}件）".format(heading, len(items))])
        lines.extend(_category_lines(items, limit=20))

    suggestions = serp_data.get("suggestion", [])
    lines.extend(["", "## Suggestion evidence（取得 {0}件）".format(len(suggestions))])
    if suggestions:
        for item in suggestions[:20]:
            query = item.get("query") or item.get("title") or "(候補なし)"
            lines.append("- 検索候補: {0}".format(query))
            if item.get("description"):
                lines.append("  - 説明: {0}".format(item["description"]))
    else:
        lines.append("- 該当結果なし")

    errors = serp_data.get("errors", {})
    warnings = serp_data.get("warnings", {})
    if errors or warnings:
        lines.extend(["", "## Retrieval limitations"])
        for key, message in warnings.items():
            lines.append("- Warning ({0}): {1}".format(key, message))
        for key, message in errors.items():
            lines.append("- Error ({0}): {1}".format(key, message))

    return "\n".join(lines)


def step2_generate_analysis(
    llm: LLMService,
    keyword: str,
    serp_data: Dict[str, Any],
    run_dir: Path,
) -> str:
    analysis_prompt = load_prompt_file("analysis-prompt.md")
    data_rules = load_prompt_file("data-integrity.md")
    if not analysis_prompt.strip():
        raise FileNotFoundError(
            "analysis-prompt.mdが見つかりません。referencesに配置してください。"
        )

    evidence = analyze_serp(serp_data)
    evidence_path = run_dir / "03-analysis-evidence.md"
    evidence_path.write_text(evidence, encoding="utf-8")

    system_prompt = "{0}\n\n# Data Integrity\n{1}".format(analysis_prompt, data_rules)
    user_prompt = """
対策キーワード: {keyword}

以下はPythonで取得・集計したSERP根拠データです。
記載されていない内容を推測で補わず、指定形式で分析してください。

{evidence}
""".format(keyword=keyword, evidence=evidence)
    analysis = llm.generate(system_prompt, user_prompt, temperature=0.3)
    analysis_path = run_dir / "04-analysis.md"
    analysis_path.write_text(analysis, encoding="utf-8")
    update_run_manifest(
        run_dir,
        "analysis-ready",
        artifacts={
            **_read_manifest(run_dir).get("artifacts", {}),
            "analysis_evidence": str(evidence_path),
            "analysis": str(analysis_path),
        },
    )
    return analysis


# ---------------------------------------------------------------------------
# Outline, originality, article and fact-check
# ---------------------------------------------------------------------------


def _ensure_outline_ids(outline: str) -> str:
    """Assign deterministic sequential IDs to every generated H2 section."""
    lines = outline.splitlines()
    next_id = 1
    output: List[str] = []
    for line in lines:
        if re.match(r"^##\s+", line):
            line = re.sub(
                r"\s*\[id:\s*h2-[^\]]+\]\s*$",
                "",
                line,
                flags=re.IGNORECASE,
            ).rstrip()
            line = "{0} [id: h2-{1:02d}]".format(line, next_id)
            next_id += 1
        output.append(line)
    return "\n".join(output).strip()


def step3_generate_outline(
    llm: LLMService,
    keyword: str,
    serp_data: Dict[str, Any],
    run_dir: Path,
    serp_analysis: str = "",
    owned_site_url: str = "",
    cta_url: str = "",
) -> str:
    outline_prompt = load_prompt_file("outline-prompt.md")
    sop_rules = load_sop_step("Step 4")
    data_rules = load_prompt_file("data-integrity.md")
    if not outline_prompt.strip():
        raise FileNotFoundError(
            "outline-prompt.mdが見つかりません。referencesに配置してください。"
        )

    system_prompt = """
{outline_prompt}

# Workflow Rules
{sop_rules}

# Data Integrity
{data_rules}
""".format(
        outline_prompt=outline_prompt,
        sop_rules=sop_rules,
        data_rules=data_rules,
    )
    user_prompt = """
対策キーワード:
{keyword}

SERP横断分析:
{analysis}

SERP根拠の要約:
{summary}

Owned Site URL:
{owned_site_url}

CTA URL:
{cta_url}

Owned Site URLは記事内で自然に案内する対象です。ただし、URL文字列だけからサービス内容、実績、価格、機能を推測しないでください。
CTA URLが「(未入力)」の場合は、リンク付きCTAを構成に入れないでください。
""".format(
        keyword=keyword,
        analysis=serp_analysis,
        summary=build_serp_summary(serp_data),
        owned_site_url=owned_site_url or "(未入力)",
        cta_url=cta_url or "(未入力)",
    )
    outline = _ensure_outline_ids(llm.generate(system_prompt, user_prompt, temperature=0.5))
    if not re.search(r"^##\s+", outline, flags=re.MULTILINE):
        raise RuntimeError("構成案にH2見出しがありません。")
    save_outline(run_dir, outline)
    return outline


def _parse_json_array(raw: str) -> List[Any]:
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", stripped, flags=re.DOTALL)
        if not match:
            raise RuntimeError("AI応答からJSON配列を抽出できませんでした。")
        value = json.loads(match.group(0))
    if not isinstance(value, list):
        raise RuntimeError("AI応答がJSON配列ではありません。")
    return value


def step4_propose_originality(
    llm: LLMService,
    keyword: str,
    serp_data: Dict[str, Any],
    outline: str,
    run_dir: Path,
    serp_analysis: str = "",
    owned_site_url: str = "",
) -> List[Dict[str, str]]:
    system_prompt = load_prompt_file("originality-prompt.md")
    data_rules = load_prompt_file("data-integrity.md")
    if not system_prompt.strip():
        raise FileNotFoundError(
            "originality-prompt.mdが見つかりません。referencesに配置してください。"
        )
    user_prompt = """
対策キーワード: {keyword}

SERP横断分析:
{analysis}

競合SERP根拠:
{summary}

現在の構成案:
{outline}

Owned Site URL:
{owned_site_url}

Owned Site URLは独自性の方向性を考える対象ですが、URLだけを根拠にサービス内容、実績、価格、機能を推測しないでください。

データ整合性ルール:
{data_rules}
""".format(
        keyword=keyword,
        analysis=serp_analysis,
        summary=build_serp_summary(serp_data),
        outline=outline,
        owned_site_url=owned_site_url or "(未入力)",
        data_rules=data_rules,
    )
    parsed = _parse_json_array(llm.generate(system_prompt, user_prompt, temperature=0.6))
    proposals: List[Dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        proposal = {
            "title": str(item.get("title", "")).strip(),
            "description": str(item.get("description", "")).strip(),
            "placement": str(item.get("placement", "")).strip(),
        }
        if all(proposal.values()):
            proposals.append(proposal)
    if len(proposals) < 3:
        raise RuntimeError("独自性提案が有効な形式で3件生成されませんでした。")
    proposals = proposals[:3]

    path = run_dir / "06-originality-proposals.json"
    path.write_text(json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8")
    update_run_manifest(
        run_dir,
        "originality-proposed",
        artifacts={
            **_read_manifest(run_dir).get("artifacts", {}),
            "originality_proposals": str(path),
        },
    )
    return proposals


def _parse_outline_sections(outline: str) -> List[Tuple[str, str, str]]:
    sections: List[Tuple[str, str, str]] = []
    matches = list(re.finditer(r"^##\s+(.+)$", outline, flags=re.MULTILINE))
    for index, match in enumerate(matches):
        heading_line = match.group(1).strip()
        id_match = re.search(r"\[id:\s*(h2-\d+)\]", heading_line, flags=re.IGNORECASE)
        h2_id = id_match.group(1).lower() if id_match else "h2-{0:02d}".format(index + 1)
        title = re.sub(r"\s*\[id:\s*h2-\d+\]\s*$", "", heading_line, flags=re.IGNORECASE)
        title = re.sub(r"^H2[-\s]*\d+\s*[:：]\s*", "", title, flags=re.IGNORECASE).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(outline)
        block = outline[match.start():end].strip()
        sections.append((title, h2_id, block))
    return sections


def _extract_outline_field(outline: str, labels: Sequence[str]) -> str:
    """Extract a one-line metadata value from the approved outline."""
    for label in labels:
        escaped = re.escape(label)
        patterns = (
            r"^-\s*{0}\s*[:：]\s*(.+)$".format(escaped),
            r"^\*\*{0}\*\*\s*[:：]\s*(.+)$".format(escaped),
            r"^{0}\s*[:：]\s*(.+)$".format(escaped),
        )
        for pattern in patterns:
            match = re.search(pattern, outline, flags=re.MULTILINE | re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
    return ""


def _extract_article_title(outline: str, keyword: str) -> str:
    h1 = _extract_outline_field(outline, ("H1",))
    if h1:
        return h1

    for match in re.finditer(r"^#\s+(.+)$", outline, flags=re.MULTILINE):
        title = match.group(1).strip()
        if "構成案" not in title:
            return title
    return "{0}のSEO記事".format(keyword)


def _extract_meta_title(outline: str, article_title: str) -> str:
    return _extract_outline_field(
        outline,
        ("Meta Title", "SEO Title", "Title", "メタタイトル"),
    ) or article_title


def _extract_meta_description(outline: str, keyword: str) -> str:
    description = _extract_outline_field(
        outline,
        ("Meta Description", "Description", "メタディスクリプション"),
    )
    if description:
        return description
    return "{0}について、検索意図に沿って要点、比較、注意点、具体的な進め方を解説します。".format(
        keyword
    )


def _build_article_frontmatter(title: str, description: str) -> str:
    """Build YAML-compatible front matter using JSON-escaped scalar values."""
    return "---\ntitle: {0}\ndescription: {1}\n---\n\n".format(
        json.dumps(title, ensure_ascii=False),
        json.dumps(description, ensure_ascii=False),
    )


def _extract_key_takeaways(outline: str) -> List[str]:
    """Extract the Key Takeaways block designed by the outline prompt."""
    lines = outline.splitlines()
    start: Optional[int] = None
    for index, line in enumerate(lines):
        normalized = re.sub(r"[>*#\s]", "", line).lower()
        if normalized.startswith("keytakeaways"):
            start = index + 1
            break
    if start is None:
        return []

    takeaways: List[str] = []
    for line in lines[start:]:
        if re.match(r"^##\s+", line):
            break
        match = re.match(r"^\s*>?\s*[-*]\s+(.+?)\s*$", line)
        if match:
            takeaways.append(match.group(1).strip())
        elif takeaways and line.strip() and not line.lstrip().startswith(">"):
            break
    return takeaways[:5]


def _find_owned_site_target(
    sections: Sequence[Tuple[str, str, str]], owned_site_url: str
) -> int:
    if not owned_site_url:
        return -1

    explicit: List[int] = []
    likely: List[int] = []
    for index, (title, _h2_id, block) in enumerate(sections):
        role_match = re.search(
            r"owned site role\s*[:：]\s*([^\n]+)", block, re.IGNORECASE
        )
        if role_match:
            role_value = role_match.group(1).strip().lower()
            if role_value not in ("なし", "none", "不要", "n/a", "na"):
                explicit.append(index)
        title_lower = title.lower()
        if any(
            token in title_lower
            for token in ("公式", "サービス", "相談", "支援", "次の行動", "詳細", "owned site")
        ):
            likely.append(index)

    if explicit:
        return explicit[-1]
    if likely:
        return likely[-1]
    for index in range(len(sections) - 1, -1, -1):
        title = sections[index][0]
        if "faq" not in title.lower() and "よくある質問" not in title:
            return index
    return len(sections) - 1


def _find_cta_target(
    sections: Sequence[Tuple[str, str, str]], cta_url: str
) -> int:
    if not cta_url:
        return -1

    explicit: List[int] = []
    owned_site_sections: List[int] = []
    summary_sections: List[int] = []
    for index, (title, _h2_id, block) in enumerate(sections):
        block_lower = block.lower()
        title_lower = title.lower()
        cta_match = re.search(
            r"cta(?: placement)?\s*[:：]\s*([^\n]+)", block, re.IGNORECASE
        )
        if cta_match:
            cta_value = cta_match.group(1).strip().lower()
            if cta_value not in ("なし", "none", "不要", "n/a", "na"):
                explicit.append(index)
        if "owned site role" in block_lower and not re.search(
            r"owned site role\s*[:：]\s*(?:なし|none|不要)", block, re.IGNORECASE
        ):
            owned_site_sections.append(index)
        if any(token in title_lower for token in ("まとめ", "結論", "next action", "次の行動", "相談", "サービス")):
            summary_sections.append(index)

    if explicit:
        return explicit[-1]
    if owned_site_sections:
        return owned_site_sections[-1]
    if summary_sections:
        return summary_sections[-1]
    for index in range(len(sections) - 1, -1, -1):
        if "faq" not in sections[index][0].lower() and "よくある質問" not in sections[index][0]:
            return index
    return len(sections) - 1


def _find_originality_target(
    sections: Sequence[Tuple[str, str, str]], originality: Dict[str, str]
) -> int:
    placement = originality.get("placement", "").lower()
    for index, (title, h2_id, _block) in enumerate(sections):
        if h2_id.lower() in placement or title.lower() in placement:
            return index
    return 0


def _strip_duplicate_heading(section: str, title: str) -> str:
    lines = section.strip().splitlines()
    if lines and re.match(r"^##\s+", lines[0]):
        candidate = re.sub(r"^##\s+", "", lines[0]).strip()
        if candidate.lower() == title.lower() or title.lower() in candidate.lower():
            lines = lines[1:]
    return "\n".join(lines).strip()


def step5_generate_sections_and_assemble(
    llm: LLMService,
    keyword: str,
    outline: str,
    originality: Dict[str, str],
    run_dir: Path,
    serp_analysis: str = "",
    owned_site_url: str = "",
    cta_url: str = "",
) -> str:
    article_prompt = load_prompt_file("article-prompt.md")
    style_rules = load_prompt_file("writing-style.md")
    data_rules = load_prompt_file("data-integrity.md")
    sop_rules = load_sop_step("Step 6")
    if not article_prompt.strip():
        raise FileNotFoundError(
            "article-prompt.mdが見つかりません。referencesに配置してください。"
        )

    sections = _parse_outline_sections(outline)
    if not sections:
        raise RuntimeError("構成案からH2セクションを取得できませんでした。")

    drafts_dir = run_dir / "08-drafts"
    drafts_dir.mkdir(exist_ok=True)
    article_title = _extract_article_title(outline, keyword)
    meta_title = _extract_meta_title(outline, article_title)
    meta_description = _extract_meta_description(outline, keyword)
    full_article = _build_article_frontmatter(meta_title, meta_description)
    full_article += "# {0}\n\n".format(article_title)
    key_takeaways = _extract_key_takeaways(outline)
    if key_takeaways:
        full_article += "> **Key Takeaways**\n"
        for takeaway in key_takeaways:
            full_article += "> - {0}\n".format(takeaway)
        full_article += "\n"
    target_index = _find_originality_target(sections, originality)
    originality_core = {
        field: str(originality.get(field, "")).strip()
        for field in ("title", "description", "placement")
    }
    additional_information = str(
        originality.get("additional_information", "")
    ).strip()
    owned_site_target_index = _find_owned_site_target(sections, owned_site_url)
    cta_target_index = _find_cta_target(sections, cta_url)

    for index, (h2_title, h2_id, outline_block) in enumerate(sections):
        if index == target_index:
            originality_instruction = (
                "このセクションは選択された独自要素の挿入先です。次の内容を自然に一度だけ反映してください。\n"
                + json.dumps(originality_core, ensure_ascii=False)
            )
            section_additional_information = additional_information
            if section_additional_information:
                originality_instruction += (
                    "\nユーザー追加情報欄の内容も、独自要素を具体化する補足として反映してください。"
                    "ただし、未検証の主張を客観的事実として断定しないでください。"
                )
        else:
            originality_instruction = (
                "選択された独自要素とユーザー追加情報は別セクションで扱うため、"
                "このセクションでは重複して書かないでください。"
            )
            section_additional_information = ""

        same_destination = bool(
            owned_site_url
            and cta_url
            and owned_site_url.rstrip("/") == cta_url.rstrip("/")
            and owned_site_target_index == cta_target_index
        )
        if owned_site_url and index == owned_site_target_index:
            if same_destination:
                owned_site_instruction = (
                    "このセクションはOwned SiteとCTAの共通挿入先です。CTAリンク1回で両方の役割を満たし、"
                    "同じURLへのリンクを重複させないでください。"
                )
            else:
                owned_site_instruction = (
                    "このセクションはOwned Site URLの案内先です。構成案のOwned Site Roleに沿い、"
                    "読者の課題とつながる具体的なアンカーテキストでMarkdownリンクを1回だけ入れてください。"
                )
        elif owned_site_url:
            owned_site_instruction = (
                "Owned Site URLは別セクションで案内するため、このセクションではURLや案内を重複させないでください。"
            )
        else:
            owned_site_instruction = "Owned Site URLは未入力です。リンクや仮URLを作らないでください。"

        if cta_url and index == cta_target_index:
            if same_destination:
                cta_instruction = (
                    "このセクションはCTAの挿入先です。Owned Site案内と共通のMarkdownリンクを1回だけ入れてください。"
                    "過度な煽りや、根拠のない成果保証は禁止です。"
                )
            else:
                cta_instruction = (
                    "このセクションはCTAの挿入先です。文脈に合う具体的なアンカーテキストで、"
                    "CTA URLへのMarkdownリンクを1回だけ入れてください。過度な煽りや、根拠のない成果保証は禁止です。"
                )
        elif cta_url:
            cta_instruction = "CTAは別セクションで扱うため、このセクションにCTA URLや重複CTAを入れないでください。"
        else:
            cta_instruction = "CTA URLは未入力です。リンク、仮URL、CTAプレースホルダーを作らないでください。"

        system_prompt = """
# Article Generation
{article_prompt}

# Writing Style
{style_rules}

# Data Integrity
{data_rules}

# Relevant SOP
{sop_rules}
""".format(
            article_prompt=article_prompt,
            style_rules=style_rules,
            data_rules=data_rules,
            sop_rules=sop_rules,
        )
        user_prompt = """
対策キーワード: {keyword}

SERP分析:
{analysis}

全体構成案:
{outline}

今回担当する構成ブロック:
{outline_block}

独自性の扱い:
{originality_instruction}

ユーザー追加情報:
{additional_information}

追加情報の扱い:
- 追加情報は、選択された独自性を具体化するためのユーザー提供コンテキストです。
- 意見や主観は、客観的な事実として断定せず、見解・判断軸として自然に反映してください。
- URLだけからリンク先の内容、実績、機能、価格、調査結果を推測しないでください。
- 追加情報が空の場合は、存在しない情報を補わないでください。

Owned Site URL:
{owned_site_url}

CTA URL:
{cta_url}

Owned Siteの扱い:
- Owned Site URLを、記事の読者課題と自然につながる案内先として扱ってください。
- URLだけからサービス内容、価格、機能、実績、対応範囲を推測しないでください。
- 構成ブロックでOwned Siteの役割が指定された場合のみ、その役割を具体化してください。
- {owned_site_instruction}

CTAの扱い:
{cta_instruction}

H2見出し「{h2_title}」の本文だけを執筆してください。
""".format(
            keyword=keyword,
            analysis=serp_analysis,
            outline=outline,
            outline_block=outline_block,
            originality_instruction=originality_instruction,
            additional_information=section_additional_information or "(このセクションでは使用しない)",
            owned_site_url=owned_site_url or "(未入力)",
            cta_url=cta_url or "(未入力)",
            owned_site_instruction=owned_site_instruction,
            cta_instruction=cta_instruction,
            h2_title=h2_title,
        )
        section = _strip_duplicate_heading(
            llm.generate(system_prompt, user_prompt, temperature=0.7), h2_title
        )
        if not section:
            raise RuntimeError("{0}の本文が空でした。".format(h2_id))
        (drafts_dir / "{0}.md".format(h2_id)).write_text(section, encoding="utf-8")
        full_article += "## {0}\n\n{1}\n\n".format(h2_title, section)
        time.sleep(0.5)

    article_path = run_dir / "09-article.md"
    article_path.write_text(full_article, encoding="utf-8")
    update_run_manifest(
        run_dir,
        "article-ready",
        artifacts={
            **_read_manifest(run_dir).get("artifacts", {}),
            "drafts": str(drafts_dir),
            "article": str(article_path),
        },
    )
    return full_article


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_file(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_json_response(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI response did not contain a JSON object.")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("AI response JSON was not an object.")
    return value


def _normalize_extracted_facts(value: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_facts = value.get("facts")
    if not isinstance(raw_facts, list):
        raise ValueError("Fact extraction response must contain a facts array.")

    facts: List[Dict[str, Any]] = []
    seen: set = set()
    for raw in raw_facts:
        if not isinstance(raw, dict):
            continue
        fact = _clean_text(raw.get("fact"))
        if not fact:
            continue
        dedupe_key = re.sub(r"\s+", " ", fact).strip().lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        facts.append(
            {
                "id": "F{0:03d}".format(len(facts) + 1),
                "fact": fact,
                "context": _clean_text(raw.get("context")),
                "search_query": _clean_text(raw.get("search_query")) or fact,
                "time_sensitive": bool(raw.get("time_sensitive", False)),
            }
        )
    return facts


def _source_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _search_fact_evidence(
    fact: Dict[str, Any],
    brave_api_key: str,
    *,
    country: str,
    search_lang: str,
    ui_lang: str,
    location_headers: Optional[Dict[str, str]] = None,
    result_limit: int = 8,
) -> Dict[str, Any]:
    query = str(fact.get("search_query") or fact.get("fact") or "").strip()
    if not query:
        return dict(fact, sources=[], search_error="Empty search query.")

    raw = _brave_get(
        "/res/v1/web/search",
        brave_api_key,
        _web_search_params(
            query,
            result_limit,
            country=country,
            search_lang=search_lang,
            ui_lang=ui_lang,
        ),
        label="Fact Check Web Search ({0})".format(fact.get("id", "fact")),
        location_headers=location_headers,
    )
    candidates = _normalize_result_items(raw.get("web"), "fact_check")

    sources: List[Dict[str, Any]] = []
    seen_hosts: set = set()
    seen_urls: set = set()
    for item in candidates:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        normalized_url = url.rstrip("/")
        host = _source_host(url)
        if normalized_url in seen_urls:
            continue
        if host and host in seen_hosts:
            continue
        seen_urls.add(normalized_url)
        if host:
            seen_hosts.add(host)
        snippets = [str(item.get("snippet") or "").strip()]
        snippets.extend(
            str(value).strip()
            for value in item.get("extra_snippets", [])
            if str(value).strip()
        )
        sources.append(
            {
                "title": str(item.get("title") or host or url),
                "url": url,
                "host": host,
                "snippet": " ".join(value for value in snippets if value),
                "age": str(item.get("age") or ""),
            }
        )
        if len(sources) >= 6:
            break

    result = dict(fact)
    result.update(
        {
            "sources": sources,
            "search_error": "",
            "brave_query": raw.get("query") or {},
        }
    )
    return result


def _factcheck_batch_prompt(
    factcheck_prompt: str, batch: List[Dict[str, Any]]
) -> Tuple[str, str]:
    schema_instruction = """

## Evidence-based batch execution rules

The web research has already been performed with Brave Search API. Do not use or request another web-search tool.
Evaluate only the facts and evidence supplied in the user message.

For each fact:
- Separate what is supported, contradicted, missing, outdated, or context-dependent.
- Aim to use at least three independent trustworthy sources when the supplied evidence permits.
- Never invent a source, URL, quotation, statistic, publication date, or source conclusion.
- If fewer than three independent sources are available, state that limitation and use `Needs Double-Checking` unless the evidence is otherwise decisive.
- Source URLs in the output must be copied exactly from the supplied evidence.

Return ONLY one valid JSON object in this schema:
{
  "results": [
    {
      "id": "F001",
      "fact": "the fact exactly as evaluated",
      "rating": "True | Minor Errors | Needs Double-Checking | False",
      "evidence_summary": "concise reasoning",
      "source_evaluation": "expertise, reliability, bias and independence assessment",
      "context_and_timeliness": "missing context, date sensitivity, or currentness",
      "sources": [
        {"title": "source title", "url": "exact supplied URL"}
      ],
      "recommended_correction": "empty string when no correction is needed"
    }
  ]
}
"""
    user_payload = {
        "facts_with_brave_evidence": batch,
        "required_result_count": len(batch),
    }
    return factcheck_prompt + schema_instruction, json.dumps(
        user_payload, ensure_ascii=False, indent=2
    )


def _normalize_batch_results(
    value: Dict[str, Any], batch: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    raw_results = value.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("Fact-check batch response must contain a results array.")

    evidence_by_id = {str(item.get("id")): item for item in batch}
    parsed_by_id: Dict[str, Dict[str, Any]] = {}
    allowed_ratings = {
        "True",
        "Minor Errors",
        "Needs Double-Checking",
        "False",
    }
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        fact_id = str(raw.get("id") or "").strip()
        if fact_id not in evidence_by_id:
            continue
        evidence = evidence_by_id[fact_id]
        allowed_sources = {
            str(source.get("url") or "").rstrip("/"): source
            for source in evidence.get("sources", [])
            if str(source.get("url") or "").strip()
        }
        sources: List[Dict[str, str]] = []
        used_hosts: set = set()
        raw_sources = raw.get("sources") if isinstance(raw.get("sources"), list) else []
        for source in raw_sources:
            if not isinstance(source, dict):
                continue
            candidate_url = str(source.get("url") or "").strip()
            matched = allowed_sources.get(candidate_url.rstrip("/"))
            if not matched:
                continue
            host = _source_host(candidate_url)
            if host and host in used_hosts:
                continue
            if host:
                used_hosts.add(host)
            sources.append(
                {
                    "title": str(matched.get("title") or source.get("title") or candidate_url),
                    "url": str(matched.get("url") or candidate_url),
                }
            )

        if not sources:
            for source in evidence.get("sources", [])[:3]:
                sources.append(
                    {
                        "title": str(source.get("title") or source.get("url") or "Source"),
                        "url": str(source.get("url") or ""),
                    }
                )

        rating = str(raw.get("rating") or "Needs Double-Checking").strip()
        if rating not in allowed_ratings:
            rating = "Needs Double-Checking"
        independent_hosts = {
            _source_host(source.get("url", ""))
            for source in sources
            if source.get("url")
        }
        independent_hosts.discard("")
        if len(independent_hosts) < 3 and rating == "True":
            rating = "Needs Double-Checking"

        parsed_by_id[fact_id] = {
            "id": fact_id,
            "fact": str(evidence.get("fact") or raw.get("fact") or ""),
            "rating": rating,
            "evidence_summary": _clean_text(raw.get("evidence_summary")),
            "source_evaluation": _clean_text(raw.get("source_evaluation")),
            "context_and_timeliness": _clean_text(raw.get("context_and_timeliness")),
            "sources": sources,
            "recommended_correction": _clean_text(raw.get("recommended_correction")),
            "search_error": str(evidence.get("search_error") or ""),
        }

    normalized: List[Dict[str, Any]] = []
    for evidence in batch:
        fact_id = str(evidence.get("id"))
        if fact_id in parsed_by_id:
            normalized.append(parsed_by_id[fact_id])
            continue
        normalized.append(
            {
                "id": fact_id,
                "fact": str(evidence.get("fact") or ""),
                "rating": "Needs Double-Checking",
                "evidence_summary": "AI batch response did not include this fact.",
                "source_evaluation": "Insufficient evaluation data.",
                "context_and_timeliness": "Manual review is required.",
                "sources": [
                    {
                        "title": str(source.get("title") or source.get("url") or "Source"),
                        "url": str(source.get("url") or ""),
                    }
                    for source in evidence.get("sources", [])[:3]
                ],
                "recommended_correction": "Verify this claim before publication.",
                "search_error": str(evidence.get("search_error") or ""),
            }
        )
    return normalized


def _markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>").strip()


def _build_factcheck_report(
    results: List[Dict[str, Any]],
    *,
    article_sha256: str,
    extraction_count: int,
) -> str:
    ratings = [str(item.get("rating") or "Needs Double-Checking") for item in results]
    if "False" in ratings:
        overall = "False"
    elif "Needs Double-Checking" in ratings:
        overall = "Needs Double-Checking"
    elif "Minor Errors" in ratings:
        overall = "Minor Errors"
    else:
        overall = "True"

    counts = Counter(ratings)
    source_complete = 0
    for item in results:
        hosts = {
            _source_host(source.get("url", ""))
            for source in item.get("sources", [])
            if source.get("url")
        }
        hosts.discard("")
        if len(hosts) >= 3:
            source_complete += 1

    lines = [
        "# Fact Check Report",
        "",
        "## Summary",
        "",
        "- **Overall reliability:** {0}".format(overall),
        "- **Extracted facts:** {0}".format(extraction_count),
        "- **Facts with 3+ independent sources:** {0}/{1}".format(
            source_complete, len(results)
        ),
        "- **Ratings:** True={0}, Minor Errors={1}, Needs Double-Checking={2}, False={3}".format(
            counts.get("True", 0),
            counts.get("Minor Errors", 0),
            counts.get("Needs Double-Checking", 0),
            counts.get("False", 0),
        ),
        "- **Article SHA-256:** `{0}`".format(article_sha256),
        "",
        "Brave Search API collected the evidence. The selected AI evaluated facts in batches of five without using its native web-search tool.",
        "",
        "## Fact-by-fact results",
        "",
        "| ID | Fact | Rating | Evidence | Source evaluation | Context / timeliness | Sources | Recommended correction |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        source_links: List[str] = []
        for source in item.get("sources", []):
            url = str(source.get("url") or "").strip()
            title = str(source.get("title") or url or "Source").strip()
            if url:
                safe_title = title.replace("[", "\\[").replace("]", "\\]")
                source_links.append("[{0}]({1})".format(safe_title, url))
        evidence_summary = str(item.get("evidence_summary") or "")
        if item.get("search_error"):
            evidence_summary = "{0} Search error: {1}".format(
                evidence_summary, item.get("search_error")
            ).strip()
        lines.append(
            "| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} |".format(
                _markdown_cell(item.get("id")),
                _markdown_cell(item.get("fact")),
                _markdown_cell(item.get("rating")),
                _markdown_cell(evidence_summary),
                _markdown_cell(item.get("source_evaluation")),
                _markdown_cell(item.get("context_and_timeliness")),
                _markdown_cell("<br>".join(source_links) or "(No source returned)"),
                _markdown_cell(item.get("recommended_correction")),
            )
        )
    return "\n".join(lines).strip() + "\n"


def step6_fact_check(
    llm: LLMService,
    article: str,
    run_dir: Path,
    *,
    brave_api_key: str,
    country: str,
    search_lang: str,
    ui_lang: str,
    location_headers: Optional[Dict[str, str]] = None,
    batch_size: int = 5,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> str:
    """Extract facts, collect Brave evidence, and evaluate resumable batches."""
    factcheck_prompt = load_prompt_file("factcheck-prompt.md")
    extraction_prompt = load_prompt_file("fact-extraction-prompt.md")
    if not factcheck_prompt.strip():
        raise FileNotFoundError(
            "factcheck-prompt.mdが見つかりません。referencesに配置してください。"
        )
    if not extraction_prompt.strip():
        raise FileNotFoundError(
            "fact-extraction-prompt.mdが見つかりません。referencesに配置してください。"
        )
    if not brave_api_key:
        raise ValueError("Fact CheckにはBrave Search API Keyが必要です。")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1.")

    article_sha256 = hashlib.sha256(article.encode("utf-8")).hexdigest()
    workspace = run_dir / "10-fact-check"
    state_path = workspace / "state.json"
    context = {
        "article_sha256": article_sha256,
        "provider": llm.provider,
        "model": llm.model,
        "country": country,
        "search_lang": search_lang,
        "ui_lang": ui_lang,
        "batch_size": batch_size,
    }
    previous_state = _load_json_file(state_path, {})
    if previous_state.get("context") != context:
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        _write_json_file(state_path, {"context": context, "status": "started"})
    else:
        workspace.mkdir(parents=True, exist_ok=True)

    facts_path = workspace / "01-facts.json"
    facts_payload = _load_json_file(facts_path, {})
    facts = facts_payload.get("facts") if isinstance(facts_payload, dict) else None
    if not isinstance(facts, list):
        if progress_callback:
            progress_callback(0, 1, "Extracting individually verifiable facts from the article")
        raw_facts = llm.generate(
            extraction_prompt,
            article,
            use_web_search=False,
            temperature=0.1,
        )
        parsed_facts = _parse_json_response(raw_facts)
        facts = _normalize_extracted_facts(parsed_facts)
        _write_json_file(
            facts_path,
            {
                "article_sha256": article_sha256,
                "facts": facts,
                "raw_response": raw_facts,
            },
        )

    if not facts:
        report = (
            "# Fact Check Report\n\n"
            "記事から外部検証可能な事実主張を抽出できませんでした。"
            "意見・提案・CTAだけで構成されている場合は、ファクトチェック対象がないことがあります。\n"
        )
        path = run_dir / "10-fact-check.md"
        path.write_text(report, encoding="utf-8")
        _write_json_file(
            state_path,
            {
                "context": context,
                "status": "complete",
                "fact_count": 0,
                "batch_count": 0,
                "report": str(path),
            },
        )
        update_run_manifest(
            run_dir,
            "done",
            fact_check={
                "fact_count": 0,
                "batch_count": 0,
                "search_provider": "brave",
                "ai_provider": llm.provider,
                "ai_model": llm.model,
            },
            artifacts={
                **_read_manifest(run_dir).get("artifacts", {}),
                "fact_check_workspace": str(workspace),
                "fact_check": str(path),
            },
        )
        return report

    batch_size = max(1, int(batch_size))
    batch_count = (len(facts) + batch_size - 1) // batch_size
    total_steps = len(facts) + batch_count
    completed_steps = 0
    if progress_callback:
        progress_callback(completed_steps, total_steps, "Preparing Brave evidence searches")

    evidence_path = workspace / "02-evidence.json"
    evidence_payload = _load_json_file(evidence_path, {})
    evidence_by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(evidence_payload, dict):
        evidence_list = evidence_payload.get("facts")
        if isinstance(evidence_list, list):
            for item in evidence_list:
                if (
                    isinstance(item, dict)
                    and item.get("id")
                    and not str(item.get("search_error") or "").strip()
                ):
                    evidence_by_id[str(item["id"])] = item

    fatal_search_markers = (" API error 401:", " API error 403:", " API error 429:")
    for fact in facts:
        fact_id = str(fact.get("id"))
        if fact_id not in evidence_by_id:
            try:
                evidence_by_id[fact_id] = _search_fact_evidence(
                    fact,
                    brave_api_key,
                    country=country,
                    search_lang=search_lang,
                    ui_lang=ui_lang,
                    location_headers=location_headers,
                )
            except Exception as exc:
                error_text = str(exc)
                failed = dict(fact)
                failed.update({"sources": [], "search_error": error_text})
                evidence_by_id[fact_id] = failed
                ordered_evidence = [
                    evidence_by_id[str(item.get("id"))]
                    for item in facts
                    if str(item.get("id")) in evidence_by_id
                ]
                _write_json_file(
                    evidence_path,
                    {"article_sha256": article_sha256, "facts": ordered_evidence},
                )
                if any(marker in error_text for marker in fatal_search_markers):
                    raise
            ordered_evidence = [
                evidence_by_id[str(item.get("id"))]
                for item in facts
                if str(item.get("id")) in evidence_by_id
            ]
            _write_json_file(
                evidence_path,
                {"article_sha256": article_sha256, "facts": ordered_evidence},
            )
            time.sleep(0.15)
        completed_steps += 1
        if progress_callback:
            progress_callback(
                completed_steps,
                total_steps,
                "Brave evidence {0}/{1}: {2}".format(
                    completed_steps, len(facts), fact_id
                ),
            )

    all_results: List[Dict[str, Any]] = []
    batches_dir = workspace / "03-batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    for batch_index in range(batch_count):
        start = batch_index * batch_size
        fact_batch = facts[start : start + batch_size]
        evidence_batch = [evidence_by_id[str(item.get("id"))] for item in fact_batch]
        batch_path = batches_dir / "batch-{0:03d}.json".format(batch_index + 1)
        saved_batch = _load_json_file(batch_path, {})
        batch_results = saved_batch.get("results") if isinstance(saved_batch, dict) else None
        expected_ids = [str(item.get("id")) for item in fact_batch]
        saved_ids = (
            [str(item.get("id")) for item in batch_results]
            if isinstance(batch_results, list)
            else []
        )

        if not isinstance(batch_results, list) or saved_ids != expected_ids:
            system_prompt, user_prompt = _factcheck_batch_prompt(
                factcheck_prompt, evidence_batch
            )
            raw_batch = llm.generate(
                system_prompt,
                user_prompt,
                use_web_search=False,
                temperature=0.1,
            )
            parsed_batch = _parse_json_response(raw_batch)
            batch_results = _normalize_batch_results(parsed_batch, evidence_batch)
            _write_json_file(
                batch_path,
                {
                    "batch": batch_index + 1,
                    "fact_ids": expected_ids,
                    "results": batch_results,
                    "raw_response": raw_batch,
                },
            )
        all_results.extend(batch_results)
        completed_steps += 1
        if progress_callback:
            progress_callback(
                completed_steps,
                total_steps,
                "AI verification batch {0}/{1}".format(batch_index + 1, batch_count),
            )

    report = _build_factcheck_report(
        all_results,
        article_sha256=article_sha256,
        extraction_count=len(facts),
    )
    path = run_dir / "10-fact-check.md"
    path.write_text(report, encoding="utf-8")
    _write_json_file(
        state_path,
        {
            "context": context,
            "status": "complete",
            "fact_count": len(facts),
            "batch_count": batch_count,
            "report": str(path),
        },
    )
    update_run_manifest(
        run_dir,
        "done",
        fact_check={
            "fact_count": len(facts),
            "batch_count": batch_count,
            "search_provider": "brave",
            "ai_provider": llm.provider,
            "ai_model": llm.model,
        },
        artifacts={
            **_read_manifest(run_dir).get("artifacts", {}),
            "fact_check_workspace": str(workspace),
            "fact_check": str(path),
        },
    )
    return report
