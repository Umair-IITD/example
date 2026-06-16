---
title: "Bank of Baroda (BOB) — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["bob"]
sop_id: "sop_bob_client_details"
---

## Overview

Reference for **Bank of Baroda (BOB)** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Primary environments (curated)

| Environment | Admin portal |
|-------------|--------------|
| Production | `https://videokyc.bankofbaroda.com/admin/` |
| UAT (Fin BOB) | `https://uat.vkyc.getkwikid.com:9090/` |

Client identifier: `BOB_prod` (production path prefix in S3).

## Storage (AWS S3)

| Path | Purpose |
|------|---------|
| `s3://uat.vkyc.kwikid/videokyc/videos/BOB_prod/` | Session videos |
| `s3://uat.vkyc.kwikid/videokyc/video_blobs/{session_id}/agent_video_screen/` | Blob recovery |
| `s3://uat.vkyc.kwikid/videokyc/images/BOB_prod/` | Images / Aadhaar redact |

## Video correction portal

- `https://auat.vkyc.getkwikid.com:5583/` — paste session ID, select half video, CORRECT (after blob check).

## DynamoDB

- Session table: `kwikid_vkyc_session_status` (search by session ID for summary data).

## First-response notes

- Check admin portal video tab first (refresh 2–3 times if popup missing).
- Aadhaar masking: download `Aadhar_redact` from S3, mask locally, re-upload to same path.

## Client identifiers (client_name / domain)

- `BOB_prod`
- `bupgb`

## Portals and URLs

- `https://auat.vkyc.getkwikid.com:3357/v1/agent/generate_token`
- `https://auat.vkyc.getkwikid.com:3357/v2/bobrrb/force_accout_open/90299d76-7d2a-4c1c-8ff2-271327f05501`
- `https://auat.vkyc.getkwikid.com:5583/`
- `https://ml.getkwikid.com/process_image`
- `https://verify.app.getkwikid.com/trace/api/v1/geocoding/reverse?keyId=296540822139043845&lat={{LATITUDE}}&lon={{LONGITUDE}}&check_risk=true&check_spoofing=true&client_name={{CLIENT_NAME}}`
- `https://videokyc.bankofbaroda.com`
- `https://videokyc.bankofbaroda.com/BOB/v1/agent/sendLink/`
- `https://videokyc.bankofbaroda.com/admin/`
- `https://videokyc.bankofbaroda.com/v1/data_upload`
- `https://videokyc.bankofbaroda.com/v1/data_upload%27`
- `https://videokyc.bankofbaroda.com/v1/download_content/uat.vkyc.kwikid-bob/videokyc/summary/BOB_prod/user_id/session_id/batch-15_out.csv`
- `https://videokyc.bankofbaroda.com/v1/download_content/uat.vkyc.kwikid-bob/videokyc/summary/BOB_prod/user_id/session_id/batch-398_out.csv`
- `https://videokyc.bankofbaroda.com/v1/download_content/uat.vkyc.kwikid-bob/videokyc/summary/bob_prod/user_id/session_id/batch-15_out.csv`
- `https://videokyc.bankofbaroda.com/v1/download_content/uat.vkyc.kwikid/videokyc/summary/BOB_prod/2023051501805049/f10b9059-847a-4816-b4c9-2862946133fc/save_details`
- `https://videokyc.bankofbaroda.com/v1/session/make_pdf`
- `https://videokyc.bankofbaroda.com/v1/session/make_pdf%27`
- `https://videokyc.bankofbaroda.com/v2/bob/force_push_to_cbs/274ec085-b65f-459e-854b-63a2bb829412`
- `https://vkycuat.bankofbaroda.com/user%3Fl%3DNVAzghYC7fxq%26d%3D1%26s%3D0`
- `https://vkycuat.bankofbaroda.com/user?l=NVAzghYC7fxq`
- `https://vkycuat.bankofbaroda.com/user?l=NVAzghYC7fxq&d=1&s=0`

## S3 and storage paths

