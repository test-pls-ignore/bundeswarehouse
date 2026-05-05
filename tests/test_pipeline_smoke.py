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


def _make_mock_response(
    status_code: int = 200,
    content_type: str = "application/json",
    url: str = "https://search.dip.bundestag.de/api/v1/vorgang",
    body: bytes = b'{"documents": [], "cursor": null}',
) -> MagicMock:
    """Helper to create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.url = url
    resp.headers = {"Content-Type": content_type}
    resp.text = body.decode("utf-8", errors="replace")
    # Set up .json() to parse correctly for JSON bodies, raise for non-JSON.
    try:
        parsed = json.loads(body)
        resp.json.return_value = parsed
    except json.JSONDecodeError as exc:
        resp.json.side_effect = exc
    resp.raise_for_status = MagicMock()
    return resp


class TestFetchPage(unittest.TestCase):
    """Unit tests for pipeline.ingest.fetch_page."""

    def setUp(self):
        pass

    def _make_session_mock(self, responses):
        """Return a mock session whose .get() yields each response in sequence."""
        session = MagicMock()
        session.get.side_effect = responses
        return session

    # ------------------------------------------------------------------ #
    # Challenge-page detection                                             #
    # ------------------------------------------------------------------ #

    def test_challenge_url_raises_challenge_page_error(self):
        """A redirect to /.enodia/challenge must raise ChallengePageError."""
        from pipeline.ingest import ChallengePageError, fetch_page

        challenge_url = (
            "https://search.dip.bundestag.de/.enodia/challenge"
            "?redirect=%2Fapi%2Fv1%2Fvorgang%3Fformat%3Djson"
        )
        resp = _make_mock_response(
            status_code=400,
            content_type="text/html; charset=utf-8",
            url=challenge_url,
            body=b"<html><body>Please complete the challenge</body></html>",
        )
        resp.raise_for_status.side_effect = None  # don't raise before we check URL
        session = self._make_session_mock([resp])

        with self.assertRaises(ChallengePageError) as ctx:
            fetch_page(session, "vorgang", "testapikey", max_retries=0)

        err = ctx.exception
        self.assertIn("/.enodia/challenge", err.final_url)
        self.assertEqual(err.status_code, 400)

    def test_html_content_type_raises_challenge_page_error(self):
        """A response with Content-Type text/html must raise ChallengePageError."""
        from pipeline.ingest import ChallengePageError, fetch_page

        resp = _make_mock_response(
            status_code=200,
            content_type="text/html; charset=utf-8",
            url="https://search.dip.bundestag.de/api/v1/vorgang",
            body=b"<html><head><title>Challenge</title></head><body>...</body></html>",
        )
        session = self._make_session_mock([resp])

        with self.assertRaises(ChallengePageError) as ctx:
            fetch_page(session, "vorgang", "testapikey", max_retries=0)

        self.assertIn("text/html", ctx.exception.content_type)

    def test_challenge_error_attributes(self):
        """ChallengePageError should carry url, final_url, status_code, content_type, body_snippet."""
        from pipeline.ingest import ChallengePageError, fetch_page

        challenge_url = "https://search.dip.bundestag.de/.enodia/challenge?redirect=%2Fapi%2Fv1%2Fvorgang"
        body = b"<html>challenge</html>"
        resp = _make_mock_response(
            status_code=400,
            content_type="text/html",
            url=challenge_url,
            body=body,
        )
        session = self._make_session_mock([resp])

        with self.assertRaises(ChallengePageError) as ctx:
            fetch_page(session, "vorgang", "testapikey", max_retries=0)

        err = ctx.exception
        self.assertEqual(err.status_code, 400)
        self.assertIn("/.enodia/challenge", err.final_url)
        self.assertIn("text/html", err.content_type)
        self.assertIn("challenge", err.body_snippet)

    # ------------------------------------------------------------------ #
    # Non-JSON response                                                    #
    # ------------------------------------------------------------------ #

    def test_non_json_content_type_raises_value_error(self):
        """A 200 response with a non-JSON content type must raise ValueError."""
        from pipeline.ingest import fetch_page

        resp = _make_mock_response(
            status_code=200,
            content_type="text/plain; charset=utf-8",
            url="https://search.dip.bundestag.de/api/v1/vorgang",
            body=b"This is not JSON",
        )
        session = self._make_session_mock([resp])

        with self.assertRaises(ValueError) as ctx:
            fetch_page(session, "vorgang", "testapikey", max_retries=0)

        self.assertIn("text/plain", str(ctx.exception))

    # ------------------------------------------------------------------ #
    # Successful end-of-pagination                                         #
    # ------------------------------------------------------------------ #

    def test_valid_empty_documents_returns_empty_list(self):
        """A valid JSON response with no documents signals end of pagination."""
        from pipeline.ingest import fetch_page

        resp = _make_mock_response(
            status_code=200,
            content_type="application/json",
            url="https://search.dip.bundestag.de/api/v1/vorgang",
            body=b'{"documents": [], "cursor": null}',
        )
        session = self._make_session_mock([resp])

        docs, next_cursor = fetch_page(session, "vorgang", "testapikey", max_retries=0)

        self.assertEqual(docs, [])
        self.assertIsNone(next_cursor)

    def test_valid_response_returns_documents_and_cursor(self):
        """A valid JSON response returns documents and the next cursor."""
        from pipeline.ingest import fetch_page

        body = json.dumps(
            {"documents": [{"id": "1"}, {"id": "2"}], "cursor": "next-cursor-xyz"}
        ).encode()
        resp = _make_mock_response(
            status_code=200,
            content_type="application/json",
            url="https://search.dip.bundestag.de/api/v1/vorgang",
            body=body,
        )
        session = self._make_session_mock([resp])

        docs, next_cursor = fetch_page(session, "vorgang", "testapikey", max_retries=0)

        self.assertEqual(len(docs), 2)
        self.assertEqual(next_cursor, "next-cursor-xyz")

    # ------------------------------------------------------------------ #
    # Retry behaviour                                                      #
    # ------------------------------------------------------------------ #

    def test_retries_on_429_then_succeeds(self):
        """A 429 response should be retried; success on the second attempt is returned."""
        from pipeline.ingest import fetch_page

        body = json.dumps({"documents": [{"id": "1"}], "cursor": None}).encode()

        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.url = "https://search.dip.bundestag.de/api/v1/vorgang"
        rate_limited.headers = {"Content-Type": "application/json"}
        rate_limited.text = ""

        success = _make_mock_response(
            status_code=200,
            content_type="application/json",
            url="https://search.dip.bundestag.de/api/v1/vorgang",
            body=body,
        )
        session = self._make_session_mock([rate_limited, success])

        with patch("pipeline.ingest.time.sleep"):
            docs, cursor = fetch_page(session, "vorgang", "testapikey", max_retries=1)

        self.assertEqual(len(docs), 1)
        self.assertEqual(session.get.call_count, 2)

    def test_retries_on_401_then_succeeds(self):
        """A 401 response should be retried; success on the second attempt is returned."""
        from pipeline.ingest import fetch_page

        body = json.dumps({"documents": [{"id": "1"}], "cursor": None}).encode()

        unauthorized = MagicMock()
        unauthorized.status_code = 401
        unauthorized.url = "https://search.dip.bundestag.de/api/v1/aktivitaet"
        unauthorized.headers = {"Content-Type": "application/json"}
        unauthorized.text = '{"error": "Unauthorized"}'

        success = _make_mock_response(
            status_code=200,
            content_type="application/json",
            url="https://search.dip.bundestag.de/api/v1/aktivitaet",
            body=body,
        )
        session = self._make_session_mock([unauthorized, success])

        with patch("pipeline.ingest.time.sleep"):
            docs, cursor = fetch_page(session, "aktivitaet", "testapikey", max_retries=1)

        self.assertEqual(len(docs), 1)
        self.assertEqual(session.get.call_count, 2)

    def test_raises_after_all_401_retries_exhausted(self):
        """After max_retries 401s, fetch_page must raise (not return empty list)."""
        import requests as req_lib
        from pipeline.ingest import fetch_page

        unauthorized = MagicMock()
        unauthorized.status_code = 401
        unauthorized.url = "https://search.dip.bundestag.de/api/v1/aktivitaet"
        unauthorized.headers = {"Content-Type": "application/json"}
        unauthorized.text = '{"error": "Unauthorized"}'

        session = self._make_session_mock([unauthorized, unauthorized, unauthorized])

        with patch("pipeline.ingest.time.sleep"):
            with self.assertRaises(req_lib.HTTPError):
                fetch_page(session, "aktivitaet", "testapikey", max_retries=2)

    def test_raises_after_all_retries_exhausted(self):
        """After max_retries 429s, fetch_page must raise (not return empty list)."""
        import requests as req_lib
        from pipeline.ingest import fetch_page

        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.url = "https://search.dip.bundestag.de/api/v1/vorgang"
        rate_limited.headers = {"Content-Type": "application/json"}
        rate_limited.text = ""
        # build a proper HTTPError so the raise works
        rate_limited_exc = req_lib.HTTPError(response=rate_limited)

        session = self._make_session_mock([rate_limited, rate_limited, rate_limited])

        with patch("pipeline.ingest.time.sleep"):
            with self.assertRaises(req_lib.HTTPError):
                fetch_page(session, "vorgang", "testapikey", max_retries=2)


class TestIngestResourceChallengeHandling(unittest.TestCase):
    """Integration-level tests ensuring ingest_resource propagates ChallengePageError."""

    def test_ingest_resource_raises_on_challenge(self):
        """ingest_resource must re-raise ChallengePageError without writing any data."""
        from pipeline.ingest import ChallengePageError, ingest_resource

        challenge_url = "https://search.dip.bundestag.de/.enodia/challenge?redirect=%2F"
        resp = _make_mock_response(
            status_code=400,
            content_type="text/html",
            url=challenge_url,
            body=b"<html>challenge</html>",
        )

        mock_client = MagicMock()
        state = {"cursors": {}, "last_seen_update": None, "run_count": 1}
        manifest = {"objects": []}

        with patch("pipeline.ingest._make_session") as mock_make_session, \
             patch("pipeline.ingest.time.sleep"):
            mock_session = MagicMock()
            mock_session.get.return_value = resp
            mock_make_session.return_value = mock_session

            with self.assertRaises(ChallengePageError):
                ingest_resource("vorgang", "testapikey", mock_client, "bucket", state, manifest)

        # No S3 uploads should have occurred
        mock_client.put_object.assert_not_called()


class TestCLIApiKeyValidation(unittest.TestCase):
    """Tests for early BUNDESTAG_API_KEY validation in CLI commands."""

    def _clear_api_key(self):
        os.environ.pop("BUNDESTAG_API_KEY", None)

    def _set_api_key(self, value):
        os.environ["BUNDESTAG_API_KEY"] = value

    def tearDown(self):
        os.environ.pop("BUNDESTAG_API_KEY", None)

    def test_cmd_full_load_missing_api_key_returns_1(self):
        """cmd_full_load must return 1 immediately when BUNDESTAG_API_KEY is missing."""
        from pipeline.cli import cmd_full_load
        self._clear_api_key()
        result = cmd_full_load(None)
        self.assertEqual(result, 1)

    def test_cmd_full_load_empty_api_key_returns_1(self):
        """cmd_full_load must return 1 immediately when BUNDESTAG_API_KEY is empty."""
        from pipeline.cli import cmd_full_load
        self._set_api_key("   ")
        result = cmd_full_load(None)
        self.assertEqual(result, 1)

    def test_cmd_incremental_update_missing_api_key_returns_1(self):
        """cmd_incremental_update must return 1 immediately when BUNDESTAG_API_KEY is missing."""
        from pipeline.cli import cmd_incremental_update
        self._clear_api_key()
        result = cmd_incremental_update(None)
        self.assertEqual(result, 1)

    def test_cmd_incremental_update_empty_api_key_returns_1(self):
        """cmd_incremental_update must return 1 immediately when BUNDESTAG_API_KEY is empty."""
        from pipeline.cli import cmd_incremental_update
        self._set_api_key("")
        result = cmd_incremental_update(None)
        self.assertEqual(result, 1)


class TestMakeS3Key(unittest.TestCase):
    """Tests for the deterministic _make_s3_key path generation."""

    def test_key_format_includes_resource_and_batch(self):
        """Key must contain the resource name and zero-padded batch index."""
        from pipeline.ingest import _make_s3_key
        key = _make_s3_key("aktivitaet", 0)
        self.assertEqual(key, "raw/aktivitaet/batch_00000.ndjson")

    def test_key_is_deterministic_across_calls(self):
        """Same arguments must always return the same key (no timestamps)."""
        from pipeline.ingest import _make_s3_key
        key1 = _make_s3_key("vorgang", 42)
        key2 = _make_s3_key("vorgang", 42)
        self.assertEqual(key1, key2)

    def test_different_batch_indices_produce_different_keys(self):
        """Different batch indices for the same resource must produce distinct keys."""
        from pipeline.ingest import _make_s3_key
        key0 = _make_s3_key("drucksache", 0)
        key1 = _make_s3_key("drucksache", 1)
        self.assertNotEqual(key0, key1)
        self.assertEqual(key0, "raw/drucksache/batch_00000.ndjson")
        self.assertEqual(key1, "raw/drucksache/batch_00001.ndjson")

    def test_different_resources_produce_different_keys(self):
        """Different resources at the same batch index must produce distinct keys."""
        from pipeline.ingest import _make_s3_key
        key_a = _make_s3_key("vorgang", 0)
        key_b = _make_s3_key("aktivitaet", 0)
        self.assertNotEqual(key_a, key_b)

    def test_key_does_not_contain_date(self):
        """Key must not include a date component (would break idempotency)."""
        from pipeline.ingest import _make_s3_key
        import re
        key = _make_s3_key("plenarprotokoll", 5)
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", key))


class TestDeletePrefix(unittest.TestCase):
    """Tests for pipeline.storage.delete_prefix."""

    def _make_paginator(self, pages):
        """Return a mock paginator whose paginate() yields *pages*."""
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = iter(pages)
        return mock_paginator

    def test_delete_prefix_deletes_all_objects(self):
        """delete_prefix must call delete_objects for every listed key."""
        from pipeline.storage import delete_prefix

        mock_client = MagicMock()
        mock_client.get_paginator.return_value = self._make_paginator([
            {"Contents": [{"Key": "raw/aktivitaet/batch_00000.ndjson"},
                          {"Key": "raw/aktivitaet/batch_00001.ndjson"}]},
        ])
        mock_client.delete_objects.return_value = {"Errors": []}

        count = delete_prefix(mock_client, "bucket", "raw/aktivitaet/")

        mock_client.delete_objects.assert_called_once()
        call_kwargs = mock_client.delete_objects.call_args[1]
        keys = [o["Key"] for o in call_kwargs["Delete"]["Objects"]]
        self.assertIn("raw/aktivitaet/batch_00000.ndjson", keys)
        self.assertIn("raw/aktivitaet/batch_00001.ndjson", keys)
        self.assertEqual(count, 2)

    def test_delete_prefix_returns_zero_when_empty(self):
        """delete_prefix must return 0 and not call delete_objects if prefix is empty."""
        from pipeline.storage import delete_prefix

        mock_client = MagicMock()
        mock_client.get_paginator.return_value = self._make_paginator([
            {"Contents": []},
        ])

        count = delete_prefix(mock_client, "bucket", "raw/empty/")

        mock_client.delete_objects.assert_not_called()
        self.assertEqual(count, 0)

    def test_delete_prefix_handles_multiple_pages(self):
        """delete_prefix must issue one delete_objects call per page of results."""
        from pipeline.storage import delete_prefix

        mock_client = MagicMock()
        mock_client.get_paginator.return_value = self._make_paginator([
            {"Contents": [{"Key": "raw/vorgang/batch_00000.ndjson"}]},
            {"Contents": [{"Key": "raw/vorgang/batch_00001.ndjson"}]},
        ])
        mock_client.delete_objects.return_value = {"Errors": []}

        count = delete_prefix(mock_client, "bucket", "raw/vorgang/")

        self.assertEqual(mock_client.delete_objects.call_count, 2)
        self.assertEqual(count, 2)


class TestCleanupRawCLI(unittest.TestCase):
    """Tests for the cleanup-raw CLI command."""

    def setUp(self):
        os.environ["S3_ACCESS_KEY_ID"] = "testkey"
        os.environ["S3_SECRET_ACCESS_KEY"] = "testsecret"
        os.environ["S3_ENDPOINT_URL"] = "http://127.0.0.1:9000"
        os.environ["S3_BUCKET"] = "test-bucket"

    def tearDown(self):
        for var in ["S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL", "S3_BUCKET"]:
            os.environ.pop(var, None)

    def test_cleanup_raw_wrong_confirm_returns_1(self):
        """cleanup-raw must return 1 if --confirm is not exactly 'DELETE'."""
        from pipeline.cli import cmd_cleanup_raw
        args = MagicMock()
        args.prefix = "raw/"
        args.confirm = "delete"  # wrong case
        result = cmd_cleanup_raw(args)
        self.assertEqual(result, 1)

    def test_cleanup_raw_empty_confirm_returns_1(self):
        """cleanup-raw must return 1 if --confirm is empty."""
        from pipeline.cli import cmd_cleanup_raw
        args = MagicMock()
        args.prefix = "raw/"
        args.confirm = ""
        result = cmd_cleanup_raw(args)
        self.assertEqual(result, 1)

    def test_cleanup_raw_correct_confirm_calls_delete_prefix(self):
        """cleanup-raw with --confirm DELETE must call delete_prefix."""
        from pipeline.cli import cmd_cleanup_raw

        args = MagicMock()
        args.prefix = "raw/aktivitaet/"
        args.confirm = "DELETE"

        mock_client = MagicMock()
        # Paginator returns one page with one object (so the deletion path is taken)
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = iter([
            {"Contents": [{"Key": "raw/aktivitaet/batch_00000.ndjson"}]},
        ])
        mock_client.get_paginator.return_value = mock_paginator

        with patch("pipeline.cli.get_s3_client", return_value=mock_client), \
             patch("pipeline.cli.get_bucket_name", return_value="test-bucket"), \
             patch("pipeline.cli.delete_prefix", return_value=1) as mock_dp:
            result = cmd_cleanup_raw(args)

        self.assertEqual(result, 0)
        mock_dp.assert_called_once_with(mock_client, "test-bucket", "raw/aktivitaet/")


if __name__ == "__main__":
    unittest.main()

