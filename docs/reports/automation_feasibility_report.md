# Automation Feasibility Report
## KwikID Freshdesk Export — Ticket Automation Classification & Readiness Assessment

> **Phase**: READ-ONLY engineering discovery
> **Analysis Date**: 2026-05-12
> **Basis**: Statistical analysis of 5,121 unique ticket records across 3 non-overlapping datasets

---

## 1. Executive Summary

| Classification | Estimated % | Ticket Volume (est.) |
|---|---|---|
| **AUTO_RESOLVABLE** | ~28–36% | ~1,450–1,870 tickets |
| **HUMAN_REVIEW_REQUIRED** | ~40–46% | ~2,075–2,390 tickets |
| **ESCALATION_REQUIRED** | ~18–26% | ~935–1,350 tickets |

**Key finding**: Roughly **one-third of all tickets are strong automation candidates** — they are recurring, closed within SLA, require few interactions, and belong to well-documented issue categories. A further 40% can benefit from AI-drafted responses with human approval. Only ~20–25% require true human escalation (dev involvement, SLA breach, or complex multi-system diagnosis).

---

## 2. Classification Methodology

Automation class is determined by a three-tier signal scoring system applied per ticket:

### AUTO_RESOLVABLE criteria (ALL must be true):
1. `Issue Recurrence` = "Recurring issue" — pattern is known
2. `Status` = Closed or Resolved — resolution was achieved
3. `Resolution status` = Within SLA — handled within expected timeframe
4. `Agent interactions` ≤ 3 — resolved quickly, without extensive back-and-forth
5. `Query Type` NOT IN exclusion list (server alerts, meetings, internal ops, manual DB tasks)

### ESCALATION_REQUIRED criteria (ANY is sufficient):
1. `Priority` = High or Urgent
2. `Resolution status` = SLA Violated
3. `RCA status` = "RCA Pending from dev" (dev involvement required)
4. `Asana Ticket Link` is populated (formal escalation already triggered)
5. `Query Type` IN {Server Alert, Patching Activity, DC-DR, Production Deployment, Production Down}

### HUMAN_REVIEW_REQUIRED:
Everything else — resolvable with AI draft but needs agent review before posting.

---

## 3. Quantitative Feasibility Analysis

### Based on RBL_RCA dataset (2,339 tickets — most complete signal set)

| Signal | Count | % |
|---|---|---|
| Status = Closed (entire dataset) | 2,339 | 100% |
| Within SLA | 2,119 | 90.6% |
| SLA Violated | 220 | 9.4% |
| Priority High or Urgent | 348 | 14.9% |
| Recurring Issues | 926 | 39.6% |
| Has RCA documentation | 343 | 14.7% |
| Low agent interactions (≤ 3) | 2,195 | 93.8% |
| Asana escalation present | ~106 | ~4.5% |

**Applied classification (RBL_RCA, 2,339 tickets):**
| Class | Count | % |
|---|---|---|
| AUTO_RESOLVABLE | 845 | **36.1%** |
| ESCALATION_REQUIRED | 533 | **22.8%** |
| HUMAN_REVIEW_REQUIRED | 1,078 | **46.1%** |

> Note: Overlap exists between ESCALATION and HUMAN_REVIEW — priority tickets that are not escalated to Asana are classified as HUMAN_REVIEW.

### Based on CSV_Tickets dataset (1,920 tickets — SOP signal available)

| Signal | Count | % |
|---|---|---|
| SOP Present | 1,061 | 55.3% |
| No SOP Required | 504 | 26.2% |
| No SOP Available | 141 | 7.3% |
| Solved by SOP | 243 | 12.7% |
| Query Type = Internal/Alert/Test | ~870 | ~45% |
| Effective customer-facing tickets | ~1,050 | ~55% |

**Estimated classification (CSV, ~1,920 tickets):**
| Class | Count | % |
|---|---|---|
| AUTO_RESOLVABLE | ~340 | ~18% |
| ESCALATION_REQUIRED | ~130 | ~7% |
| NOT FOR AI (internal/ops/alerts) | ~870 | **~45%** |
| HUMAN_REVIEW_REQUIRED | ~580 | ~30% |

> The CSV dataset has a dramatically higher proportion of non-AI tickets (server alerts, meetings, internal ops) — 45% should be filtered out entirely before AI training.

---

## 4. Category-Level Automation Feasibility Matrix

