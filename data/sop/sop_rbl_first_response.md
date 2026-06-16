---
title: "RBL / BFL (RBL Bank infrastructure) — First Response (Ticket Acknowledgment)"
query_type: "ESCALATION"
issue_area: "Support Triage"
clients: ["rbl"]
sop_id: "sop_rbl_first_response"
---

## Overview

First-response and triage guidance for **RBL / BFL (RBL Bank infrastructure)** tickets. Use the global holding reply, then follow the client intake checklist below.

## Standard holding reply

Use the global first-response template (`sop_global_first_response`): acknowledge the ticket, request time to analyze, and sign as Kwik.ID Support.

## Client intake checklist

- **RBL | Video Recovery?** — see `sop_rbl_client_details` for steps.
- **RBL | agent/auditor | Auditor Hold Notification Mail manual export to csv** — see `sop_rbl_client_details` for steps.
- **RBL | Case not assign and visible in agent portal?** — see `sop_rbl_client_details` for steps.
- **Callback Request Logs Download for All RBL Products** — see `sop_rbl_client_details` for steps.
- **How to get BFL Approved Session ID against User ID?** — see `sop_rbl_client_details` for steps.
- **RBL | NSDL Logs Download?** — see `sop_rbl_client_details` for steps.
- **RBL | How to fetch PAN details?** — see `sop_rbl_client_details` for steps.
- **RBL | How to retrigger Callback Manually for RBL through script?** — see `sop_rbl_client_details` for steps.

## After analysis

1. Document findings in the internal ticket.
2. Send a follow-up email with specific session or root-cause details.
3. Reference procedural fixes from `sop_rbl_client_details` for portals, servers, and identifiers.

## When to escalate

- Multiple users affected or production portal down
- CBS push, audit, or callback API failure
- No matching runbook in Teams KB — ping team lead