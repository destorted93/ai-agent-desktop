"""Diff utilities for the filesystem revision store.

Goal: compute stable diffs for a transaction using only the recorded before/after
snapshots (no reliance on current filesystem state).

Design:
- Text diffs use difflib.unified_diff.
- Binary/large files return summaries instead of huge payloads.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

from .fs_revisions import FsRevisionStore


DEFAULT_MAX_FILE_BYTES = 512 * 1024
DEFAULT_MAX_DIFF_LINES = 10_000
DEFAULT_MAX_FILES = 200


def _is_binary_bytes(data: bytes) -> bool:
    # Simple, fast heuristic.
    if not data:
        return False
    head = data[:8192]
    if b"\x00" in head:
        return True
    # Heuristic: if UTF-8 decoding produces many replacement chars, treat as binary.
    try:
        s = head.decode("utf-8", errors="replace")
    except Exception:
        return True
    if not s:
        return False
    bad = s.count("\ufffd")
    return (bad / max(1, len(s))) > 0.02


def _decode_text(data: bytes) -> str:
    # We want diffability, not perfection.
    try:
        return data.decode("utf-8")
    except Exception:
        return data.decode("utf-8", errors="replace")


def _line_diff(
    before_text: str,
    after_text: str,
    *,
    fromfile: str,
    tofile: str,
    max_lines: int,
) -> Tuple[str, int, int, bool]:
    """Return (diff_text, added, removed, truncated)."""
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )

    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))

    truncated = False
    if max_lines and len(diff_lines) > max_lines:
        diff_lines = diff_lines[:max_lines]
        truncated = True

    return "\n".join(diff_lines), added, removed, truncated


def _join_snapshot_path(base: Optional[str], rel: Optional[str]) -> Optional[str]:
    """Join snapshot base path + rel path safely.

    Back-compat: older dir snapshots may store file entries with paths that already
    include the base directory prefix (e.g. rel="sandbox/x/file.txt" instead of "file.txt").
    In that case, avoid doubling it.
    """
    if not isinstance(rel, str) or not rel:
        return None
    if not isinstance(base, str) or not base:
        return rel

    b = base.replace("\\", "/").rstrip("/")
    r = rel.replace("\\", "/").lstrip("/")

    if r == b or r.startswith(b + "/"):
        return r
    return f"{b}/{r}"


def _snapshot_file_map(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map relative file path -> file entry for dir snapshots."""
    files = snapshot.get("files")
    if not isinstance(files, list):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for f in files:
        if not isinstance(f, dict):
            continue
        p = f.get("path")
        if isinstance(p, str) and p:
            out[p] = f
    return out


def _snapshot_kind(snap: Any) -> str:
    return snap.get("kind") if isinstance(snap, dict) else "unknown"


