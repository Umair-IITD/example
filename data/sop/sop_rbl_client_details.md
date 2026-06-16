---
title: "RBL / BFL (RBL Bank infrastructure) — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["rbl"]
sop_id: "sop_rbl_client_details"
---

## Overview

Reference for **RBL / BFL (RBL Bank infrastructure)** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Primary environments (curated)

| Brand | Production API base | UAT API base |
|-------|---------------------|--------------|
| RBL VCIP | `https://vcip.rblbank.com` | `https://vcipuat.rblbank.com` |
| BFL (Bajaj Fin domain on RBL stack) | Same VCIP hosts | UAT admin often `https://uat.vkyc.getkwikid.com:9090/admin` |

Domain values in APIs: `BFL`, `BFL_uat`, client_name `BFL` in DynamoDB.

## Common operations

| Task | Reference |
|------|-----------|
| Half-video correction | `POST https://vcip.rblbank.com/api/v3/correctionAPI` body `{"domain":"BFL","session_id":"...","correction_list":["halfVideo"]}` |
| Agent token (prod) | `https://vcip.rblbank.com/api/v1/agent/generate_token` |
| Agent register (UAT) | `https://vcipuat.rblbank.com/api/v1/agent/register` |
| SFDC logs on S3 | Prefix `s3://rbl-prod-kwikid-vkyc/BFL/rbl_sfdc/` or BFL dated paths |

## Access path

1. VPN / ThinkAnalytics EC2
2. RBL AWS bastion
3. Application server of choice

## First-response notes

- Blob count zero on correction API usually means video is not recoverable.
- BFL admin may require Chrome `--disable-web-security` for local dashboard access (internal KB).

## Client identifiers (client_name / domain)

- `RBL`

## Portals and URLs

- `https://baadal.rblbank.com/logon/LogonPoint/index.html`
- `https://kwikid.s3.ap-south-1.amazonaws.com/pintu/bfl_rbl_video_merge_main.py`
- `https://uat.vkyc.getkwikid.com:9090/admin`
- `https://vcip.rblbank.com/agents`
- `https://vcip.rblbank.com/api/v1/agent/deregister/`
- `https://vcip.rblbank.com/api/v1/agent/generate_token`
- `https://vcip.rblbank.com/api/v1/all_uploaded_confirmation/session`
- `https://vcip.rblbank.com/api/v3/correctionAPI`
- `https://vcip.rblbank.com/api/v4/v4/agent/deregister/G129365`
- `https://vcip.rblbank.com/api/v5/admin/drop_audit_hold/RBL_agent_10/114fec9a-1b28-4828-96f7-6f3f46f3d78f`
- `https://vcip.rblbank.com/api/v5/admin/drop_audit_session/RBL_agent_10/114fec9a-1b28-4828-96f7-6f3f46f3d78f`
- `https://vcip.rblbank.com/ice-portal`
- `https://vcip.rblbank.com/p1agent/dashboard`
- `https://vcipuat.rblbank.com/api/v1/agent/generate_token`
- `https://vcipuat.rblbank.com/api/v1/agent/register`
- `https://vcipuat.rblbank.com/api/v1/agent/test_callback/PB10001614751/202fd2f7-eac5-4dc1-8841-7541fd29d4bf/RBL_uat/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJSQkxfdWF0IiwiaXNzIjoiaXB2X2FwaV9Jb25pY0FwcCIsInJvbGUiOiJhZ2VudCIsImV4cCI6MTczNDQxODk2NiwiaWF0IjoxNzAyODgyOTY2LCJhdWQiOiJ1c3IifQ.KkLSYKBtUjwBGaJyHkYhNkKNBlq27TpJGzZqMiNtT40/CHECKER_APPROVED?key=sQYDEuz0it4Vz4n`
- `https://vcipuat.rblbank.com/api/v1/agent/test_callback/\`

## S3 and storage paths

- `s3://kwikid/sumati/bfl/repush.txt`
- `s3://rbl-prod-kwikid-vkyc`
- `s3://rbl-prod-kwikid-vkyc/BFL`
- `s3://rbl-prod-kwikid-vkyc/BFL/`
- `s3://rbl-prod-kwikid-vkyc/BFL/2022/8/4/BFLC0108606038_1659614982.json`
- `s3://rbl-prod-kwikid-vkyc/BFL/2024/10/03/BFLCA0017408037`
- `s3://rbl-prod-kwikid-vkyc/BFL/rbl`
- `s3://rbl-prod-kwikid-vkyc/BFL/rbl_sfdc/`
- `s3://rbl-prod-kwikid-vkyc/BFL/rbl_sfdc/2022/8/4/BFLC0108606038`
- `s3://rbl-prod-kwikid-vkyc/BFL/rbl_sfdc/2022/8/4/BFLC0108606038_1659600607.json`
- `s3://rbl-prod-kwikid-vkyc/BFL/rbl_sfdc/BFLCA0017001779/`
- `s3://rbl-prod-kwikid-vkyc/videokyc/logs/`
- `s3://rbl-prod-kwikid-vkyc/videokyc/logs/Digiremit/NA210820230059/d6c194e3-0cf5-497c-b4d0-a61eac0c8121/digiremit_VKYCStatusupdate.json`
- `s3://rbl-prod-kwikid-vkyc/videokyc/logs/Retailasset/V1403113239/`
- `s3://rbl-prod-kwikid-vkyc/videokyc/logs/Retailasset/V1403113239/e2d206df-de89-4388-99f1-7e81f6d12a18/`
- `s3://rbl-prod-kwikid-vkyc/videokyc/logs/Retailasset/V1403113239/e2d206df-de89-4388-99f1-7e81f6d12a18/TCS_default.json`
- `s3://rbl-prod-kwikid-vkyc/videokyc/summary/RBL/`
- `s3://rbl-prod-kwikid-vkyc/videokyc/summary/RBL/CC01012239998_3990504/83e54259-c2e8-42bf-85f8-c9f4f3ca2d36/83e54259-c2e8-42bf-85f8-c9f4f3ca2d36.zip`
- `s3://rbl-prod-kwikid-vkyc/videokyc/summary/RBL/PM10044683373/33a1c697-ac4e-44ef-ad19-48189671ac2f/logs/apis/app_services/agent_api/CHECKER_APPROVED/`
- `s3://rbl-prod-kwikid-vkyc/videokyc/summary/RBL/PM10044683373/33a1c697-ac4e-44ef-ad19-48189671ac2f/logs/apis/app_services/agent_api/CHECKER_APPROVED/latest.json`
- `s3://rbl-prod-kwikid-vkyc/videokyc/video_blobs/1262f099-ddd0-45de-8bf3-4a87f1b1d382/agent_video_screen/`
- `s3://rbl-prod-kwikid-vkyc/videokyc/videos/BFL/`

