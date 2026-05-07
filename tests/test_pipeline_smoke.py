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

    def test_set_full_load_run(self):
        from pipeline.state import set_full_load_run
        state = {"cursors": {}, "full_load_completed_resources": []}
        updated = set_full_load_run(state, "run-abc")
        self.assertEqual(updated["full_load_run_id"], "run-abc")
        self.assertEqual(updated["full_load_completed_resources"], [])

    def test_mark_full_load_resource_done(self):
        from pipeline.state import mark_full_load_resource_done
        state = {"full_load_completed_resources": ["vorgang"]}
        updated = mark_full_load_resource_done(state, "drucksache")
        self.assertIn("vorgang", updated["full_load_completed_resources"])
        self.assertIn("drucksache", updated["full_load_completed_resources"])

    def test_mark_full_load_resource_done_idempotent(self):
        from pipeline.state import mark_full_load_resource_done
        state = {"full_load_completed_resources": ["vorgang"]}
        updated = mark_full_load_resource_done(state, "vorgang")
        self.assertEqual(updated["full_load_completed_resources"].count("vorgang"), 1)

    def test_clear_full_load(self):
        from pipeline.state import clear_full_load
        state = {"full_load_run_id": "run-abc", "full_load_completed_resources": ["vorgang"]}
        updated = clear_full_load(state)
        self.assertIsNone(updated["full_load_run_id"])
        self.assertEqual(updated["full_load_completed_resources"], [])

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

    def test_retries_on_challenge_then_succeeds(self):
        """A challenge response should be retried; success on the second attempt is returned."""
        from pipeline.ingest import fetch_page

        challenge = _make_mock_response(
            status_code=503,
            content_type="text/html; charset=utf-8",
            url="https://search.dip.bundestag.de/.enodia/challenge?redirect=%2Fapi%2Fv1%2Fvorgang",
            body=b"<html><body>challenge</body></html>",
        )
        body = json.dumps({"documents": [{"id": "1"}], "cursor": None}).encode()
        success = _make_mock_response(
            status_code=200,
            content_type="application/json",
            url="https://search.dip.bundestag.de/api/v1/vorgang",
            body=body,
        )
        session = self._make_session_mock([challenge, success])

        with patch("pipeline.ingest.time.sleep"):
            docs, cursor = fetch_page(session, "vorgang", "testapikey", max_retries=1)

        self.assertEqual(len(docs), 1)
        self.assertIsNone(cursor)
        self.assertEqual(session.get.call_count, 2)

    def test_raises_after_all_challenge_retries_exhausted(self):
        """After max_retries challenge responses, fetch_page must raise ChallengePageError."""
        from pipeline.ingest import ChallengePageError, fetch_page

        challenge = _make_mock_response(
            status_code=503,
            content_type="text/html; charset=utf-8",
            url="https://search.dip.bundestag.de/.enodia/challenge?redirect=%2Fapi%2Fv1%2Fvorgang",
            body=b"<html><body>challenge</body></html>",
        )
        session = self._make_session_mock([challenge, challenge, challenge])

        with patch("pipeline.ingest.time.sleep"):
            with self.assertRaises(ChallengePageError):
                fetch_page(session, "vorgang", "testapikey", max_retries=2)

        self.assertEqual(session.get.call_count, 3)

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


