"""
Sprint 2.28.3 — Tenant resolution tests.

Verifies that resolve_tenant() correctly identifies the tenant slug through
every priority level. These tests FAIL on the OLD code (which checked cf_client
without 's') and PASS on the fixed code (which checks cf_clients first).
"""
import pytest
from app.freshdesk_webhook import resolve_tenant


TAG_PREFIX = "client:"


def _ticket(
    *,
    cf: dict | None = None,
    tags: list[str] | None = None,
    email: str = "",
    ticket_id: str = "197416",
) -> dict:
    return {
        "ticket_id": ticket_id,
        "subject": "Test ticket",
        "description": "",
        "description_text": "",
        "requester_email": email,
        "tags": tags or [],
        "custom_fields": cf or {},
        "status": "open",
        "priority": "medium",
    }


# ── Priority 1: cf_clients (the Sprint 2.28.3 primary fix) ────────────────────

class TestCfClients:
    def test_unity_bank_via_cf_clients(self):
        """cf_clients='Unity' must resolve to 'Unity' — was broken before the fix."""
        ticket = _ticket(cf={"cf_clients": "Unity"})
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "Unity"

    def test_bob_via_cf_clients(self):
        ticket = _ticket(cf={"cf_clients": "BOB"})
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "BOB"

    def test_cbi_via_cf_clients(self):
        ticket = _ticket(cf={"cf_clients": "CBI"})
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "CBI"

    def test_rbl_via_cf_clients(self):
        ticket = _ticket(cf={"cf_clients": "RBL"})
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "RBL"

    def test_cf_clients_case_preserved(self):
        """Slug casing must be preserved exactly as Freshdesk sends it."""
        ticket = _ticket(cf={"cf_clients": "BAJAJ_FIN"})
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "BAJAJ_FIN"

    def test_cf_clients_whitespace_stripped(self):
        """Leading/trailing whitespace in cf_clients must be stripped."""
        ticket = _ticket(cf={"cf_clients": "  Unity  "})
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "Unity"

    def test_cf_clients_takes_priority_over_tags(self):
        """cf_clients wins even when a valid tag is also present."""
        ticket = _ticket(cf={"cf_clients": "BOB"}, tags=["client:CBI"])
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "BOB"

    def test_cf_clients_takes_priority_over_email(self):
        """cf_clients wins even when email domain is in the lookup map."""
        ticket = _ticket(cf={"cf_clients": "BOB"}, email="user@unitybank.co.in")
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "BOB"


# ── cf_clients passthrough values (must fall through) ─────────────────────────

class TestCfClientsPassthrough:
    @pytest.mark.parametrize("passthrough", ["others", "Others", "OTHERS", "unknown", ""])
    def test_passthrough_falls_through(self, passthrough):
        """'Others', 'unknown', '' are sentinels — fall through to next priority."""
        ticket = _ticket(
            cf={"cf_clients": passthrough},
            email="user@unitybank.co.in",
        )
        result = resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None)
        assert result == "Unity"

    def test_others_falls_through_to_default(self):
        """With no email or tags, 'Others' cf_clients should use default_client."""
        ticket = _ticket(cf={"cf_clients": "Others"})
        result = resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client="DefaultBank")
        assert result == "DefaultBank"


# ── Priority 2: tag prefix ─────────────────────────────────────────────────────

class TestTagPrefix:
    def test_tag_prefix_match(self):
        ticket = _ticket(tags=["client:unity_bank"])
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "unity_bank"

    def test_tag_prefix_case_insensitive_matching(self):
        """Prefix matching is case-insensitive; slug preserves original tag casing."""
        ticket = _ticket(tags=["CLIENT:Unity"])
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "Unity"

    def test_tag_prefix_no_cf_clients(self):
        """Tag used only when cf_clients is missing."""
        ticket = _ticket(tags=["client:BOB"])
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "BOB"

    def test_multiple_tags_first_prefix_match_wins(self):
        """First matching tag wins when multiple have the prefix."""
        ticket = _ticket(tags=["other_tag", "client:CBI", "client:BOB"])
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "CBI"

    def test_non_matching_tags_skipped(self):
        """Tags without the configured prefix are ignored."""
        ticket = _ticket(tags=["urgent", "escalated", "no-prefix"])
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client="Fallback") == "Fallback"


