# Query Pattern Analysis
## KwikID Freshdesk Export — Issue Category Discovery & Support Topology

> **Phase**: READ-ONLY engineering discovery
> **Analysis Date**: 2026-05-12

---

## 1. Executive Summary

Across 7,121 raw ticket records (5,194 unique), this dataset represents support operations for a **Video KYC (VKYC) platform** serving multiple Indian banking clients (Unity Small Finance Bank, RBL Bank, Bank of Baroda, Bajaj Finance, and others). The support taxonomy is operational-heavy — the majority of tickets represent **backend system alerts, manual operational tasks, and recurring infrastructure issues** rather than end-customer queries.

**Key finding**: Only ~25–30% of tickets represent "answerable" customer-facing issues. The rest are internal operations, server alerts, meeting records, and manual data manipulation tasks that an AI system should not attempt to answer.

---

## 2. Primary Category Distribution

### By `Query Type` (Operational Classification)

**Dataset: CSV_Tickets (1,920 tickets)**

| Query Type | Count | % | AI Relevance |
|---|---|---|---|
| Server Alert | 506 | 26.4% | ❌ Not for AI — monitoring alert |
| Non support related (internal) | 194 | 10.1% | ❌ Internal operations |
| Auditor Hold | 114 | 5.9% | ❌ Compliance process, not support |
| Call Test | 80 | 4.2% | ❌ Internal test |
| Reports | 80 | 4.2% | ⚠️ Low — data extraction request |
| Account Creation | 71 | 3.7% | ⚠️ Operational — partially automatable |
| Manual Repush | 68 | 3.5% | ❌ Backend manual op |
| ID Creation | 59 | 3.1% | ⚠️ Operational task |
| ID Mapping Change | 48 | 2.5% | ❌ DB admin task |
| Connectivity Issue | 45 | 2.3% | ✅ Customer-facing, answerable |
| Unable to Login | 42 | 2.2% | ✅ Common, high-AI-value |
| Send Link Issue | 41 | 2.1% | ✅ Answerable |
| Portal Not Working | 40 | 2.1% | ✅ Answerable |
| Video Related | 34 | 1.8% | ✅ Core product issue |
| API Issues | 23 | 1.2% | ✅ Technical, with SOP |
| User Form Issue | 23 | 1.2% | ✅ Answerable |
| Video Recovery | 22 | 1.1% | ⚠️ Manual recovery op |
| Case Not Visible | 19 | 1.0% | ✅ Answerable |
| Audio Related | 16 | 0.8% | ✅ Answerable |
| Patching Activity | 16 | 0.8% | ❌ DevOps/infra |
| *Other (30+ types)* | ~350 | ~18% | mixed |

**Dataset: RBL_RCA (2,339 tickets)**

| Query Type | Count | % | AI Relevance |
|---|---|---|---|
| Video Related | 704 | 30.1% | ✅ Core — highest volume |
| Others | 367 | 15.7% | ⚠️ Unclassified |
| Summary Data Update | 69 | 3.0% | ❌ Manual DB op |
| Connectivity Issue | 56 | 2.4% | ✅ Answerable |
| Server Alert | 54 | 2.3% | ❌ Monitoring |
| Reports | 47 | 2.0% | ⚠️ Partially answerable |
| Audio Related | 46 | 2.0% | ✅ Answerable |
| Account Creation | 43 | 1.8% | ⚠️ Operational |
| ID Creation | 36 | 1.5% | ⚠️ Operational |
| Audit Lock | 27 | 1.2% | ❌ Compliance process |
| API Issues | 21 | 0.9% | ✅ Technical |
| Unable to Login | 18 | 0.8% | ✅ Answerable |
| Summary Data Not Available | 17 | 0.7% | ✅ Answerable |
| Manual session status update | 16 | 0.7% | ❌ Manual DB op |
| Session Not Available | 12 | 0.5% | ✅ Answerable |
| NSDL/PAN Related | 11 | 0.5% | ✅ Answerable |

---

## 3. Issue Area Distribution (Technical Stack Layer)

| Issue Area | CSV | RBL_RCA1 | RBL_RCA | Combined est. |
|---|---|---|---|---|
| Backend | 694 (54%) | 284 (33%) | 742 (37%) | ~45% |
| Frontend | 524 (41%) | 300 (35%) | 276 (14%) | ~28% |
| Other | 262 (20%) | 65 (8%) | 396 (20%) | ~17% |
| API | 113 (9%) | 12 (1%) | 114 (6%) | ~6% |
| Database | 102 (8%) | 97 (11%) | 73 (4%) | ~8% |
| DevOps/Infrastructure | 13 (1%) | 33 (4%) | 7–8 (<1%) | ~2% |
| Network/Firewall | 2 (<1%) | 3 (<1%) | 3 (<1%) | ~1% |
| Third-party Integrations | 1 (<1%) | 0 | 0 | <1% |

**Observation**: Backend issues dominate (45%), but the split between Backend and Frontend varies significantly by client. This suggests different clients are hitting different product layers — Unity Bank (RBL_RCA1) has more Frontend issues (35%), while the broader dataset has more Backend.

