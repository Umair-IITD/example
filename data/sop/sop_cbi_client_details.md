---
title: "Central Bank of India (CBI) — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["cbi"]
sop_id: "sop_cbi_client_details"
---

## Overview

Reference for **Central Bank of India (CBI)** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Primary environments (curated)

| Environment | User portal | Agent portal | Admin portal |
|-------------|---------------|----------------|--------------|
| VKYC UAT | `https://vkycuat.centralbank.co.in:49169/home?client_id=CBI_uat&api_key=CBI_uat&process=U` | `https://vkycuat.centralbank.co.in:49166/` | `https://vkycuat.centralbank.co.in:49167/` |
| DKYC UAT | `https://dkycuat.centralbank.co.in/vkycuserapp/home?client_id=CBI_uat&api_key=CBI_uat&process=U` | `https://dkycuat.centralbank.co.in/vkycagentapp` | `https://dkycuat.centralbank.co.in/admin` |

Domain identifier for UAT: `CBI_uat`. Production uses bank-hosted domains (`vkyc.centralbank.co.in`, `dkyc.centralbank.co.in`).

## API health checks

| API | Production | UAT |
|-----|------------|-----|
| User | `https://vkyc.centralbank.co.in/v1/user/health` | `https://vkycuat.centralbank.co.in:49173/v1/user/health` |
| Agent | `https://vkyc.centralbank.co.in/v1/agent/health` | `https://vkycuat.centralbank.co.in:49172/v1/agent/health` |
| Admin | `https://vkyc.centralbank.co.in/api/v1/health` | `https://vkycuat.centralbank.co.in/api/v1/health` |

## MinIO / S3 UI (credentials from vault)

- UAT MinIO UI: `https://vkycuat.centralbank.co.in:49155/login` (bucket `kwikid`)
- Prod data UI: `https://dbui.vkyc.cbi.dc.getkwikid.com/login`

## CBS / DKYC

- DKYC CBS queue (example): `https://dkycuat.centralbank.co.in/api/v1/cbs_put_queue`
- Typical script path on CBI servers: `/home/ubuntu/script`

## First-response notes

- Confirm whether ticket is **VKYC** or **DKYC** before deep investigation.
- For database disk-full errors on DB server, check `/oradata` usage (`df -h`) per KB.

## Client identifiers (client_name / domain)

- No explicit `client_name` values extracted — check admin portal or DynamoDB `kwikid_vkyc_agent` table.

## Portals and URLs

- `http://msr.centralbank.co.in:65525/`
- `http://proxy1.cbi.bank.in:8080`
- `https://auat.vkyc.getkwikid.com:5586/face_match_custom_file`
- `https://bulkpushcbi.mytoday.com/BulkSms/SingleMsgCBIApi\`
- `https://cbi.app.getkwikid.com/admin/`
- `https://cbi.app.getkwikid.com/admin/sessions`
- `https://cbi.vkyc.getkwikid.com:8001/form`
- `https://centsftp.centralbank.co.in/`
- `https://db.vkyc.cbi.dc.getkwikid.com`
- `https://db.vkyc.cbi.dc.getkwikid.com/`
- `https://db.vkyc.cbi.prod.getkwikid.com:443`
- `https://dbui.vkyc.cbi.dc.getkwikid.com`
- `https://dbui.vkyc.cbi.dc.getkwikid.com/`
- `https://dbui.vkyc.cbi.dc.getkwikid.com/login`
- `https://dev.vkyc.getkwikid.com:5000/form`
- `https://dev.vkyc.getkwikid.com:5000/user`
- `https://dkyc.centralbank.bank.in/api/v1/cbs_put_queue`
- `https://dkyc.centralbank.co.in/api/v1/cbs_put_queue`
- `https://dkyc.centralbank.co.in/ml/infer/face_match_custom`
- `https://dkyc.centralbank.co.in/v1/agent/signup`
- `https://dkycuat.centralbank.co.in/admin`
- `https://dkycuat.centralbank.co.in/api/v1/cbs_put_queue`
- `https://dkycuat.centralbank.co.in/v1/agent/signup`
- `https://dkycuat.centralbank.co.in/vkycagentapp`
- `https://dkycuat.centralbank.co.in/vkycuserapp/home?client_id=CBI_uat&api_key=CBI_uat&process=U`
- `https://emq.vkyc.cbi.prod.getkwikid.com`
- `https://emq.vkyc.cbi.prod.getkwikid.com/000000000000/CBI_PLC_free_vkyc`
- `https://emq.vkyc.cbi.prod.getkwikid.com/000000000000/CBI_PLC_priority_vkyc`
- `https://emq.vkyc.cbi.prod.getkwikid.com/000000000000/CBI_free_cug_vkyc`
- `https://emq.vkyc.cbi.prod.getkwikid.com/000000000000/CBI_free_vkyc*https://emq.vkyc.cbi.prod.getkwikid.com/000000000000/CBI_free_vkyc`
- `https://emq.vkyc.cbi.prod.getkwikid.com/000000000000/CBI_priority_vkyc`
- `https://emq.vkyc.cbi.prod.getkwikid.com:443`
- `https://emqui.vkyc.cbi.dc.getkwikid.com/`
- `https://mio.dkycuat.cbi.getkwikid.com`
- `https://mio.vkyc.cbi.dc.getkwikid.com`
- `https://mio.vkyc.cbi.dc.getkwikid.com/`
- `https://mio.vkyc.cbi.prod.getkwikid.com`
- `https://mio.vkyc.cbi.prod.getkwikid.com:443`
- `https://msr.centralbank.co.in:49155/`
- `https://msr.centralbank.co.in:49155/buckets`