def compute_transaction_diff(
    revision_store: FsRevisionStore,
    txn_id: str,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
    max_files: int = DEFAULT_MAX_FILES,
) -> Dict[str, Any]:
    manifest = revision_store.get_transaction(str(txn_id))
    if not isinstance(manifest, dict):
        return {"status": "error", "message": "Transaction not found"}

    changes = manifest.get("changes")
    if not isinstance(changes, list):
        return {"status": "error", "message": "Transaction manifest missing changes"}

    out_files: List[Dict[str, Any]] = []
    truncated_files = False

    def add_file_entry(entry: Dict[str, Any]) -> None:
        nonlocal truncated_files
        if max_files and len(out_files) >= max_files:
            truncated_files = True
            return
        out_files.append(entry)

    for ch in changes:
        if not isinstance(ch, dict):
            continue

        op = ch.get("op") if isinstance(ch.get("op"), str) else "unknown"
        before = ch.get("before") if isinstance(ch.get("before"), dict) else None
        after = ch.get("after") if isinstance(ch.get("after"), dict) else None

        # If we don't have after yet, we can't diff reliably.
        if after is None:
            add_file_entry(
                {
                    "op": op,
                    "is_binary": False,
                    "too_large": False,
                    "added_lines": 0,
                    "removed_lines": 0,
                    "diff": "",
                    "note": "Missing after snapshot",
                    "path_before": before.get("path") if isinstance(before, dict) else None,
                    "path_after": None,
                }
            )
            continue

        bk = _snapshot_kind(before) if before else "missing"
        ak = _snapshot_kind(after)

        # File-level diff.
        if bk in ("missing", "file") and ak in ("missing", "file"):
            p_before = before.get("path") if isinstance(before, dict) else None
            p_after = after.get("path") if isinstance(after, dict) else None

            blob_before = before.get("blob") if isinstance(before, dict) else None
            blob_after = after.get("blob") if isinstance(after, dict) else None

            data_before = b""
            data_after = b""

            if bk == "file" and isinstance(blob_before, str) and blob_before:
                data_before = revision_store.load_blob(blob_before)
            if ak == "file" and isinstance(blob_after, str) and blob_after:
                data_after = revision_store.load_blob(blob_after)

            # Size guard.
            too_large = False
            if max_file_bytes:
                if len(data_before) > max_file_bytes or len(data_after) > max_file_bytes:
                    too_large = True

            is_binary = _is_binary_bytes(data_before) or _is_binary_bytes(data_after)

            if too_large or is_binary:
                add_file_entry(
                    {
                        "op": op,
                        "path_before": p_before,
                        "path_after": p_after,
                        "is_binary": bool(is_binary),
                        "too_large": bool(too_large),
                        "added_lines": 0,
                        "removed_lines": 0,
                        "diff": "",
                        "before_size": len(data_before),
                        "after_size": len(data_after),
                        "before_blob": blob_before,
                        "after_blob": blob_after,
                    }
                )
                continue

            dtext, add, rem, trunc = _line_diff(
                _decode_text(data_before),
                _decode_text(data_after),
                fromfile=str(p_before or "before"),
                tofile=str(p_after or "after"),
                max_lines=max_diff_lines,
            )

            add_file_entry(
                {
                    "op": op,
                    "path_before": p_before,
                    "path_after": p_after,
                    "is_binary": False,
                    "too_large": False,
                    "added_lines": int(add),
                    "removed_lines": int(rem),
                    "diff": dtext,
                    "truncated": bool(trunc),
                }
            )
            continue

        # Dir-level diff: compare per-file blobs.
        if bk in ("missing", "dir") and ak in ("missing", "dir"):
            base_before = before.get("path") if isinstance(before, dict) else None
            base_after = after.get("path") if isinstance(after, dict) else None

            m_before = _snapshot_file_map(before or {}) if bk == "dir" else {}
            m_after = _snapshot_file_map(after or {}) if ak == "dir" else {}

            all_paths = sorted(set(m_before.keys()) | set(m_after.keys()))

            for rel in all_paths:
                fb = m_before.get(rel)
                fa = m_after.get(rel)

                blob_before = fb.get("blob") if isinstance(fb, dict) else None
                blob_after = fa.get("blob") if isinstance(fa, dict) else None

                data_before = revision_store.load_blob(blob_before) if isinstance(blob_before, str) else b""
                data_after = revision_store.load_blob(blob_after) if isinstance(blob_after, str) else b""

                p_before = _join_snapshot_path(str(base_before) if base_before else None, str(rel) if rel else None) if fb else None
                p_after = _join_snapshot_path(str(base_after) if base_after else None, str(rel) if rel else None) if fa else None

                too_large = False
                if max_file_bytes:
                    if len(data_before) > max_file_bytes or len(data_after) > max_file_bytes:
                        too_large = True

                is_binary = _is_binary_bytes(data_before) or _is_binary_bytes(data_after)

                if too_large or is_binary:
                    add_file_entry(
                        {
                            "op": op,
                            "path_before": p_before,
                            "path_after": p_after,
                            "is_binary": bool(is_binary),
                            "too_large": bool(too_large),
                            "added_lines": 0,
                            "removed_lines": 0,
                            "diff": "",
                            "before_size": len(data_before),
                            "after_size": len(data_after),
                            "before_blob": blob_before,
                            "after_blob": blob_after,
                        }
                    )
                    continue

                dtext, add, rem, trunc = _line_diff(
                    _decode_text(data_before),
                    _decode_text(data_after),
                    fromfile=str(p_before or "before"),
                    tofile=str(p_after or "after"),
                    max_lines=max_diff_lines,
                )

                add_file_entry(
                    {
                        "op": op,
                        "path_before": p_before,
                        "path_after": p_after,
                        "is_binary": False,
                        "too_large": False,
                        "added_lines": int(add),
                        "removed_lines": int(rem),
                        "diff": dtext,
                        "truncated": bool(trunc),
                    }
                )

            continue

        # Fallback: unknown snapshot types.
        add_file_entry(
            {
                "op": op,
                "path_before": before.get("path") if isinstance(before, dict) else None,
                "path_after": after.get("path") if isinstance(after, dict) else None,
                "is_binary": False,
                "too_large": False,
                "added_lines": 0,
                "removed_lines": 0,
                "diff": "",
                "note": f"Unsupported snapshot kinds: {bk} -> {ak}",
            }
        )

    return {
        "status": "success",
        "transaction_id": str(txn_id),
        "tool": manifest.get("tool"),
        "manifest_status": manifest.get("status"),
        "files": out_files,
        "files_truncated": bool(truncated_files),
    }