| Issue Category | Ticket Count | Auto-Resolvable | Reasoning | Confidence |
|---|---|---|---|---|
| **Unable to Login** | ~70 | ✅ YES | SOP present, recurring, <3 agent interactions typical | High |
| **OTP Not Received / Failed** | ~221 | ✅ YES | Well-defined troubleshooting SOP, high recurrence | High |
| **Send Link Issue** | ~44 | ✅ YES | SOP documented, recurring | High |
| **Portal Not Working** | ~46 | ✅ YES | SOP typically exists, short resolution | High |
| **Audio Issue** | ~124 | ✅ YES | Device checklist available, recurring | High |
| **Video Related (general)** | ~826 | ⚠️ PARTIAL | Session ID required for diagnosis; with it, SOP applies | Medium |
| **Connectivity Issue** | ~132 | ⚠️ PARTIAL | AI can provide checklist; root cause often external | Medium |
| **Session Not Available** | ~12 | ⚠️ PARTIAL | Needs session ID lookup — manual data check required | Medium-Low |
| **NSDL/PAN Related** | ~70 | ⚠️ PARTIAL | Third-party dependency; AI can provide standard steps | Medium |
| **Aadhaar Issues** | ~85 | ⚠️ PARTIAL | Well-defined document requirement steps | Medium |
| **Face Match / Liveness** | ~41 | ⚠️ PARTIAL | Technical diagnostic, SOP dependent | Medium |
| **Report / Export Request** | ~189 | ⚠️ PARTIAL | AI can explain how; actual report generation is human | Low-Medium |
| **API Issues** | ~44 | ⚠️ PARTIAL | Technical — depends on error code available | Low-Medium |
| **Account Creation** | ~114 | 🔴 HUMAN | Manual process requiring privileged DB access | Low |
| **ID Creation / Mapping** | ~175 | 🔴 HUMAN | Requires direct DB operation — never AI | None |
| **Manual Repush** | ~69 | 🔴 HUMAN | Backend data correction — requires dev | None |
| **Summary Data Update** | ~75 | 🔴 HUMAN | Database correction task | None |
| **Server Alert** | ~560 | 🔴 ESCALATION | DevOps response required | None |
| **Patching Activity** | ~27 | 🔴 ESCALATION | Infrastructure change management | None |
| **Production Deployment** | ~6 | 🔴 ESCALATION | Dev team action | None |
| **Audit Lock / Auditor Hold** | ~141 | 🔴 ESCALATION | Compliance process | None |
| **Non support related (internal)** | ~274 | ❌ EXCLUDE | Not a customer support issue | N/A |
| **Test Mail / Meeting / Call Test** | ~124 | ❌ EXCLUDE | Internal operations | N/A |

---

## 5. The "Missing Signal" Problem

A significant blocker for automation is **missing diagnostic fields** in the ticket body. The n8n workflow already identifies this — without these signals, AI cannot generate actionable responses:

| Category | Required Signal | Availability in Dataset |
|---|---|---|
| Video Related | `session_id` | 44.5% present in RBL_RCA1, ~25% in RBL_RCA |
| Authentication | `phone_number` or `PAN` | Often mentioned in Subject/Description |
| Network Issues | `app_version`, `platform` (mobile/web) | Rarely structured — usually in free text |
| Audio Issues | `device_type`, `browser` | Almost never structured |
| All Categories | `environment` (prod/UAT) | 89–92% populated |

**Engineering implication**: The AI draft quality for Video Related tickets (30% of volume) is fundamentally limited until session ID extraction from free text is implemented. This is partially present in the Description field but requires NER or regex extraction, not a simple column lookup.

---

## 6. SOP-Based Automation Readiness

SOPs are the primary knowledge source for AUTO_RESOLVABLE tickets.

### SOP coverage by automation class:

| Class | Has SOP | No SOP Available | No SOP Required |
|---|---|---|---|
| AUTO_RESOLVABLE candidates | ~80% | ~5% | ~15% |
| HUMAN_REVIEW | ~40% | ~20% | ~40% |
| ESCALATION | ~10% | ~50% | ~40% |

**The 141 CSV tickets tagged "No SOP Available"** represent a knowledge gap — real customer-facing issues with no documented resolution path. These are the highest-priority targets for the "Teach-the-AI" knowledge card feature.

---

## 7. Resolution Quality Signal for Training Data Selection

For generating high-quality AI training pairs (query → response), the best candidates are:

