"""OpenAlex search tool — httpx, abstract reconstruction from inverted index."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from alfred.tools.base import AlfredTool, ToolResult

logger = logging.getLogger(__name__)
_BASE_URL = "https://api.openalex.org/works"
_USER_AGENT = "alfred-agent (mailto:alfred-agent@local)"


class OpenAlexSearchInput(BaseModel):
    query: str = Field(..., description="Full-text search query")
    max_results: int = Field(10, ge=1, le=50)
    filter_type: str | None = Field(None, description="Optional OpenAlex filter string")


def _reconstruct_abstract(inv: dict[str, list[int]] | None) -> str:
    if not inv:
        return ""
    try:
        return " ".join(sorted(inv.keys(), key=lambda w: inv[w][0]))
    except Exception:
        return ""


class OpenAlexSearchTool(AlfredTool):
    name = "openalex_search"
    description = (
        "Search OpenAlex's 250M+ academic works corpus. No API key required. "
        "Returns title, publication_year, cited_by_count, venue, concepts, abstract."
    )
    input_schema = OpenAlexSearchInput

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            parsed = OpenAlexSearchInput(**input_data)
        except Exception as exc:
            return ToolResult(tool_name=self.name, success=False, data=[], error=f"Invalid input: {exc}")

        max_results = min(parsed.max_results, self.config.get("max_results", 10))
        params: dict[str, Any] = {
            "search": parsed.query,
            "per-page": max_results,
            "select": "id,title,publication_year,cited_by_count,primary_location,concepts,abstract_inverted_index,doi",
        }
        if parsed.filter_type:
            params["filter"] = parsed.filter_type

        try:
            async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=20.0) as client:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()

            papers = []
            for w in payload.get("results", []):
                venue = ""
                loc = w.get("primary_location") or {}
                source = loc.get("source") or {}
                if source:
                    venue = source.get("display_name", "")
                concepts = [c.get("display_name", "") for c in (w.get("concepts") or [])[:5]]
                abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))
                papers.append({
                    "id": w.get("id", ""),
                    "title": w.get("title", ""),
                    "publication_year": w.get("publication_year"),
                    "cited_by_count": w.get("cited_by_count", 0),
                    "venue": venue,
                    "concepts": concepts,
                    "abstract": abstract[:500],
                    "doi": w.get("doi", ""),
                    "url": w.get("id", ""),
                })
            sources = [f"{p['title']} ({p['publication_year']}) — {p['url']}" for p in papers]
            return ToolResult(tool_name=self.name, success=True, data=papers, sources=sources)
        except httpx.HTTPStatusError as exc:
            return ToolResult(tool_name=self.name, success=False, data=[],
                              error=f"OpenAlex API error {exc.response.status_code}")
        except Exception as exc:
            logger.warning("OpenAlexSearch failed for '%s': %s", parsed.query, exc)
            return ToolResult(tool_name=self.name, success=False, data=[], error=str(exc))