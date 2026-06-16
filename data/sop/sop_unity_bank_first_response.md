---
title: "Unity Bank — First Response (Ticket Acknowledgment)"
query_type: "ESCALATION"
issue_area: "Support Triage"
clients: ["unity_bank"]
sop_id: "sop_unity_bank_first_response"
---

## Overview

First-response and triage guidance for **Unity Bank** tickets. Use the global holding reply, then follow the client intake checklist below.

## Scenario: Two agents connected to one user

After the standard holding reply, if analysis confirms dual routing:

Hi Team,

After analyzing, we have identified that this user got routed to 2 different agents.

However, we have identified the cause, and a planned deployment is in progress, which will permanently resolve this concern.

Please feel free to reach out to us if you have any further concerns or questions.

Best regards,

**Kwik.ID Support**

## Standard holding reply

Use the global first-response template (`sop_global_first_response`): acknowledge the ticket, request time to analyze, and sign as Kwik.ID Support.

## Client intake checklist

- **Unity Audit lock removing steps:** — see `sop_unity_bank_client_details` for steps.
- **How to recover Unity Auditor status not reflecting** — see `sop_unity_bank_client_details` for steps.
- **Unity | How to recover Aadhar_XML_Face_match?** — see `sop_unity_bank_client_details` for steps.
- **Unity Config Update - Agent & Admin Portal** — see `sop_unity_bank_client_details` for steps.
- **Unity | How to modify prodcut mapping for exsisting agent id | FINTECHFARM** — see `sop_unity_bank_client_details` for steps.
- **New ID creation | agent | admin | both for the unity** — see `sop_unity_bank_client_details` for steps.
- **How to analyze Nginx logs by session ID or user ID?** — see `sop_unity_bank_client_details` for steps.
- **unity | remove agents_ids from incorrect session status New** — see `sop_unity_bank_client_details` for steps.

## After analysis

1. Document findings in the internal ticket.
2. Send a follow-up email with specific session or root-cause details.
3. Reference procedural fixes from `sop_unity_bank_client_details` for portals, servers, and identifiers.

## When to escalate

- Multiple users affected or production portal down
- CBS push, audit, or callback API failure
- No matching runbook in Teams KB — ping team lead