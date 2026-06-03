"""Web search tool — DuckDuckGo via duckduckgo-search. No API key."""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from alfred.tools.base import AlfredTool, ToolResult

logger = logging.getLogger(__name__)


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query string")
    max_results: int = Field(10, ge=1, le=20)


class WebSearchTool(AlfredTool):
    name = "web_search"
    description = (
        "Search the web using DuckDuckGo. Returns titles, URLs and snippets. "
        "Use for GitHub repos, blog posts, benchmark leaderboards, recent papers."
    )
    input_schema = WebSearchInput

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            parsed = WebSearchInput(**input_data)
        except Exception as exc:
            return ToolResult(tool_name=self.name, success=False, data=[], error=f"Invalid input: {exc}")

        n = min(parsed.max_results, self.config.get("max_results", 10))
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(parsed.query, max_results=n):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    })
            sources = [f"{r['title']} — {r['url']}" for r in results]
            return ToolResult(tool_name=self.name, success=True, data=results, sources=sources)
        except ImportError:
            return ToolResult(tool_name=self.name, success=False, data=[],
                              error="duckduckgo-search not installed. Run: pip install duckduckgo-search")
        except Exception as exc:
            logger.warning("WebSearch failed for '%s': %s", parsed.query, exc)
            return ToolResult(tool_name=self.name, success=False, data=[],
                              error=f"Web search temporarily unavailable: {exc}")