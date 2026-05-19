"""Filesystem revision store ("tiny git") for agent-initiated destructive ops.

POC goals:
- Before any write/move/delete, capture a reversible snapshot.
- Store snapshots outside the project root (in app data dir), encrypted at rest.
- Provide undo-by-transaction-id.

Security posture: deny-by-default inside the revision layer too.
- No symlink support (fail closed) to avoid escape games in this first pass.
"""

from __future__ import annotations

import json
import os
import uuid
import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .secure import get_app_data_dir, write_encrypted_json, read_encrypted_json, encrypt_bytes, decrypt_bytes


class FsRevisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotTarget:
    """A target relative to a project root."""

    relative_path: str


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ensure_no_symlink(path: Path) -> None:
    # Fail closed: symlinks are where "safe path" checks go to die.
    if path.is_symlink():
        raise FsRevisionError(f"Symlinks are not supported by fs revision store (path: {path})")


class FsRevisionStore:
    """Encrypted revision store for filesystem operations."""

    def __init__(self, store_name: str = "fs_revisions") -> None:
        base = get_app_data_dir() / store_name
        self.base_dir = base
        self.txns_dir = base / "txns"
        self.blobs_dir = base / "blobs"
        self.index_path = base / "index.enc"
        self.audit_path = base / "audit.jsonl"  # intentionally plain for append-only simplicity (no file contents)

        self.txns_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)

    # ---- index / audit ----

    def _load_index(self) -> List[Dict[str, Any]]:
        data = read_encrypted_json(self.index_path)
        return data if isinstance(data, list) else []

    def _save_index(self, index: List[Dict[str, Any]]) -> None:
        write_encrypted_json(self.index_path, index)

    def _append_audit(self, entry: Dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("ab") as f:
            f.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))

    # ---- blobs ----

    def _blob_path(self, blob_id: str) -> Path:
        return self.blobs_dir / f"{blob_id}.blob"

    def _store_blob(self, data: bytes) -> str:
        blob_id = _sha256(data)
        path = self._blob_path(blob_id)
        if path.exists():
            return blob_id

        enc = encrypt_bytes(data)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(enc)
        os.replace(tmp, path)
        return blob_id

    def _load_blob(self, blob_id: str) -> bytes:
        path = self._blob_path(blob_id)
        if not path.exists():
            raise FsRevisionError(f"Missing blob {blob_id}")
        enc = path.read_bytes()
        return decrypt_bytes(enc)

    # ---- snapshots ----

    def snapshot_path(self, project_root: str, relative_path: str) -> Dict[str, Any]:
        """Snapshot a path (file or directory) under project_root.

        Returns a JSON-serializable snapshot dict.
        """
        rel = relative_path
        root = Path(project_root)
        full = (root / rel).resolve()

        # Resolve and validate it stays within root (best-effort; the tool layer also checks).
        try:
            root_resolved = root.resolve()
            if root_resolved not in full.parents and full != root_resolved:
                raise FsRevisionError(f"Snapshot target escapes project root: {relative_path}")
        except Exception as e:
            raise FsRevisionError(str(e))

        if not full.exists():
            return {"path": rel, "kind": "missing"}

        _ensure_no_symlink(full)

        if full.is_file():
            data = full.read_bytes()
            blob_id = self._store_blob(data)
            st = full.stat()
            return {
                "path": rel,
                "kind": "file",
                "blob": blob_id,
                "size": st.st_size,
                "mode": int(st.st_mode),
                "mtime": st.st_mtime,
            }

        if full.is_dir():
            dirs: List[str] = []
            files: List[Dict[str, Any]] = []

            base_rel = Path(rel)
            # Include the root directory itself (so we can restore empty dirs).
            dirs.append(str(base_rel).replace("\\", "/"))

            for dirpath, dirnames, filenames in os.walk(full, topdown=True, followlinks=False):
                p = Path(dirpath)
                _ensure_no_symlink(p)

                # Record directories
                rel_dir = str(base_rel / p.relative_to(full)).replace("\\", "/")
                if rel_dir not in dirs:
                    dirs.append(rel_dir)

                # Prevent symlink traversal via dirnames
                safe_dirnames = []
                for d in dirnames:
                    child = p / d
                    if child.is_symlink():
                        raise FsRevisionError(f"Symlink dir not supported in snapshot (path: {child})")
                    safe_dirnames.append(d)
                dirnames[:] = safe_dirnames

                for fn in filenames:
                    fpath = p / fn
                    if fpath.is_symlink():
                        raise FsRevisionError(f"Symlink file not supported in snapshot (path: {fpath})")
                    data = fpath.read_bytes()
                    blob_id = self._store_blob(data)
                    st = fpath.stat()
                    rel_file = str(base_rel / fpath.relative_to(full)).replace("\\", "/")
                    files.append({
                        "path": rel_file,
                        "blob": blob_id,
                        "size": st.st_size,
                        "mode": int(st.st_mode),
                        "mtime": st.st_mtime,
                    })

            return {
                "path": rel,
                "kind": "dir",
                "dirs": dirs,
                "files": files,
            }

        raise FsRevisionError(f"Unsupported path type: {relative_path}")

    # ---- transactions ----

    def begin_transaction(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        txn_id = str(uuid.uuid4())
        meta = {
            "id": txn_id,
            "ts": _utc_now_iso(),
            "tool": tool_name,
            "args": tool_args,
        }
        # Pre-register in index (so crashes still leave a breadcrumb).
        index = self._load_index()
        index.append(meta)
        self._save_index(index)
        self._append_audit({**meta, "event": "begin"})
        return txn_id

    def commit_transaction(self, txn_id: str, manifest: Dict[str, Any]) -> None:
        # Persist full manifest per txn.
        path = self.txns_dir / f"{txn_id}.enc"
        write_encrypted_json(path, manifest)
        self._append_audit({"id": txn_id, "ts": _utc_now_iso(), "event": "commit"})

    def get_transaction(self, txn_id: str) -> Optional[Dict[str, Any]]:
        path = self.txns_dir / f"{txn_id}.enc"
        data = read_encrypted_json(path)
        return data if isinstance(data, dict) else None

    def load_blob(self, blob_id: str) -> bytes:
        """Public wrapper to load and decrypt a blob by id."""
        return self._load_blob(blob_id)

    def list_transactions(self, limit: int = 20) -> List[Dict[str, Any]]:
        index = self._load_index()
        return index[-limit:]

    # ---- restore / undo ----

    def _remove_path(self, full: Path) -> None:
        if not full.exists() and not full.is_symlink():
            return
        _ensure_no_symlink(full)
        if full.is_dir():
            shutil.rmtree(full)
        else:
            full.unlink(missing_ok=True)  # type: ignore[arg-type]

    def _restore_snapshot(self, project_root: str, snapshot: Dict[str, Any]) -> None:
        root = Path(project_root)
        kind = snapshot.get("kind")
        rel = snapshot.get("path")
        if not isinstance(rel, str):
            raise FsRevisionError("Snapshot missing path")

        target = (root / rel)


        # Final safety check: never restore outside the project root.
        root_resolved = root.resolve()
        target_resolved = target.resolve()
        if root_resolved not in target_resolved.parents and target_resolved != root_resolved:
            raise FsRevisionError(f"Restore target escapes project root: {rel}")

        _ensure_no_symlink(target)
        if kind == "missing":
            # Ensure it does not exist now.
            self._remove_path(target)
            return

        if kind == "file":
            blob_id = snapshot.get("blob")
            if not isinstance(blob_id, str):
                raise FsRevisionError("File snapshot missing blob")
            data = self._load_blob(blob_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._remove_path(target)
            target.write_bytes(data)
            return

        if kind == "dir":
            dirs = snapshot.get("dirs")
            files = snapshot.get("files")
            if not isinstance(dirs, list) or not isinstance(files, list):
                raise FsRevisionError("Dir snapshot missing dirs/files")

            # Nuke current state, then restore.
            self._remove_path(target)

            # Recreate directories.
            for d in sorted({str(x) for x in dirs}, key=len):
                p = root / d
                p.mkdir(parents=True, exist_ok=True)

            # Recreate files.
            for f in files:
                if not isinstance(f, dict):
                    continue
                f_rel = f.get("path")
                blob_id = f.get("blob")
                if not isinstance(f_rel, str) or not isinstance(blob_id, str):
                    continue
                data = self._load_blob(blob_id)
                p = root / f_rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            return

        raise FsRevisionError(f"Unknown snapshot kind: {kind}")

    def undo_transaction(self, project_root: str, txn_id: str) -> str:
        """Undo a previous transaction.

        Returns the new (undo) transaction id that captures the pre-undo state.
        """
        manifest = self.get_transaction(txn_id)
        if not manifest:
            raise FsRevisionError(f"Transaction not found: {txn_id}")

        changes = manifest.get("changes")
        if not isinstance(changes, list):
            raise FsRevisionError("Transaction manifest missing changes")

        # Determine which top-level paths we will touch.
        touch_paths: List[str] = []
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            before = ch.get("before")
            if isinstance(before, dict) and isinstance(before.get("path"), str):
                touch_paths.append(before["path"])

        # Snapshot current state so undo itself is reversible.
        undo_txn_id = self.begin_transaction("fs_undo", {"target_txn_id": txn_id})
        undo_manifest = {
            "id": undo_txn_id,
            "ts": _utc_now_iso(),
            "tool": "fs_undo",
            "args": {"target_txn_id": txn_id},
            "changes": [],
        }
        for p in sorted(set(touch_paths)):
            undo_manifest["changes"].append({
                "op": "pre_undo_snapshot",
                "before": self.snapshot_path(project_root, p),
            })

        # Apply the original "before" snapshots.
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            before = ch.get("before")
            if isinstance(before, dict):
                self._restore_snapshot(project_root, before)

        self.commit_transaction(undo_txn_id, undo_manifest)
        return undo_txn_id
