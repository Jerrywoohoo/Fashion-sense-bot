"""Lightweight web search retriever for style trends and dress code context."""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def search_style_context(
    occasion: str,
    location_name: str = "Singapore",
    max_snippets: int = 2,
    timeout_seconds: float = 4.0,
) -> list[str]:
    """Retrieve 2-3 concise trend/dress-code text snippets from the web.

    Keeps snippets under 200 characters each to prevent LLM token burn.
    """
    query = f"{occasion} {location_name} outfit what to wear style trend"
    logger.info("🔍 [Web Search RAG] Searching DuckDuckGo for: '%s'", query)

    # The library supports both modern ``ddgs`` and legacy ``duckduckgo_search`` packages
    ddgs_cls = None
    try:
        from ddgs import DDGS as ddgs_cls
    except ImportError:
        try:
            from duckduckgo_search import DDGS as ddgs_cls
        except ImportError:
            ddgs_cls = None

    if ddgs_cls is not None:
        try:
            results = ddgs_cls().text(query, max_results=max_snippets)
            snippets = [
                re.sub(r"\s+", " ", r.get("body", "")).strip()[:180]
                for r in results
                if r.get("body")
            ]
            if snippets:
                logger.info("🌐 [Web Search RAG] Found %d DDGS trend snippet(s): %s", len(snippets), snippets)
                return snippets[:max_snippets]
        except Exception as exc:
            logger.info("ℹ️ DDGS package query failed (%s), falling back to light HTML scraper...", exc)
    else:
        logger.info("ℹ️ DDGS package not installed, falling back to light HTML scraper...")

    # Fallback to direct DuckDuckGo HTML endpoint via urllib
    try:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout_seconds) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        raw_snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
        cleaned = []
        for s in raw_snippets:
            text = re.sub(r"<[^>]+>", "", s)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 30:
                cleaned.append(text[:180])
            if len(cleaned) >= max_snippets:
                break
        if cleaned:
            logger.info("🌐 [Web Search RAG] Retrieved %d HTML snippet(s): %s", len(cleaned), cleaned)
        return cleaned
    except Exception as exc:
        logger.warning("Web search trend RAG failed (proceeding without web context): %s", exc)
        return []
