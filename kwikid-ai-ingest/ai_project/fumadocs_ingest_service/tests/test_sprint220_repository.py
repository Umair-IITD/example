"""
tests/test_sprint220_repository.py

Sprint 2.20: InMemoryKnowledgeRepository unit tests.

Coverage:
  - starts empty (count=0)
  - store() adds entry
  - bulk_store() returns count
  - get_by_id() returns entry
  - get_by_id() returns None for missing
  - get_all() returns only ACTIVE entries
  - get_by_topic() filters correctly
  - get_by_root_cause() filters correctly
  - get_by_recommended_action() filters correctly
  - count() counts all statuses
  - count_active() counts only ACTIVE
  - clear() empties the repository
  - overwrite on re-store same entry_id
"""
from __future__ import annotations

import pytest

from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
)
from case_engine.knowledge.repository import InMemoryKnowledgeRepository


def _make_entry(
    entry_id: str,
    topic_keys: tuple = ("VKYC_Session_Failure",),
    root_cause_categories: tuple = ("EXPIRED_SESSION",),
    recommended_actions: tuple = ("SESSION_RESET",),
    status: KnowledgeEntryStatus = KnowledgeEntryStatus.ACTIVE,
) -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=entry_id,
        title=f"Entry {entry_id}",
        body="body text",
        entry_type=KnowledgeEntryType.SOP,
        tags=("vkyc",),
        topic_keys=topic_keys,
        root_cause_categories=root_cause_categories,
        recommended_actions=recommended_actions,
        resolution_steps=(),
        source="manual",
        source_id=None,
        accepted_answer=False,
        score=0,
        created_at="2024-01-01T00:00:00Z",
        status=status,
    )


class TestInMemoryKnowledgeRepositoryEmpty:
    def test_starts_empty(self):
        repo = InMemoryKnowledgeRepository()
        assert repo.count() == 0

    def test_count_active_is_zero(self):
        repo = InMemoryKnowledgeRepository()
        assert repo.count_active() == 0

    def test_get_all_returns_empty_list(self):
        assert InMemoryKnowledgeRepository().get_all() == []

    def test_get_by_id_returns_none(self):
        assert InMemoryKnowledgeRepository().get_by_id("missing") is None

    def test_get_by_topic_returns_empty_list(self):
        assert InMemoryKnowledgeRepository().get_by_topic("VKYC_Session_Failure") == []

    def test_get_by_root_cause_returns_empty_list(self):
        assert InMemoryKnowledgeRepository().get_by_root_cause("EXPIRED_SESSION") == []

    def test_get_by_recommended_action_returns_empty_list(self):
        assert InMemoryKnowledgeRepository().get_by_recommended_action("SESSION_RESET") == []


class TestInMemoryKnowledgeRepositoryStore:
    def setup_method(self):
        self.repo = InMemoryKnowledgeRepository()
        self.entry = _make_entry("e-001")
        self.repo.store(self.entry)

    def test_count_is_one(self):
        assert self.repo.count() == 1

    def test_count_active_is_one(self):
        assert self.repo.count_active() == 1

    def test_get_by_id_returns_entry(self):
        assert self.repo.get_by_id("e-001") is self.entry

    def test_get_all_returns_entry(self):
        assert self.entry in self.repo.get_all()

    def test_overwrite_same_id(self):
        new_entry = _make_entry("e-001")
        self.repo.store(new_entry)
        assert self.repo.count() == 1
        assert self.repo.get_by_id("e-001") is new_entry


class TestInMemoryKnowledgeRepositoryBulkStore:
    def test_bulk_store_returns_count(self):
        repo = InMemoryKnowledgeRepository()
        entries = [_make_entry(f"e-{i}") for i in range(5)]
        result = repo.bulk_store(entries)
        assert result == 5

    def test_bulk_store_populates_repo(self):
        repo = InMemoryKnowledgeRepository()
        repo.bulk_store([_make_entry("a"), _make_entry("b"), _make_entry("c")])
        assert repo.count() == 3

    def test_bulk_store_empty_list(self):
        repo = InMemoryKnowledgeRepository()
        result = repo.bulk_store([])
        assert result == 0
        assert repo.count() == 0


