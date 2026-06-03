"""arXiv search tool — arxiv PyPI library, ML category filter."""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from alfred.tools.base import AlfredTool, ToolResult

logger = logging.getLogger(__name__)
_DEFAULT_CATS = ["cs.LG", "cs.AI", "cs.CL", "cs.CV", "cs.NE", "cs.RO", "stat.ML"]


class ArxivSearchInput(BaseModel):
    query: str = Field(..., description="Search query for arXiv")
    max_results: int = Field(10, ge=1, le=50)
    categories: list[str] | None = Field(None, description="arXiv category filters")


class ArxivSearchTool(AlfredTool):
    name = "arxiv_search"
    description = (
        "Search arXiv preprints for ML/AI papers. Filters to ML categories by default. "
        "Returns id, title, abstract, authors, published, categories, pdf_url."
    )
    input_schema = ArxivSearchInput

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            parsed = ArxivSearchInput(**input_data)
        except Exception as exc:
            return ToolResult(tool_name=self.name, success=False, data=[], error=f"Invalid input: {exc}")

        categories = parsed.categories or self.config.get("categories", _DEFAULT_CATS)
        max_results = min(parsed.max_results, self.config.get("max_results", 10))

        try:
            import arxiv
            cat_clause = " OR ".join(f"cat:{c}" for c in categories)
            full_query = f"({parsed.query}) AND ({cat_clause})"
            client = arxiv.Client()
            search = arxiv.Search(query=full_query, max_results=max_results,
                                  sort_by=arxiv.SortCriterion.Relevance)
            papers = []
            for r in client.results(search):
                papers.append({
                    "id": r.entry_id,
                    "title": r.title,
                    "abstract": (r.summary or "")[:500],
                    "authors": [str(a) for a in r.authors[:5]],
                    "published": r.published.isoformat() if r.published else "",
                    "categories": r.categories,
                    "pdf_url": r.pdf_url or "",
                    "arxiv_url": r.entry_id,
                })
            sources = [f"{p['title']} — {p['arxiv_url']}" for p in papers]
            return ToolResult(tool_name=self.name, success=True, data=papers, sources=sources)
        except ImportError:
            return ToolResult(tool_name=self.name, success=False, data=[],
                              error="arxiv package not installed. Run: pip install arxiv")
        except Exception as exc:
            logger.warning("ArxivSearch failed for '%s': %s", parsed.query, exc)
            return ToolResult(tool_name=self.name, success=False, data=[],
                              error=f"arXiv search failed: {exc}")