# ── Priority 3: other cf field aliases ────────────────────────────────────────

class TestCfAliases:
    @pytest.mark.parametrize("field", ["cf_client_slug", "cf_client", "client_slug", "client"])
    def test_cf_alias_fields(self, field):
        ticket = _ticket(cf={field: "SomeTenant"})
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "SomeTenant"

    def test_cf_clients_wins_over_cf_client(self):
        """cf_clients (plural) must win over cf_client (singular) alias."""
        ticket = _ticket(cf={"cf_clients": "BOB", "cf_client": "Unity"})
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "BOB"


# ── Priority 4: email domain lookup ───────────────────────────────────────────

class TestEmailDomain:
    @pytest.mark.parametrize("email,expected", [
        ("agent@unitybank.co.in", "Unity"),
        ("support@bankofbaroda.com", "BOB"),
        ("ticket@centralbank.co.in", "CBI"),
        ("user@rblbank.com", "RBL"),
        ("info@bajajfinserv.in", "BAJAJ_FIN"),
        ("help@thomascook.in", "THOMAS_COOK"),
        ("ops@canarabank.com", "CANARA"),
        ("agent@finobank.com", "FINO"),
        ("svc@tfsin.co.in", "TOYOTA"),
        ("staff@grihumhousing.com", "GHF"),
    ])
    def test_email_domain_lookup(self, email, expected):
        ticket = _ticket(email=email)
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == expected

    def test_unknown_email_domain_falls_to_default(self):
        ticket = _ticket(email="user@unknownbank.com")
        result = resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client="GenericTenant")
        assert result == "GenericTenant"

    def test_unknown_email_domain_no_default_returns_none(self):
        ticket = _ticket(email="user@unknownbank.com")
        result = resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None)
        assert result is None


# ── Priority 5: default_client fallback ───────────────────────────────────────

class TestDefaultClient:
    def test_default_client_used_when_all_else_fails(self):
        ticket = _ticket()  # no cf, no tags, no email
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client="DefaultTenant") == "DefaultTenant"

    def test_no_default_returns_none(self):
        ticket = _ticket()
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) is None


# ── Edge cases ─────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_missing_custom_fields_key(self):
        """Ticket dict without 'custom_fields' key must not raise."""
        ticket = {
            "ticket_id": "999",
            "subject": "No CF",
            "description": "",
            "description_text": "",
            "requester_email": "",
            "tags": [],
            "status": "open",
            "priority": "low",
            # 'custom_fields' deliberately absent
        }
        result = resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client="Safe")
        assert result == "Safe"

    def test_none_custom_fields(self):
        """custom_fields=None must not raise (treated as empty dict)."""
        ticket = _ticket(cf=None)
        result = resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client="Safe")
        assert result == "Safe"

    def test_empty_ticket_id_still_resolves(self):
        """Missing ticket_id must not prevent resolution."""
        ticket = _ticket(cf={"cf_clients": "BOB"}, ticket_id="")
        assert resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client=None) == "BOB"

    def test_email_without_at_sign(self):
        """Malformed email must not crash email domain lookup."""
        ticket = _ticket(email="not-an-email")
        result = resolve_tenant(ticket, tag_prefix=TAG_PREFIX, default_client="Safe")
        assert result == "Safe"

    def test_custom_tag_prefix(self):
        """A different tag prefix must match tags with that prefix."""
        ticket = _ticket(tags=["tenant:BOB"])
        result = resolve_tenant(ticket, tag_prefix="tenant:", default_client=None)
        assert result == "BOB"