- `Uat.vkyc.kwikid`
- `s3://uat.vkyc.kwikid-bob/videokyc/images/BOB_prod/2025110903305393/a8b52602-df96-4340-88f9-2735f7e7e68d/2025110903305393_aadhaar_redact.jpg`
- `s3://uat.vkyc.kwikid/videokyc/videos/BOB`
- `s3://uat.vkyc.kwikid/videokyc/videos/BOB_prod/2024051202428638/65ecef1f-8b01-4d06-86a2-e85b6805080d/agent_video_screen.webm`
- `uat.vkyc.kwikid`
- `uat.vkyc.kwikid-bob/videokyc/summary/BOB_prod/user_id/session_id/batch-15_out.csv`
- `uat.vkyc.kwikid-bob/videokyc/summary/BOB_prod/user_id/session_id/batch-398_out.csv`
- `uat.vkyc.kwikid-bob/videokyc/summary/bob_prod/user_id/session_id/batch-15_out.csv`
- `uat.vkyc.kwikid/videokyc/summary/`
- `uat.vkyc.kwikid/videokyc/summary/BOB_prod/2023051501805049/f10b9059-847a-4816-b4c9-2862946133fc/save_details`
- `uat.vkyc.kwikid/videokyc/summary/bupgb/8797784136/5639eabc-b762-4fdc-a016-7d61d25a3db9/cbs_request_response`

## DynamoDB and data stores

- `Kwikid_ipv_poc`
- `kwikid_ipv_backend_new`
- `kwikid_poc_ipv_backend`
- `kwikid_vkyc_agent`
- `kwikid_vkyc_approved_session_status`
- `kwikid_vkyc_mod_schedule_slots`
- `kwikid_vkyc_session_status`

## Servers and script paths

- `/home/ubuntu/.local/lib/python3.6/site-packages/OpenSSL/_util.py`
- `/home/ubuntu/BOB`
- `/home/ubuntu/BOB/`
- `/home/ubuntu/BOB/error_logs`
- `/home/ubuntu/BOB/prod_token`
- `/home/ubuntu/BOB/token_gen.py`
- `/home/ubuntu/BOB/token_refresh.log`
- `/home/ubuntu/BOB/venv`
- `/home/ubuntu/BOB/venv/bin/python`
- `/home/ubuntu/Kwikid_ipv_poc`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_ipv_backend_new`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend/nohup_videos`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend/nohup_videos/65ecef1f-8b01-4d06-86a2-e85b6805080d/65ecef1f-8b01-4d06-86a2-e85b6805080d_agent_video_screen_full.webm`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend/nohup_videos/65ecef1f-8b01-4d06-86a2-e85b6805080d/agent_video_screen.webm`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend/nohup_videos/65ecef1f-8b01-4d06-86a2-e85b6805080d/logfile4.log`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend/test_save_details.py`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend/video_part/65ecef1f-8b01-4d06-86a2-e85b6805080d/agent_video_screen/65ecef1f-8b01-4d06-86a2-e85b6805080d_agent_video_screen_full.webm`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend/video_part/6e3163b1-a5de-461b-b0d3-9a7471ed0dc7`
- `/home/ubuntu/Kwikid_ipv_poc/kwikid_poc_ipv_backend/video_part/6e3163b1-a5de-461b-b0d3-9a7471ed0dc7/agent_video_screen.zip`

## Common KB topics for this client

- `RBL | agent/auditor | Auditor Hold Notification Mail manual export to csv`
- `how to to recover BOB_prod videos?`
- `How to Download any client report?`
- `BOB_prod | agent | Location missing or Half location`
- `BOB RRB - Account opening issue & logs`
- `How to disable/enable particular language in BOB?`
- `How to up an kwikid-vkyc-bob-prod-admin-api services`
- `BOB - Clearing message_in_queue_at and sending new link`
- `How to repush BDMS cases`
- `How do you do Masking of Aadhaar card for BOB?`
- `All video Recovery script`
- `BOB_prod video recovery if the session is half or have different random number answer than the video`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.