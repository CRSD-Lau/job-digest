"""Offline integration checks for the digest's external dependencies."""
import json
import os
from pathlib import Path
import tempfile
import socket
import unittest
from unittest.mock import patch

import anthropic
import httpx2

import run_weekly
from src import db, digest, llm_classifier


class DependencyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test attempted network access"))
        network.start()
        self.addCleanup(network.stop)

    def test_classifier_uses_current_sdk_and_parses_message(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx2.Response(200, json={
                "id": "msg_test", "type": "message", "role": "assistant",
                "model": "claude-haiku-4-5", "stop_reason": "end_turn",
                "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
                "content": [{"type": "text", "text": json.dumps([{
                    "id": "job-1", "is_relevant": True, "category": "trades",
                    "exclusion_reason": None, "confidence": 0.95,
                }])}],
            })

        with anthropic.Anthropic(api_key="test-only", http_client=httpx2.Client(
            transport=httpx2.MockTransport(respond)
        )) as client:
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-only"}), patch.object(
                llm_classifier.anthropic, "Anthropic", return_value=client
            ):
                postings = [{"posting_id": "job-1", "title": "Welder", "flagged_for_review": True}]
                result = llm_classifier.classify_batch(postings)
        self.assertTrue(result[0]["is_relevant"])
        self.assertEqual(result[0]["normalized_category"], "trades")
        self.assertFalse(result[0]["flagged_for_review"])
        self.assertEqual(requests[0].headers["anthropic-version"], "2026-10-21")
        self.assertEqual(json.loads(requests[0].content)["messages"][0]["role"], "user")

    def test_classifier_without_key_leaves_review_pending(self):
        postings = [{"posting_id": "job-1", "flagged_for_review": True}]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), patch.object(
            llm_classifier.anthropic, "Anthropic"
        ) as client:
            self.assertIs(llm_classifier.classify_batch(postings), postings)
        client.assert_not_called()
        self.assertTrue(postings[0]["flagged_for_review"])

    def test_fresh_database_renders_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "jobs.db")
            db.init_db(database)
            data = db.get_digest_data(database)
            report = Path(digest.generate(data, str(Path(tmp) / "reports")))
            self.assertIn(data["week"], report.read_text(encoding="utf-8"))

    def test_email_renderer_has_its_declared_markdown_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "digest.md"
            report.write_text("# Digest\n\n**1** relevant\n\n| Job |\n| --- |\n| Welder |\n", encoding="utf-8")
            with patch.object(run_weekly.smtplib, "SMTP") as smtp:
                run_weekly.send_digest_email(str(report), "2026-W45", "test@example.invalid", "test-only")
            smtp.assert_called_once()
            smtp.return_value.__enter__.return_value.sendmail.assert_called_once()

    def test_failed_database_write_rolls_back_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "jobs.db")
            with db.get_connection(database) as conn:
                conn.execute("CREATE TABLE sample (value TEXT)")
            with self.assertRaisesRegex(ValueError, "rollback"):
                with db.get_connection(database) as conn:
                    conn.execute("INSERT INTO sample VALUES ('discard')")
                    raise ValueError("rollback")
            with db.get_connection(database) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
