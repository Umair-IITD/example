---
title: "Unity Bank — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["unity_bank"]
sop_id: "sop_unity_bank_client_details"
---

## Overview

Reference for **Unity Bank** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Primary environments (curated)

| Environment | Base URL | Notes |
|-------------|----------|-------|
| Production | `https://vkyc360.unitybank.co.in` | Agent, admin (:9090), session APIs |
| UAT | `https://vkycuat.unitybank.co.in` | Form API e.g. `:8000/form` |

Client identifiers: `unity` (production), `unity_uat` (UAT).

## Key APIs

| Purpose | Endpoint |
|---------|----------|
| CBS put queue | `GET/POST https://vkyc360.unitybank.co.in/api/v1/cbs_put_queue` |
| Session details | `https://vkyc360.unitybank.co.in/v1/session/get_details/{session_id}` |
| Callback check | `https://vkyc360.unitybank.co.in/v1/agent/callback_event` |
| Clear form data (UAT) | `https://vkycuat.unitybank.co.in:8000/form` with header `client_name: unity_uat` |

## Configuration (S3)

- Admin config: `s3://kwikid-prod/CONFIG/ADMIN/unity/adminConfig.json`
- Agent config: `s3://kwikid-prod/CONFIG/AGENT/unity/agentConfig.json`
- User config: `s3://kwikid-prod/CONFIG/USER/unity/userConfig.json`

## ADV / Vault (UAT)

- Vault insert URL (verify on UAT server `VaultAPI.java`): `https://avaultuat.unitybank.co.in/vault/insert`
- Service: `kwikid_cbs_vault_api.service` — check with `systemctl status` / `journalctl`

## Scripts

- UAT scripts: `/home/ubuntu/script` on Unity UAT server (e.g. `get_audit_id_added.py`, `create_userid_v2.0.py`)

## First-response notes

- Two agents on one session: use standard holding reply, then verify routing/callback logs.
- RO ID / product mapping updates: `mapping.py` on UAT server `172.29.11.111` under celery tasks path.

## Client identifiers (client_name / domain)

- `unity`
- `unity_uat`

## Portals and URLs

