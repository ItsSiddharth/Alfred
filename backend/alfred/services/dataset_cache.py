"""
services/dataset_cache.py — Content-addressed dataset cache (Stage 7.1).

Datasets are stored at:
  <workspace>/datasets/<hash[:2]>/<hash>/

…and symlinked/hardlinked/copied into experiment_folder/data/ on use.
A DatasetCacheEntry DB row is maintained per cached dataset.

source_uri formats accepted:
  hf://<dataset_name>              — HuggingFace `datasets` library
  hf://<dataset_name>/<config>     — HuggingFace with named config
  http(s)://...                    — direct file download via httpx
  /absolute/local/path             — local file or directory (no copy; hash only)
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import httpx
from sqlmodel import Session, select

from alfred.models.db_models import DatasetCacheEntry

logger = logging.getLogger(__name__)


class DatasetCacheError(Exception):
    """Raised when a dataset cannot be fetched or cached."""


class DatasetCache:
    """Content-addressed dataset cache stored in the workspace directory."""

    def __init__(self, workspace_path: Path) -> None:
        self.workspace_path = workspace_path
        self._cache_dir = workspace_path / "datasets"
        self._hf_cache_dir = self._cache_dir / "hf_cache"

    def cache_dir(self) -> Path:
        return self._cache_dir

    # ── Public API ─────────────────────────────────────────────────────────

    async def get_or_download(
        self,
        source_uri: str,
        experiment_folder: Path,
        session: Session,
    ) -> Path:
        """
        Return the local path to the cached dataset, downloading if needed.
        A symlink/hardlink/copy is created inside `experiment_folder/data/`.
        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._hf_cache_dir.mkdir(parents=True, exist_ok=True)

        # Check DB cache by source URI
        existing: DatasetCacheEntry | None = session.exec(
            select(DatasetCacheEntry).where(DatasetCacheEntry.source_uri == source_uri)
        ).first()

        if existing is not None and Path(existing.local_path).exists():
            logger.info("Dataset cache HIT: %s → %s", source_uri, existing.local_path)
            existing.last_used_at = datetime.utcnow()
            session.add(existing)
            session.commit()
            return self._link_into(Path(existing.local_path), experiment_folder)

        # Cache miss — download
        logger.info("Dataset cache MISS: %s — fetching", source_uri)
        cached_path, content_hash, size_bytes = await self._fetch(source_uri)

        # Upsert DB record
        if existing is not None:
            existing.local_path = str(cached_path)
            existing.content_hash = content_hash
            existing.size_bytes = size_bytes
            existing.last_used_at = datetime.utcnow()
            session.add(existing)
        else:
            entry = DatasetCacheEntry(
                content_hash=content_hash,
                source_uri=source_uri,
                local_path=str(cached_path),
                size_bytes=size_bytes,
            )
            session.add(entry)
        session.commit()
        logger.info("Dataset cached: %s (%d bytes)", content_hash[:8], size_bytes)

        return self._link_into(cached_path, experiment_folder)

    # ── Fetch strategies ───────────────────────────────────────────────────

    async def _fetch(self, source_uri: str) -> tuple[Path, str, int]:
        """Dispatch to the appropriate fetch strategy. Returns (path, hash, size)."""
        if source_uri.startswith("hf://"):
            return await self._fetch_hf(source_uri)
        if source_uri.startswith(("http://", "https://")):
            return await self._fetch_http(source_uri)
        # Treat as a local path
        local = Path(source_uri)
        if not local.exists():
            raise DatasetCacheError(f"Local dataset not found: {source_uri}")
        h = self._hash_path(local)
        size = local.stat().st_size if local.is_file() else self._dir_size(local)
        return local, h, size

    async def _fetch_hf(self, source_uri: str) -> tuple[Path, str, int]:
        """Download a HuggingFace dataset via the `datasets` library."""
        try:
            import datasets as hf_datasets  # noqa: PLC0415
        except ImportError as exc:
            raise DatasetCacheError(
                "HuggingFace `datasets` library not installed. "
                "Install it in the project conda env: pip install datasets"
            ) from exc

        remainder = source_uri[len("hf://"):]
        parts = remainder.split("/", 1)
        dataset_name = parts[0]
        config_name = parts[1] if len(parts) > 1 else None

        logger.info(
            "Downloading HuggingFace dataset: %s config=%s", dataset_name, config_name
        )
        hf_datasets.load_dataset(
            dataset_name,
            config_name,
            cache_dir=str(self._hf_cache_dir),
        )

        # HF manages its own directory structure; we point at the cache root
        info_key = f"{dataset_name}/{config_name or 'default'}"
        content_hash = hashlib.sha256(info_key.encode()).hexdigest()
        size_bytes = self._dir_size(self._hf_cache_dir)
        return self._hf_cache_dir, content_hash, size_bytes

    async def _fetch_http(self, url: str) -> tuple[Path, str, int]:
        """Stream-download a file from HTTP(S), computing SHA-256 on the fly."""
        hasher = hashlib.sha256()
        url_key = hashlib.md5(url.encode()).hexdigest()
        dest_tmp = self._cache_dir / f"_tmp_{url_key}"
        dest_tmp.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(follow_redirects=True, timeout=300.0) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest_tmp, "wb") as fh:
                    async for chunk in resp.aiter_bytes(65_536):
                        fh.write(chunk)
                        hasher.update(chunk)

        content_hash = hasher.hexdigest()
        dest_dir = self._cache_dir / content_hash[:2] / content_hash
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(url.split("?")[0]).name or "data"
        dest_file = dest_dir / filename
        dest_tmp.rename(dest_file)

        size_bytes = dest_file.stat().st_size
        logger.info("HTTP download complete: %s → %s (%d bytes)", url, dest_file, size_bytes)
        return dest_file, content_hash, size_bytes

    # ── Link into experiment folder ────────────────────────────────────────

    def _link_into(self, cached_path: Path, experiment_folder: Path) -> Path:
        """
        Link or copy `cached_path` into `experiment_folder/data/`.
        Priority: symlink → hardlink → shutil.copy2/copytree.
        """
        data_dir = experiment_folder / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        dest = data_dir / cached_path.name
        # dest is always experiment_folder/data/<name> — jail-safe by construction.
        # We intentionally do NOT call assert_within(experiment_folder, dest) here
        # because assert_within follows symlinks and dest may already be a symlink
        # pointing to the cache (which is outside the experiment folder by design).

        # Remove stale link / directory
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.is_dir():
            shutil.rmtree(dest)

        # Try symlink (preferred — no disk duplication)
        try:
            dest.symlink_to(cached_path.resolve())
            logger.debug("Symlinked %s → %s", cached_path, dest)
            return dest
        except OSError:
            pass

        # Try hardlink (only works for files on the same filesystem)
        if cached_path.is_file():
            try:
                os.link(str(cached_path.resolve()), str(dest))
                logger.debug("Hardlinked %s → %s", cached_path, dest)
                return dest
            except OSError:
                pass

        # Fallback: copy
        if cached_path.is_dir():
            shutil.copytree(str(cached_path), str(dest))
        else:
            shutil.copy2(str(cached_path), str(dest))
        logger.debug("Copied %s → %s", cached_path, dest)
        return dest

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _hash_path(p: Path) -> str:
        hasher = hashlib.sha256()
        if p.is_file():
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(65_536), b""):
                    hasher.update(chunk)
        else:
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    with open(f, "rb") as fh:
                        for chunk in iter(lambda: fh.read(65_536), b""):
                            hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _dir_size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
