"""S3 client for the backup pipeline (files).

Wraps boto3 with bucket-prefix-aware put/head/list operations. Works
with AWS S3 and S3-compatible blob stores (MinIO, SeaweedFS, Cloudflare
R2, Backblaze B2) — pass the appropriate ``endpoint`` URL or empty
string for AWS default endpoint resolution.

Bucket naming convention:
  ``{bucket_prefix}{suffix}`` — e.g. bucket_prefix="mlss-" + suffix="photos"
  -> ``mlss-photos``. Call sites use the suffix only, keeping prefix
  decisions inside this module.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


# S3 codes we treat as "object does not exist". Different S3 providers
# normalise this differently — AWS returns "404", MinIO + some others
# return "NoSuchKey".
_NOT_FOUND_CODES = frozenset(["404", "NoSuchKey"])


class S3Client:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        bucket_prefix: str = "mlss-",
        verify_tls: bool | str = True,
        timeout: int = 10,
    ) -> None:
        cfg = Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 1},
        )
        # boto3 wants None (not "") for the default-AWS-endpoint case;
        # passing endpoint_url="" makes it raise.
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint if endpoint else None,
            region_name=region if region else None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            verify=verify_tls,
            config=cfg,
        )
        self.prefix = bucket_prefix

    def _bucket(self, suffix: str) -> str:
        return f"{self.prefix}{suffix}"

    def test_connection(self) -> dict:
        """Try to list buckets — cheap server-side, doesn't need our
        buckets to exist yet (POST /init runs make_bucket later).
        Returns a dict — never raises."""
        try:
            self._client.list_buckets()
            return {"ok": True}
        except Exception as exc:  # pylint: disable=broad-except
            return {"ok": False, "error": str(exc)}

    def head(self, *, bucket_suffix: str, key: str) -> bool:
        """Return True if the object exists, False on 404/NoSuchKey, and
        raise on any other ClientError (auth, network, etc.)."""
        try:
            self._client.head_object(
                Bucket=self._bucket(bucket_suffix), Key=key,
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in _NOT_FOUND_CODES:
                return False
            raise

    def put(
        self,
        *,
        bucket_suffix: str,
        key: str,
        source_path: str,
        sha256: str,
    ) -> None:
        """Upload ``source_path`` to ``{bucket_prefix}{bucket_suffix}/{key}``
        with sha256 as object metadata.

        boto3.upload_file uses multipart for large objects automatically,
        so this works for any blob size we care about (camera JPEGs ~2MB,
        ML model pickles up to ~100MB).
        """
        self._client.upload_file(
            source_path,
            self._bucket(bucket_suffix),
            key,
            ExtraArgs={"Metadata": {"sha256": sha256}},
        )

    def make_bucket(self, suffix: str) -> None:
        """Idempotent bucket creation. Used by POST /init?pipeline=files
        to set up the server-side buckets on first run."""
        try:
            self._client.create_bucket(Bucket=self._bucket(suffix))
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                raise
