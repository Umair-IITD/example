---
title: "NRFSI — First Response (Ticket Acknowledgment)"
query_type: "ESCALATION"
issue_area: "Support Triage"
clients: ["nrfsi"]
sop_id: "sop_nrfsi_first_response"
---

## Overview

First-response and triage guidance for **NRFSI** tickets. Use the global holding reply, then follow the client intake checklist below.

## Standard holding reply

Use the global first-response template (`sop_global_first_response`): acknowledge the ticket, request time to analyze, and sign as Kwik.ID Support.

## Client intake checklist

- **NRFSI | how to recovery zip file when missing** — see `sop_nrfsi_client_details` for steps.
- **How to update session_status in NRFSI || step** — see `sop_nrfsi_client_details` for steps.
- **How to recover NRFSI video?** — see `sop_nrfsi_client_details` for steps.
- **NRFSI protal not working --Solution** — see `sop_nrfsi_client_details` for steps.
- **How to Update NRFSI Summary Data?** — see `sop_nrfsi_client_details` for steps.
- **NRFSI | how to recovery the summary.json | s3 bucket verisioning** — see `sop_nrfsi_client_details` for steps.
- **How to remove audit lock in nrfsi** — see `sop_nrfsi_client_details` for steps.
- **How to add missing Name, DOB or Location in NRFSI?** — see `sop_nrfsi_client_details` for steps.

## After analysis

1. Document findings in the internal ticket.
2. Send a follow-up email with specific session or root-cause details.
3. Reference procedural fixes from `sop_nrfsi_client_details` for portals, servers, and identifiers.

## When to escalate

- Multiple users affected or production portal down
- CBS push, audit, or callback API failure
- No matching runbook in Teams KB — ping team lead