class TestPrefixHelpers(unittest.TestCase):
    """Tests for pipeline.storage list_prefix / delete_prefix / copy_object."""

    # ------------------------------------------------------------------ #
    # list_prefix                                                          #
    # ------------------------------------------------------------------ #

    def test_list_prefix_returns_all_keys(self):
        """list_prefix should collect every key returned by the paginator."""
        from pipeline.storage import list_prefix

        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = iter([
            {"Contents": [
                {"Key": "raw/_staging/run1/vorgang/batch_00001.ndjson"},
                {"Key": "raw/_staging/run1/vorgang/batch_00002.ndjson"},
            ]},
            {"Contents": [
                {"Key": "raw/_staging/run1/drucksache/batch_00001.ndjson"},
            ]},
        ])

        keys = list_prefix(mock_client, "bucket", "raw/_staging/run1/")

        self.assertEqual(len(keys), 3)
        self.assertIn("raw/_staging/run1/vorgang/batch_00001.ndjson", keys)
        self.assertIn("raw/_staging/run1/drucksache/batch_00001.ndjson", keys)
        mock_client.get_paginator.assert_called_once_with("list_objects_v2")

    def test_list_prefix_returns_empty_when_no_contents(self):
        """list_prefix should return [] when no objects match the prefix."""
        from pipeline.storage import list_prefix

        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = iter([{}])  # page with no 'Contents'

        keys = list_prefix(mock_client, "bucket", "raw/_staging/nonexistent/")

        self.assertEqual(keys, [])

    # ------------------------------------------------------------------ #
    # delete_prefix                                                        #
    # ------------------------------------------------------------------ #

    def test_delete_prefix_deletes_all_objects(self):
        """delete_prefix should call delete_objects with all found keys."""
        from pipeline.storage import delete_prefix

        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = iter([
            {"Contents": [
                {"Key": "raw/_staging/run1/a.ndjson"},
                {"Key": "raw/_staging/run1/b.ndjson"},
            ]},
        ])

        count = delete_prefix(mock_client, "bucket", "raw/_staging/run1/")

        self.assertEqual(count, 2)
        mock_client.delete_objects.assert_called_once_with(
            Bucket="bucket",
            Delete={"Objects": [
                {"Key": "raw/_staging/run1/a.ndjson"},
                {"Key": "raw/_staging/run1/b.ndjson"},
            ]},
        )

    def test_delete_prefix_returns_zero_on_empty_prefix(self):
        """delete_prefix should return 0 and skip delete_objects when nothing found."""
        from pipeline.storage import delete_prefix

        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = iter([{}])

        count = delete_prefix(mock_client, "bucket", "raw/_staging/nonexistent/")

        self.assertEqual(count, 0)
        mock_client.delete_objects.assert_not_called()


class TestMakeS3KeyStaging(unittest.TestCase):
    """Tests for pipeline.ingest._make_s3_key with the prefix parameter."""

    def test_make_s3_key_with_prefix_uses_prefix(self):
        """When prefix is given, key should be <prefix><resource>/batch_XXXXX.ndjson."""
        from pipeline.ingest import _make_s3_key

        key = _make_s3_key("vorgang", 5, prefix="raw/_staging/run1/")
        self.assertEqual(key, "raw/_staging/run1/vorgang/batch_00005.ndjson")

    def test_make_s3_key_without_prefix_uses_date(self):
        """Without a prefix, key should follow the legacy date-partitioned layout."""
        from pipeline.ingest import _make_s3_key

        key = _make_s3_key("vorgang", 0)
        self.assertRegex(key, r"^raw/vorgang/\d{4}-\d{2}-\d{2}/batch_00000\.ndjson$")

    def test_make_s3_key_zero_pads_batch_index(self):
        """Batch index should be zero-padded to 5 digits."""
        from pipeline.ingest import _make_s3_key

        key = _make_s3_key("drucksache", 42, prefix="raw/_staging/run1/")
        self.assertEqual(key, "raw/_staging/run1/drucksache/batch_00042.ndjson")