class TestInMemoryKnowledgeRepositoryFilters:
    def setup_method(self):
        self.repo = InMemoryKnowledgeRepository()
        self.repo.bulk_store([
            _make_entry("e-1", topic_keys=("VKYC_Session_Failure",),
                        root_cause_categories=("EXPIRED_SESSION",),
                        recommended_actions=("SESSION_RESET",)),
            _make_entry("e-2", topic_keys=("OTP_Delivery_Failure",),
                        root_cause_categories=("SMS_DELIVERY_FAILURE",),
                        recommended_actions=("OTP_RESEND",)),
            _make_entry("e-3", topic_keys=("VKYC_Session_Failure",),
                        root_cause_categories=("TIMEOUT",),
                        recommended_actions=("SESSION_RESET",)),
        ])

    def test_get_by_topic_vkyc(self):
        results = self.repo.get_by_topic("VKYC_Session_Failure")
        ids = [e.entry_id for e in results]
        assert "e-1" in ids
        assert "e-3" in ids
        assert "e-2" not in ids

    def test_get_by_topic_otp(self):
        results = self.repo.get_by_topic("OTP_Delivery_Failure")
        assert len(results) == 1
        assert results[0].entry_id == "e-2"

    def test_get_by_root_cause_expired_session(self):
        results = self.repo.get_by_root_cause("EXPIRED_SESSION")
        assert len(results) == 1
        assert results[0].entry_id == "e-1"

    def test_get_by_root_cause_no_match(self):
        results = self.repo.get_by_root_cause("UNKNOWN")
        assert results == []

    def test_get_by_recommended_action_session_reset(self):
        results = self.repo.get_by_recommended_action("SESSION_RESET")
        ids = [e.entry_id for e in results]
        assert "e-1" in ids
        assert "e-3" in ids

    def test_get_all_returns_all_active(self):
        assert len(self.repo.get_all()) == 3


class TestInMemoryKnowledgeRepositoryActiveFilter:
    def test_deprecated_entry_excluded_from_get_all(self):
        repo = InMemoryKnowledgeRepository()
        repo.store(_make_entry("e-active", status=KnowledgeEntryStatus.ACTIVE))
        repo.store(_make_entry("e-deprecated", status=KnowledgeEntryStatus.DEPRECATED))
        active = repo.get_all()
        ids = [e.entry_id for e in active]
        assert "e-active" in ids
        assert "e-deprecated" not in ids

    def test_count_includes_deprecated(self):
        repo = InMemoryKnowledgeRepository()
        repo.store(_make_entry("e-active"))
        repo.store(_make_entry("e-deprecated", status=KnowledgeEntryStatus.DEPRECATED))
        assert repo.count() == 2

    def test_count_active_excludes_deprecated(self):
        repo = InMemoryKnowledgeRepository()
        repo.store(_make_entry("e-active"))
        repo.store(_make_entry("e-deprecated", status=KnowledgeEntryStatus.DEPRECATED))
        assert repo.count_active() == 1


class TestInMemoryKnowledgeRepositoryClear:
    def test_clear_empties_repo(self):
        repo = InMemoryKnowledgeRepository()
        repo.bulk_store([_make_entry(f"e-{i}") for i in range(5)])
        repo.clear()
        assert repo.count() == 0

    def test_store_after_clear(self):
        repo = InMemoryKnowledgeRepository()
        repo.store(_make_entry("e-1"))
        repo.clear()
        repo.store(_make_entry("e-2"))
        assert repo.count() == 1
        assert repo.get_by_id("e-1") is None
        assert repo.get_by_id("e-2") is not None
