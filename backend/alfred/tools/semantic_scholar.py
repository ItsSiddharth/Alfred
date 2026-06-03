"""Semantic Scholar tool — search + expand (citation snowball), rate-limit sleep."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from alfred.tools.base import AlfredTool, ToolResult

logger = logging.getLogger(__name__)


class SemanticScholarInput(BaseModel):
    op: Literal["search", "expand"] = Field(..., description="'search' or 'expand'")
    query: str | None = Field(None, description="Search query (op=search)")
    paper_id: str | None = Field(None, description="Paper ID (op=expand)")
    max_results: int = Field(10, ge=1, le=50)


class SemanticScholarTool(AlfredTool):
    name = "semantic_scholar"
    description = (
        "Search Semantic Scholar (214M papers) or expand a paper's citation network. "
        "op=search: returns paperId, title, abstract, year, venue, citationCount, tldr. "
        "op=expand: given a paperId, returns references and citations lists."
    )
    input_schema = SemanticScholarInput

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            parsed = SemanticScholarInput(**input_data)
        except Exception as exc:
            return ToolResult(tool_name=self.name, success=False, data={}, error=f"Invalid input: {exc}")

        sleep_s = float(self.config.get("sleep_seconds", 3.5))
        if parsed.op == "search":
            return await self._search(parsed, sleep_s)
        return await self._expand(parsed, sleep_s)

    async def _search(self, parsed: SemanticScholarInput, sleep_s: float) -> ToolResult:
        if not parsed.query:
            return ToolResult(tool_name=self.name, success=False, data=[], error="query required for op=search")
        try:
            from semanticscholar import SemanticScholar
            sch = SemanticScholar()
            await asyncio.sleep(sleep_s)
            results = sch.search_paper(
                parsed.query, limit=parsed.max_results,
                fields=["paperId", "title", "abstract", "year", "venue",
                        "citationCount", "tldr", "externalIds"],
            )
            papers = []
            for r in results:
                papers.append({
                    "paper_id": r.paperId or "",
                    "title": r.title or "",
                    "abstract": (r.abstract or "")[:500],
                    "year": r.year,
                    "venue": r.venue or "",
                    "citation_count": r.citationCount or 0,
                    "tldr": r.tldr.get("text", "") if r.tldr else "",
                    "url": f"https://www.semanticscholar.org/paper/{r.paperId}" if r.paperId else "",
                })
            sources = [f"{p['title']} — {p['url']}" for p in papers]
            return ToolResult(tool_name=self.name, success=True, data=papers, sources=sources)
        except ImportError:
            return ToolResult(tool_name=self.name, success=False, data=[],
                              error="semanticscholar not installed. Run: pip install semanticscholar")
        except Exception as exc:
            if "429" in str(exc) or "rate" in str(exc).lower():
                await asyncio.sleep(10)
                try:
                    from semanticscholar import SemanticScholar
                    sch = SemanticScholar()
                    results = sch.search_paper(parsed.query, limit=parsed.max_results)
                    papers = [{"paper_id": r.paperId, "title": r.title} for r in results]
                    return ToolResult(tool_name=self.name, success=True, data=papers, sources=[])
                except Exception as exc2:
                    return ToolResult(tool_name=self.name, success=False, data=[], error=str(exc2))
            logger.warning("SemanticScholar search failed: %s", exc)
            return ToolResult(tool_name=self.name, success=False, data=[], error=str(exc))

    async def _expand(self, parsed: SemanticScholarInput, sleep_s: float) -> ToolResult:
        if not parsed.paper_id:
            return ToolResult(tool_name=self.name, success=False, data={}, error="paper_id required for op=expand")
        try:
            from semanticscholar import SemanticScholar
            sch = SemanticScholar()
            await asyncio.sleep(sleep_s)
            paper = sch.get_paper(parsed.paper_id, fields=["paperId", "title", "references", "citations"])

            def _slim(lst: list) -> list[dict]:
                return [{"paper_id": getattr(p, "paperId", "") or "",
                         "title": getattr(p, "title", "") or "",
                         "year": getattr(p, "year", None)} for p in (lst or [])[:20]]

            data = {
                "paper_id": parsed.paper_id,
                "references": _slim(getattr(paper, "references", []) or []),
                "citations": _slim(getattr(paper, "citations", []) or []),
            }
            return ToolResult(tool_name=self.name, success=True, data=data,
                              sources=[f"https://www.semanticscholar.org/paper/{parsed.paper_id}"])
        except ImportError:
            return ToolResult(tool_name=self.name, success=False, data={},
                              error="semanticscholar not installed.")
        except Exception as exc:
            logger.warning("SemanticScholar expand failed for %s: %s", parsed.paper_id, exc)
            return ToolResult(tool_name=self.name, success=False, data={}, error=str(exc))