"""S3 client — connect, head, put, make_bucket.

Mocks boto3.client — no real S3 instance required. The integration test
(later) will hit a real MinIO instance.
"""
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture
def client():
    from mlss_monitor.backup.s3_client import S3Client
    return S3Client(
        endpoint="https://server.local:9000",
        region="auto",
        access_key="AK",
        secret_key="SK",
        bucket_prefix="mlss-",
    )


def test_init_does_not_make_network_calls():
    """Construction must not list buckets or otherwise touch the network."""
    from mlss_monitor.backup.s3_client import S3Client
    with patch("mlss_monitor.backup.s3_client.boto3.client") as mock_boto:
        # boto3.client itself can be called — it's just object construction —
        # but no method calls on the returned client should happen at init.
        S3Client(endpoint="x", region="auto", access_key="a", secret_key="b")
        # If boto3.client was called, the returned mock should NOT have
        # been used for any actual operations.
        client_mock = mock_boto.return_value
        client_mock.list_buckets.assert_not_called()
        client_mock.head_object.assert_not_called()
        client_mock.upload_file.assert_not_called()


def test_test_connection_returns_ok_on_success(client):
    """Happy path: list_buckets succeeds -> ok:True."""
    with patch.object(client, "_client") as mock_s3:
        mock_s3.list_buckets.return_value = {
            "Buckets": [{"Name": "mlss-photos"}, {"Name": "mlss-anomaly"}],
        }
        result = client.test_connection()
    assert result["ok"] is True


def test_test_connection_returns_error_on_auth_failure(client):
    """Auth failure surfaces as ok:False — must NOT raise."""
    from botocore.exceptions import ClientError
    err = ClientError(
        {"Error": {"Code": "InvalidAccessKeyId",
                   "Message": "The AWS Access Key Id you provided does not exist"}},
        "ListBuckets",
    )
    with patch.object(client, "_client") as mock_s3:
        mock_s3.list_buckets.side_effect = err
        result = client.test_connection()
    assert result["ok"] is False
    assert "InvalidAccessKeyId" in result["error"] or "Access Key" in result["error"]


def test_test_connection_returns_error_on_network_failure(client):
    """Connection failure (DNS / TLS / unreachable) — also ok:False."""
    with patch.object(client, "_client") as mock_s3:
        mock_s3.list_buckets.side_effect = Exception(
            "Could not connect to the endpoint URL")
        result = client.test_connection()
    assert result["ok"] is False
    assert "endpoint" in result["error"].lower()


def test_head_returns_true_when_object_exists(client):
    with patch.object(client, "_client") as mock_s3:
        mock_s3.head_object.return_value = {"ContentLength": 12345}
        assert client.head(bucket_suffix="photos", key="unit_1/x.jpg") is True
    mock_s3.head_object.assert_called_once_with(
        Bucket="mlss-photos", Key="unit_1/x.jpg"
    )


def test_head_returns_false_on_404(client):
    """404 NoSuchKey on head_object is the existence-check sentinel — must
    return False, not raise."""
    from botocore.exceptions import ClientError
    with patch.object(client, "_client") as mock_s3:
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        assert client.head(bucket_suffix="photos", key="missing.jpg") is False


def test_head_returns_false_on_nosuchkey_code(client):
    """Some S3 providers return 'NoSuchKey' as the error code instead of
    '404'. Both must be treated as 'object does not exist'."""
    from botocore.exceptions import ClientError
    with patch.object(client, "_client") as mock_s3:
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}},
            "HeadObject",
        )
        assert client.head(bucket_suffix="photos", key="missing.jpg") is False


def test_head_raises_on_other_errors(client):
    """Auth failures, network errors, etc. should propagate — caller
    decides retry strategy, not the head() method."""
    from botocore.exceptions import ClientError
    err = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}},
        "HeadObject",
    )
    with patch.object(client, "_client") as mock_s3:
        mock_s3.head_object.side_effect = err
        with pytest.raises(ClientError):
            client.head(bucket_suffix="photos", key="forbidden.jpg")


