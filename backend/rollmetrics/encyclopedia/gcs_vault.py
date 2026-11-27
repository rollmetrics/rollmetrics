"""Private Google Cloud Storage (GCS) vault adapter for the IBJJF Match Encyclopedia."""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import subprocess
from pathlib import Path


def _gcloud_env() -> dict[str, str]:
    env = dict(os.environ)
    extra = "/opt/homebrew/bin:/usr/local/bin"
    env["PATH"] = f"{extra}:{env.get('PATH', '')}"
    return env


def _resolve_gcloud() -> str:
    env = _gcloud_env()
    binary = shutil.which("gcloud", path=env["PATH"])
    if not binary:
        raise FileNotFoundError("gcloud CLI not found in PATH")
    return binary


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class GCSVault:
    """Thin, resumable wrapper over `gcloud storage` for private GCS vault management."""

    def __init__(
        self,
        bucket: str = "gs://rollmetrics-ibjjf-vault",
        project: str = "collyder-74093",
        location: str = "us-central1",
    ) -> None:
        cleaned = bucket.strip().rstrip("/")
        if not cleaned.startswith("gs://"):
            cleaned = f"gs://{cleaned}"
        self.bucket = cleaned
        self.project = project
        self.location = location

    def uri(self, rel_path: str) -> str:
        return f"{self.bucket}/{rel_path.lstrip('/')}"

    def _run(self, args: list[str], *, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
        gcloud = _resolve_gcloud()
        return subprocess.run(
            [gcloud, *args],
            env=_gcloud_env(),
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    def bucket_exists(self) -> bool:
        proc = self._run(["storage", "buckets", "describe", self.bucket], check=False, timeout=30)
        return proc.returncode == 0

    def ensure_bucket(self) -> str:
        """Creates the private uniform-access bucket if it does not yet exist."""
        if self.bucket_exists():
            return self.bucket
        self._run(
            [
                "storage",
                "buckets",
                "create",
                self.bucket,
                f"--project={self.project}",
                f"--location={self.location}",
                "--uniform-bucket-level-access",
                "--public-access-prevention",
            ],
            check=True,
            timeout=60,
        )
        return self.bucket

    def exists(self, rel_path: str) -> bool:
        proc = self._run(["storage", "ls", self.uri(rel_path)], check=False, timeout=30)
        return proc.returncode == 0

    def upload_file(self, local_path: Path, rel_path: str) -> tuple[str, int, str]:
        """Uploads a local file to GCS and returns (gcs_uri, size_bytes, sha256)."""
        local_path = Path(local_path)
        size_bytes = local_path.stat().st_size
        digest = file_sha256(local_path)
        dest = self.uri(rel_path)
        self._run(["storage", "cp", str(local_path), dest], check=True, timeout=900)
        return dest, size_bytes, digest

    def download_file(self, rel_path: str, local_path: Path) -> Path:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["storage", "cp", self.uri(rel_path), str(local_path)], check=True, timeout=900)
        return local_path

    def backup_catalog(self, db_path: Path) -> tuple[str, int, str]:
        """Compresses and uploads the SQLite catalog to `catalog/catalog.sqlite.gz`."""
        db_path = Path(db_path)
        gz_path = db_path.with_suffix(".sqlite.gz")
        with db_path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            return self.upload_file(gz_path, "catalog/catalog.sqlite.gz")
        finally:
            if gz_path.exists():
                gz_path.unlink()
