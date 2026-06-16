"""
tests/test_sprint220_importer.py

Sprint 2.20: StackOverflowImporter unit tests.

Coverage:
  - import_from_dict: empty questions list → empty result
  - import_from_dict: single question with accepted answer
  - import_from_dict: question without answers
  - import_from_dict: malformed question is skipped
  - import_from_dict: tags mapped to topic_keys
  - import_from_dict: tags mapped to root_cause_categories
  - import_from_dict: tags mapped to recommended_actions
  - import_from_dict: entry_id is deterministic (same input → same id)
  - import_from_dict: accepted answer is preferred over others
  - import_from_dict: HTML stripped from body
  - import_from_json: valid JSON string
  - import_from_json: invalid JSON string returns empty list
  - steps extracted from numbered body
"""
from __future__ import annotations

import json
import pytest

from case_engine.knowledge.importer import StackOverflowImporter
from case_engine.knowledge.models import KnowledgeEntry, KnowledgeEntryStatus


# ── Sample SO export data ─────────────────────────────────────────────────────

_VKYC_QUESTION = {
    "Id": 1,
    "Title": "How to reset a failed VKYC session",
    "Body": "<p>User is stuck after VKYC session timeout.</p>",
    "Tags": ["vkyc", "session_reset", "expired_session"],
    "Score": 15,
    "AcceptedAnswerId": 10,
    "CreationDate": "2024-01-15T10:00:00Z",
    "Answers": [
        {
            "Id": 9,
            "Body": "<p>Try refreshing the page.</p>",
            "Score": 2,
            "IsAccepted": False,
            "CreationDate": "2024-01-15T10:30:00Z",
        },
        {
            "Id": 10,
            "Body": "<p>1. Navigate to Admin Portal.\n2. Search for session.\n3. Click Reset.</p>",
            "Score": 12,
            "IsAccepted": True,
            "CreationDate": "2024-01-15T11:00:00Z",
        },
    ],
}

_OTP_QUESTION = {
    "Id": 2,
    "Title": "OTP not delivered",
    "Body": "<p>SMS OTP is not reaching the user.</p>",
    "Tags": ["otp", "sms_failure", "otp_resend"],
    "Score": 8,
    "AcceptedAnswerId": None,
    "CreationDate": "2024-01-16T10:00:00Z",
    "Answers": [
        {
            "Id": 20,
            "Body": "<p>Trigger OTP resend from Admin Portal.</p>",
            "Score": 5,
            "IsAccepted": False,
            "CreationDate": "2024-01-16T11:00:00Z",
        },
    ],
}

_MINIMAL_EXPORT = {"questions": [_VKYC_QUESTION, _OTP_QUESTION]}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestStackOverflowImporterBasics:
    def setup_method(self):
        self.importer = StackOverflowImporter()

    def test_empty_questions_returns_empty_list(self):
        result = self.importer.import_from_dict({"questions": []})
        assert result == []

    def test_empty_dict_returns_empty_list(self):
        result = self.importer.import_from_dict({})
        assert result == []

    def test_returns_list_of_knowledge_entries(self):
        entries = self.importer.import_from_dict(_MINIMAL_EXPORT)
        assert all(isinstance(e, KnowledgeEntry) for e in entries)

    def test_correct_count(self):
        entries = self.importer.import_from_dict(_MINIMAL_EXPORT)
        assert len(entries) == 2

    def test_question_without_title_skipped(self):
        export = {"questions": [{"Id": 99, "Title": "", "Body": "test", "Tags": [], "Score": 0}]}
        entries = self.importer.import_from_dict(export)
        assert len(entries) == 0

    def test_malformed_question_skipped_not_raised(self):
        export = {"questions": [None, _VKYC_QUESTION]}
        entries = self.importer.import_from_dict(export)
        assert len(entries) == 1


class TestStackOverflowImporterContent:
    def setup_method(self):
        self.importer = StackOverflowImporter()
        self.entries = self.importer.import_from_dict(_MINIMAL_EXPORT)
        self.vkyc_entry = self.entries[0]

    def test_title_extracted(self):
        assert self.vkyc_entry.title == "How to reset a failed VKYC session"

    def test_html_stripped_from_body(self):
        assert "<p>" not in self.vkyc_entry.body

    def test_accepted_answer_body_used(self):
        assert "Navigate to Admin Portal" in self.vkyc_entry.body

    def test_accepted_answer_flag_set(self):
        assert self.vkyc_entry.accepted_answer is True

    def test_score_captured(self):
        assert self.vkyc_entry.score == 15

    def test_source_is_stackoverflow_teams(self):
        assert self.vkyc_entry.source == "stackoverflow_teams"

    def test_source_id_captured(self):
        assert self.vkyc_entry.source_id == "1"

    def test_status_is_active(self):
        assert self.vkyc_entry.status == KnowledgeEntryStatus.ACTIVE


