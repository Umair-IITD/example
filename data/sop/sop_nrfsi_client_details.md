---
title: "NRFSI — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["nrfsi"]
sop_id: "sop_nrfsi_client_details"
---

## Overview

Reference for **NRFSI** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Client identifiers (client_name / domain)

- `NRFSI`

## Portals and URLs

- `https://auat.vkyc.getkwikid.com:9090/sessions`
- `https://chaand.getkwikid.com/kms/access-token`
- `https://status.getkwikid.com:5055/verification/v1/idverification/dl/basic`
- `https://vkyc.nrfsi.com/admin`
- `https://vkyc.nrfsi.com/v1/agent/utility/get_user_table_data/8197872663`
- `https://vkyc.nrfsi.com/v1/agent/utility/update_summary_pdf`

## S3 and storage paths

- `s3://nrfsi-video-kyc-prod/videokyc/videos/NRFSI/`

## DynamoDB and data stores

- `kwikid_vkyc_agent`
- `kwikid_vkyc_session_status`

## Servers and script paths

- `/home/ubuntu/monitoring`
- `/home/ubuntu/script`

## Common KB topics for this client

- `NRFSI | how to recovery zip file when missing`
- `How to update session_status in NRFSI || step`
- `How to recover NRFSI video?`
- `NRFSI protal not working --Solution`
- `How to Update NRFSI Summary Data?`
- `NRFSI | how to recovery the summary.json | s3 bucket verisioning`
- `How to remove audit lock in nrfsi`
- `How to add missing Name, DOB or Location in NRFSI?`
- `NRFSI | how to restart cug services when not running`
- `NRFSI Admin Portal`
- `How to add agent/auditor in NRFSI - CLI script`
- `NRFSI || end_time addition though aws cli`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.