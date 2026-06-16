---
title: "Bajaj Finserv / Bajaj Finance — Client Reference (Portals and Infrastructure)"
query_type: "GENERAL_KNOWLEDGE"
issue_area: "Client Reference"
clients: ["bajaj"]
sop_id: "sop_bajaj_client_details"
---

## Overview

Reference for **Bajaj Finserv / Bajaj Finance** support: portals, client identifiers, storage, and common procedures mined from internal Teams knowledge base.

## Primary environments (curated)

| Environment | Portal / API |
|-------------|--------------|
| Production user/agent | `https://bflvkyc.bajajfinserv.in` |
| UAT agent | `https://uat-bflvkyc.bajajfinserv.in/agent` |
| Domain id | `bajajfin` (paths), `bajajfin_uat` (UAT) |

## Storage

| Bucket | Path pattern |
|--------|--------------|
| `bajaj.vkyc.kwikid` | `videokyc/summary/bajajfin/{user_id}/{session_id}/` |
| `bajaj.vkyc.kwikid` | `videokyc/images/bajajfin/{user_id}/{session_id}/` |
| `bajaj.digilocker.kwikid` | Digilocker XML by mobile number |

## Servers

- Bajaj script server (example): `ip-10-187-162-173`, path `/home/ubuntu/script`
- Scripts: `get_sessionid.py` (deal_id), `get_digilocker_response.py`, `get_summary_data_updated.py`

## Download content URL pattern

`https://bflvkyc.bajajfinserv.in/api/v1/download_content/bajaj.vkyc.kwikid/videokyc/summary/bajajfin/{user_id}/{session_id}/{session_id}.json`

## Access

1. VPN `svpn.thinkanalytics.in`
2. Bastion `ubuntu@15.207.207.114` (bajaj_bast.pem)
3. UAT app server from bastion

## First-response notes

- Collect **deal_id**, **session_id**, or **user_id** — use `get_sessionid.py` if only deal_id known.
- Always verify case in admin portal first when accessible.

## Client identifiers (client_name / domain)

- `BOB_prod`

## Portals and URLs

- `http://bajaj_admin_dumper_recover.py`
- `https://ap-south-1.console.aws.amazon.com/s3/buckets/bajaj.vkyc.kwikid`
- `https://ap-south-1.console.aws.amazon.com/s3/buckets/bajaj.vkyc.kwikid?prefix=videokyc/`
- `https://ap-south-1.console.aws.amazon.com/s3/buckets/bajaj.vkyc.kwikid?prefix=videokyc/images/`
- `https://ap-south-1.console.aws.amazon.com/s3/buckets/bajaj.vkyc.kwikid?prefix=videokyc/images/bajajfin/`
- `https://ap-south-1.console.aws.amazon.com/s3/buckets/bajaj.vkyc.kwikid?prefix=videokyc/images/bajajfin/ASHOK_TUKARAM_LANDGE/`
- `https://bflvkyc.bajajfinserv.in/?l=kFYYWoThhZ2d&d=1&s=0**https://bflvkyc.bajajfinserv.in/?l=kFYYWoThhZ2d&d=1&s=0**https://bflvkyc.bajajfinserv.in/?l=kFYYWoThhZ2d&d=1&s=0`
- `https://bflvkyc.bajajfinserv.in/api/v1/agent/generate_token`
- `https://bflvkyc.bajajfinserv.in/api/v1/agent/sendLink/bajajfin_agent_25`
- `https://bflvkyc.bajajfinserv.in/api/v1/all_uploaded_confirmation/2467e918-8385-4a77-9134-fa985b9b0cd1/agent_video_screen`
- `https://bflvkyc.bajajfinserv.in/api/v1/all_uploaded_confirmation/cfd2e509-aea3-458b-89cd-9a5ffe0d4ee8/agent_video_screen`
- `https://bflvkyc.bajajfinserv.in/api/v1/download_content/bajaj.vkyc.kwikid/videokyc/images/bajajfin/**null**/c17d4847-0e85-414c-9db4-71d8a3389089/**null**\_pan_original.jpg`
- `https://bflvkyc.bajajfinserv.in/api/v1/download_content/bajaj.vkyc.kwikid/videokyc/images/bajajfin/ANKIT_KANSAL/fdabd400-5ee1-4d93-952f-2321758ddfe1/ANKIT_KANSAL_aadhaar_redact.jpg`
- `https://bflvkyc.bajajfinserv.in/api/v1/download_content/bajaj.vkyc.kwikid/videokyc/summary/bajajfin/ANKIT_KANSAL/fdabd400-5ee1-4d93-952f-2321758ddfe1/fdabd400-5ee1-4d93-952f-2321758ddfe1.pdf`
- `https://bflvkyc.bajajfinserv.in/api/v1/download_content/bajaj.vkyc.kwikid/videokyc/summary/bajajfin/C_ARUN_JOSHUA/e0ee8e91-139f-4970-8047-8996d855d45c/e0ee8e91-139f-4970-8047-8996d855d45c.json`
- `https://bflvkyc.bajajfinserv.in/connection-test`
- `https://bflvkyc.bajajfinserv.in/dashboard`
- `https://uat-bflvkyc.bajajfinserv.in/agent`
- `https://uat-bflvkyc.bajajfinserv.in/api/v1/agent/generate_token`
- `https://uat-bflvkyc.bajajfinserv.in/api/v1/agent/sendLink/bajajfin_uat_agent_25`
- `https://uat-bflvkyc.bajajfinserv.in/dashboard`

