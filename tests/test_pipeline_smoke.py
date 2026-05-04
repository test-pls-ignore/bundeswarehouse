"""
Smoke tests for the pipeline package.

These tests run without a live MinIO instance by mocking boto3.
They validate that the core S3 helpers, state management, and manifest
utilities work correctly in isolation.
"""

import json
import os
import unittest
from unittest.mock import MagicMock, patch


class TestStorageHelpers(unittest.TestCase):
    """Tests for pipeline.storage utility functions."""

    def setUp(self):
        os.environ["S3_ACCESS_KEY_ID"] = "testkey"
        os.environ["S3_SECRET_ACCESS_KEY"] = "testsecret"
        os.environ["S3_ENDPOINT_URL"] = "http://127.0.0.1:9000"
        os.environ["S3_BUCKET"] = "test-bucket"

    def tearDown(self):
        for var in ["S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL", "S3_BUCKET"]:
            os.environ.pop(var, None)

    def test_get_bucket_name_from_env(self):
        from pipeline.storage import get_bucket_name
        self.assertEqual(get_bucket_name(), "test-bucket")

    def test_get_bucket_name_default(self):
        from pipeline.storage import get_bucket_name
        os.environ.pop("S3_BUCKET", None)
        self.assertEqual(get_bucket_name(), "bundeswarehouse")

    def test_get_s3_client_missing_creds_raises(self):
        from pipeline.storage import get_s3_client
        os.environ.pop("S3_ACCESS_KEY_ID")
        with self.assertRaises(EnvironmentError):
            get_s3_client()

    def test_upload_bytes_success(self):
        from pipeline.storage import upload_bytes
        mock_client = MagicMock()
        upload_bytes(mock_client, "bucket", "raw/test.ndjson", b"hello", retries=1)
        mock_client.put_object.assert_called_once_with(
            Bucket="bucket",
            Key="raw/test.ndjson",
            Body=b"hello",
            ContentType="application/octet-stream",
        )

    def test_download_bytes_returns_none_on_404(self):
        from botocore.exceptions import ClientError
        from pipeline.storage import download_bytes

        mock_client = MagicMock()
        mock_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
        )
        result = download_bytes(mock_client, "bucket", "missing/key.json", retries=1)
        self.assertIsNone(result)

    def test_check_connection_returns_true_on_success(self):
        from pipeline.storage import check_connection
        mock_client = MagicMock()
        self.assertTrue(check_connection(mock_client, "bucket"))
        mock_client.head_bucket.assert_called_once_with(Bucket="bucket")

    def test_check_connection_returns_false_on_client_error(self):
        from botocore.exceptions import ClientError
        from pipeline.storage import check_connection
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket"
        )
        self.assertFalse(check_connection(mock_client, "bucket"))

    def test_ensure_bucket_creates_when_missing(self):
        from botocore.exceptions import ClientError
        from pipeline.storage import ensure_bucket_exists
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )
        ensure_bucket_exists(mock_client, "new-bucket")
        mock_client.create_bucket.assert_called_once_with(Bucket="new-bucket")

    def test_ensure_bucket_skips_create_when_exists(self):
        from pipeline.storage import ensure_bucket_exists
        mock_client = MagicMock()
        ensure_bucket_exists(mock_client, "existing-bucket")
        mock_client.create_bucket.assert_not_called()


class TestStateManagement(unittest.TestCase):
    """Tests for pipeline.state utility functions."""

    def test_mark_run_start_increments_count(self):
        from pipeline.state import mark_run_start
        state = {"run_count": 5, "last_run_at": None}
        updated = mark_run_start(state)
        self.assertEqual(updated["run_count"], 6)
        self.assertIsNotNone(updated["last_run_at"])

    def test_update_cursor(self):
        from pipeline.state import update_cursor
        state = {"cursors": {}}
        updated = update_cursor(state, "vorgang", "abc123")
        self.assertEqual(updated["cursors"]["vorgang"], "abc123")

    def test_clear_cursor(self):
        from pipeline.state import clear_cursor, update_cursor
        state = {"cursors": {}}
        state = update_cursor(state, "vorgang", "abc123")
        state = clear_cursor(state, "vorgang")
        self.assertNotIn("vorgang", state["cursors"])

    def test_update_last_seen(self):
        from pipeline.state import update_last_seen
        state = {"last_seen_update": None}
        updated = update_last_seen(state, "2024-01-15")
        self.assertEqual(updated["last_seen_update"], "2024-01-15")

    def test_save_and_load_state_local(self):
        import tempfile
        import os
        from pipeline import state as state_module
        original = state_module.STATE_LOCAL_PATH
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            tmp_path = fh.name
        try:
            state_module.STATE_LOCAL_PATH = tmp_path
            test_state = {"last_run_at": "2024-01-01T00:00:00Z", "run_count": 3, "cursors": {}, "last_seen_update": None}
            state_module.save_state(test_state)
            loaded = state_module.load_state()
            self.assertEqual(loaded["run_count"], 3)
        finally:
            state_module.STATE_LOCAL_PATH = original
            os.unlink(tmp_path)


class TestManifest(unittest.TestCase):
    """Tests for pipeline.manifest utility functions."""

    def test_record_object_adds_entry(self):
        from pipeline.manifest import record_object
        manifest = {"objects": []}
        updated = record_object(manifest, "raw/test.ndjson", 1024, "abc")
        self.assertEqual(len(updated["objects"]), 1)
        self.assertEqual(updated["objects"][0]["key"], "raw/test.ndjson")
        self.assertEqual(updated["objects"][0]["size_bytes"], 1024)

    def test_record_object_updates_existing(self):
        from pipeline.manifest import record_object
        manifest = {"objects": [{"key": "raw/test.ndjson", "size_bytes": 512}]}
        updated = record_object(manifest, "raw/test.ndjson", 1024)
        self.assertEqual(len(updated["objects"]), 1)
        self.assertEqual(updated["objects"][0]["size_bytes"], 1024)

    def test_sha256_of_bytes(self):
        from pipeline.manifest import sha256_of_bytes
        digest = sha256_of_bytes(b"hello")
        self.assertEqual(digest, "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")

    def test_load_manifest_returns_empty_when_none(self):
        from pipeline.manifest import load_manifest
        mock_client = MagicMock()
        mock_client.get_object.side_effect = __import__("botocore.exceptions", fromlist=["ClientError"]).ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}}, "GetObject"
        )
        manifest = load_manifest(mock_client, "bucket")
        self.assertEqual(manifest["objects"], [])


if __name__ == "__main__":
    unittest.main()