- `http://status.getkwikid.com:3001/status/unity-bank`
- `https://aml.unitybank.co.in/ramp/webservices/request/handle-ramp-request`
- `https://avaultuat.unitybank.co.in/vault/insert`
- `https://bitbucket.org/team360noscope/unity-rekyc-cron/src/master/`
- `https://config.app.getkwikid.com/projects/default/features/notification`
- `https://kwikid-prod.s3.amazonaws.com/videokyc/summary/unity/[user_id`
- `https://kwikid-prod.s3.ap-south-1.amazonaws.com/CONFIG/ADMIN/unity/roles/report_downloader/adminConfig.json`
- `https://kwikid-prod.s3.ap-south-1.amazonaws.com/CONFIG/ADMIN/unity/roles/session_viewer/adminConfig.json`
- `https://kwikid.s3.ap-south-1.amazonaws.com/kwikid/jar/unity-vault-api/VaultAPI.jar`
- `https://pg.test.getkwikid.com/postgrest/kwikid-event-logger`
- `https://rekyc.unitybank.co.in/v1/download_content/kwikid-prod/rekyc/reports/2024/09/Report_REKYC_2024-09-20.xlsx`
- `https://vkyc360.unitybank.co.in`
- `https://vkyc360.unitybank.co.in/api/v1`
- `https://vkyc360.unitybank.co.in/api/v1/cbs_put_queue`
- `https://vkyc360.unitybank.co.in/correctionAPI`
- `https://vkyc360.unitybank.co.in/slot/get/day/admin/`
- `https://vkyc360.unitybank.co.in/v1/agent`
- `https://vkyc360.unitybank.co.in/v1/agent/callback_event`
- `https://vkyc360.unitybank.co.in/v1/agent/generate_token`
- `https://vkyc360.unitybank.co.in/v1/agent/sendLink/`
- `https://vkyc360.unitybank.co.in/v1/all_uploaded_confirmation/`
- `https://vkyc360.unitybank.co.in/v1/all_uploaded_confirmation/{sid}/agent_video_screen`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/aadhaar/unity/9163527717/9163527717_aadhaar.xml`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/aadhaar/unity/9163527717/9163527717_aadhaar_redacted.pdf`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/aadhaar/unity/9182157364/9182157364_aadhaar.xml`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/aadhaar/unity/9890876884/9890876884_aadhaar.xml`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/aadhaar/unity/9890876884/9890876884_aadhaar_redacted.pdf`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/summary`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/summary/unity/`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/summary/unity/9182157364/54f8393f-4b19-4ddb-a24b-83108e0a2cdb/9182157364_aadhaar_redacted.pdf`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/summary/unity/9825848861/c70c4e08-3d9e-438b-896b-e0e2507582c7/9825848861_aadhaar_redacted.pdf`
- `https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/{destination_key}`
- `https://vkyc360.unitybank.co.in/v1/session/get_details/`
- `https://vkyc360.unitybank.co.in/v1/session/get_details/0723ef2b-c02a-47b5-98b7-fdba3d7637cf`
- `https://vkyc360.unitybank.co.in/v1/session/get_details/70f1d34d-8dce-4bfa-a3d4-3e246fb905fb`
- `https://vkyc360.unitybank.co.in/v1/session/get_details/72e21186-35e7-479f-a690-df406a00c9c5`
- `https://vkyc360.unitybank.co.in/v1/user/load_form`
- `https://vkyc360.unitybank.co.in/v2/admin/get_all_sessions`
- `https://vkyc360.unitybank.co.in:9090`
- `https://vkycuat.unitybank.co.in:3357/api/auth/pan-signature`

## S3 and storage paths

- `s3://kwikid-prod/`
- `s3://kwikid-prod/CONFIG/ADMIN/`
- `s3://kwikid-prod/CONFIG/ADMIN/unity/`
- `s3://kwikid-prod/CONFIG/ADMIN/unity/adminConfig.json`
- `s3://kwikid-prod/CONFIG/AGENT/unity/agentConfig.json`
- `s3://kwikid-prod/CONFIG/USER/unity/userConfig.json`
- `s3://kwikid-prod/api_logs/unity/`
- `s3://kwikid-prod/api_logs/unity/7016192816/`
- `s3://kwikid-prod/api_logs/unity/7016192816/9f13ec92-0a82-4a5f-9249-44e6b0960491/`
- `s3://kwikid-prod/api_logs/unity/7016192816/9f13ec92-0a82-4a5f-9249-44e6b0960491/sendlink_20251107_133834_041363.json`
- `s3://kwikid-prod/videokyc/aadhaar/unity/`
- `s3://kwikid-prod/videokyc/images/unity/8460577003/2df49a6b-bc9b-4d5e-9888-5963b6075fbd/`
- `s3://kwikid-prod/videokyc/images/unity/8460577003/2df49a6b-bc9b-4d5e-9888-5963b6075fbd/8460577003_pan_1757420324.jpg`
- `s3://kwikid-prod/videokyc/images/unity/8460577003/2df49a6b-bc9b-4d5e-9888-5963b6075fbd/8460577003_pan_original.jpg`
- `s3://kwikid-prod/videokyc/logs/callback/unity/`
- `s3://kwikid-prod/videokyc/logs/callback/unity/7980242880/6890172f-94a1-42cb-844d-8f5c52f68d72/`
- `s3://kwikid-prod/videokyc/logs/callback/unity/7980242880/6890172f-94a1-42cb-844d-8f5c52f68d72/kyc_checker_approved_1740651961.json`
- `s3://kwikid-prod/videokyc/logs/callback/unity/9435696472/649b2a27-227c-4f0f-a3fc-e1f11f5476ed/`
- `s3://kwikid-prod/videokyc/logs/callback/unity/9435696472/649b2a27-227c-4f0f-a3fc-e1f11f5476ed/kyc_checker_approved_1729768030.json`
- `s3://kwikid-prod/videokyc/logs/cbs/unity/`
- `s3://kwikid-prod/videokyc/logs/cbs/unity/7574978207/70f1d34d-8dce-4bfa-a3d4-3e246fb905fb/`
- `s3://kwikid-prod/videokyc/logs/cbs/unity/7973808861/2a860869-b8c6-43cf-8663-2f306d9297a1/`
- `s3://kwikid-prod/videokyc/logs/cbs/unity/7973808861/2a860869-b8c6-43cf-8663-2f306d9297a1/create_custid.json`
- `s3://kwikid-prod/videokyc/logs/cbs/unity/8237614574/06bf866d-6eab-40db-a90c-5cc9e7ced37b/`
- `s3://kwikid-prod/videokyc/logs/cbs/unity/8460072502/c519e579-bec1-48dd-a6a2-9d2a28674d0c/`