## S3 and storage paths

- `Bajaj.vkyc.kwikid`
- `bajaj.vkyc.kwikid`
- `bajaj.vkyc.kwikid/videokyc/images/bajajfin/`
- `bajaj.vkyc.kwikid/videokyc/images/bajajfin/ANKIT_KANSAL/fdabd400-5ee1-4d93-952f-2321758ddfe1/ANKIT_KANSAL_aadhaar_redact.jpg`
- `bajaj.vkyc.kwikid/videokyc/summary/bajajfin/ANKIT_KANSAL/fdabd400-5ee1-4d93-952f-2321758ddfe1/fdabd400-5ee1-4d93-952f-2321758ddfe1.pdf`
- `bajaj.vkyc.kwikid/videokyc/summary/bajajfin/C_ARUN_JOSHUA/e0ee8e91-139f-4970-8047-8996d855d45c/e0ee8e91-139f-4970-8047-8996d855d45c.json`
- `s3://bajaj.digilocker.kwikid/9573988800/`
- `s3://bajaj.digilocker.kwikid/9573988800/ADHAR_xml.xml`
- `s3://bajaj.vkyc.kwikid`
- `s3://bajaj.vkyc.kwikid/videokyc/images/bajajfin/ANKIT_KANSAL/fdabd400-5ee1-4d93-952f-2321758ddfe1/ANKIT_KANSAL_aadhaar_redact.jpg`
- `s3://kwikid/`

## DynamoDB and data stores

- `kwikid_vkyc_agent`
- `kwikid_vkyc_agent_uat`
- `kwikid_vkyc_session_status`

## Servers and script paths

- `/home/ubuntu/BFL_ADMIN_CSV.csv`
- `/home/ubuntu/Kwikid/user_portal/www/`
- `/home/ubuntu/kwikid/app-services/admin-api`
- `/home/ubuntu/kwikid/app-services/admin-api/BFL_ADMIN_CSV.csv`
- `/home/ubuntu/kwikid/app-services/agent-api/`
- `/home/ubuntu/script`
- `/home/ubuntu/script/`
- `/home/ubuntu/script/ADHAR_xml.xml`
- `/home/ubuntu/script/B_id_create_v3.py`
- `/home/ubuntu/script/agent_creation_logs/`
- `/home/ubuntu/script/correct_org_bajaj.py`
- `/home/ubuntu/venv/bin/python`
- `ubuntu@10.134.138.36`
- `ubuntu@10.187.160.188`
- `ubuntu@10.187.162.28`
- `ubuntu@10.187.178.229`
- `ubuntu@15.207.207.114`
- `ubuntu@ip-10-187-160-188`
- `ubuntu@ip-10-187-162-173`
- `ubuntu@ip-10-187-162-188`

## Common KB topics for this client

- `bajajfin | agent | Video Recovery Step`
- `Bajaj | Missing IP Address | how to recover`
- `Bajaj | Ekyc adhar pdf missing in zip | genrated zip file`
- `How to Download any client report?`
- `how to download Bajaj report form backend`
- `BAJAJ | how to create user link -from session id`
- `Bajaj | How to created agent/admin/auditors id in bajajfin`
- `Bajaj video recovery steps`
- `Bajaj - Admin Portal Session Data Retrieval and 0 Sessions Display issue`
- `Bajaj | How can we change the session status in Bajaj?`
- `How to Create Agent_id in Bajaj`
- `how to bulk register for bajajfin?`

## Credentials and access

Do not store passwords in tickets. Request current admin, VPN, and API credentials from the team lead or password vault.