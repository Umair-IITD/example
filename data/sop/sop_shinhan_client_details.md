---
title: "Shinhan — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["shinhan"]
sop_id: "sop_shinhan_client_details"
---

## Overview

Reference for **Shinhan** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Client identifiers (client_name / domain)

- No explicit `client_name` values extracted — check admin portal or DynamoDB `kwikid_vkyc_agent` table.

## Portals and URLs

- `https://ekyc.shinhanbank.in:8070/getSession/80b920de-1eec-443a-a976-35e6e09147f5`

## S3 and storage paths

- No S3 paths extracted from KB posts.

## DynamoDB and data stores

- `Kwikid_ipv_poc`
- `kwikid_ipv_backend_new`

## Servers and script paths

- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/celery_shinhan.logs`
- `/home/ubuntu/Shinhan/celery_shinhan.logs`

## Common KB topics for this client

- `Shinhan - Check data push to Minio`
- `shinhan | agent portal not working (error Media server connection: Failed)`
- `SHINHAN | CKYC Error - check if CERSAI is down`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.