class TestPublishFullLoad(unittest.TestCase):
    """Tests for pipeline.ingest.publish_full_load."""

    def _make_client_with_staging_keys(self, staging_keys):
        """Return a mock S3 client whose paginator yields:
        - first call: empty (for delete_prefix of current/)
        - second call: staging keys (for list_prefix of staging/)
        """
        mock_client = MagicMock()
        paginator_current = MagicMock()
        paginator_current.paginate.return_value = iter([{}])  # empty current
        paginator_staging = MagicMock()
        page = {"Contents": [{"Key": k} for k in staging_keys]} if staging_keys else {}
        paginator_staging.paginate.return_value = iter([page])
        mock_client.get_paginator.side_effect = [paginator_current, paginator_staging]
        return mock_client

    def test_manifest_keys_remapped_from_staging_to_current(self):
        """publish_full_load must remap manifest object keys from staging to current."""
        from pipeline.ingest import publish_full_load

        staging_keys = [
            "raw/_staging/run1/vorgang/batch_00001.ndjson",
            "raw/_staging/run1/drucksache/batch_00001.ndjson",
        ]
        mock_client = self._make_client_with_staging_keys(staging_keys)
        manifest = {
            "objects": [
                {"key": "raw/_staging/run1/vorgang/batch_00001.ndjson", "size_bytes": 100},
                {"key": "raw/_staging/run1/drucksache/batch_00001.ndjson", "size_bytes": 200},
            ]
        }
        state = {"run_count": 1}

        updated = publish_full_load(mock_client, "bucket", "run1", state, manifest)

        keys = [o["key"] for o in updated["objects"]]
        self.assertIn("raw/current/vorgang/batch_00001.ndjson", keys)
        self.assertIn("raw/current/drucksache/batch_00001.ndjson", keys)
        self.assertNotIn("raw/_staging/run1/vorgang/batch_00001.ndjson", keys)
        self.assertNotIn("raw/_staging/run1/drucksache/batch_00001.ndjson", keys)

    def test_copy_object_called_for_each_staging_key(self):
        """publish_full_load must copy every staging object to current."""
        from pipeline.ingest import publish_full_load

        staging_keys = [
            "raw/_staging/run1/vorgang/batch_00001.ndjson",
            "raw/_staging/run1/vorgang/batch_00002.ndjson",
        ]
        mock_client = self._make_client_with_staging_keys(staging_keys)
        manifest = {"objects": [{"key": k, "size_bytes": 10} for k in staging_keys]}
        state = {"run_count": 2}

        publish_full_load(mock_client, "bucket", "run1", state, manifest)

        self.assertEqual(mock_client.copy_object.call_count, 2)

    def test_staging_objects_deleted_after_publish(self):
        """publish_full_load must delete staging objects once they are copied."""
        from pipeline.ingest import publish_full_load

        staging_keys = ["raw/_staging/run1/vorgang/batch_00001.ndjson"]
        mock_client = self._make_client_with_staging_keys(staging_keys)
        manifest = {"objects": [{"key": k, "size_bytes": 50} for k in staging_keys]}
        state = {"run_count": 1}

        publish_full_load(mock_client, "bucket", "run1", state, manifest)

        all_delete_calls = mock_client.delete_objects.call_args_list
        deleted_keys = []
        for call in all_delete_calls:
            deleted_keys.extend(
                obj["Key"] for obj in call.kwargs.get("Delete", {}).get("Objects", [])
            )
        self.assertIn("raw/_staging/run1/vorgang/batch_00001.ndjson", deleted_keys)

    def test_latest_run_json_written_on_publish(self):
        """publish_full_load must upload LATEST_RUN.json with the correct run_id."""
        from pipeline.ingest import publish_full_load

        mock_client = self._make_client_with_staging_keys([])
        manifest = {"objects": []}
        state = {"run_count": 3}

        publish_full_load(mock_client, "bucket", "run-abc", state, manifest)

        put_calls = mock_client.put_object.call_args_list
        latest_run_calls = [
            c for c in put_calls if c.kwargs.get("Key") == "raw/LATEST_RUN.json"
        ]
        self.assertEqual(len(latest_run_calls), 1)
        body = latest_run_calls[0].kwargs["Body"]
        pointer = json.loads(body)
        self.assertEqual(pointer["run_id"], "run-abc")
        self.assertEqual(pointer["run_count"], 3)


