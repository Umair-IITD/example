# Unity Admin Portal — Source of Truth

**Portal (React SPA)**: `https://vkyc360.unitybank.co.in:9090`  
**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443 — standard HTTPS)  
**Client**: Unity Bank (Video KYC)  
**Discovery phase**: Phase A Wave 2 — READ ONLY  
**Audit date**: 2026-07-13 — **COMPLETE** (API confirmed via live calls)  
**Scope**: Complete reverse-engineering of Unity Admin Portal for KwikID Support Automation Platform integration

> **Status**: API discovery COMPLETE. All endpoints confirmed via live calls on 2026-07-13.
> Port 9090 (React SPA) cannot be reached from CCR due to proxy limitation — UI workflow documented from API data only.
> Port 443 REST API confirmed fully working.

---

## Quick Reference

| Property | Value |
|:---|:---|
| API Base URL | `https://vkyc360.unitybank.co.in` (port 443) |
| Admin Portal (SPA) | `https://vkyc360.unitybank.co.in:9090` (browser only) |
| Auth mechanism | JWT via custom `auth` header (NOT `Authorization: Bearer`) |
| Token endpoint | `POST /v1/agent/generate_token` |
| Agent credentials | `{"username": "unity", "password": "unity"}` |
| Token response field | `Token` (capital T) |
| Token TTL | 12 hours (43200 seconds), algorithm HS256 |
| Session list endpoint | `GET /api/v1/getAllUserSession/{domain}/{phone_number}` |
| Session detail endpoint | `GET /v1/session/get_details/{session_id}` |
| Domain value | `unity` (always) |
| Primary customer ID | `phone_number` — 10-digit mobile number |
| Session ID format | UUID v4 (e.g., `329c5f9e-9406-4f47-b717-5c87f04054d2`) |
| Protocol | HTTPS (TLS on port 443) |
| Server | AWS ELB, ap-south-1 (Mumbai) |

> **SECURITY NOTE**: Admin portal credentials (`shubham.singh@think360.ai` / `New@12345`) were shared in plaintext and must be treated as compromised. Rotate immediately and store at `kwikid/unity/vkyc_admin_credentials` in AWS Secrets Manager. Agent API credentials must be stored at `kwikid/unity/vkyc_api_credentials`.

---

## Key Corrections (vs Initial Assumptions)

| Assumption | Reality (confirmed 2026-07-13) |
|:---|:---|
| Primary identifier is URN | **No URN exists** — primary identifier is `phone_number` (10-digit mobile) |
| Auth: `Authorization: Bearer <token>` | Auth: `auth: <token>` — custom header name, no "Bearer" prefix |
| Token field: `token` | Token field: `Token` (capital T) |
| Credentials: admin email/password | Credentials: `{"username":"unity","password":"unity"}` (service account) |
| API on port 9090 | API on port **443** — port 9090 is React SPA frontend only |
| GET params for session lookup | Path params: `/api/v1/getAllUserSession/unity/{phone_number}` |
| TTL unknown | TTL confirmed: 12 hours (agent token), 7 days (admin portal token) |

---

## Discovery File Index

| File | Contents | Status |
|:---|:---|:---|
| `README.md` | This file — index and quick reference | ✅ COMPLETE |
| `authentication.md` | JWT lifecycle, token generation, refresh, storage | ✅ COMPLETE |
| `api_reference.md` | Every endpoint: method, URL, headers, payload, response | ✅ COMPLETE |
| `network_analysis.md` | Network findings, port 443 vs 9090, CCR proxy limitation | ✅ COMPLETE |
| `jwt_analysis.md` | JWT structure, claims, expiry, live token decode | ✅ COMPLETE |
| `session_discovery.md` | getAllUserSession: full field list, session_status enum | ✅ COMPLETE |
| `session_details.md` | get_details: full field list, summary_data, audit fields | ✅ COMPLETE |
| `entity_model.md` | Entity model: Customer, Session, Audit, Documents | ✅ COMPLETE |
| `field_dictionary.md` | Every field: name, type, example, nullable, AI significance | ✅ COMPLETE |
| `search_behaviour.md` | Search by phone_number, session_id, regex patterns | ✅ COMPLETE |
| `ui_workflow.md` | Admin portal workflow (documented from API data) | ✅ COMPLETE |
| `investigation_mapping.md` | Unity field → Evidence Collector → Root Cause Engine | ✅ COMPLETE |
| `integration_notes.md` | Implementation: auth, caching, retry, endpoints | ✅ COMPLETE |
| `jwt_analysis.md` | JWT structure, claims, expiry, scope | ✅ COMPLETE |
| `observations.md` | Findings, gaps, risks, recommendations | ✅ COMPLETE |
| `limitations.md` | What Unity cannot provide; blind spots | ✅ COMPLETE |
| `summary.json` | Machine-readable summary of all key findings | ✅ COMPLETE |

---

## Architecture Overview

Unity Admin Portal is the bank-facing administrative dashboard for KwikID's VKYC (Video KYC) service deployed for Unity Bank. It consists of two separate layers:

**REST API (port 443)** — the integration target for AI automation. Stateless, JWT-authenticated, returns JSON. Fully accessible from CCR.

**React SPA frontend (port 9090)** — the browser-based UI used by human bank agents and auditors. Served separately, cannot be reached from CCR due to proxy TLS limitation on non-443 ports.

---

## Integration Target

The AI Investigation Layer (UNITYTOOL) queries the REST API on port 443 to:
1. Confirm whether a customer (by `phone_number`) attempted a KYC session
2. Determine the session status at the time of a support ticket
3. Identify failure points — `session_status`, `auditor_feedback`, `feedback` JSON
4. Correlate ticket creation time with `init_time`, `start_time`, `end_time`
5. Provide root cause evidence for the Observation Generator

---

## Live Discovery Status

| Part | Area | Status |
|:---|:---:|:---|
| 1 | Authentication (JWT lifecycle) | ✅ CONFIRMED — live token obtained |
| 2 | Network analysis (port 443 vs 9090) | ✅ CONFIRMED — CCR proxy limitation documented |
| 3 | API catalog (all endpoints) | ✅ CONFIRMED — 7 endpoints documented |
| 4 | Search by phone_number | ✅ CONFIRMED — path parameter pattern |
| 5 | Entity model (all fields) | ✅ CONFIRMED — from live JSON responses |
| 6 | getAllUserSession response | ✅ CONFIRMED — 76 fields documented |
| 7 | get_details response | ✅ CONFIRMED — summary_data, audit fields |
| 8 | Field value classification | ✅ CONFIRMED — field_dictionary.md complete |
| 9 | Root cause evidence mapping | ✅ CONFIRMED — investigation_mapping.md |
| 10 | Loki correlation identifiers | ✅ session_id (UUID v4) confirmed |
| 11 | UI capabilities | ⚠️ PARTIAL — port 9090 SPA not accessible from CCR |
| 12 | session_status enum | ✅ CONFIRMED — 7 values |
| 13 | JWT claims and TTL | ✅ CONFIRMED — live token decoded |
| 14 | Security analysis | ✅ CONFIRMED — HS256, HTTPS, auth: header |
| 15 | Limitations | ✅ CONFIRMED |
| 16 | Implementation recommendations | ✅ CONFIRMED — integration_notes.md |
