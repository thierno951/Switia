"""Unit tests for channels.py helpers extracted during P3 refactor.

These cover the pure-logic helpers that don't require Mongo/Gmail/IMAP — they
are the safety net for the refactor of `check_inbox_and_draft`.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

from routers.channels import (
    _build_reply_record,
    _extract_recipient_address,
    _try_send_gmail,
    _try_send_smtp,
)


class TestBuildReplyRecord:
    def test_returns_all_required_fields(self):
        rec = _build_reply_record(
            user_id="u1", agent_type="support", provider="imap",
            message_id="<mid@x>", sender="alice@x.com", subject="Hi", body="Long body" * 1000, draft="OK",
        )
        assert rec["user_id"] == "u1"
        assert rec["agent_type"] == "support"
        assert rec["provider"] == "imap"
        assert rec["message_id"] == "<mid@x>"
        assert rec["sender"] == "alice@x.com"
        assert rec["subject"] == "Hi"
        assert rec["draft"] == "OK"
        assert rec["sent"] is False
        assert isinstance(rec["received_at"], datetime)

    def test_body_is_truncated_to_2000(self):
        rec = _build_reply_record("u", "support", "imap", "m", "a@x", "s", "x" * 5000, "d")
        assert len(rec["body"]) == 2000

    def test_received_at_is_tz_aware_utc(self):
        rec = _build_reply_record("u", "support", "imap", "m", "a@x", "s", "b", "d")
        assert rec["received_at"].tzinfo == timezone.utc


class TestExtractRecipientAddress:
    @pytest.mark.parametrize("raw,expected", [
        ("Alice <alice@example.com>", "alice@example.com"),
        ("alice@example.com", "alice@example.com"),
        ("\"Alice Smith\" <alice@example.com>", "alice@example.com"),
        ("Marie Dupont marie.dupont@example.fr", "marie.dupont@example.fr"),
        ("=?utf-8?Q?Marie?= <m@x.com>", "m@x.com"),
    ])
    def test_extracts_email_from_various_formats(self, raw, expected):
        assert _extract_recipient_address(raw) == expected

    def test_returns_input_when_no_email_found(self):
        # Fallback when regex matches nothing useful
        assert _extract_recipient_address("nothing here") == "nothing here"


class TestTrySendGmail:
    def test_marks_sent_on_success(self):
        # Build a fake service whose methods return chained mocks
        from unittest.mock import MagicMock
        service = MagicMock()
        service.users.return_value.messages.return_value.modify.return_value.execute.return_value = {}

        record = {"sent": False}
        with patch("routers.channels._send_gmail_api"):
            _try_send_gmail(service, "mid123", "a@x.com", "Subject", "Draft body", record)
        assert record["sent"] is True
        assert isinstance(record["sent_at"], datetime)
        assert "send_error" not in record

    def test_records_error_on_failure(self):
        service = object()  # will raise AttributeError on .users
        record = {"sent": False}
        with patch("routers.channels._send_gmail_api", side_effect=RuntimeError("smtp down")):
            _try_send_gmail(service, "mid", "a@x", "s", "d", record)
        assert record["sent"] is False
        assert "smtp down" in record["send_error"]


class TestTrySendSmtp:
    def test_marks_sent_on_success(self):
        ec = {"smtp_host": "x", "smtp_port": 587, "email_address": "me@x", "app_password": "p"}
        record = {"sent": False}
        with patch("routers.channels._send_smtp"):
            _try_send_smtp(ec, "Alice <alice@x>", "Hi", "Body", record)
        assert record["sent"] is True
        assert isinstance(record["sent_at"], datetime)

    def test_records_error_on_failure(self):
        ec = {}
        record = {"sent": False}
        with patch("routers.channels._send_smtp", side_effect=RuntimeError("auth failed")):
            _try_send_smtp(ec, "alice@x", "Hi", "Body", record)
        assert record["sent"] is False
        assert "auth failed" in record["send_error"]


class TestPersistAndSerialize:
    @pytest.mark.asyncio
    async def test_inserts_and_serializes_dates(self):
        from routers import channels
        fake_insert_result = type("R", (), {"inserted_id": "abc123"})()
        with patch.object(channels, "db") as fake_db:
            fake_db.email_replies.insert_one = AsyncMock(return_value=fake_insert_result)
            record = {
                "received_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "sent_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            }
            result = await channels._persist_and_serialize(record)
        assert result["_id"] == "abc123"
        assert isinstance(result["received_at"], str)
        assert isinstance(result["sent_at"], str)
        assert "2026-01-01" in result["received_at"]