class TestCleanupCLI(unittest.TestCase):
    """Tests for pipeline.cli cleanup-staging and cleanup-current."""

    _S3_ENV_VARS = ["S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL", "S3_BUCKET"]

    def setUp(self):
        os.environ["S3_ACCESS_KEY_ID"] = "testkey"
        os.environ["S3_SECRET_ACCESS_KEY"] = "testsecret"
        os.environ["S3_ENDPOINT_URL"] = "http://127.0.0.1:9000"
        os.environ["S3_BUCKET"] = "test-bucket"

    def tearDown(self):
        for var in self._S3_ENV_VARS:
            os.environ.pop(var, None)

    def _make_mock_s3(self, keys=None):
        mock_client = MagicMock()
        paginator = MagicMock()
        mock_client.get_paginator.return_value = paginator
        contents = [{"Key": k} for k in (keys or [])]
        paginator.paginate.return_value = iter(
            [{"Contents": contents}] if contents else [{}]
        )
        return mock_client

    def test_cleanup_staging_returns_0(self):
        from pipeline.cli import cmd_cleanup_staging

        with patch("pipeline.cli.get_s3_client") as mock_get, \
             patch("pipeline.cli.get_bucket_name", return_value="test-bucket"):
            mock_get.return_value = self._make_mock_s3()
            result = cmd_cleanup_staging(None)

        self.assertEqual(result, 0)

    def test_cleanup_current_no_resource_returns_0(self):
        from pipeline.cli import cmd_cleanup_current

        args = MagicMock()
        args.resource = None

        with patch("pipeline.cli.get_s3_client") as mock_get, \
             patch("pipeline.cli.get_bucket_name", return_value="test-bucket"):
            mock_get.return_value = self._make_mock_s3()
            result = cmd_cleanup_current(args)

        self.assertEqual(result, 0)

    def test_cleanup_current_with_resource_returns_0(self):
        from pipeline.cli import cmd_cleanup_current

        args = MagicMock()
        args.resource = "aktivitaet"

        with patch("pipeline.cli.get_s3_client") as mock_get, \
             patch("pipeline.cli.get_bucket_name", return_value="test-bucket"):
            mock_get.return_value = self._make_mock_s3()
            result = cmd_cleanup_current(args)

        self.assertEqual(result, 0)


class TestIngestResourceCheckpoint(unittest.TestCase):
    """Tests that ingest_resource calls checkpoint_fn at the right cadence."""

    def _make_page_responses(self, n_pages):
        """Return a list of session.get mock side-effects: n_pages with docs, then empty."""
        responses = []
        for i in range(n_pages):
            cursor = f"cursor-{i + 1}" if i < n_pages - 1 else None
            body = json.dumps({"documents": [{"id": str(i)}], "cursor": cursor}).encode()
            responses.append(_make_mock_response(body=body))
        # Final empty page
        responses.append(_make_mock_response(body=b'{"documents": [], "cursor": null}'))
        return responses

    def test_checkpoint_fn_called_at_interval(self):
        """checkpoint_fn should be called every CHECKPOINT_INTERVAL batches."""
        from pipeline.ingest import CHECKPOINT_INTERVAL, ingest_resource

        n_pages = CHECKPOINT_INTERVAL * 2  # trigger checkpoint exactly twice
        mock_client = MagicMock()
        mock_client.get_paginator.return_value.paginate.return_value = iter([{}])

        state = {"cursors": {}, "last_seen_update": None, "run_count": 1}
        manifest = {"objects": []}
        checkpoint_calls = []

        def _cp(s, m):
            checkpoint_calls.append((s, m))

        with patch("pipeline.ingest._make_session") as mock_make_session, \
             patch("pipeline.ingest.time.sleep"):
            mock_session = MagicMock()
            mock_session.get.side_effect = self._make_page_responses(n_pages)
            mock_make_session.return_value = mock_session

            ingest_resource(
                "vorgang", "testapikey", mock_client, "bucket", state, manifest,
                checkpoint_fn=_cp,
            )

        self.assertEqual(len(checkpoint_calls), 2)

    def test_no_checkpoint_fn_does_not_raise(self):
        """ingest_resource must work normally when no checkpoint_fn is provided."""
        from pipeline.ingest import ingest_resource

        mock_client = MagicMock()
        mock_client.get_paginator.return_value.paginate.return_value = iter([{}])
        state = {"cursors": {}, "last_seen_update": None, "run_count": 1}
        manifest = {"objects": []}

        with patch("pipeline.ingest._make_session") as mock_make_session, \
             patch("pipeline.ingest.time.sleep"):
            mock_session = MagicMock()
            mock_session.get.return_value = _make_mock_response(
                body=b'{"documents": [], "cursor": null}'
            )
            mock_make_session.return_value = mock_session

            # Should not raise
            ingest_resource("vorgang", "testapikey", mock_client, "bucket", state, manifest)