## DynamoDB and data stores

- `kwikid_poc_ipv_backend_v2`
- `kwikid_vkyc_agent`
- `kwikid_vkyc_agent_uat`
- `kwikid_vkyc_approved_session_status`
- `kwikid_vkyc_link_status`
- `kwikid_vkyc_session_status`
- `kwikid_vkyc_session_status_uat`
- `kwikid_vkyc_user_status`

## Servers and script paths

- `/home/ubuntu/deployment-scripts/celery-worker-1/.env`
- `/home/ubuntu/deployment-scripts/merge_blobs_renderer_worker/.env`
- `/home/ubuntu/deployment-scripts/merge_blobs_worker/.env`
- `/home/ubuntu/deployment-scripts/merge_blobs_worker_vpd/.env`
- `/home/ubuntu/kwikid-queue-management/.env`
- `/home/ubuntu/scripts/dump_clickhouse_logs.py`
- `ip-10-43-16-69`
- `ip-10-45-246-14`
- `ip-10-45-246-19`
- `ip-10-45-246-4`
- `ip-10-45-246-43`
- `ip-10-45-246-61`
- `ip-10-45-246-62`

## Common KB topics for this client

- `RBL | Video Recovery?`
- `RBL | agent/auditor | Auditor Hold Notification Mail manual export to csv`
- `RBL | Case not assign and visible in agent portal?`
- `Callback Request Logs Download for All RBL Products`
- `How to get BFL Approved Session ID against User ID?`
- `RBL | NSDL Logs Download?`
- `RBL | How to fetch PAN details?`
- `RBL | How to retrigger Callback Manually for RBL through script?`
- `RBL Audit-lock remove [script] V3 [dyanamo/pg_admin]`
- `How to access RBL/BFL servers`
- `RBL || Video recovery (script) -with ffmpeg logic (on server) V2`
- `RBL | How to create agent/auditor and admin id in rbl domain []`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.