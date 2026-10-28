import hashlib
import io
from pathlib import Path

import pytest
from pydantic import ValidationError

from universal_video_maker.config import Settings
from universal_video_maker.providers.minio_storage import (
    MinioObjectStore,
    MinioStorageError,
)
from universal_video_maker.rendering import (
    ArtifactFacts,
    ArtifactInspectionError,
    MinioArtifactInspector,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.uploads: list[dict[str, object]] = []
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs) -> None:
        self.uploads.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def upload_file(self, source, bucket, key, *, ExtraArgs) -> None:
        self.uploads.append({"Bucket": bucket, "Key": key, **ExtraArgs})
        self.objects[key] = Path(source).read_bytes()

    def download_file(self, bucket, key, destination) -> None:
        Path(destination).write_bytes(self.objects[key])

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


class FakeLocalInspector:
    def __init__(self, facts: ArtifactFacts) -> None:
        self.facts = facts
        self.paths: list[str] = []

    def inspect(self, storage_uri: str) -> ArtifactFacts:
        self.paths.append(storage_uri)
        path = Path(storage_uri.removeprefix("file:///"))
        assert path.is_file()
        return self.facts


def _store(client: FakeS3Client) -> MinioObjectStore:
    return MinioObjectStore(
        endpoint_url="http://localhost:9000",
        access_key="local-access",
        secret_key="local-secret",
        client=client,
    )


def test_minio_store_uploads_checksummed_objects_and_downloads_them() -> None:
    client = FakeS3Client()
    store = _store(client)
    payload = b"verified media bytes"

    uri, checksum = store.put_bytes(
        "episodes/episode-1/episode.mp4",
        payload,
        content_type="video/mp4",
    )

    assert uri == "s3://universal-video-assets/episodes/episode-1/episode.mp4"
    assert checksum == hashlib.sha256(payload).hexdigest()
    assert client.uploads[0]["Metadata"] == {"sha256": checksum}
    assert store.get_bytes("episodes/episode-1/episode.mp4") == payload
    assert store.key_from_uri(uri) == "episodes/episode-1/episode.mp4"


def test_minio_creates_missing_bucket_and_presigns_downloads() -> None:
    from botocore.exceptions import ClientError

    class BucketClient(FakeS3Client):
        def __init__(self) -> None:
            super().__init__()
            self.created: list[str] = []

        def head_bucket(self, *, Bucket) -> None:
            if not self.created:
                raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

        def create_bucket(self, *, Bucket) -> None:
            self.created.append(Bucket)

        def generate_presigned_url(self, operation, *, Params, ExpiresIn) -> str:
            return f"http://localhost:9000/{Params['Bucket']}/{Params['Key']}?ttl={ExpiresIn}"

    client = BucketClient()
    store = _store(client)
    store.ensure_bucket()
    store.ensure_bucket()

    assert client.created == ["universal-video-assets"]
    url = store.presigned_get_url("s3://universal-video-assets/a/b.mp4", expires_seconds=60)
    assert url.endswith("/a/b.mp4?ttl=60")
    with pytest.raises(ValueError):
        store.presigned_get_url("s3://other-bucket/a.mp4")
    with pytest.raises(ValueError):
        store.presigned_get_url("s3://universal-video-assets/a.mp4", expires_seconds=0)


def test_minio_file_upload_and_path_validation(tmp_path) -> None:
    client = FakeS3Client()
    store = _store(client)
    source = tmp_path / "captions.vtt"
    source.write_text("WEBVTT\n", encoding="utf-8")

    uri, checksum = store.put_file(
        "episode-1/captions.vtt",
        source,
        content_type="text/vtt",
    )

    assert uri.endswith("/episode-1/captions.vtt")
    assert checksum == hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="safe relative"):
        store.put_bytes("../escape", b"data", content_type="text/plain")
    with pytest.raises(ValueError, match="configured MinIO bucket"):
        store.key_from_uri("s3://another-bucket/object")


def test_minio_artifact_inspector_downloads_before_media_validation() -> None:
    client = FakeS3Client()
    store = _store(client)
    uri, checksum = store.put_bytes(
        "episode-1/video.mp4",
        b"video contents",
        content_type="video/mp4",
    )
    facts = ArtifactFacts(
        checksum=f"sha256:{checksum}",
        content_type="video/mp4",
        width=1920,
        height=1080,
        duration_seconds=180,
        has_audio=True,
    )
    local_inspector = FakeLocalInspector(facts)

    result = MinioArtifactInspector(store, local_inspector).inspect(uri)

    assert result == facts
    assert len(local_inspector.paths) == 1
    assert local_inspector.paths[0].startswith("file:///")


def test_minio_artifact_inspector_sanitizes_unrecognized_file_suffix() -> None:
    client = FakeS3Client()
    store = _store(client)
    uri, checksum = store.put_bytes(
        "episode-1/video.mp4:unexpected",
        b"video contents",
        content_type="video/mp4",
    )
    facts = ArtifactFacts(
        checksum=f"sha256:{checksum}",
        content_type="video/mp4",
        width=1920,
        height=1080,
        duration_seconds=180,
        has_audio=True,
    )
    local_inspector = FakeLocalInspector(facts)

    MinioArtifactInspector(store, local_inspector).inspect(uri)

    assert local_inspector.paths[0].endswith("/artifact.bin")


def test_minio_configuration_requires_credentials_and_secure_remote_endpoint() -> None:
    with pytest.raises(ValueError, match="access and secret keys"):
        MinioObjectStore(endpoint_url="http://localhost:9000", access_key="", secret_key="")
    with pytest.raises(ValueError, match="HTTPS"):
        MinioObjectStore(
            endpoint_url="http://minio.example.org",
            access_key="key",
            secret_key="secret",
        )
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(_env_file=None, minio_endpoint_url="http://minio.example.org")


def test_minio_inspector_rejects_download_errors() -> None:
    class FailingS3Client(FakeS3Client):
        def download_file(self, bucket, key, destination) -> None:
            raise MinioStorageError("Download unavailable.")

    store = _store(FailingS3Client())
    inspector = MinioArtifactInspector(store)

    with pytest.raises(ArtifactInspectionError, match="could not be downloaded"):
        inspector.inspect("s3://universal-video-assets/episode/video.mp4")