def test_put_uploads_file_with_correct_bucket_and_key(client, tmp_path):
    """put() should upload to `{bucket_prefix}{bucket_suffix}` with the
    given key + SHA256 metadata."""
    f = tmp_path / "test.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100)
    with patch.object(client, "_client") as mock_s3:
        client.put(
            bucket_suffix="photos",
            key="unit_1/2026-05-18/120000.jpg",
            source_path=str(f),
            sha256="deadbeef",
        )
    mock_s3.upload_file.assert_called_once()
    args, kwargs = mock_s3.upload_file.call_args
    # boto3.upload_file(Filename, Bucket, Key, ExtraArgs=...) accepts
    # positional or keyword args depending on style; check both.
    if args:
        assert args[0] == str(f)
        assert args[1] == "mlss-photos"
        assert args[2] == "unit_1/2026-05-18/120000.jpg"
    else:
        assert kwargs["Filename"] == str(f)
        assert kwargs["Bucket"] == "mlss-photos"
        assert kwargs["Key"] == "unit_1/2026-05-18/120000.jpg"
    extra = kwargs.get("ExtraArgs", args[3] if len(args) > 3 else None)
    assert extra is not None
    assert extra["Metadata"]["sha256"] == "deadbeef"


def test_make_bucket_creates_with_prefix(client):
    """make_bucket(suffix='photos') -> boto3.create_bucket(Bucket='mlss-photos')."""
    with patch.object(client, "_client") as mock_s3:
        client.make_bucket("photos")
    mock_s3.create_bucket.assert_called_once_with(Bucket="mlss-photos")


def test_make_bucket_idempotent_on_already_exists(client):
    """Re-running POST /init?pipeline=files shouldn't fail just because
    the bucket already exists. Swallow BucketAlreadyOwnedByYou."""
    from botocore.exceptions import ClientError
    with patch.object(client, "_client") as mock_s3:
        mock_s3.create_bucket.side_effect = ClientError(
            {"Error": {"Code": "BucketAlreadyOwnedByYou",
                       "Message": "You already own this bucket"}},
            "CreateBucket",
        )
        # Should not raise
        client.make_bucket("photos")


def test_make_bucket_raises_on_other_errors(client):
    """A real error (e.g. AccessDenied trying to create a bucket the
    operator doesn't own) should propagate."""
    from botocore.exceptions import ClientError
    err = ClientError(
        {"Error": {"Code": "AccessDenied",
                   "Message": "Not authorized to create bucket"}},
        "CreateBucket",
    )
    with patch.object(client, "_client") as mock_s3:
        mock_s3.create_bucket.side_effect = err
        with pytest.raises(ClientError):
            client.make_bucket("photos")


def test_init_passes_aws_credentials_to_boto3():
    """Verify the access_key / secret_key / region / endpoint / verify_tls
    are forwarded to boto3.client when it's first constructed."""
    from mlss_monitor.backup.s3_client import S3Client
    with patch("mlss_monitor.backup.s3_client.boto3.client") as mock_boto:
        S3Client(
            endpoint="https://server.local:9000",
            region="us-east-1",
            access_key="AKIA...",
            secret_key="SK...",
            bucket_prefix="mlss-",
            verify_tls="/etc/ssl/ca.crt",
            timeout=30,
        )
    mock_boto.assert_called_once()
    args, kwargs = mock_boto.call_args
    assert args[0] == "s3"
    assert kwargs["endpoint_url"] == "https://server.local:9000"
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["aws_access_key_id"] == "AKIA..."
    assert kwargs["aws_secret_access_key"] == "SK..."
    assert kwargs["verify"] == "/etc/ssl/ca.crt"


def test_init_empty_endpoint_passes_none_to_boto3():
    """endpoint='' (AWS-default) must NOT be passed as endpoint_url='';
    boto3 wants None there to fall back to its default endpoint
    resolution."""
    from mlss_monitor.backup.s3_client import S3Client
    with patch("mlss_monitor.backup.s3_client.boto3.client") as mock_boto:
        S3Client(endpoint="", region="us-east-1",
                 access_key="a", secret_key="b")
    kwargs = mock_boto.call_args.kwargs
    assert kwargs["endpoint_url"] is None