def compute_transaction_diff_preview(
    revision_store: FsRevisionStore,
    txn_id: str,
    *,
    max_file_bytes: int = 256 * 1024,
    max_files: int = 50,
    max_lines_per_file: int = 20_000,
) -> Dict[str, Any]:
    """Compute a lightweight diff preview (added/removed line counts) for a transaction.

    This is intended for UI badges like +50/-30.
    It does NOT return full diff text.

    Safeguards:
    - skips binary files
    - skips files > max_file_bytes
    - skips files with too many lines
    - truncates file enumeration at max_files
    """
    manifest = revision_store.get_transaction(str(txn_id))
    if not isinstance(manifest, dict):
        return {"status": "error", "message": "Transaction not found"}

    changes = manifest.get("changes")
    if not isinstance(changes, list):
        return {"status": "error", "message": "Transaction manifest missing changes"}

    added_total = 0
    removed_total = 0
    files_considered = 0
    files_truncated = False
    binary_files = 0
    too_large_files = 0
    missing_after = 0

    def consider_one_file(b: bytes, a: bytes) -> Tuple[int, int, bool, bool, bool]:
        """Return (added, removed, is_binary, too_large, skipped)."""
        # Size guard
        if max_file_bytes and (len(b) > max_file_bytes or len(a) > max_file_bytes):
            return 0, 0, False, True, True

        if _is_binary_bytes(b) or _is_binary_bytes(a):
            return 0, 0, True, False, True

        # Line-based counts via SequenceMatcher opcodes.
        bt = _decode_text(b)
        at = _decode_text(a)
        bl = bt.splitlines(keepends=False)
        al = at.splitlines(keepends=False)

        if max_lines_per_file and (len(bl) > max_lines_per_file or len(al) > max_lines_per_file):
            return 0, 0, False, True, True

        sm = difflib.SequenceMatcher(a=bl, b=al)
        add = 0
        rem = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "insert":
                add += (j2 - j1)
            elif tag == "delete":
                rem += (i2 - i1)
            elif tag == "replace":
                add += (j2 - j1)
                rem += (i2 - i1)
        return add, rem, False, False, False

    for ch in changes:
        if not isinstance(ch, dict):
            continue

        before = ch.get("before") if isinstance(ch.get("before"), dict) else None
        after = ch.get("after") if isinstance(ch.get("after"), dict) else None

        if after is None:
            missing_after += 1
            continue

        bk = _snapshot_kind(before) if before else "missing"
        ak = _snapshot_kind(after)

        # File snapshots
        if bk in ("missing", "file") and ak in ("missing", "file"):
            if max_files and files_considered >= max_files:
                files_truncated = True
                break

            blob_before = before.get("blob") if isinstance(before, dict) else None
            blob_after = after.get("blob") if isinstance(after, dict) else None

            b = revision_store.load_blob(blob_before) if (bk == "file" and isinstance(blob_before, str)) else b""
            a = revision_store.load_blob(blob_after) if (ak == "file" and isinstance(blob_after, str)) else b""

            add, rem, is_bin, too_large, skipped = consider_one_file(b, a)
            files_considered += 1
            if is_bin:
                binary_files += 1
            if too_large:
                too_large_files += 1
            if not skipped:
                added_total += add
                removed_total += rem
            continue

        # Dir snapshots
        if bk in ("missing", "dir") and ak in ("missing", "dir"):
            m_before = _snapshot_file_map(before or {}) if bk == "dir" else {}
            m_after = _snapshot_file_map(after or {}) if ak == "dir" else {}
            all_paths = sorted(set(m_before.keys()) | set(m_after.keys()))

            for rel in all_paths:
                if max_files and files_considered >= max_files:
                    files_truncated = True
                    break

                fb = m_before.get(rel)
                fa = m_after.get(rel)

                blob_before = fb.get("blob") if isinstance(fb, dict) else None
                blob_after = fa.get("blob") if isinstance(fa, dict) else None

                b = revision_store.load_blob(blob_before) if isinstance(blob_before, str) else b""
                a = revision_store.load_blob(blob_after) if isinstance(blob_after, str) else b""

                add, rem, is_bin, too_large, skipped = consider_one_file(b, a)
                files_considered += 1
                if is_bin:
                    binary_files += 1
                if too_large:
                    too_large_files += 1
                if not skipped:
                    added_total += add
                    removed_total += rem

            if files_truncated:
                break

    return {
        "status": "success",
        "transaction_id": str(txn_id),
        "added_lines": int(added_total),
        "removed_lines": int(removed_total),
        "files_considered": int(files_considered),
        "files_truncated": bool(files_truncated),
        "binary_files": int(binary_files),
        "too_large_files": int(too_large_files),
        "missing_after": int(missing_after),
    }


# =====================================================================
# Side-by-side diff support (Beyond Compare-ish)
# =====================================================================

def compute_transaction_diff_index(
    revision_store: FsRevisionStore,
    txn_id: str,
    *,
    max_files: int = DEFAULT_MAX_FILES,
) -> Dict[str, Any]:
    """Return a flattened list of per-file entries for a transaction.

    This is intentionally lightweight: it does NOT load blobs or compute diffs.
    It provides stable `file_key` identifiers so the UI can request a specific
    file pair on demand.
    """
    manifest = revision_store.get_transaction(str(txn_id))
    if not isinstance(manifest, dict):
        return {"status": "error", "message": "Transaction not found"}

    changes = manifest.get("changes")
    if not isinstance(changes, list):
        return {"status": "error", "message": "Transaction manifest missing changes"}

    files: List[Dict[str, Any]] = []
    truncated = False

    def _add(entry: Dict[str, Any]) -> None:
        nonlocal truncated
        if max_files and len(files) >= max_files:
            truncated = True
            return
        files.append(entry)

    for ch_idx, ch in enumerate(changes):
        if not isinstance(ch, dict):
            continue

        op = ch.get("op") if isinstance(ch.get("op"), str) else "unknown"
        before = ch.get("before") if isinstance(ch.get("before"), dict) else None
        after = ch.get("after") if isinstance(ch.get("after"), dict) else None

        bk = _snapshot_kind(before) if before else "missing"

        if after is None:
            _add(
                {
                    "file_key": f"{ch_idx}",
                    "op": op,
                    "path_before": before.get("path") if isinstance(before, dict) else None,
                    "path_after": None,
                    "kind_before": bk,
                    "kind_after": None,
                    "missing_after": True,
                }
            )
            continue

        ak = _snapshot_kind(after)

        # Single file snapshots.
        if bk in ("missing", "file") and ak in ("missing", "file"):
            _add(
                {
                    "file_key": f"{ch_idx}",
                    "op": op,
                    "path_before": before.get("path") if isinstance(before, dict) else None,
                    "path_after": after.get("path") if isinstance(after, dict) else None,
                    "kind_before": bk,
                    "kind_after": ak,
                    "before_size": int(before.get("size") or 0) if isinstance(before, dict) else 0,
                    "after_size": int(after.get("size") or 0) if isinstance(after, dict) else 0,
                }
            )
            continue

        # Directory snapshots -> flatten to per-file keys.
        if bk in ("missing", "dir") and ak in ("missing", "dir"):
            base_before = before.get("path") if isinstance(before, dict) else None
            base_after = after.get("path") if isinstance(after, dict) else None

            m_before = _snapshot_file_map(before or {}) if bk == "dir" else {}
            m_after = _snapshot_file_map(after or {}) if ak == "dir" else {}

            all_paths = sorted(set(m_before.keys()) | set(m_after.keys()))

            # Directory-only change (empty dir create/delete) should still appear in the index.
            if not all_paths:
                _add(
                    {
                        "file_key": f"{ch_idx}:__dir__",
                        "op": op,
                        "path_before": (str(base_before) if isinstance(base_before, str) and base_before else None),
                        "path_after": (str(base_after) if isinstance(base_after, str) and base_after else None),
                        "kind_before": bk,
                        "kind_after": ak,
                        "dir_only": True,
                        "before_size": 0,
                        "after_size": 0,
                    }
                )
                continue

            for rel in all_paths:
                fb = m_before.get(rel)
                fa = m_after.get(rel)

                p_before = _join_snapshot_path(str(base_before) if base_before else None, str(rel) if rel else None) if fb else None
                p_after = _join_snapshot_path(str(base_after) if base_after else None, str(rel) if rel else None) if fa else None

                _add(
                    {
                        "file_key": f"{ch_idx}:{rel}",
                        "op": op,
                        "path_before": p_before,
                        "path_after": p_after,
                        "kind_before": "file" if fb else "missing",
                        "kind_after": "file" if fa else "missing",
                        "before_size": int(fb.get("size") or 0) if isinstance(fb, dict) else 0,
                        "after_size": int(fa.get("size") or 0) if isinstance(fa, dict) else 0,
                    }
                )

                if truncated:
                    break

            continue

        # Fallback
        _add(
            {
                "file_key": f"{ch_idx}",
                "op": op,
                "path_before": before.get("path") if isinstance(before, dict) else None,
                "path_after": after.get("path") if isinstance(after, dict) else None,
                "kind_before": bk,
                "kind_after": ak,
                "note": f"Unsupported snapshot kinds: {bk} -> {ak}",
            }
        )

    return {
        "status": "success",
        "transaction_id": str(txn_id),
        "tool": manifest.get("tool"),
        "manifest_status": manifest.get("status"),
        "files": files,
        "files_truncated": bool(truncated),
    }


