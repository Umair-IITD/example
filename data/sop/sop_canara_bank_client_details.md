---
title: "Canara Bank — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["canara_bank"]
sop_id: "sop_canara_bank_client_details"
---

## Overview

Reference for **Canara Bank** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Primary environments (curated)

| Item | Value |
|------|-------|
| DynamoDB `client_name` | `canarabank` |
| SQS queues (example dev) | `canarabank_dev_free_en_vkyc` — confirm queue name with requester before purge |

## Operations

- **SQS purge**: TA AWS console → SQS → select correct Canara queue → Purge (multiple queues exist; ask which environment).
- **Agent region update**: DynamoDB table `kwikid_vkyc_agent` with key `client_name=canarabank`.
- **Scheduled calls agent change**: See Teams KB tag `canarabank` for CLI steps.

## First-response notes

- Always confirm environment (dev/UAT/prod) and exact queue or portal before destructive actions (purge).

## Client identifiers (client_name / domain)

- `canarabank`

## Portals and URLs

- `https://apibanking.canarabank.in/v1/oauth2/token`
- `https://auat.vkyc.getkwikid.com:5583/config-update`
- `https://oauth.canarabank.in/v1/oauth2/authorize?client_id=AW7zcHQx8pYKurvCbxOc94sfRCSmFjNB&redirect_uri=https://videokyc.canarabank.com/&response_type=code&state=acdfgb&scope=vcip`
- `https://oauth.canarabank.in/v1/oauth2/authorize?client_id=Vo3qBIDgbchXdrebPNuIGadX54ODWV1u&redirect_uri=https://videokyclc.canarabank.com/&response_type=code&state=acdfgb&scope=lifecertificate`
- `https://uat-apibanking.canarabank.in/oauth-test`
- `https://uat-apibanking.canarabank.in/v1/oauth2/authorize?client_id=kdKa0AuHx1LpzQiiB2CJCAk89yvW2abv&redirect_uri=https://uatvideokyclc.canarabank.com/&response_type=code&state=acdfgb&scope=lifecertificate`
- `https://uat-apibanking.canarabank.in/v1/oauth2/token`
- `https://uatvideokyc.canarabank.com`
- `https://uatvideokyc.canarabank.com/canara_wrapper_api/decrypt_kwikid_request`
- `https://uatvideokyc.canarabank.com/canara_wrapper_api/encrypt_kwikid_request`
- `https://uatvideokyc.canarabank.com/user/forms?stepKey=FORM`
- `https://uatvideokyclc.canarabank.com/`
- `https://uatvideokyclc.canarabank.com/agent`
- `https://uatvideokyclc.canarabank.com/user/home?client_id=canarabank_lc_uat&api_key=canarabank_lc_uat&process=U`
- `https://videokyc.canarabank.bank.in`
- `https://videokyc.canarabank.bank.in/agent/`
- `https://videokyc.canarabank.bank.in/user/forms?stepKey=FORM`
- `https://videokyc.canarabank.bank.in/user/home?client_id=canarabank&api_key=canarabank&process=U`
- `https://videokyc.canarabank.bank.in/v1/download_content/canara-kwikid-vkyc/videokyc/images/canarabank/6360569538/6360569538_aadhaar_pdf.pdf`
- `https://videokyc.canarabank.bank.in/wrapper/auditor_approve_email`
- `https://videokyc.canarabank.bank.in/wrapper/cbs_push`
- `https://videokyc.canarabank.bank.in/wrapper/ckyc_ref_update`
- `https://videokyc.canarabank.bank.in/wrapper/ckyc_scan_update`
- `https://videokyc.canarabank.bank.in/wrapper/nominee_update`
- `https://videokyc.canarabank.bank.in/wrapper/signature_update`
- `https://videokyc.canarabank.bank.in/wrapper/storage_utils/get_session_data`
- `https://videokyc.canarabank.bank.in/wrapper/storage_utils/set_session_data`
- `https://videokyc.canarabank.bank.in/wrapper/terrorist_inquiry`
- `https://videokyc.canarabank.com`
- `https://videokyc.canarabank.com/`
- `https://videokyc.canarabank.com/v1/all_uploaded_confirmation/6d710d9b-be07-4c6a-8909-829b99aec397/agent_video_screen`
- `https://videokyc.canarabank.com/wrapper/dms_push`
- `https://videokyclc.canarabank.com`
- `https://videokyclc.canarabank.com/`
- `https://videokyclc.canarabank.com/admin`
- `https://videokyclc.canarabank.com/agent`
- `https://videokyclc.canarabank.com/user/home?client_id=canarabank_lc&api_key=canarabank_lc&process=U`
- `https://videokyclc.canarabank.com/wrapper/get_lc_account_summary_api`
- `https://videokyclc.canarabank.com/wrapper/get_lc_from_accno_api`
- `https://videokyclc.canarabank.com/wrapper/update_life_certificate`

## S3 and storage paths

- No S3 paths extracted from KB posts.

## DynamoDB and data stores

- `Kwikid_ipv_poc`
- `kwikid_aws`
- `kwikid_request`
- `kwikid_vkyc_agent`
- `kwikid_vkyc_request_status`
- `kwikid_vkyv_agent`

## Servers and script paths

- `/home/ubuntu/Kwikid_ipv_poc/`
- `/home/ubuntu/Kwikid_ipv_poc/clear_docker_container_logs.py`
- `/home/ubuntu/Kwikid_ipv_poc/venv/bin/activate`
- `/home/ubuntu/c`
- `/home/ubuntu/canara_wrapper`
- `/home/ubuntu/canara_wrapper/aadhaar_pdf_compression/9381829551_aadhaar_pdf`
- `/home/ubuntu/canara_wrapper/aadhaar_pdf_compression/9381829551_aadhaar_pdf.pdf`
- `/home/ubuntu/canara_wrapper/aadhaar_pdf_compression/9381829551_aadhaar_pdf_compression.pdf`
- `/home/ubuntu/canara_wrapper/venv/bin/activate`
- `/home/ubuntu/canara_wrapper/venv/bin/celery`
- `/home/ubuntu/canara_wrapper/venv/bin/gunicorn`
- `/home/ubuntu/canara_wrapper/venv/bin/python3`
- `/home/ubuntu/data_movement_canara_wrapper`
- `/home/ubuntu/data_movement_canara_wrapper/celery_cw.logs`
- `/home/ubuntu/data_movement_canara_wrapper/error_logs`
- `/home/ubuntu/data_movement_canara_wrapper/wrapper_logs`
- `/home/ubuntu/debugger`
- `/home/ubuntu/debugger/scripts/inputs/update_aadhaar_ref.csv`
- `/home/ubuntu/debugger/scripts/log/update_aadhaar_ref.log`
- `/home/ubuntu/debugger/scripts/outputs/update_aadhaar_ref.csv`

## Common KB topics for this client

- `Aadhaar PDF Recovery`
- `Aadhaar Reference Updater Script`
- `Canara All servers Down`
- `Canara | DMS PUSH?`
- `Canara | Steps to generate token`
- `How to Recover Canara Incomplete Video?`
- `How to update canara aadhaar_reference_number in the value of aadhaar_number?`
- `Canara | Branch Master updating`
- `canara CBS failed/manual account opening?`
- `Canara [Disk-Space] canara.prod.vkyc.app.base down`
- `Canara monthly report?`
- `How can we check if the agent screen URL is missing in Canara?`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.