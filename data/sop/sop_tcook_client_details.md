---
title: "Thomas Cook (TCOOK) — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["tcook"]
sop_id: "sop_tcook_client_details"
---

## Overview

Reference for **Thomas Cook (TCOOK)** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Client identifiers (client_name / domain)

- No explicit `client_name` values extracted — check admin portal or DynamoDB `kwikid_vkyc_agent` table.

## Portals and URLs

- `https://ap-south-1.console.aws.amazon.com/s3/buckets/ta-videokyc?prefix=ipv_API/vkyc_summary_files/TCOOKP/`
- `https://videokyc.thomascook.in/connection-test`
- `https://videokyc.thomascook.in:5757/booking/put/FX5000000/*`
- `https://videokyc.thomascook.in:5757/slot/get/day/27-02-2024`

## S3 and storage paths

- No S3 paths extracted from KB posts.

## DynamoDB and data stores

- `Kwikid_ipv_poc`
- `kwikid_poc`
- `kwikid_vkyc_request_status`
- `kwikid_vkyc_session_status`

## Servers and script paths

- `/home/ubuntu/Kwikid_ipv_poc/vkyc_mod_schedule_backend_uat`
- `/home/ubuntu/script`
- `ubuntu@ip-172-81-12-53`
- `ubuntu@videokyc.thomascook.in`

## Common KB topics for this client

- `How to find A2 From signature Thomas?`
- `How do you do put booking in TCOOKP`
- `Back to branch in Tcook`
- `How to Recover Tcook Videos through script?`
- `TCOOKP New Back to Branch`
- `TCOOKP (Thomas cook) connectivity test`
- `How to create slots in TCOOK UAT?`
- `TCook | VKYC Agent Assignment`
- `How to check audit is completed or not | TCOOK_BR | TCOOKP | cli command`
- `TCOOKP | Basic Info is missing | Summary Data Update`
- `How to Recover TCOOK_BR Video?`
- `What do I do if a booking isn't available to an agent because its too old or if I want to assign it to another agent?`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.