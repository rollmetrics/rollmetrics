"""Durable 1-file-per-record runtime state store with atomic local writes and GCS cloud sync."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from rollmetrics.config import Settings

SCHEMA_VERSION = 1
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,160}$")


def _safe_segment(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned or not _SAFE_SEGMENT_RE.match(cleaned):
        raise ValueError(f"Invalid {label} identifier: {value!r}")
    return cleaned


def _get_gcs_bearer_token() -> str | None:
    """Obtain an ADC bearer token for GCS JSON API calls, returning None when unavailable."""
    try:
        import google.auth
        import google.auth.transport.requests as g_requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
        if not getattr(creds, "valid", False) or not getattr(creds, "token", None):
            creds.refresh(g_requests.Request())
        token = getattr(creds, "token", None)
        return str(token) if token else None
    except (ImportError, OSError, RuntimeError, ValueError):
        return None


class RuntimeStateStore:
    """One-file-per-record JSON store in data/runtime/state/ with atomic GCS sync in cloud mode."""

    def __init__(self, settings: Settings, timeout_sec: float = 15.0) -> None:
        self.settings = settings
        self.timeout_sec = timeout_sec
        self.state_root = settings.runtime_dir / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)

    @property
    def cloud_sync_enabled(self) -> bool:
        return self.settings.environment == "cloud" and bool(self.settings.gcs_bucket.strip())

    def _local_path(self, collection: str, key: str) -> Path:
        col = _safe_segment(collection, "collection")
        safe_key = _safe_segment(key, "key")
        return self.state_root / col / f"{safe_key}.json"

    def _gcs_object_name(self, collection: str, key: str) -> str:
        col = _safe_segment(collection, "collection")
        safe_key = _safe_segment(key, "key")
        return f"state/{col}/{safe_key}.json"

    def _write_local_atomic(self, path: Path, envelope: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f".{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, path)

    def _gcs_headers(self) -> dict[str, str] | None:
        token = _get_gcs_bearer_token()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}

    def _gcs_put(self, object_name: str, payload_bytes: bytes) -> bool:
        headers = self._gcs_headers()
        if not headers:
            return False
        bucket = self.settings.gcs_bucket.strip()
        url = (
            f"https://storage.googleapis.com/upload/storage/v1/b/{quote(bucket, safe='')}/o"
            f"?uploadType=media&name={quote(object_name, safe='')}"
        )
        with contextlib.suppress(httpx.HTTPError):
            resp = httpx.post(
                url,
                content=payload_bytes,
                headers={**headers, "Content-Type": "application/json"},
                timeout=self.timeout_sec,
            )
            return resp.status_code in (200, 201)
        return False

    def _gcs_get(self, object_name: str) -> dict[str, Any] | None:
        headers = self._gcs_headers()
        if not headers:
            return None
        bucket = self.settings.gcs_bucket.strip()
        url = (
            f"https://storage.googleapis.com/storage/v1/b/{quote(bucket, safe='')}/o/"
            f"{quote(object_name, safe='')}?alt=media"
        )
        with contextlib.suppress(httpx.HTTPError, ValueError):
            resp = httpx.get(url, headers=headers, timeout=self.timeout_sec)
            if resp.status_code == 200:
                parsed = resp.json()
                if isinstance(parsed, dict):
                    return parsed
        return None

    def _gcs_delete(self, object_name: str) -> bool:
        headers = self._gcs_headers()
        if not headers:
            return False
        bucket = self.settings.gcs_bucket.strip()
        url = (
            f"https://storage.googleapis.com/storage/v1/b/{quote(bucket, safe='')}/o/"
            f"{quote(object_name, safe='')}"
        )
        with contextlib.suppress(httpx.HTTPError):
            resp = httpx.delete(url, headers=headers, timeout=self.timeout_sec)
            return resp.status_code in (200, 204, 404)
        return False

    def _gcs_list_objects(self, collection: str) -> list[str]:
        headers = self._gcs_headers()
        if not headers:
            return []
        bucket = self.settings.gcs_bucket.strip()
        prefix = f"state/{_safe_segment(collection, 'collection')}/"
        url = (
            f"https://storage.googleapis.com/storage/v1/b/{quote(bucket, safe='')}/o"
            f"?prefix={quote(prefix, safe='')}"
        )
        with contextlib.suppress(httpx.HTTPError, ValueError):
            resp = httpx.get(url, headers=headers, timeout=self.timeout_sec)
            if resp.status_code == 200:
                items = resp.json().get("items") or []
                return [str(item["name"]) for item in items if isinstance(item, dict) and item.get("name")]
        return []

    def put_record(self, collection: str, key: str, data: dict[str, Any]) -> dict[str, Any]:
        """Atomically persist a single record locally and sync to GCS in cloud mode."""
        col = _safe_segment(collection, "collection")
        safe_key = _safe_segment(key, "key")
        envelope: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "collection": col,
            "key": safe_key,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": dict(data),
        }
        path = self._local_path(col, safe_key)
        self._write_local_atomic(path, envelope)
        if self.cloud_sync_enabled:
            self._gcs_put(
                self._gcs_object_name(col, safe_key),
                json.dumps(envelope, indent=2, sort_keys=True).encode("utf-8"),
            )
        return envelope

    def get_record(self, collection: str, key: str) -> dict[str, Any] | None:
        """Read a record from local cache, hydrating from GCS on cloud cold start."""
        col = _safe_segment(collection, "collection")
        safe_key = _safe_segment(key, "key")
        path = self._local_path(col, safe_key)
        if path.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
                    return dict(parsed["data"])
        if self.cloud_sync_enabled:
            remote = self._gcs_get(self._gcs_object_name(col, safe_key))
            if remote and isinstance(remote.get("data"), dict):
                self._write_local_atomic(path, remote)
                return dict(remote["data"])
        return None

    def delete_record(self, collection: str, key: str) -> bool:
        """Delete a record locally and from GCS in cloud mode."""
        col = _safe_segment(collection, "collection")
        safe_key = _safe_segment(key, "key")
        path = self._local_path(col, safe_key)
        deleted_local = False
        if path.is_file():
            path.unlink()
            deleted_local = True
        deleted_remote = False
        if self.cloud_sync_enabled:
            deleted_remote = self._gcs_delete(self._gcs_object_name(col, safe_key))
        return deleted_local or deleted_remote

    def list_records(self, collection: str, hydrate_cloud: bool = True) -> list[dict[str, Any]]:
        """List all records in a collection, hydrating missing cloud records when enabled."""
        col = _safe_segment(collection, "collection")
        col_dir = self.state_root / col
        records_by_key: dict[str, dict[str, Any]] = {}

        if col_dir.is_dir():
            for fp in sorted(col_dir.glob("*.json")):
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    parsed = json.loads(fp.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
                        records_by_key[fp.stem] = dict(parsed["data"])

        if self.cloud_sync_enabled and hydrate_cloud:
            for obj_name in self._gcs_list_objects(col):
                stem = Path(obj_name).stem
                if stem not in records_by_key and obj_name.endswith(".json"):
                    remote = self._gcs_get(obj_name)
                    if remote and isinstance(remote.get("data"), dict):
                        self._write_local_atomic(self._local_path(col, stem), remote)
                        records_by_key[stem] = dict(remote["data"])

        return [records_by_key[k] for k in sorted(records_by_key)]

    def export_bundle_zip(
        self,
        collections: list[str] | None = None,
        owner_email: str | None = None,
    ) -> bytes:
        """Export runtime state records into a portable ZIP archive."""
        clean_owner = (owner_email or "").strip().lower()
        target_cols = (
            [_safe_segment(c, "collection") for c in collections]
            if collections is not None
            else sorted(p.name for p in self.state_root.iterdir() if p.is_dir())
        )

        buf = io.BytesIO()
        exported_count = 0
        manifest_collections: dict[str, int] = {}

        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for col in target_cols:
                col_dir = self.state_root / col
                if not col_dir.is_dir():
                    continue
                col_count = 0
                for fp in sorted(col_dir.glob("*.json")):
                    with contextlib.suppress(OSError, json.JSONDecodeError):
                        envelope = json.loads(fp.read_text(encoding="utf-8"))
                        data = envelope.get("data") if isinstance(envelope, dict) else None
                        if not isinstance(data, dict):
                            continue
                        if clean_owner:
                            rec_owner = str(data.get("owner_email") or data.get("email") or "").strip().lower()
                            if rec_owner != clean_owner:
                                continue
                        zf.writestr(f"state/{col}/{fp.name}", json.dumps(envelope, indent=2, sort_keys=True))
                        col_count += 1
                        exported_count += 1
                manifest_collections[col] = col_count

            manifest = {
                "schema_version": SCHEMA_VERSION,
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "owner_email": clean_owner or None,
                "record_count": exported_count,
                "collections": manifest_collections,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))

        return buf.getvalue()
