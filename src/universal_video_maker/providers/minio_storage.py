"""S3-compatible object storage adapter for MinIO-hosted assets."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import SecretStr

from universal_video_maker.config import Settings

BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class MinioStorageError(Exception):
    """Sanitized MinIO/S3-compatible storage failure."""


class MinioObjectStore:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: SecretStr | str,
        secret_key: SecretStr | str,
        bucket: str = "universal-video-assets",
        region: str = "us-east-1",
        client: BaseClient | None = None,
    ) -> None:
        self._endpoint_url = _validate_endpoint(endpoint_url)
        self._access_key = _secret_value(access_key)
        self._secret_key = _secret_value(secret_key)
        if not self._access_key or not self._secret_key:
            raise ValueError("MinIO access and secret keys are required.")
        if not BUCKET_PATTERN.fullmatch(bucket):
            raise ValueError("MinIO bucket must be a valid lowercase S3 bucket name.")
        if not region.strip():
            raise ValueError("MinIO region cannot be empty.")
        self.bucket = bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=region,
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client: BaseClient | None = None,
    ) -> MinioObjectStore:
        if settings.minio_access_key is None or settings.minio_secret_key is None:
            raise ValueError(
                "Configure UVM_MINIO_ACCESS_KEY and UVM_MINIO_SECRET_KEY before using MinIO."
            )
        return cls(
            endpoint_url=settings.minio_endpoint_url,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            region=settings.minio_region,
            client=client,
        )

    def put_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str,
    ) -> tuple[str, str]:
        normalized_key = _validate_key(key)
        if not payload:
            raise ValueError("Cannot store an empty object.")
        if not content_type.strip():
            raise ValueError("Object content type cannot be empty.")
        checksum = hashlib.sha256(payload).hexdigest()
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=normalized_key,
                Body=payload,
                ContentType=content_type.strip(),
                Metadata={"sha256": checksum},
            )
        except (BotoCoreError, ClientError) as error:
            raise MinioStorageError("MinIO object upload failed.") from error
        return self._storage_uri(normalized_key), checksum

    def put_file(
        self,
        key: str,
        source: str | Path,
        *,
        content_type: str,
    ) -> tuple[str, str]:
        normalized_key = _validate_key(key)
        path = Path(source)
        if not path.is_file():
            raise ValueError("MinIO upload source must be an existing file.")
        if not content_type.strip():
            raise ValueError("Object content type cannot be empty.")
        checksum = _file_sha256(path)
        try:
            self._client.upload_file(
                str(path),
                self.bucket,
                normalized_key,
                ExtraArgs={
                    "ContentType": content_type.strip(),
                    "Metadata": {"sha256": checksum},
                },
            )
        except (BotoCoreError, ClientError) as error:
            raise MinioStorageError("MinIO file upload failed.") from error
        return self._storage_uri(normalized_key), checksum

    def download_to_file(self, key: str, destination: str | Path) -> None:
        normalized_key = _validate_key(key)
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self.bucket, normalized_key, str(path))
        except (BotoCoreError, ClientError) as error:
            raise MinioStorageError("MinIO object download failed.") from error

    def get_bytes(self, key: str) -> bytes:
        normalized_key = _validate_key(key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=normalized_key)
            body = response["Body"]
            try:
                payload = body.read()
            finally:
                body.close()
        except (BotoCoreError, ClientError, KeyError, AttributeError) as error:
            raise MinioStorageError("MinIO object download failed.") from error
        if not isinstance(payload, bytes):
            raise MinioStorageError("MinIO returned an invalid object body.")
        return payload

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise MinioStorageError("MinIO bucket check failed.") from error
        except BotoCoreError as error:
            raise MinioStorageError("MinIO bucket check failed.") from error
        try:
            self._client.create_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as error:
            raise MinioStorageError("MinIO bucket creation failed.") from error

    def presigned_get_url(self, storage_uri: str, *, expires_seconds: int = 900) -> str:
        key = self.key_from_uri(storage_uri)
        if not 1 <= expires_seconds <= 3600:
            raise ValueError("Download links must expire within one hour.")
        try:
            return str(
                self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=expires_seconds,
                )
            )
        except (BotoCoreError, ClientError) as error:
            raise MinioStorageError("MinIO download link could not be created.") from error

    def key_from_uri(self, storage_uri: str) -> str:
        parsed = urlsplit(storage_uri)
        if (
            parsed.scheme != "s3"
            or parsed.netloc != self.bucket
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Asset URI must identify an object in the configured MinIO bucket.")
        return _validate_key(unquote(parsed.path.lstrip("/")))

    def _storage_uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{quote(key, safe='/')}"


def _secret_value(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    is_local_http = parsed.scheme.lower() == "http" and hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (
        (parsed.scheme.lower() != "https" and not is_local_http)
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MinIO endpoint must be HTTPS, or HTTP on a loopback address.")
    return value.rstrip("/")


def _validate_key(key: str) -> str:
    normalized = key.strip()
    path_parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or any(part in {"", ".", ".."} for part in path_parts)
        or len(normalized.encode("utf-8")) > 1024
    ):
        raise ValueError("MinIO object key must be a safe relative object path.")
    return normalized


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise MinioStorageError("MinIO upload source could not be read.") from error
    return digest.hexdigest()