def compute_transaction_diff_sbs_file(
    revision_store: FsRevisionStore,
    txn_id: str,
    file_key: str,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_lines_per_file: int = 50_000,
    autojunk: bool = False,
) -> Dict[str, Any]:
    """Compute a single-file side-by-side diff payload (lines + opcodes).

    Returns:
    - before_lines / after_lines: lists of strings (no line endings)
    - opcodes: list of [tag, i1, i2, j1, j2]

    This stays stable across UI renderers: highlights-only vs aligned rows.
    """
    if not isinstance(file_key, str) or not file_key:
        return {"status": "error", "message": "file_key is required"}

    manifest = revision_store.get_transaction(str(txn_id))
    if not isinstance(manifest, dict):
        return {"status": "error", "message": "Transaction not found"}

    changes = manifest.get("changes")
    if not isinstance(changes, list):
        return {"status": "error", "message": "Transaction manifest missing changes"}

    # Parse file_key -> change index + optional rel path.
    rel: Optional[str] = None
    try:
        if ":" in file_key:
            a, b = file_key.split(":", 1)
            ch_idx = int(a)
            rel = b
        else:
            ch_idx = int(file_key)
    except Exception:
        return {"status": "error", "message": "Invalid file_key"}

    if ch_idx < 0 or ch_idx >= len(changes):
        return {"status": "error", "message": "file_key out of range"}

    ch = changes[ch_idx]
    if not isinstance(ch, dict):
        return {"status": "error", "message": "Invalid change entry"}

    op = ch.get("op") if isinstance(ch.get("op"), str) else "unknown"
    before = ch.get("before") if isinstance(ch.get("before"), dict) else None
    after = ch.get("after") if isinstance(ch.get("after"), dict) else None

    if after is None:
        return {"status": "error", "message": "Missing after snapshot"}

    bk = _snapshot_kind(before) if before else "missing"
    ak = _snapshot_kind(after)

    blob_before: Optional[str] = None
    blob_after: Optional[str] = None
    path_before: Optional[str] = None
    path_after: Optional[str] = None

    # Direct file snapshots
    if rel is None and bk in ("missing", "file") and ak in ("missing", "file"):
        path_before = before.get("path") if isinstance(before, dict) else None
        path_after = after.get("path") if isinstance(after, dict) else None
        blob_before = before.get("blob") if (bk == "file" and isinstance(before, dict)) else None
        blob_after = after.get("blob") if (ak == "file" and isinstance(after, dict)) else None

    # Flattened dir snapshot file (or dir-only entry)
    elif rel is not None and bk in ("missing", "dir") and ak in ("missing", "dir"):
        base_before = before.get("path") if isinstance(before, dict) else None
        base_after = after.get("path") if isinstance(after, dict) else None

        # Directory-only entry (e.g. mkdir/rmdir of an empty directory)
        if rel == "__dir__":
            path_before = str(base_before) if isinstance(base_before, str) else None
            path_after = str(base_after) if isinstance(base_after, str) else None
            return {
                "status": "success",
                "transaction_id": str(txn_id),
                "file_key": str(file_key),
                "op": op,
                "path_before": path_before,
                "path_after": path_after,
                "too_large": False,
                "is_binary": False,
                "message": "Directory change (no file contents to diff)",
            }

        m_before = _snapshot_file_map(before or {}) if bk == "dir" else {}
        m_after = _snapshot_file_map(after or {}) if ak == "dir" else {}

        fb = m_before.get(rel)
        fa = m_after.get(rel)

        blob_before = fb.get("blob") if isinstance(fb, dict) else None
        blob_after = fa.get("blob") if isinstance(fa, dict) else None

        path_before = _join_snapshot_path(str(base_before) if base_before else None, str(rel) if rel else None) if fb else None
        path_after = _join_snapshot_path(str(base_after) if base_after else None, str(rel) if rel else None) if fa else None

    else:
        return {
            "status": "error",
            "message": f"Unsupported snapshot kinds for file_key: {bk} -> {ak}",
        }

    data_before = revision_store.load_blob(blob_before) if isinstance(blob_before, str) else b""
    data_after = revision_store.load_blob(blob_after) if isinstance(blob_after, str) else b""

    # Size guard.
    if max_file_bytes and (len(data_before) > max_file_bytes or len(data_after) > max_file_bytes):
        return {
            "status": "success",
            "transaction_id": str(txn_id),
            "file_key": str(file_key),
            "op": op,
            "path_before": path_before,
            "path_after": path_after,
            "too_large": True,
            "is_binary": False,
            "before_size": len(data_before),
            "after_size": len(data_after),
            "message": "Too large to render side-by-side",
        }

    is_binary = _is_binary_bytes(data_before) or _is_binary_bytes(data_after)
    if is_binary:
        return {
            "status": "success",
            "transaction_id": str(txn_id),
            "file_key": str(file_key),
            "op": op,
            "path_before": path_before,
            "path_after": path_after,
            "too_large": False,
            "is_binary": True,
            "before_size": len(data_before),
            "after_size": len(data_after),
            "message": "Binary file; cannot render side-by-side diff",
        }

    bt = _decode_text(data_before)
    at = _decode_text(data_after)

    before_lines = bt.splitlines(keepends=False)
    after_lines = at.splitlines(keepends=False)

    if max_lines_per_file and (len(before_lines) > max_lines_per_file or len(after_lines) > max_lines_per_file):
        return {
            "status": "success",
            "transaction_id": str(txn_id),
            "file_key": str(file_key),
            "op": op,
            "path_before": path_before,
            "path_after": path_after,
            "too_large": True,
            "is_binary": False,
            "before_lines": len(before_lines),
            "after_lines": len(after_lines),
            "message": "Too many lines to render side-by-side",
        }

    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=bool(autojunk))
    opcodes = [[tag, i1, i2, j1, j2] for (tag, i1, i2, j1, j2) in sm.get_opcodes()]

    return {
        "status": "success",
        "transaction_id": str(txn_id),
        "file_key": str(file_key),
        "op": op,
        "path_before": path_before,
        "path_after": path_after,
        "too_large": False,
        "is_binary": False,
        "before_lines": before_lines,
        "after_lines": after_lines,
        "opcodes": opcodes,
        "before_line_count": int(len(before_lines)),
        "after_line_count": int(len(after_lines)),
    }