---

## 4. Subject Line Pattern Mining (All 5,124 Subjects Combined)

### Top Topic Keywords

| Topic / Pattern | Occurrences | % of Subjects |
|---|---|---|
| "Not working / Issue / Unable" | 1,411 | **27.5%** |
| "Video KYC / VKYC" | 790 | **15.4%** |
| "Authentication / Login / Session" | 494 | **9.6%** |
| "Camera / Video not available/working" | 446 | **8.7%** |
| "Error / Failed / Failure" | 292 | **5.7%** |
| "Report / Export" | 252 | **4.9%** |
| "OTP" | 221 | **4.3%** |
| "API / Integration / Endpoint" | 149 | **2.9%** |
| "Network / Connectivity / Timeout" | 84 | **1.6%** |
| "Aadhaar" | 85 | **1.7%** |
| "Face match / Liveness" | 41 | **0.8%** |
| "Download / Upload" | 37 | **0.7%** |
| "PAN" | 59 | **1.2%** |
| "RBL Bank" | 151 | **2.9%** |
| "Slow / Performance / Lag" | 9 | **0.2%** |

---

## 5. Recurring vs. One-Time Issues

| File | Recurring Issue | New/One-time | Other |
|---|---|---|---|
| CSV | — | — | (field sparse) |
| RBL_RCA1 | ~520 (33%) | ~130 (8%) | — |
| RBL_RCA | **926 (39.6%)** | ~150 (6.4%) | — |

**39–40% of all tickets are explicitly tagged as recurring issues.** This is the highest-value segment for AI automation — these are known patterns with existing resolutions that can be systematically retrieved.

---

## 6. Category Deep-Dive: High-Value AI Categories

### Category 1: Video KYC / Video Related (Highest Volume)
- **Volume**: 704/2339 in RBL_RCA (30%), 34/1920 in CSV, 88/869 in RBL_RCA1
- **Client**: Primarily Unity Bank
- **Sub-patterns**: Camera not working, video not connecting, audio issues, video recovery (restore dropped calls)
- **RCA patterns**: Device-side (high CPU/memory on agent PCs), network disconnects, container restarts
- **SOP existence**: Likely present — this is the highest-volume recurring category
- **AI automation potential**: Medium-High — needs session ID for diagnosis

### Category 2: Server Alerts (Monitoring Noise)
- **Volume**: 506/1920 in CSV (26%), 54/2339 in RBL_RCA (2%), 128/869 in RBL_RCA1
- **Nature**: Auto-generated — triggered by monitoring systems (Zabbix, etc.)
- **AI relevance**: ❌ None — these are infrastructure alerts, not support queries
- **Recommendation**: Exclude entirely from AI training corpus

### Category 3: Unable to Login / Authentication
- **Volume**: ~42 in CSV, 10 in RBL_RCA1, 18 in RBL_RCA (~70 total)
- **Patterns**: Agent can't log in, wrong credentials, session expired
- **SOP existence**: Yes — "SOP Present" frequently tagged
- **AI automation potential**: High — well-defined resolution steps

### Category 4: Connectivity Issues
- **Volume**: 45 in CSV, 31 in RBL_RCA1, 56 in RBL_RCA (~132 total)
- **Patterns**: Network failure, VPN issues, call not connecting
- **RCA**: Network/infra related, often not within KwikID control
- **AI automation potential**: Medium — can provide diagnostic checklist

### Category 5: ID Operations (ID Creation, Mapping, Deactivation)
- **Volume**: 59+48+47+21 ≈ 175 in CSV, similar in others
- **Nature**: Manual database operations requested by bank administrators
- **AI relevance**: ❌ Operational task — should route to human agent, not AI
- **Pattern**: "Please create agent ID for [name] at [branch]"

### Category 6: Reports / Data Export
- **Volume**: 80 in CSV, 62 in RBL_RCA1, 47 in RBL_RCA (~189 total)
- **Patterns**: Request for monthly data dumps, session statistics reports
- **AI relevance**: ⚠️ Partially — AI can explain how to access self-service reports; actual report generation is operational
- **AI automation potential**: Low-Medium

### Category 7: Audio Issues
- **Volume**: 16 in CSV, 62 in RBL_RCA1, 46 in RBL_RCA (~124 total)
- **Patterns**: Agent microphone not working during video call, echo, no audio
- **RCA**: Device-side (microphone hardware), browser settings
- **AI automation potential**: High — can provide troubleshooting checklist

### Category 8: OTP / Authentication
- **Volume**: 221 subject mentions across all files (~4.3%)
- **Patterns**: OTP not received, OTP expired, retry limit hit
- **SOP**: "SOP Present" common for this category
- **AI automation potential**: High — well-defined troubleshooting SOP

---

## 7. Client / Bank Distribution

From the Tags analysis:
| Client | Tag occurrences | Notes |
|---|---|---|
| Unity (Small Finance Bank) | 268 + 210 + 24 = 502 | Primary client in XLSX files |
| Bank of Baroda | `bob` / `BankOfBaroda` = 183 | Significant in CSV |
| RBL Bank | `RBL` = 13 + 13 = 26 | Gives name to RBL_RCA files |
| Bajaj Finance | `Bajaj` = 24 | Present in CSV |
| IntegrateCloud | 5 | Minor |
| Fino | 1 | Minor |

