"""
fetch_serp.py — seo-growth-navigator Skill の唯一の外部 Web 取得経路

設計:
- Claude 側に HTML/DOM を**渡さない**。本ファイル内で fetch → 抽出 → サニタイズまで完結し、
  JSON のみを出力する(信頼境界)。
- SERP 一覧取得は Google HTML スクレイピング(ユーザー選択)。
  - `--engine http` (既定): httpx ベース。軽量だが Google ボット検知で 0 件になり得る。
  - `--engine playwright`: 実 Chrome (channel="chrome") を駆動。TLS/HTTP2 指紋を本物 Chrome に
    揃え、さらに「ホーム → 検索」の自然な動線でクッキー (NID/CONSENT) を獲得することで
    Google の bot 検知を回避する。失敗時は `--headed` で可視モードに切替可能。
- 本文側 (各 URL の H2/H3 抽出) は httpx + selectolax。
- どの経路でも HTML→URL 抽出関数と sanitize_text() を共用し、信頼境界を一本化。

詳細仕様:
- .claude/skills/seo-growth-navigator/references/serp-fallback.md (CLI/JSONスキーマ)
- .claude/skills/seo-growth-navigator/references/security-model.md (サニタイズ層)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

import httpx
from selectolax.parser import HTMLParser


# --- 既知ペイロード(security-model.md レイヤー2 と同期させること) -------------

_PAYLOAD_SUBSTRINGS: tuple[str, ...] = (
    "ignore previous",
    "ignore above",
    "system:",
    "assistant:",
    "<|im_start|>",
    "<|im_end|>",
    ".env",
    "environment variable",
    "api key",
    "secret_key",
    "powershell",
    "bash -c",
    "curl ",
    "wget ",
)

_PAYLOAD_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"send\s+to[^a-z]{0,20}https?://", re.IGNORECASE),
)

# 不可視文字: ゼロ幅・Bidi 制御
_INVISIBLE_CHARS_RE = re.compile(
    "["
    "​"  # ZERO WIDTH SPACE
    "‌"  # ZERO WIDTH NON-JOINER
    "‍"  # ZERO WIDTH JOINER
    "﻿"  # BOM / ZERO WIDTH NO-BREAK SPACE
    "‎"  # LEFT-TO-RIGHT MARK
    "‏"  # RIGHT-TO-LEFT MARK
    "‪-‮"  # Bidi 制御
    "]"
)

_HEADING_MAX_LEN = 200


# --- データ構造 -----------------------------------------------------------------

@dataclass
class FetchResult:
    rank: int
    url: str
    title: str | None = None
    h2: list[str] = field(default_factory=list)
    h3: list[str] = field(default_factory=list)
    fetch_error: bool = False
    blocked_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "url": self.url,
            "title": self.title,
            "headings": {"h2": self.h2, "h3": self.h3},
            "fetch_error": self.fetch_error,
            "blocked_count": self.blocked_count,
            "notes": self.notes,
        }


# --- サニタイズ -----------------------------------------------------------------

def sanitize_text(text: str) -> str:
    """単一テキストノードのサニタイズ(タグ削除はパース側で実施済み前提)"""
    text = _INVISIBLE_CHARS_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _HEADING_MAX_LEN:
        text = text[:_HEADING_MAX_LEN]
    return text


def count_payload_hits(headings: Iterable[str]) -> int:
    """見出し配列に対してペイロード検知件数を返す"""
    hits = 0
    for h in headings:
        lo = h.lower()
        if any(p in lo for p in _PAYLOAD_SUBSTRINGS):
            hits += 1
            continue
        if any(rx.search(h) for rx in _PAYLOAD_REGEXES):
            hits += 1
    return hits


# --- Google SERP スクレイピング -------------------------------------------------

# Google 検索結果の <a> をどう選別するかはここに集約(セレクタが変わったらここだけ直す)
_GOOGLE_INTERNAL_HOSTS = (
    "google.",
    "youtube.com",
    "googleusercontent.com",
    "googleadservices.",
    "gstatic.com",
    "schema.org",
    "webcache.googleusercontent.com",
    "translate.google.",
    "support.google.",
    "policies.google.",
    "accounts.google.",
    "maps.google.",
)


def _extract_serp_urls_from_google_html(html: str, top_n: int) -> list[str]:
    """Google 検索結果ページ HTML から上位 N 件の URL を抽出"""
    tree = HTMLParser(html)
    urls: list[str] = []
    seen: set[str] = set()

    for a in tree.css("a"):
        href = a.attributes.get("href")
        if not href:
            continue

        candidate: str | None = None
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            q = qs.get("q", [None])[0]
            if q and q.startswith("http"):
                candidate = q
        elif href.startswith("http://") or href.startswith("https://"):
            candidate = href

        if not candidate:
            continue

        host = urlparse(candidate).netloc.lower()
        if not host:
            continue
        if any(internal in host for internal in _GOOGLE_INTERNAL_HOSTS):
            continue
        if candidate in seen:
            continue

        seen.add(candidate)
        urls.append(candidate)
        if len(urls) >= top_n:
            break

    return urls


def fetch_google_serp(
    keyword: str,
    top_n: int,
    user_agent: str,
    timeout: float,
) -> list[str]:
    """Google 検索結果ページを httpx で取得して上位 URL を返す(--engine http)"""
    params = {
        "q": keyword,
        "hl": "ja",
        "gl": "jp",
        "num": str(min(top_n + 5, 20)),
    }
    headers = {
        "User-Agent": user_agent,
        "Accept-Language": "ja,en;q=0.7",
    }
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        resp = client.get("https://www.google.com/search", params=params)
        resp.raise_for_status()
        return _extract_serp_urls_from_google_html(resp.text, top_n)


def fetch_google_serp_playwright(
    keyword: str,
    top_n: int,
    user_agent: str,
    timeout: float,
    headed: bool = False,
) -> list[str]:
    """Playwright で Google SERP HTML を取得し上位 URL を返す。

    URL 抽出は _extract_serp_urls_from_google_html() を共用するため、その後段で
    通る sanitize_text() / count_payload_hits() の信頼境界はそのまま維持される。

    headed=False (既定): ヘッドレス Chromium。軽量だが Google bot 検知でブロックされる場合あり。
    headed=True: 可視 Chromium。実 GPU パイプライン経由で fingerprint がほぼ実ブラウザと
                 同等になり、検知を回避しやすい。Windows のインタラクティブセッションで動作。
    """
    # importlib による遅延 import: playwright 未インストール環境でも
    # --engine http 利用時にエラーにならないようにする。
    try:
        from playwright.sync_api import (  # type: ignore[import-not-found]
            sync_playwright,
            TimeoutError as PWTimeoutError,
        )
    except ImportError as e:
        raise RuntimeError(
            "playwright がインストールされていません。"
            "`mcp_server/.venv/Scripts/python -m pip install playwright` と "
            "`mcp_server/.venv/Scripts/python -m playwright install chromium` を実行してください。"
        ) from e

    from urllib.parse import quote
    import random
    import re as _re

    query = quote(keyword)
    num = min(top_n + 5, 20)
    # pws=0 はパーソナライズ無効化(結果の安定化と "通常 Chrome" 寄りの振る舞いに)
    url = f"https://www.google.com/search?q={query}&hl=ja&gl=jp&num={num}&pws=0"
    timeout_ms = int(timeout * 1000)

    # Stealth init script: searchRankRecorder の手法を踏襲。
    # - navigator.webdriver を undefined に
    # - ChromeDriver/Playwright が埋め込む cdc_* プロパティを削除(Google が検知に使う典型)
    # - plugins / languages / window.chrome の代表的ヘッドレス痕跡を上書き
    stealth_init = (
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;"
        "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;"
        "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;"
        "Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });"
        "Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP','ja','en-US','en'] });"
        "window.chrome = window.chrome || { runtime: {} };"
    )

    # UA から Chrome メジャーバージョンを推定して sec-ch-ua の値を整合させる
    m = _re.search(r"Chrome/(\d+)\.", user_agent)
    chrome_major = m.group(1) if m else "134"
    sec_ch_ua = (
        f'"Chromium";v="{chrome_major}", '
        f'"Google Chrome";v="{chrome_major}", '
        f'"Not-A.Brand";v="99"'
    )
    sec_ch_platform = '"macOS"' if "Macintosh" in user_agent else '"Windows"'

    with sync_playwright() as p:
        # channel="chrome" でシステムにインストール済みの本物 Chrome を駆動する。
        # Playwright 既定の chrome-headless-shell は TLS/HTTP2 指紋が本物 Chrome と異なり
        # Google の bot 検知でブロックされやすいため、これが最重要のステップ。
        # 本物 Chrome が無い環境では Chromium にフォールバック。
        chrome_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--window-size=1366,768",
            "--lang=ja-JP,ja",
        ]
        try:
            browser = p.chromium.launch(
                channel="chrome",
                headless=not headed,
                args=chrome_args,
            )
        except Exception:
            browser = p.chromium.launch(
                headless=not headed,
                args=chrome_args,
            )
        try:
            context = browser.new_context(
                user_agent=user_agent,
                locale="ja-JP",
                viewport={"width": 1366, "height": 768},
                timezone_id="Asia/Tokyo",
                geolocation={"longitude": 139.6917, "latitude": 35.6895},
                permissions=["geolocation"],
                extra_http_headers={
                    # 現代 Chrome は UA 縮減方針で Client Hints を必須化。Google はこれを bot 判定に使う。
                    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,image/apng,*/*;q=0.8"
                    ),
                    "Accept-Encoding": "gzip, deflate, br",
                    "sec-ch-ua": sec_ch_ua,
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": sec_ch_platform,
                },
            )
            context.add_init_script(stealth_init)
            page = context.new_page()
            # ランダム待機で rate-based 判定を回避(searchRankRecorder の手法)
            time.sleep(random.uniform(0.5, 1.5))
            # Plan A 第2段階: ホーム → 検索 の自然な動線を再現してクッキー (NID/CONSENT/1P_JAR) を獲得する。
            # いきなり /search?q=... を叩くと初訪問扱いで /sorry/ に送られやすいが、
            # ホームを 1 回挟むことで Set-Cookie 経由で人間らしいセッションが確立される。
            try:
                page.goto(
                    "https://www.google.com/",
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )
                time.sleep(random.uniform(0.8, 1.5))
            except PWTimeoutError:
                # ホーム取得が失敗しても、検索本体に進んで一応試す(致命ではない)。
                pass
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            time.sleep(random.uniform(0.8, 2.0))
            try:
                page.wait_for_selector("a", timeout=min(5000, timeout_ms))
            except PWTimeoutError:
                pass
            final_url = page.url
            html = page.content()
        finally:
            browser.close()

    if "/sorry/" in final_url or "recaptcha" in html.lower()[:5000]:
        raise RuntimeError(
            f"Google bot 検知でブロックされました (final_url={final_url[:120]})。"
            " しばらく時間を置くか、別IP/別UAを試してください。"
        )

    return _extract_serp_urls_from_google_html(html, top_n)


# --- 各ページの本文取得と H2/H3 抽出 -------------------------------------------

def _strip_noise(tree: HTMLParser) -> None:
    """script/style/noscript/template/iframe ノードを除去"""
    for tag in ("script", "style", "noscript", "template", "iframe"):
        for node in tree.css(tag):
            node.decompose()


def _extract_headings(tree: HTMLParser, selector: str) -> list[str]:
    out: list[str] = []
    for node in tree.css(selector):
        # nav/header/footer/aside 配下の見出しはノイズが多いので除外
        parent = node.parent
        skip = False
        while parent is not None:
            tag = (parent.tag or "").lower()
            if tag in ("nav", "header", "footer", "aside"):
                skip = True
                break
            parent = parent.parent
        if skip:
            continue
        text = sanitize_text(node.text(separator=" "))
        if text:
            out.append(text)
    return out


def fetch_page_headings(
    url: str,
    user_agent: str,
    timeout: float,
) -> tuple[str | None, list[str], list[str], list[str]]:
    """1 ページを取得し (title, h2[], h3[], notes[]) を返す。
    通信/パース失敗時は例外を投げる(呼び出し側で notes に詰める)。
    """
    notes: list[str] = []
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": user_agent,
                "Accept-Language": "ja,en;q=0.7",
            },
        ) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                raise RuntimeError(f"http_{resp.status_code}")
            html = resp.text
    except httpx.TimeoutException:
        notes.append("timeout")
        raise

    tree = HTMLParser(html)
    _strip_noise(tree)

    title_node = tree.css_first("title")
    title = sanitize_text(title_node.text(separator=" ")) if title_node else None
    if title == "":
        title = None

    h2 = _extract_headings(tree, "h2")
    h3 = _extract_headings(tree, "h3")
    return title, h2, h3, notes


# --- メイン ---------------------------------------------------------------------

_DEFAULT_HTTP_UA = "ictGrowthHacker-SerpFetcher/1.0 (+seo-growth-navigator Skill; respects robots; contact via repo)"
# Playwright 経路で Bot UA を使うと Google にブロックされるため、実 Chrome を偽装する。
# 用途は Google SERP の bot 検知回避に限定(本文ページ取得には使わない)。
# 複数 UA をローテーションして単一指紋検知を避ける(searchRankRecorder の手法を踏襲)。
_PLAYWRIGHT_UA_POOL: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
)
_DEFAULT_PLAYWRIGHT_UA = _PLAYWRIGHT_UA_POOL[0]
# 後方互換用エイリアス(従来の `_DEFAULT_UA` を import している外部コードがあった場合に備える)
_DEFAULT_UA = _DEFAULT_HTTP_UA


def _should_exclude(url: str, exclude_hosts: list[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h.lower() in host for h in exclude_hosts)


def run(
    keyword: str,
    top_n: int,
    out_path: Path,
    user_agent: str,
    timeout: float,
    exclude_hosts: list[str],
    run_id: str | None,
    engine: str = "http",
    serp_user_agent: str | None = None,
    headed: bool = False,
) -> int:
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # SERP 取得用 UA: engine ごとに既定が異なる(Bot UA は Playwright 経路で Google にブロックされる)。
    if serp_user_agent is None:
        serp_user_agent = user_agent

    # 1. Google SERP から上位 URL を取得 (engine で経路切替)
    try:
        if engine == "playwright":
            candidate_urls = fetch_google_serp_playwright(
                keyword=keyword,
                top_n=top_n + len(exclude_hosts) + 5,
                user_agent=serp_user_agent,
                timeout=timeout,
                headed=headed,
            )
        else:
            candidate_urls = fetch_google_serp(
                keyword=keyword,
                top_n=top_n + len(exclude_hosts) + 5,
                user_agent=serp_user_agent,
                timeout=timeout,
            )
    except httpx.HTTPError as e:
        print(
            f"[fetch_serp] FATAL: Google SERP取得に失敗 ({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as e:
        # Playwright 未インストール等
        print(
            f"[fetch_serp] FATAL: SERP取得に失敗 (engine={engine}): {e}",
            file=sys.stderr,
        )
        return 2

    # 2. 除外ホストフィルタ
    filtered = [u for u in candidate_urls if not _should_exclude(u, exclude_hosts)]
    target_urls = filtered[:top_n]

    if not target_urls:
        _write_output(out_path, run_id, keyword, fetched_at, top_n, [])
        print(
            f"[fetch_serp] WARN: '{keyword}' で有効なSERP結果が0件でした",
            file=sys.stderr,
        )
        return 0

    # 3. 各 URL を順次取得
    results: list[FetchResult] = []
    for rank, url in enumerate(target_urls, start=1):
        fr = FetchResult(rank=rank, url=url)
        try:
            title, h2, h3, notes = fetch_page_headings(
                url=url,
                user_agent=user_agent,
                timeout=timeout,
            )
            fr.title = title
            fr.h2 = h2
            fr.h3 = h3
            fr.notes.extend(notes)
        except httpx.TimeoutException:
            fr.fetch_error = True
            fr.notes.append("timeout")
        except (httpx.HTTPError, RuntimeError) as e:
            fr.fetch_error = True
            msg = str(e) if isinstance(e, RuntimeError) else type(e).__name__
            fr.notes.append(f"fetch_error:{msg}")
        else:
            # 4. URL 単位ペイロード検知
            total_hits = count_payload_hits(fr.h2) + count_payload_hits(fr.h3)
            if total_hits > 0:
                fr.blocked_count = total_hits
                fr.h2 = []
                fr.h3 = []
                fr.notes.append("injection_suspected")

        results.append(fr)
        time.sleep(0.5)

    _write_output(out_path, run_id, keyword, fetched_at, top_n, results)

    if all(r.fetch_error for r in results):
        print(
            f"[fetch_serp] WARN: 全 {len(results)} 件で取得失敗",
            file=sys.stderr,
        )
        return 3

    return 0


def _write_output(
    out_path: Path,
    run_id: str | None,
    keyword: str,
    fetched_at: str,
    top_n: int,
    results: list[FetchResult],
) -> None:
    payload = {
        "run_id": run_id,
        "keyword": keyword,
        "fetched_at": fetched_at,
        "top_n": top_n,
        "results": [r.to_dict() for r in results],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _infer_run_id(out_path: Path) -> str | None:
    # `.seo/runs/{run_id}/03-serp.json` 形式なら親ディレクトリ名を run_id とする
    parent = out_path.parent
    if parent.parent.name == "runs":
        return parent.name
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SERP 取得 + サニタイズ済み H2/H3 抽出 (seo-growth-navigator Skill 用)"
    )
    parser.add_argument("--keyword", required=True, help="検索キーワード(UTF-8)")
    parser.add_argument("--top-n", type=int, default=8, help="上位 N 件(既定 8、上限 10)")
    parser.add_argument("--out", required=True, help="出力 JSON パス")
    parser.add_argument(
        "--engine",
        choices=["http", "playwright"],
        default="http",
        help=(
            "SERP 取得経路 (既定 http)。playwright はヘッドレス Chromium 経由で "
            "Google の bot 検知を回避する(要 `playwright install chromium`)。"
        ),
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help=(
            "Playwright を可視モード(ヘッドあり)で起動。ヘッドレスが reCAPTCHA で "
            "弾かれる場合のフォールバック。デスクトップ環境でのみ動作。"
        ),
    )
    parser.add_argument(
        "--user-agent",
        default=None,
        help=(
            "HTTP User-Agent。未指定なら engine ごとの既定 "
            "(http→Bot UA / playwright→実 Chrome 偽装) を使う。"
        ),
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP タイムアウト秒")
    parser.add_argument(
        "--exclude-host",
        action="append",
        default=[],
        help="分析対象から除外するホスト(部分一致、複数指定可)",
    )
    parser.add_argument("--run-id", default=None, help="run_id を明示(省略時は出力パスから推定)")
    args = parser.parse_args(argv)

    if args.top_n < 1 or args.top_n > 10:
        print("[fetch_serp] FATAL: --top-n は 1〜10 の範囲", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    run_id = args.run_id or _infer_run_id(out_path)

    # UA 解決:
    # - ページ本文取得側 (user_agent) は従来通り Bot UA 既定で透明性を保つ
    # - SERP 取得側 (serp_user_agent) は engine が playwright のとき実ブラウザ偽装が既定
    if args.user_agent is not None:
        page_ua = args.user_agent
        serp_ua: str | None = args.user_agent
    else:
        page_ua = _DEFAULT_HTTP_UA
        if args.engine == "playwright":
            import random as _random
            serp_ua = _random.choice(_PLAYWRIGHT_UA_POOL)
        else:
            serp_ua = _DEFAULT_HTTP_UA

    return run(
        keyword=args.keyword,
        top_n=args.top_n,
        out_path=out_path,
        user_agent=page_ua,
        timeout=args.timeout,
        exclude_hosts=args.exclude_host,
        run_id=run_id,
        engine=args.engine,
        serp_user_agent=serp_ua,
        headed=args.headed,
    )


if __name__ == "__main__":
    sys.exit(main())