# =====================================================================
# Run-level consolidated diffs (Phase B)
# =====================================================================

def _preview_counts_from_bytes(
    before_bytes: bytes,
    after_bytes: bytes,
    *,
    max_file_bytes: int,
    max_lines_per_file: int,
) -> Tuple[int, int, bool, bool, bool]:
    """Return (added, removed, is_binary, too_large, skipped)."""
    # Size guard
    if max_file_bytes and (len(before_bytes) > max_file_bytes or len(after_bytes) > max_file_bytes):
        return 0, 0, False, True, True

    if _is_binary_bytes(before_bytes) or _is_binary_bytes(after_bytes):
        return 0, 0, True, False, True

    bt = _decode_text(before_bytes)
    at = _decode_text(after_bytes)
    bl = bt.splitlines(keepends=False)
    al = at.splitlines(keepends=False)

    if max_lines_per_file and (len(bl) > max_lines_per_file or len(al) > max_lines_per_file):
        return 0, 0, False, True, True

    sm = difflib.SequenceMatcher(a=bl, b=al)
    add = 0
    rem = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            add += (j2 - j1)
        elif tag == "delete":
            rem += (i2 - i1)
        elif tag == "replace":
            add += (j2 - j1)
            rem += (i2 - i1)

    return int(add), int(rem), False, False, False