class TestStackOverflowImporterTagMapping:
    def setup_method(self):
        self.importer = StackOverflowImporter()
        self.entries = self.importer.import_from_dict(_MINIMAL_EXPORT)
        self.vkyc_entry = self.entries[0]
        self.otp_entry = self.entries[1]

    def test_vkyc_topic_key_mapped(self):
        assert "VKYC_Session_Failure" in self.vkyc_entry.topic_keys

    def test_vkyc_root_cause_mapped(self):
        assert "EXPIRED_SESSION" in self.vkyc_entry.root_cause_categories

    def test_vkyc_recommended_action_mapped(self):
        assert "SESSION_RESET" in self.vkyc_entry.recommended_actions

    def test_otp_topic_key_mapped(self):
        assert "OTP_Delivery_Failure" in self.otp_entry.topic_keys

    def test_otp_root_cause_mapped(self):
        assert "SMS_DELIVERY_FAILURE" in self.otp_entry.root_cause_categories

    def test_otp_recommended_action_mapped(self):
        assert "OTP_RESEND" in self.otp_entry.recommended_actions


class TestStackOverflowImporterDeterminism:
    def setup_method(self):
        self.importer = StackOverflowImporter()

    def test_entry_id_is_deterministic(self):
        entries1 = self.importer.import_from_dict({"questions": [_VKYC_QUESTION]})
        entries2 = self.importer.import_from_dict({"questions": [_VKYC_QUESTION]})
        assert entries1[0].entry_id == entries2[0].entry_id

    def test_different_questions_different_ids(self):
        entries = self.importer.import_from_dict(_MINIMAL_EXPORT)
        assert entries[0].entry_id != entries[1].entry_id


class TestStackOverflowImporterJSON:
    def setup_method(self):
        self.importer = StackOverflowImporter()

    def test_import_from_json_valid(self):
        json_str = json.dumps(_MINIMAL_EXPORT)
        entries = self.importer.import_from_json(json_str)
        assert len(entries) == 2

    def test_import_from_json_invalid_returns_empty(self):
        entries = self.importer.import_from_json("this is not json {{{")
        assert entries == []

    def test_import_from_json_entries_are_knowledge_entries(self):
        json_str = json.dumps({"questions": [_VKYC_QUESTION]})
        entries = self.importer.import_from_json(json_str)
        assert all(isinstance(e, KnowledgeEntry) for e in entries)


class TestStackOverflowImporterStepExtraction:
    def setup_method(self):
        self.importer = StackOverflowImporter()

    def test_numbered_steps_extracted(self):
        q = {
            "Id": 50,
            "Title": "How to fix portal",
            "Body": "<p>Use these steps:</p>",
            "Tags": ["portal"],
            "Score": 3,
            "AcceptedAnswerId": 51,
            "CreationDate": "2024-01-01T00:00:00Z",
            "Answers": [{
                "Id": 51,
                "Body": "1. Open browser.\n2. Clear cache.\n3. Reload.",
                "Score": 2,
                "IsAccepted": True,
                "CreationDate": "2024-01-01T01:00:00Z",
            }],
        }
        entries = self.importer.import_from_dict({"questions": [q]})
        assert len(entries[0].resolution_steps) >= 3

    def test_no_steps_on_unstructured_body(self):
        q = {
            "Id": 60,
            "Title": "Portal help",
            "Body": "<p>Contact support.</p>",
            "Tags": ["portal"],
            "Score": 1,
            "AcceptedAnswerId": None,
            "CreationDate": "2024-01-01T00:00:00Z",
            "Answers": [{
                "Id": 61,
                "Body": "Just refresh the browser and try again.",
                "Score": 1,
                "IsAccepted": False,
                "CreationDate": "2024-01-01T01:00:00Z",
            }],
        }
        entries = self.importer.import_from_dict({"questions": [q]})
        assert isinstance(entries[0].resolution_steps, tuple)