### Tier 1 — Gold Training Data
Criteria: `SOP Status = "SOP Present"` AND `Resolution Classification = "Solved by SOP"` AND `Agent interactions ≤ 3` AND `Issue Recurrence = "Recurring"`

**Estimated count**: ~200–300 tickets across all files

### Tier 2 — Silver Training Data
Criteria: `SOP Status = "SOP Present"` AND resolution was Within SLA AND has description text AND has RCA (non-trivial)

**Estimated count**: ~400–600 tickets

### Tier 3 — Bronze (Context Only — not resolution pairs)
Criteria: Closed tickets with Description + Subject but no RCA, no SOP classification

**Estimated count**: ~1,500–2,000 tickets — useful for RAG retrieval corpus but not direct Q→A training

### Excluded from Training
- Server alerts, internal ops, meetings, test mails: ~870–1,200 tickets
- Tickets with only Subject (no Description): all XLS/CSV tickets (no Description column)
- Trivial RCA entries (<50 chars): ~206 tickets

---

## 8. Handling Time Analysis — Automation Efficiency Benchmark

The `Handling Time` column in RBL_RCA1 (in minutes) provides a baseline for current human resolution time:

| Time Band | Count | % | AI opportunity |
|---|---|---|---|
| ≤ 10 min | ~389 | ~48% | Already fast — AI must not slow these down |
| 11–30 min | ~305 | ~38% | AI can match or improve |
| 31–60 min | ~75 | ~9% | AI provides significant savings |
| > 60 min | ~33 | ~4% | High complexity — AI drafts, human refines |

**Mode handling time**: 10 minutes. This is the benchmark — AI draft response + human review must stay under this time to deliver ROI.

---

## 9. AI Tags — Evidence of Existing Automation Performance

The `ai_auto_replied` and `ai_note` tags reveal the current AI system is live:

| Tag | Count | Implication |
|---|---|---|
| `ai_auto_replied` | 158 tickets | AI already auto-posting responses |
| `ai_note` | 557 tickets | AI adding internal notes (not public replies) |
| `rag_context` | 8 tickets | RAG pipeline retrieving and tagging context |

**These 158 auto-replied tickets are a ground-truth evaluation set** — we can compare AI-drafted responses to human-approved final responses for those tickets (if Description includes the conversation thread). This is a built-in feedback loop opportunity.

---

## 10. Risk Assessment by Category

| Risk | Affected Categories | Severity | Mitigation |
|---|---|---|---|
| Hallucinated session IDs or ticket IDs | Video Related, Session issues | 🔴 Critical | Enforce citation rules; never invent IDs |
| Wrong bank-specific URLs or credentials | All per-client queries | 🔴 Critical | Tenant-scoped retrieval only |
| AI suggesting backend/infra actions to customers | Server Alert bleed | 🟠 High | Prompt engineering guardrails (already in n8n) |
| AI posting to wrong client's thread | Multi-tenant tickets | 🟠 High | Enforce `Clients` field matching |
| Stale SOP content (product was updated) | All SOP-based responses | 🟡 Medium | Freshdesk ingestion recency filter |
| Auto-replying to Auditor Hold / Compliance tickets | Compliance process | 🟡 Medium | Blocklist certain Query Types |

---

## 11. Implementation Readiness Score

| Dimension | Score | Rationale |
|---|---|---|
| Volume sufficiency | 7/10 | 5,100+ unique tickets; Description available for 3,208 |
| Classification quality | 5/10 | Query Type populated for only ~89%; 50+ inconsistent values |
| Resolution documentation | 4/10 | RCA only 14–27% coverage; Resolution Classification 87–96% null |
| SOP availability | 6/10 | 55% SOP Present in best dataset; but SOP text not in data |
| Deduplication cleanliness | 9/10 | No within-file duplicates; cross-file overlap identified |
| Text quality (after cleaning) | 5/10 | HTML + signatures require mandatory preprocessing |
| Temporal coverage | 7/10 | Covers 2024–2026 period adequately |
| Multi-client coverage | 6/10 | Unity, BoB, RBL, Bajaj present; but uneven distribution |

**Overall readiness**: **5.6 / 10** — Data is **sufficient for an initial RAG corpus** but NOT ready for supervised fine-tuning without significant preprocessing and enrichment. The pipeline can be built now with the available data, but resolution quality will improve substantially once:
1. SOP documents are ingested (from Fumadocs/Bitbucket)
2. RCA coverage is expanded (ongoing tagging discipline)
3. HTML/signature stripping is applied
