"""Confluence module (modular-monolith style).

This is the SINGLE owner of Confluence-specific mechanics:
- URL normalization + base URL inference
- page id extraction
- keychain secret naming for per-base tokens
- page fetch (HTML -> markdown)

Consumers:
- storage/vectordb.py (Documents ingestion)
- tools/confluence.py (search_confluence tool)
- app_services/settings_helpers.py (token indexing + key naming)

Important: This module must NOT import appcore Runtime to avoid import cycles
(Runtime imports VectorDBManager). Keep it stdlib + storage.secure + optional libs.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from ..storage.secure import get_secret

try:
    from markdownify import markdownify as md
except Exception:
    md = None  # type: ignore


_SECRET_PREFIX = "confluence_token_v1_"


def _ensure_url_has_scheme(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    return s


def _pick_confluence_context_path(path: str) -> str:
    p = (path or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p

    # Atlassian Cloud
    if p == "/wiki" or p.startswith("/wiki/"):
        return "/wiki"

    # Common Server/DC deployments
    if p == "/confluence" or p.startswith("/confluence/"):
        return "/confluence"

    return ""


def normalize_confluence_base_url(raw_base_url: str) -> str:
    """Normalize a Confluence base URL entered by the user."""
    s = _ensure_url_has_scheme(raw_base_url)
    if not s:
        return ""

    u = urlparse(s)
    scheme = (u.scheme or "https").lower()
    host = (u.hostname or "").strip().lower()
    if not host:
        return ""

    netloc = host
    if u.port:
        netloc = f"{host}:{u.port}"

    ctx = _pick_confluence_context_path(u.path)
    return f"{scheme}://{netloc}{ctx}".rstrip("/")


def infer_confluence_base_url_from_page_url(page_url: str) -> str:
    """Infer Confluence base URL from a Confluence URL (page/space/etc.)."""
    s = _ensure_url_has_scheme(page_url)
    if not s:
        return ""

    u = urlparse(s)
    scheme = (u.scheme or "https").lower()
    host = (u.hostname or "").strip().lower()
    if not host:
        return ""

    netloc = host
    if u.port:
        netloc = f"{host}:{u.port}"

    ctx = _pick_confluence_context_path(u.path)
    return f"{scheme}://{netloc}{ctx}".rstrip("/")


def looks_like_confluence_page_url(url: str) -> bool:
    """Heuristic detection for Confluence page URLs.

    We avoid requiring the substring 'confluence' in the host because Atlassian
    Cloud typically doesn't include it.
    """
    s = _ensure_url_has_scheme(url)
    if not s:
        return False

    u = urlparse(s)
    if (u.scheme or "").lower() not in ("http", "https"):
        return False

    path = (u.path or "").lower()
    if "/pages/" in path:
        return True

    q = parse_qs(u.query or "")
    for k in q.keys():
        if str(k).lower() == "pageid":
            return True

    return False


def extract_confluence_page_id(url: str) -> Optional[str]:
    """Best-effort extraction of Confluence page id.

    Supports:
    - /.../pages/<digits>/...
    - ...?pageId=<digits>
    """
    s = _ensure_url_has_scheme(url)
    if not s:
        return None

    u = urlparse(s)

    # Path form
    parts = [p for p in (u.path or "").split("/") if p]
    for i, part in enumerate(parts):
        if part.lower() == "pages" and i + 1 < len(parts):
            cand = parts[i + 1]
            if cand.isdigit():
                return cand

    # Query form
    q = parse_qs(u.query or "")
    for k, v in q.items():
        if str(k).lower() == "pageid" and v:
            cand = str(v[0]).strip()
            if cand.isdigit():
                return cand

    return None


def confluence_token_secret_name(base_url: str) -> str:
    """Return keyring secret name for a Confluence base URL."""
    norm = normalize_confluence_base_url(base_url)
    if not norm:
        norm = "(empty)"
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return f"{_SECRET_PREFIX}{h}"


def get_confluence_token_for_base(base_url: str) -> Optional[str]:
    """Fetch the configured Confluence PAT for a normalized base URL (no legacy fallback)."""
    nb = normalize_confluence_base_url(base_url)
    if not nb:
        return None
    return get_secret(confluence_token_secret_name(nb))


def create_confluence_client(*, base_url: str, token: Optional[str] = None, cloud: bool = True) -> Any:
    """Create an atlassian-python-api Confluence client using the configured token.

    - If token is provided, uses it.
    - Else, loads token from keychain for the base_url.
    """
    try:
        from atlassian import Confluence
    except Exception:
        raise RuntimeError("Confluence library not available. Install: pip install atlassian-python-api")

    nb = normalize_confluence_base_url(base_url)
    if not nb:
        raise ValueError("Invalid Confluence base URL")

    tok = (token or "").strip() if isinstance(token, str) else ""
    if not tok:
        tok = get_confluence_token_for_base(nb) or ""

    if not tok:
        raise RuntimeError(f"No Confluence token found for base URL '{nb}'. Add one in Settings.")

    return Confluence(url=nb, token=tok, cloud=bool(cloud))


def fetch_confluence_page_content(
    *,
    confluence: Any,
    base_url: str,
    page_id: str,
    keep_markdown_format: bool = True,
    attachment_downloader: Optional[Callable[[str, str, str], List[str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a Confluence page and return {title, content, id, url, attachments}.

    attachment_downloader:
      Optional callback: (page_id, title, content_html) -> [downloaded_paths]
      Used by VectorDB ingestion; tools typically pass None.
    """

    if keep_markdown_format and not md:
        raise RuntimeError("markdownify not available. Install: pip install markdownify")

    try:
        page = confluence.get_page_by_id(page_id, expand="body.editor,body.storage,body.view")
        if not isinstance(page, dict):
            return None

        title = page.get("title", "Untitled")

        content_html = ""
        body = page.get("body") if isinstance(page.get("body"), dict) else {}
        if isinstance(body, dict):
            for fmt in ("editor", "storage", "view"):
                block = body.get(fmt)
                if isinstance(block, dict) and block.get("value"):
                    content_html = str(block.get("value") or "")
                    break

        attachments: List[str] = []
        if attachment_downloader and content_html:
            try:
                attachments = attachment_downloader(str(page_id), str(title), str(content_html)) or []
            except Exception:
                attachments = []

        if keep_markdown_format and content_html:
            content = md(content_html, heading_style="ATX")
        else:
            content = content_html

        # Prefer _links.webui if present.
        url = ""
        links = page.get("_links") if isinstance(page.get("_links"), dict) else {}
        webui = links.get("webui") if isinstance(links.get("webui"), str) else ""
        if webui:
            if webui.startswith("http://") or webui.startswith("https://"):
                url = webui
            else:
                url = base_url.rstrip("/") + "/" + webui.lstrip("/")
        else:
            url = f"{base_url.rstrip('/')}/pages/viewpage.action?pageId={page_id}"

        return {
            "title": title,
            "content": content or "",
            "id": str(page_id),
            "url": url,
            "attachments": attachments,
        }

    except Exception:
        return None
