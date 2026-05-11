import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from analytics.rag import RetrievalError
from analytics.server import app


class TestAnalyticsServer(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_search_limits_top_k(self):
        response = self.client.post("/search", json={"question": "klima", "top_k": 999})
        self.assertEqual(response.status_code, 422)

    def test_search_maps_retrieval_error_to_503(self):
        with patch("analytics.server.retrieve_sources", side_effect=RetrievalError("unavailable")):
            response = self.client.post("/search", json={"question": "klima"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "unavailable")

    def test_ask_hides_unexpected_internal_error_details(self):
        with patch("analytics.server.ask", side_effect=RuntimeError("secret path /tmp/foo")):
            response = self.client.post("/ask", json={"question": "klima"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Internal server error")


if __name__ == "__main__":
    unittest.main()
