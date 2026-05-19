"""Confluence tools.

Currently:
- search_confluence: search Confluence pages using CQL.

Tokens are stored per Confluence base URL in the OS keychain (configured via Settings).
This tool never accepts tokens directly.

Input contract (strict):
- query
- url (nullable; when provided, used only to infer which Confluence base to search)
- limit
- content_type

If url is null, the tool searches across *all configured* Confluence base URLs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...appcore.runtime_context import Runtime
from ...services.confluence import (
    create_confluence_client,
    infer_confluence_base_url_from_page_url,
    normalize_confluence_base_url,
    fetch_confluence_page_content,
)


def _escape_cql_string(s: str) -> str:
    # Minimal escaping: backslash + double quote.
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


class SearchConfluenceTool:
    """Search Confluence via CQL across configured base URLs (or a single inferred base)."""

    schema = {
        "type": "function",
        "name": "search_confluence",
        "description": (
            "Search Confluence pages using your configured Confluence base URLs + tokens. "
            "Use this when the user explicitly asks for Confluence, or when the current task clearly needs information likely stored in internal pages such as specs, requirements, or how-tos. "
            "Provide url=null to search across all configured bases, or pass a Confluence URL "
            "(space/page/etc.) to infer which base URL to search."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text."},
                "url": {
                    "type": ["string", "null"],
                    "description": "Optional Confluence URL used only to infer which Confluence base to search. If null, searches all configured bases.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max total results to return (across all bases).",
                    "minimum": 1,
                    "maximum": 50,
                },
                "content_type": {
                    "type": ["string", "null"],
                    "description": "Confluence content type filter (default: page).",
                },
                "survive": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                },
            },
            "required": ["query", "url", "limit", "content_type", "survive"],
            "additionalProperties": False,
        },
    }

    def run(
        self,
        query: str,
        url: Optional[str],
        limit: int,
        content_type: Optional[str],
        survive: Optional[bool] = None,
    ) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"status": "error", "message": "query is required"}

        try:
            lim = int(limit)
        except Exception:
            lim = 10
        lim = max(1, min(50, lim))

        ct = (content_type or "").strip().lower() if content_type else ""
        if not ct:
            ct = "page"

        # Determine base candidates.
        candidates: List[str] = []

        if url:
            inferred = infer_confluence_base_url_from_page_url(str(url))
            if not inferred:
                return {"status": "error", "message": f"Could not infer Confluence base URL from url: {url}"}
            candidates = [inferred]
        else:
            # Search all configured bases.
            cfg = Runtime.get_config_manager()
            try:
                refs = getattr(getattr(cfg.app, "confluence", None), "tokens", None) or []
            except Exception:
                refs = []

            bases: List[str] = []
            if isinstance(refs, list):
                for ref in refs:
                    try:
                        raw_base = ""
                        if isinstance(ref, dict):
                            raw_base = str(ref.get("base_url") or "")
                        else:
                            raw_base = str(getattr(ref, "base_url", "") or "")
                        nb = normalize_confluence_base_url(raw_base)
                        if nb:
                            bases.append(nb)
                    except Exception:
                        continue

            candidates = sorted({b for b in bases if b})

        if not candidates:
            return {
                "status": "error",
                "message": "No Confluence base URLs are configured. Add one in Settings first.",
            }

        # Build CQL.
        # Use siteSearch because it generally searches title/body.
        q_esc = _escape_cql_string(q)
        ct_esc = _escape_cql_string(ct)
        cql = f'siteSearch ~ "{q_esc}" and type = "{ct_esc}"'


        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []

        # Keep tool output bounded.
        # Fetch full page markdown only for the first few results; the rest get metadata-only.
        max_pages_with_content = 3
        max_markdown_chars = 6000

        remaining = lim
        for b in candidates:
            if remaining <= 0:
                break

            try:
                con = create_confluence_client(base_url=b, token=None, cloud=True)
            except Exception as e:
                errors.append({"base_url": b, "error": str(e)})
                continue

            try:
                raw = con.cql(cql, start=0, limit=min(remaining, 50))
            except Exception as e:
                errors.append({"base_url": b, "error": str(e)})
                continue

            if not isinstance(raw, dict):
                errors.append({"base_url": b, "error": "Unexpected response type"})
                continue

            items = raw.get("results")
            if not isinstance(items, list):
                items = []

            for it in items:
                if remaining <= 0:
                    break
                if not isinstance(it, dict):
                    continue

                content_obj = it.get("content") if isinstance(it.get("content"), dict) else {}
                page_id = None
                try:
                    if isinstance(content_obj.get("id"), (str, int)):
                        page_id = str(content_obj.get("id"))
                except Exception:
                    page_id = None

                title = content_obj.get("title") or it.get("title") or ""

                links = content_obj.get("_links") if isinstance(content_obj.get("_links"), dict) else {}
                webui = links.get("webui") or it.get("url") or ""

                full_url = ""
                if isinstance(webui, str) and webui:
                    if webui.startswith("http://") or webui.startswith("https://"):
                        full_url = webui
                    else:
                        full_url = b.rstrip("/") + "/" + webui.lstrip("/")

                row: Dict[str, Any] = {"base_url": b, "title": title, "url": full_url}

                # Enrich with content if possible (bounded).
                if page_id and len([r for r in results if r.get("content_markdown")]) < max_pages_with_content:
                    try:
                        page_data = fetch_confluence_page_content(
                            confluence=con,
                            base_url=b,
                            page_id=page_id,
                            keep_markdown_format=True,
                            attachment_downloader=None,
                        )
                    except Exception as e:
                        page_data = None
                        errors.append({"base_url": b, "error": f"Failed to fetch page content for id={page_id}: {e}"})

                    if page_data and isinstance(page_data.get("content"), str):
                        md_text = page_data.get("content") or ""
                        truncated = len(md_text) > max_markdown_chars
                        row["content_markdown"] = md_text[:max_markdown_chars]
                        row["content_truncated"] = truncated
                        row["content_len"] = len(md_text)
                        # Prefer canonical url from fetch (often better than webui guess)
                        if page_data.get("url"):
                            row["url"] = page_data.get("url")

                results.append(row)
                remaining -= 1

        out = {
            "status": "success",
            "query": q,
            "url": url,
            "content_type": ct,
            "cql": cql,
            "bases_searched": candidates,
            "count": len(results),
            "results": results,
            "errors": errors,
        }
        if survive is False:
            out["__wrap_meta__"] = {"survive": False}
        return out