## S3 and storage paths

- `s3://kwikid-test/data/dummy/abc.json`
- `s3://kwikid/CONFIG/AGENT/CBI_uat/branchList/`
- `s3://kwikid/videokyc/agents/CBI/agents_dump.json`
- `s3://kwikid/videokyc/logs/CBI/rekyc_data/9403936738/`
- `s3://kwikid/videokyc/logs/CBI/rekyc_data/9403936738/9403936738_1738643642.json`
- `s3://kwikid/videokyc/logs/cbs_latest/CBI_DKYC_uat/9421506511/5afd5a1a-f4e9-4a4b-8afb-15c740dd3c05/`
- `s3://kwikid/videokyc/reports/CBI_PLC/GBM/VKYC_YYYYMMDD_001_P.dat`
- `s3://kwikid/videokyc/summary/CBI/`
- `s3://kwikid/videokyc/video_blobs/`
- `s3://kwikid/videokyc/video_blobs/6e7c13e4-65d4-4367-ae77-493dfa1b63d6/agent_video_screen/`
- `s3://kwikid/videokyc/videos/CBI/`
- `s3://sumati/gbm/GBM_pensioner_data_2025.csv`
- `s3://sumati/gbm_data/`
- `s3://sumati/gbm_data/GBM_data_pensioner.csv`

## DynamoDB and data stores

- `Kwikid_celery_task`
- `Kwikid_cug`
- `Kwikid_ipv_poc`
- `Kwikid_ipv_poc_vkyc`
- `kwikid_aadhaar_service`
- `kwikid_admin_api_backend_service`
- `kwikid_cbs_accounts`
- `kwikid_celery_tasks`
- `kwikid_customer_form_data`
- `kwikid_dkyc_apk`
- `kwikid_dkyc_apk1`
- `kwikid_dkyc_apk2`
- `kwikid_dkyc_apk3`
- `kwikid_dkyc_apk4`
- `kwikid_dkyc_apk5`
- `kwikid_dkyc_apk6`
- `kwikid_dkyc_dkyc`
- `kwikid_dkyc_flor`
- `kwikid_dkyc_wrapper`
- `kwikid_dkyc_wrapper_d`

## Servers and script paths

- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/error_logs`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/ipv_logs`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_cbs_celery/com/getkwikid/pansig`
- `/home/ubuntu/debugger/scripts/auditor_visibility/`
- `/home/ubuntu/debugger/scripts/maker_checker_branch`
- `/home/ubuntu/kwikid_ipv_backend_new`
- `/home/ubuntu/script`
- `/home/ubuntu/script/86b75179-5014-41f0-8d87-176d9b53d2c9.json`
- `/home/ubuntu/script/venv3.7/bin/activate`
- `/home/ubuntu/scripts`
- `/home/ubuntu/support_sop_scripts`
- `/home/ubuntu/venv3/bin/activate`
- `ip-172-31-21-73`
- `ip-172-31-29-11`
- `ubuntu@ip-172-29-11-111`
- `ubuntu@ip-172-31-29-11`

## Common KB topics for this client

- `Fund Transfer - CBI SA`
- `CBI | dkyc | Retrigger`
- `CBI | VKYC | How to resolve Failed to search in database issue`
- `How to Execute the Aadhaar PDF Recovery Script for CBI?`
- `"Global Unhandled Exception" in CBI DKYC due to Form Data Missing some Keys in Account Creation. How to Fix?`
- `CBI dkyc account creation`
- `CBI Linux TTYD setUp`
- `CBI Production | How to Repush CBS Cases?`
- `CBI PROD | DKYC - Retrigger Failure in Doc/Aadhaar Upload (CKYCR Upload failure)`
- `CBI PROD DKYC | account opening | CBS`
- `CBI | DKYC | Backend Containers`
- `CBI - How to download a file to minio/s3`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.