**Multi-tenant implication**: The `Clients` column identifies the bank/client for each ticket. Retrieval must be **tenant-aware** — KwikID answers for Unity Bank differ from RBL answers (different product configurations, different ID formats, different server URLs).

---

## 8. SOP Coverage Analysis

SOPs (Standard Operating Procedures) are the primary source of reusable AI training content.

| SOP Status | CSV | RBL_RCA1 | RBL_RCA |
|---|---|---|---|
| SOP Present | 1,061 (55.3%) | 272 (31.3%) | 147 (6.3%) |
| No SOP Required | 504 (26.2%) | 262 (30.1%) | — (mostly null) |
| No SOP Available | 141 (7.3%) | 88 (10.1%) | 71 (3.0%) |
| SOP Created (New) | 4 (0.2%) | 3 (0.3%) | 7 (0.3%) |

**Key insight**: CSV has the highest SOP coverage (55.3%). This implies that for the CSV/XLS ticket batch, more than half of tickets were resolved using documented SOPs — these are the highest-value tickets for AI training (resolvable → has SOP → AI can replicate).

The RBL_RCA file has only 6.3% SOP coverage — this is an older or different operational cohort where SOP tagging was not consistently applied.

---

## 9. Resolution Quality Assessment

### Resolution Classification
| Classification | CSV | RBL_RCA1 | RBL_RCA |
|---|---|---|---|
| Solved by SOP (Temporary Workaround) | 234 (12.2%) | 80 (9.2%) | 84 (3.6%) |
| Solved by SOP (Permanent Fix) | 9 (0.5%) | 5 (0.6%) | 4 (0.2%) |
| Permanent Fix Applied by Dev | — | 6 (0.7%) | 7 (0.3%) |
| Temporary Fix, Pending Permanent | — | — | 7 (0.3%) |
| No SOP, Issue Exists | 1 (0.1%) | 2 (0.2%) | 6 (0.3%) |
| Wrongly reported by client | — | — | 1 (0.05%) |
| **Not filled (null)** | **87.3%** | **89.3%** | **96.1%** |

**Critical gap**: Resolution Classification is unfilled for 87–96% of tickets. This means the resolution quality assessment relies almost entirely on the text in Description, RCA, and StackOverflow Link fields.

---

## 10. Tags — AI System Activity Evidence

The tags reveal that **the AI system is already partially operational** on this Freshdesk instance:

| Tag | Count | What it signals |
|---|---|---|
| `ai_note` | 492 + 65 = 557 | AI has already added a note to this ticket |
| `ai_auto_replied` | 103 + 55 = 158 | AI has already auto-replied to this ticket |
| `rag_context` | 4 + 4 = 8 | RAG context was retrieved and used |
| `ai_test` | 4 + 4 = 8 | Test mode AI interaction |
| `auto_assigned_group` | 604 + 168 = 772 | n8n auto-routed to a group |
| `auto_polled` | 577 | n8n auto-polled ticket |
| `new ticket webhook` | 207 | n8n webhook triggered on creation |
| `alert` | 406 + 2 + 16 = 424 | Server alert ticket |

**This is significant**: ~158 tickets already have `ai_auto_replied` tags, and ~557 have `ai_note` tags. The system has been processing live tickets with the AI. This existing data is **feedback** on AI performance — these tickets can be used to evaluate current system quality.

---

## 11. Escalation-Heavy Categories

Categories that frequently trigger SLA violations or high-priority escalations:

| Category | High/Urgent Count | SLA Violated Est. | Escalation indicator |
|---|---|---|---|
| Server Alert | High (system-wide) | When SLA checked | `Asana Ticket Link` present |
| Video Related | Medium | 9.4% avg | Session ID required |
| Backend DB issues | High | High | Dev escalation via RCA |
| Connectivity | Low | Low | Often external factor |
| Unable to Login | Low | Low | SOP-resolvable |

Categories with `Asana Ticket Link` present (indicating formal escalation occurred) in RBL_RCA1: approximately **88.6% of Asana tickets are null** — only 11.4% had formal Asana escalations.

---

## 12. Likely Auto-Resolvable Categories

Based on patterns observed:

| Category | Auto-resolve Signal | Confidence |
|---|---|---|
| Unable to Login | SOP Present, recurring, <3 interactions | High |
| OTP Not Received | SOP Present, recurring | High |
| Send Link Issue | SOP Present, short resolution | High |
| Portal Not Working | SOP Present, recurring | High |
| Audio Issue | Troubleshooting checklist exists | Medium |
| Video Not Starting | Session ID needed, then SOP | Medium |
| Aadhaar/PAN upload fails | Document type-specific SOP | Medium |
| Session Not Available | Session ID lookup + manual update | Low (needs human) |
| ID Creation/Mapping | Manual DB operation | ❌ Human required |
| Server Alert | DevOps response needed | ❌ Human required |
| Summary Data Update | Manual DB operation | ❌ Human required |