class TestRunFullLoadResume(unittest.TestCase):
    """Tests for run_full_load resume behaviour."""

    def _make_empty_s3_client(self):
        mock_client = MagicMock()
        paginator = MagicMock()
        mock_client.get_paginator.return_value = paginator
        paginator.paginate.return_value = iter([{}])
        return mock_client

    def test_run_full_load_skips_completed_resources(self):
        """run_full_load must skip resources listed in full_load_completed_resources."""
        from pipeline.ingest import RESOURCES, run_full_load

        state = {
            "cursors": {},
            "last_seen_update": None,
            "run_count": 1,
            "full_load_run_id": "run-resume",
            "full_load_completed_resources": RESOURCES[:2],  # first two already done
        }
        manifest = {"objects": []}
        mock_client = self._make_empty_s3_client()

        ingested = []

        def _fake_ingest(resource, api_key, client, bucket, s, m,
                         incremental=False, s3_prefix=None, checkpoint_fn=None):
            ingested.append(resource)
            return s, m

        with patch("pipeline.ingest.ingest_resource", side_effect=_fake_ingest), \
             patch("pipeline.ingest.load_api_key", return_value="key"), \
             patch("pipeline.ingest.save_state"), \
             patch("pipeline.ingest.save_manifest"):
            run_full_load(mock_client, "bucket", state, manifest, run_id="run-resume")

        # Only the resources not yet completed should have been ingested
        self.assertEqual(ingested, RESOURCES[2:])

    def test_run_full_load_fresh_clears_stale_cursors(self):
        """A fresh full load must clear any cursors left over from a previous run."""
        from pipeline.ingest import run_full_load

        state = {
            "cursors": {"vorgang": "stale-cursor"},
            "last_seen_update": None,
            "run_count": 1,
            "full_load_run_id": None,
            "full_load_completed_resources": [],
        }
        manifest = {"objects": []}
        mock_client = self._make_empty_s3_client()

        captured_states = []

        def _fake_ingest(resource, api_key, client, bucket, s, m,
                         incremental=False, s3_prefix=None, checkpoint_fn=None):
            captured_states.append(dict(s))
            return s, m

        with patch("pipeline.ingest.ingest_resource", side_effect=_fake_ingest), \
             patch("pipeline.ingest.load_api_key", return_value="key"), \
             patch("pipeline.ingest.save_state"), \
             patch("pipeline.ingest.save_manifest"):
            run_full_load(mock_client, "bucket", state, manifest, run_id="run-new")

        # The first ingest call should see an empty cursors dict (stale cursor cleared)
        first_state = captured_states[0]
        self.assertNotIn("vorgang", first_state.get("cursors", {}))

    def test_run_full_load_sets_and_clears_full_load_run_id(self):
        """run_full_load must set full_load_run_id at the start and clear it at the end."""
        from pipeline.ingest import run_full_load

        state = {
            "cursors": {},
            "last_seen_update": None,
            "run_count": 1,
            "full_load_run_id": None,
            "full_load_completed_resources": [],
        }
        manifest = {"objects": []}
        mock_client = self._make_empty_s3_client()

        mid_run_states = []

        def _fake_ingest(resource, api_key, client, bucket, s, m,
                         incremental=False, s3_prefix=None, checkpoint_fn=None):
            mid_run_states.append(s.get("full_load_run_id"))
            return s, m

        with patch("pipeline.ingest.ingest_resource", side_effect=_fake_ingest), \
             patch("pipeline.ingest.load_api_key", return_value="key"), \
             patch("pipeline.ingest.save_state"), \
             patch("pipeline.ingest.save_manifest"):
            final_state, _ = run_full_load(
                mock_client, "bucket", state, manifest, run_id="run-xyz"
            )

        # run_id should be set while resources are being ingested
        self.assertTrue(all(r == "run-xyz" for r in mid_run_states))
        # and cleared once the run completes
        self.assertIsNone(final_state.get("full_load_run_id"))


if __name__ == "__main__":
    unittest.main()