def _iter_change_file_entries(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten a single manifest change (before/after snapshots) into per-file entries.

    Each entry includes:
      - path: best-effort path (after preferred, else before)
      - kind_before/kind_after: 'file' or 'missing'
      - blob_before/blob_after: blob ids (or None)
    """
    if after is None:
        return []

    bk = _snapshot_kind(before) if before else "missing"
    ak = _snapshot_kind(after)

    out: List[Dict[str, Any]] = []

    # Single file snapshots.
    if bk in ("missing", "file") and ak in ("missing", "file"):
        p = (after.get("path") if isinstance(after, dict) else None) or (before.get("path") if isinstance(before, dict) else None)
        if not isinstance(p, str) or not p:
            p = "(unknown)"

        blob_before = before.get("blob") if (bk == "file" and isinstance(before, dict)) else None
        blob_after = after.get("blob") if (ak == "file" and isinstance(after, dict)) else None

        out.append(
            {
                "path": p,
                "kind_before": bk,
                "kind_after": ak,
                "blob_before": blob_before if isinstance(blob_before, str) else None,
                "blob_after": blob_after if isinstance(blob_after, str) else None,
            }
        )
        return out

    # Directory snapshots -> flatten per-file.
    if bk in ("missing", "dir") and ak in ("missing", "dir"):
        base_before = before.get("path") if isinstance(before, dict) else None
        base_after = after.get("path") if isinstance(after, dict) else None

        m_before = _snapshot_file_map(before or {}) if bk == "dir" else {}
        m_after = _snapshot_file_map(after or {}) if ak == "dir" else {}

        all_paths = sorted(set(m_before.keys()) | set(m_after.keys()))

        # IMPORTANT: empty directories still matter (mkdir/rmdir) even when no files changed.
        # If there are no per-file entries, emit a single directory-level entry so run receipts
        # and run-level diff indexes can show the operation.
        if not all_paths:
            p_dir = (base_after if isinstance(base_after, str) and base_after else None) or (base_before if isinstance(base_before, str) and base_before else None)
            if isinstance(p_dir, str) and p_dir:
                out.append(
                    {
                        "path": p_dir,
                        "kind_before": bk,
                        "kind_after": ak,
                        "blob_before": None,
                        "blob_after": None,
                        "dir_only": True,
                    }
                )
            return out

        for rel in all_paths:
            fb = m_before.get(rel)
            fa = m_after.get(rel)

            p_before = _join_snapshot_path(str(base_before) if base_before else None, str(rel) if rel else None) if fb else None
            p_after = _join_snapshot_path(str(base_after) if base_after else None, str(rel) if rel else None) if fa else None
            p = p_after or p_before
            if not isinstance(p, str) or not p:
                continue

            blob_before = fb.get("blob") if isinstance(fb, dict) else None
            blob_after = fa.get("blob") if isinstance(fa, dict) else None

            out.append(
                {
                    "path": p,
                    "kind_before": "file" if fb else "missing",
                    "kind_after": "file" if fa else "missing",
                    "blob_before": blob_before if isinstance(blob_before, str) else None,
                    "blob_after": blob_after if isinstance(blob_after, str) else None,
                }
            )

        return out

    # Unsupported snapshot types.
    p = (after.get("path") if isinstance(after, dict) else None) or (before.get("path") if isinstance(before, dict) else None)
    if isinstance(p, str) and p:
        out.append({"path": p, "kind_before": bk, "kind_after": ak, "blob_before": None, "blob_after": None})
    return out


def compute_run_diff_index(
    revision_store: FsRevisionStore,
    txn_ids: List[str],
    *,
    max_file_bytes: int = 256 * 1024,
    max_lines_per_file: int = 20_000,
    max_files: int = 5000,
) -> Dict[str, Any]:
    """Compute a consolidated per-file diff index across multiple transactions.

    This returns net before/after for each file touched by the run (rename chains supported
    for rename_path and move_paths manifests).

    Output format is intentionally close to compute_transaction_diff_index(), plus:
      - added_lines/removed_lines (net)
      - counts_unknown/is_binary/too_large
      - before_blob/after_blob (for run-level side-by-side payload)
    """
    txn_ids = [str(t) for t in (txn_ids or []) if isinstance(t, str) and t]
    if not txn_ids:
        return {
            "status": "success",
            "transaction_ids": [],
            "files": [],
            "files_truncated": False,
            "diff_totals": {"added_lines": 0, "removed_lines": 0},
        }

    # (scope,path) -> id (current known location)
    key_to_id: Dict[str, str] = {}
    # id -> record
    recs: Dict[str, Dict[str, Any]] = {}
    next_id = 0

    def _scope_norm(sc: Any) -> str:
        s = str(sc or "project").strip().lower()
        return s if s in ("project", "sandbox") else "project"

    def _make_key(scope: str, path: str) -> str:
        return f"{_scope_norm(scope)}:{str(path)}"

    def _get_id(scope: str, path: str) -> str:
        nonlocal next_id
        key = _make_key(scope, path)
        if key in key_to_id:
            return key_to_id[key]
        rid = str(next_id)
        next_id += 1
        key_to_id[key] = rid
        recs[rid] = {
            "id": rid,
            "scope": _scope_norm(scope),
            "path_before": path,
            "path_after": path,
            "kind_before": None,
            "kind_after": None,
            "before_blob": None,
            "after_blob": None,
            # Keep the last known on-disk file blob even if the file is later deleted.
            # This lets ephemeral (missing→missing) entries render meaningful diffs.
            "last_file_blob": None,
            "rename_chain": [],
        }
        return rid

    def _touch(
        rid: str,
        *,
        path: str,
        kind_before: str,
        kind_after: str,
        blob_before: Optional[str],
        blob_after: Optional[str],
        skip_after_override: bool = False,
    ) -> None:
        r = recs.get(rid)
        if not isinstance(r, dict):
            return

        # First-seen semantics.
        if r.get("kind_before") is None:
            r["kind_before"] = kind_before
        # If we have a real before blob and haven't captured one yet, take it.
        if r.get("before_blob") is None and kind_before == "file" and isinstance(blob_before, str) and blob_before:
            r["before_blob"] = blob_before

        # Track path evolution.
        try:
            if isinstance(path, str) and path:
                # If this is the first meaningful path, keep it.
                if not r.get("path_before"):
                    r["path_before"] = path
                # Always update the last seen path unless told not to.
                if not skip_after_override:
                    r["path_after"] = path
        except Exception:
            pass

        # Last-seen semantics.
        if not skip_after_override:
            r["kind_after"] = kind_after
            if kind_after == "file" and isinstance(blob_after, str) and blob_after:
                r["after_blob"] = blob_after
                r["last_file_blob"] = blob_after
            elif kind_after == "missing":
                r["after_blob"] = None

    files_truncated = False
    touched_files = 0

    for txn_id in txn_ids:
        manifest = revision_store.get_transaction(str(txn_id))
        if not isinstance(manifest, dict):
            continue

        tool = manifest.get("tool")
        args = manifest.get("args") if isinstance(manifest.get("args"), dict) else {}
        txn_scope = str(args.get("scope") or "project").strip().lower()
        if txn_scope not in ("project", "sandbox"):
            txn_scope = "project"
        changes = manifest.get("changes") if isinstance(manifest.get("changes"), list) else []

        # Extract move/rename operations for identity tracking.
        ops: List[Dict[str, Any]] = []
        try:
            if tool == "rename_path":
                old_p = args.get("old_path")
                new_p = args.get("new_path")
                if isinstance(old_p, str) and isinstance(new_p, str) and old_p and new_p:
                    ops.append({"src": old_p, "dst": new_p})
            elif tool == "move_paths":
                operations = args.get("operations")
                if isinstance(operations, list):
                    for op in operations:
                        if not isinstance(op, dict):
                            continue
                        s = op.get("source")
                        d = op.get("destination")
                        if isinstance(s, str) and isinstance(d, str) and s and d:
                            ops.append({"src": s, "dst": d})
        except Exception:
            ops = []

        # Best-effort: detect if op src is a dir by looking at snapshots.
        if ops:
            try:
                snap_by_path: Dict[str, Dict[str, Any]] = {}
                for ch in changes:
                    if not isinstance(ch, dict):
                        continue
                    b = ch.get("before") if isinstance(ch.get("before"), dict) else None
                    if isinstance(b, dict) and isinstance(b.get("path"), str):
                        snap_by_path[b["path"]] = b
                for op in ops:
                    src = op.get("src")
                    if isinstance(src, str) and src in snap_by_path:
                        op["is_dir"] = (_snapshot_kind(snap_by_path.get(src)) == "dir")
                    else:
                        op["is_dir"] = False
            except Exception:
                for op in ops:
                    op["is_dir"] = False

        moved_pairs: List[Tuple[str, str, str]] = []  # (scope, src_file_path, dst_file_path)

        def _match_op(path: str) -> Optional[Tuple[Dict[str, Any], str, str]]:
            """Return (op, role, src_equiv) where role is 'src' or 'dst'."""
            for op in ops:
                src = op.get("src")
                dst = op.get("dst")
                is_dir = bool(op.get("is_dir"))
                if not isinstance(src, str) or not isinstance(dst, str):
                    continue

                if is_dir:
                    if path == dst or path.startswith(dst + "/"):
                        rest = path[len(dst):].lstrip("/")
                        src_equiv = (src + "/" + rest) if rest else src
                        return op, "dst", src_equiv
                    if path == src or path.startswith(src + "/"):
                        return op, "src", path
                else:
                    if path == dst:
                        return op, "dst", src
                    if path == src:
                        return op, "src", src

            return None

        for ch in changes:
            if not isinstance(ch, dict):
                continue

            before = ch.get("before") if isinstance(ch.get("before"), dict) else None
            after = ch.get("after") if isinstance(ch.get("after"), dict) else None
            if after is None:
                continue

            per_files = _iter_change_file_entries(before, after)
            for fe in per_files:
                if not isinstance(fe, dict):
                    continue
                p = fe.get("path")
                if not isinstance(p, str) or not p:
                    continue

                touched_files += 1
                if max_files and touched_files > max_files:
                    files_truncated = True
                    break

                kb = fe.get("kind_before") or "missing"
                ka = fe.get("kind_after") or "missing"
                bb = fe.get("blob_before") if isinstance(fe.get("blob_before"), str) else None
                ba = fe.get("blob_after") if isinstance(fe.get("blob_after"), str) else None

                m = _match_op(p) if ops else None
                skip_after = False
                lookup_path = p

                if m:
                    op, role, src_equiv = m
                    if role == "dst":
                        lookup_path = src_equiv
                        moved_pairs.append((txn_scope, src_equiv, p))
                        try:
                            rid = _get_id(txn_scope, lookup_path)
                            # For UX: track rename chain on the lineage record.
                            if isinstance(op.get("src"), str) and isinstance(op.get("dst"), str):
                                recs[rid]["rename_chain"].append([op.get("src"), op.get("dst")])
                        except Exception:
                            rid = _get_id(txn_scope, lookup_path)
                    else:
                        # Source side: don't let the "missing" after snapshot override the file's final state.
                        if tool in ("rename_path", "move_paths") and ka == "missing":
                            skip_after = True
                        lookup_path = src_equiv

                rid = _get_id(txn_scope, lookup_path)
                _touch(
                    rid,
                    path=p,
                    kind_before=str(kb),
                    kind_after=str(ka),
                    blob_before=bb,
                    blob_after=ba,
                    skip_after_override=bool(skip_after),
                )

            if files_truncated:
                break

        # Update current-path mapping after a move/rename.
        if ops and moved_pairs:
            # Atomic-ish: avoid clobber on swaps.
            try:
                tmp: List[Tuple[str, str, str, Optional[str]]] = []
                for sc, src_equiv, dst_path in moved_pairs:
                    rid = key_to_id.get(_make_key(sc, src_equiv))
                    tmp.append((sc, src_equiv, dst_path, rid))

                for sc, src_equiv, dst_path, rid in tmp:
                    if not rid:
                        continue
                    # dst now points to this id.
                    key_to_id[_make_key(sc, dst_path)] = rid
                    # src no longer a live location.
                    try:
                        ksrc = _make_key(sc, src_equiv)
                        if ksrc in key_to_id:
                            del key_to_id[ksrc]
                    except Exception:
                        pass
            except Exception:
                pass

    # Build output list, compute counts.
    files_out: List[Dict[str, Any]] = []
    add_total = 0
    rem_total = 0

    for rid, r in recs.items():
        if not isinstance(r, dict):
            continue
        pb = r.get("path_before")
        pa = r.get("path_after")
        kb = r.get("kind_before") or "missing"
        ka = r.get("kind_after") or "missing"
        bb = r.get("before_blob")
        ba = r.get("after_blob")

        # If this entry is ephemeral (missing→missing), prefer the last-known file blob
        # so it renders as a deletion (content → empty) instead of empty→empty.
        ephemeral = (kb == "missing" and ka == "missing")
        if ephemeral:
            bb2 = r.get("last_file_blob") or bb
            ba2 = None
            bb = bb2
            ba = ba2

        # Net-change filter: skip if nothing changed (including path).
        if (str(pb or "") == str(pa or "")) and (str(kb) == str(ka)) and (str(bb or "") == str(ba or "")):
            continue

        # For temporary (ephemeral) entries, kind_before/kind_after may be missing→missing,
        # but we can still have a real blob from the file's lifetime within the run.
        # Load blobs based on presence, not only on kind.
        try:
            before_bytes = revision_store.load_blob(bb) if (isinstance(bb, str) and bb) else b""
        except Exception:
            before_bytes = b""
        try:
            after_bytes = revision_store.load_blob(ba) if (isinstance(ba, str) and ba) else b""
        except Exception:
            after_bytes = b""

        add, rem, is_bin, too_large, skipped = _preview_counts_from_bytes(
            before_bytes,
            after_bytes,
            max_file_bytes=max_file_bytes,
            max_lines_per_file=max_lines_per_file,
        )

        # Run-level totals should reflect net-visible changes, not temporary churn.
        if (not skipped) and (not ephemeral):
            add_total += add
            rem_total += rem

        # Action label
        action = "modified"
        if kb == "missing" and ka == "file":
            action = "created"
        elif kb == "file" and ka == "missing":
            action = "deleted"
        elif str(pb or "") != str(pa or ""):
            action = "renamed"

        files_out.append(
            {
                "file_key": None,  # assigned after sort
                "action": action,
                "path_before": pb,
                "scope": r.get("scope") if isinstance(r.get("scope"), str) else "project",
                "path_after": pa,
                "kind_before": kb,
                "kind_after": ka,
                "before_blob": bb,
                "after_blob": ba,
                "added_lines": int(add),
                "removed_lines": int(rem),
                "counts_unknown": bool(skipped),
                "is_binary": bool(is_bin),
                "too_large": bool(too_large),
                "ephemeral": bool(ephemeral),
                "rename_chain": r.get("rename_chain") if isinstance(r.get("rename_chain"), list) else [],
            }
        )

    # Stable-ish sort: by final path, then before.
    def _sort_key(e: Dict[str, Any]) -> str:
        p = e.get("path_after") or e.get("path_before") or ""
        return str(p)

    files_out.sort(key=_sort_key)
    for i, e in enumerate(files_out):
        e["file_key"] = str(i)

    return {
        "status": "success",
        "transaction_ids": list(txn_ids),
        "files": files_out,
        "files_truncated": bool(files_truncated),
        "diff_totals": {"added_lines": int(add_total), "removed_lines": int(rem_total)},
    }


def compute_run_diff_sbs_file(
    revision_store: FsRevisionStore,
    *,
    run_id: str,
    file_key: str,
    files_index: List[Dict[str, Any]],
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_lines_per_file: int = 50_000,
    autojunk: bool = False,
) -> Dict[str, Any]:
    """Compute a run-scoped side-by-side diff payload for a single file.

    `files_index` is the run-level index produced by compute_run_diff_index()['files'].
    """
    if not isinstance(file_key, str) or not file_key:
        return {"status": "error", "message": "file_key is required"}

    target = None
    for f in (files_index or []):
        if isinstance(f, dict) and str(f.get("file_key")) == file_key:
            target = f
            break

    if not isinstance(target, dict):
        return {"status": "error", "message": "file_key not found"}

    pb = target.get("path_before")
    pa = target.get("path_after")
    kb = target.get("kind_before") or "missing"
    ka = target.get("kind_after") or "missing"
    bb = target.get("before_blob")
    ba = target.get("after_blob")

    # Directory entries have no file blobs; show a clear message instead of an empty diff.
    if kb == "dir" or ka == "dir":
        return {
            "status": "success",
            "run_id": str(run_id),
            "file_key": str(file_key),
            "op": "run",
            "path_before": pb,
            "path_after": pa,
            "too_large": False,
            "is_binary": False,
            "message": "Directory change (no file contents to diff)",
        }

    # For ephemeral entries, kind_before/kind_after may be missing→missing,
    # but we can still have a real blob from the file's lifetime within the run.
    try:
        data_before = revision_store.load_blob(bb) if (isinstance(bb, str) and bb) else b""
    except Exception:
        data_before = b""
    try:
        data_after = revision_store.load_blob(ba) if (isinstance(ba, str) and ba) else b""
    except Exception:
        data_after = b""

    # Size guard.
    if max_file_bytes and (len(data_before) > max_file_bytes or len(data_after) > max_file_bytes):
        return {
            "status": "success",
            "run_id": str(run_id),
            "file_key": str(file_key),
            "op": "run",
            "path_before": pb,
            "path_after": pa,
            "too_large": True,
            "is_binary": False,
            "before_size": len(data_before),
            "after_size": len(data_after),
            "message": "Too large to render side-by-side",
        }

    is_binary = _is_binary_bytes(data_before) or _is_binary_bytes(data_after)
    if is_binary:
        return {
            "status": "success",
            "run_id": str(run_id),
            "file_key": str(file_key),
            "op": "run",
            "path_before": pb,
            "path_after": pa,
            "too_large": False,
            "is_binary": True,
            "before_size": len(data_before),
            "after_size": len(data_after),
            "message": "Binary file; cannot render side-by-side diff",
        }

    bt = _decode_text(data_before)
    at = _decode_text(data_after)

    before_lines = bt.splitlines(keepends=False)
    after_lines = at.splitlines(keepends=False)

    if max_lines_per_file and (len(before_lines) > max_lines_per_file or len(after_lines) > max_lines_per_file):
        return {
            "status": "success",
            "run_id": str(run_id),
            "file_key": str(file_key),
            "op": "run",
            "path_before": pb,
            "path_after": pa,
            "too_large": True,
            "is_binary": False,
            "before_lines": len(before_lines),
            "after_lines": len(after_lines),
            "message": "Too many lines to render side-by-side",
        }

    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=bool(autojunk))
    opcodes = [[tag, i1, i2, j1, j2] for (tag, i1, i2, j1, j2) in sm.get_opcodes()]

    return {
        "status": "success",
        "run_id": str(run_id),
        "file_key": str(file_key),
        "op": "run",
        "path_before": pb,
        "path_after": pa,
        "too_large": False,
        "is_binary": False,
        "before_lines": before_lines,
        "after_lines": after_lines,
        "opcodes": opcodes,
        "before_line_count": int(len(before_lines)),
        "after_line_count": int(len(after_lines)),
    }
