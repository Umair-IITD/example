---
title: "Opdyta — First Response (Ticket Acknowledgment)"
query_type: "ESCALATION"
issue_area: "Support Triage"
clients: ["opdyta"]
sop_id: "sop_opdyta_first_response"
---

## Overview

First-response and triage guidance for **Opdyta** tickets. Use the global holding reply, then follow the client intake checklist below.

## Standard holding reply

Use the global first-response template (`sop_global_first_response`): acknowledge the ticket, request time to analyze, and sign as Kwik.ID Support.

## Client intake checklist

- **How to hit webhook event for a specific through script?** — see `sop_opdyta_client_details` for steps.
- **Urgent-Error in EKYC Report -- opdyta** — see `sop_opdyta_client_details` for steps.

## After analysis

1. Document findings in the internal ticket.
2. Send a follow-up email with specific session or root-cause details.
3. Reference procedural fixes from `sop_opdyta_client_details` for portals, servers, and identifiers.

## When to escalate

- Multiple users affected or production portal down
- CBS push, audit, or callback API failure
- No matching runbook in Teams KB — ping team lead