## DynamoDB and data stores

- `Kwikid_ipv_poc`
- `Kwikid_poc_apk_backend`
- `kwikid_cbs_vault_api`
- `kwikid_celery_tasks`
- `kwikid_customer_form_data`
- `kwikid_ipv_backend_new`
- `kwikid_nsdl_signature`
- `kwikid_poc_admin`
- `kwikid_poc_apk_backend`
- `kwikid_poc_cbs_celery`
- `kwikid_poc_dkyc`
- `kwikid_poc_ipv`
- `kwikid_unity_celery_tasks`
- `kwikid_vkyc`
- `kwikid_vkyc_agent`
- `kwikid_vkyc_agentAWS`
- `kwikid_vkyc_agentRegionap`
- `kwikid_vkyc_agentThe`
- `kwikid_vkyc_session_status`
- `kwikid_vkyc_unity`

## Servers and script paths

- `/home/ubuntu/Kwikid_ipv_poc/admin-portal-backend`
- `/home/ubuntu/Kwikid_ipv_poc/celery_poc.logs`
- `/home/ubuntu/Kwikid_ipv_poc/celery_poc_al.logs`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_celery_tasks`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_celery_tasks/app`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_celery_tasks/app/create_custid.py`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_celery_tasks/app/risk.py`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_celery_tasks/cbs.py`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/callback_files`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/error_logs`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/ipv_logs`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/video_part`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/video_part/6ed5ee47-9500-4eb1-8c11-f633243a2cb6/agent_video_screen/6ed5ee47-9500-4eb1-8c11-f633243a2cb6_agent_video_screen_full.webm`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/video_part/6ed5ee47-9500-4eb1-8c11-f633243a2cb6/agent_video_screen/agent_video_screen.webm`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/video_part/6ed5ee47-9500-4eb1-8c11-f633243a2cb6/agent_video_screen/logfile4.log`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new/video_partVideo`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_apk_backend/aadhaar_pdf_files/`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_apk_backend/app/aadhaar_pdf_files/`

## Common KB topics for this client

- `Unity Audit lock removing steps:`
- `How to recover Unity Auditor status not reflecting`
- `Unity | How to recover Aadhar_XML_Face_match?`
- `Unity Config Update - Agent & Admin Portal`
- `Unity | How to modify prodcut mapping for exsisting agent id | FINTECHFARM`
- `New ID creation | agent | admin | both for the unity`
- `How to analyze Nginx logs by session ID or user ID?`
- `unity | remove agents_ids from incorrect session status New`
- `Unity Video recovery?`
- `How to retrigger CBS customer creation for failed cases in Unity?`
- `how to check unity callbacks getting hit or not?`
- `Unity | How to update session data using given session_id and cli in Unity?`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.