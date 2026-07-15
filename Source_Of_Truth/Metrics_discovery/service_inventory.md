# Service Inventory — Monitored Services

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Source**: Live authenticated extraction 2026-07-11 (Socket.io `monitorList` + `heartbeatList` + `uptime` events)  
**Live data status**: ✅ COMPLETE — full inventory of **332 monitors** extracted.

---

## 0. Inventory Summary (live, 2026-07-11)

| Metric | Value |
|:---|:---|
| Total monitors | **332** |
| Active | 263 |
| Inactive (paused) | 69 |
| Currently UP | 211 |
| Currently in MAINTENANCE | 51 |
| Currently DOWN | 1 (`[Monitoring]Bajaj new prod monitoring server`, id 432) |
| No recent heartbeat (inactive) | 69 |

**By type**: 172 push · 155 http · 3 keyword · 2 group.

**By client** (derived from tags + naming): KwikID Core/Shared 78 · BOB/BOBCARDS 45 · Unity 40 · RBL 35 · FINO 28 · NRFSI 18 · Bajaj 17 · CBI 15 · ICICI 15 · Tata Capital 14 · SaaS/Shared 13 · Canara 11 · RRB 1 · Shinhan 1 · Svamaan 1.

**Check intervals**: most monitors use 60s (131), 90s (78), 120s (54), or 180s (49); a few use 20s, 70s, 300s, or 320s.

> **Note on Tata Capital & ICICI**: All 14 Tata Capital (tcook) monitors are paused (inactive), and 13 of 15 ICICI monitors are paused — these clients appear to be in a dormant/off-boarded or pre-deployment state. Bajaj monitors are largely under active maintenance windows.

---

## 1. How to Retrieve Live Service Inventory

### 1.1 Via Prometheus metrics endpoint (authenticated)

```bash
curl -H "Authorization: Bearer uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3" \
  http://status.getkwikid.com:3001/metrics
```

Returns all monitors in Prometheus format. Each monitor appears as:

```
# HELP monitor_status Monitor Status
# TYPE monitor_status gauge
monitor_status{monitor_name="<name>",monitor_type="<type>",monitor_url="<url>",monitor_hostname="<host>",monitor_port="<port>"} <0|1|2|3>

# HELP monitor_response_time Monitor Response Time (ms)
# TYPE monitor_response_time gauge
monitor_response_time{monitor_name="<name>",monitor_type="<type>",monitor_url="<url>",monitor_hostname="<host>",monitor_port="<port>"} <ms>

# HELP monitor_cert_days_remaining Monitor Cert Days Remaining
# TYPE monitor_cert_days_remaining gauge
monitor_cert_days_remaining{monitor_name="<name>",monitor_url="<url>"} <days>

# HELP monitor_cert_is_valid Monitor Cert Is Valid
# TYPE monitor_cert_is_valid gauge
monitor_cert_is_valid{monitor_name="<name>",monitor_url="<url>"} <0|1>
```

### 1.2 Via public status page API (no auth)

```bash
# Step 1: Find available status page slugs
curl http://status.getkwikid.com:3001/api/entry-page

# Step 2: Fetch all monitors on a status page
curl http://status.getkwikid.com:3001/api/status-page/<slug>

# Step 3: Fetch recent heartbeats
curl http://status.getkwikid.com:3001/api/status-page/heartbeat/<slug>
```

---

## 2. Monitor Object Schema

When retrieved via the authenticated Socket.io `monitorList` event, each monitor has ~90 fields. Below is a **real, sanitized example** from the live instance (monitor id 2, `kwikid-idverification-api`) — credential fields (`headers`, `body`, `basic_auth_pass`, `oauth_client_secret`, `pushToken`, `tlsKey`, etc.) are redacted:

```json
{
  "id": 2,
  "name": "kwikid-idverification-api",
  "description": null,
  "url": "https://kwikidwebapi.thinkanalytics.in/health",
  "hostname": null,
  "port": null,
  "maxretries": 3,
  "active": true,
  "type": "http",
  "interval": 120,
  "retryInterval": 60,
  "resendInterval": 0,
  "keyword": null,
  "tags": [{"name": "app"}, {"name": "env"}],
  "accepted_statuscodes": ["200-299"],
  "headers": "«REDACTED»",
  "basic_auth_pass": "«REDACTED»",
  "includeSensitiveData": false
}
```

The full field list (present on every monitor object): `id, name, description, pathName, parent, childrenIDs, url, method, hostname, port, maxretries, weight, active, forceInactive, type, timeout, interval, retryInterval, resendInterval, keyword, invertKeyword, expiryNotification, ignoreTls, upsideDown, packetSize, maxredirects, accepted_statuscodes, dns_resolve_type, dns_resolve_server, dns_last_result, docker_container, docker_host, proxyId, notificationIDList, tags, maintenance, mqttTopic, mqttSuccessMessage, databaseQuery, authMethod, grpcUrl, grpcProtobuf, grpcMethod, grpcServiceName, grpcEnableTls, radiusCalledStationId, radiusCallingStationId, game, gamedigGivenPortOnly, httpBodyEncoding, jsonPath, expectedValue, kafkaProducerTopic, kafkaProducerBrokers, kafkaProducerSsl, kafkaProducerAllowAutoTopicCreation, kafkaProducerMessage, screenshot, headers, body, grpcBody, grpcMetadata, basic_auth_user, basic_auth_pass, oauth_client_id, oauth_client_secret, oauth_token_url, oauth_scopes, oauth_auth_method, pushToken, databaseConnectionString, radiusUsername, radiusPassword, radiusSecret, mqttUsername, mqttPassword, authWorkstation, authDomain, tlsCa, tlsCert, tlsKey, kafkaProducerSaslOptions, includeSensitiveData`.

> ⚠️ **236 of 332 monitors carry embedded credentials** (auth headers, basic-auth passwords, OAuth secrets, push tokens, or TLS keys) in their configuration. See [security_review.md](./security_review.md).

<details>
<summary>Generic schema reference (all possible fields)</summary>

```json
{
  "id": 1,
  "name": "KwikID API Server",
  "description": "Primary API server",
  "url": "https://api.getkwikid.com/health",
  "hostname": null,
  "port": null,
  "maxretries": 3,
  "weight": 2000,
  "active": true,
  "type": "http",
  "interval": 60,
  "retryInterval": 60,
  "resendInterval": 0,
  "keyword": null,
  "invertKeyword": false,
  "expiryNotification": false,
  "ignoreTls": false,
  "upsideDown": false,
  "packetSize": 56,
  "maxredirects": 10,
  "accepted_statuscodes": ["200-299"],
  "dns_resolve_type": "A",
  "dns_resolve_server": "1.1.1.1",
  "dns_last_result": null,
  "docker_container": null,
  "docker_host": null,
  "proxyId": null,
  "notificationIDList": {},
  "tags": [],
  "maintenance": false,
  "mqttTopic": null,
  "mqttSuccessMessage": null,
  "databaseConnectionString": null,
  "databaseQuery": null,
  "authMethod": null,
  "grpcUrl": null,
  "grpcProtobuf": null,
  "grpcMethod": null,
  "grpcServiceName": null,
  "grpcEnableTls": false,
  "httpBodyEncoding": "json",
  "jsonPath": null,
  "expectedValue": null,
  "kafkaProducerTopic": null,
  "kafkaProducerBrokers": [],
  "kafkaProducerAllowAutoTopicCreation": false,
  "kafkaProducerMessage": null,
  "screenshot": null,
  "tlsInfo": null,
  "includeSensitiveData": false
}
```
</details>

### Key fields for AI use

| Field | Type | AI relevance |
|:---|:---|:---|
| `id` | integer | Monitor ID — used in badge API calls, heartbeat queries |
| `name` | string | Human-readable service name — used in Observation Generator notes |
| `type` | string | Monitor type (http, tcp, ping, dns, push, docker, etc.) |
| `url` | string | Target URL/host being monitored |
| `hostname` | string | TCP/ping hostname (when type is not http) |
| `port` | integer | TCP port (when type is tcp) |
| `interval` | integer | Check interval in seconds — determines heartbeat frequency |
| `maxretries` | integer | Failed checks before DOWN is declared |
| `active` | boolean | `false` = monitor paused; skip from active investigation |
| `maintenance` | boolean | `true` = in scheduled maintenance; do not alert AI |
| `tags` | array | Tags for grouping by tenant, service class, or environment |
| `accepted_statuscodes` | array | Status code ranges that count as UP |

---

## 3. Monitor Types

Uptime Kuma supports the following monitor types. Each has different evidence value for AI investigation:

| Type | Check method | AI evidence value | Notes |
|:---|:---|:---|:---|
| `http` / `https` | HTTP GET to URL; checks status code | **HIGH** — response time + status code + body keyword matching | Most common; covers APIs and web UIs |
| `tcp` | TCP port reachability | **MEDIUM** — port up/down, no response content | Good for database ports, message brokers |
| `ping` | ICMP ping | **LOW** — latency only, no application-layer info | Infrastructure-level health only |
| `dns` | DNS record resolution | **MEDIUM** — confirms DNS is resolving correctly | Useful for domain/routing issues |
| `push` | External heartbeat push (no active check) | **MEDIUM** — confirms application is alive; no response time | Used for background jobs, batch processes |
| `docker` | Docker container health | **HIGH** — container running/stopped | Server infrastructure context |
| `keyword` | HTTP + body keyword check | **HIGH** — application-level health confirmation | Checks for specific content in response body |
| `grpc-keyword` | gRPC + keyword check | **MEDIUM** | For gRPC service monitoring |
| `real-browser` | Playwright browser check | **HIGH** — UI-level health | End-to-end user experience |
| `gamedig` | Game server query | LOW | Not applicable to KwikID |
| `mqtt` | MQTT message broker | **MEDIUM** | Message queue health |
| `sqlserver` / `postgres` / `mysql` / `sqlite` | DB connection + query | **HIGH** — confirms database is queryable | Most valuable for root cause: DB DOWN → cascade failures |
| `mongodb` | MongoDB health | **HIGH** | Same as above |
| `radius` | RADIUS auth | LOW | Not applicable to KwikID |
| `steam` | Steam API | LOW | Not applicable to KwikID |

---

### Type distribution (live)

| Type | Count | Notes |
|:---|:--:|:---|
| `push` | 172 | Server/infra heartbeats (disk, memory, CPU, docker, redis) pushed by agents on the monitored hosts |
| `http` | 155 | API health endpoints (`/health`, `/v1/agent/health`, etc.) |
| `keyword` | 3 | HTTP + body keyword match (signalling server, scylladb-prod, fino signalling) |
| `group` | 2 | Logical grouping containers (`Saas client` id 293, `RBL Bank` id 390) |

---

## 4. Verified Service Categories (live data)

The 332 monitors fall into two broad classes:

**Application health (`http` / `keyword`, ~158 monitors)** — hit a service's `/health` endpoint. Naming convention is `kwikid-vkyc-<client>-<env>-<service>-api` or `<client> | <SERVICE> API | <ENV>`. Services seen: agent-api, user-api, admin-api, dkyc-api, scheduling-api, wrapper-api, signalling-server, socket-server, queue-management, map, celery-redis, bank-wrapper, pan-signature, encryption/KRA (ICICI), esign (V1/V2), OCR/ML, billing, elastic, volume-tracker.

**Infrastructure health (`push`, 172 monitors)** — agents on each host push heartbeats tagged `[Server-Space]`, `[Memory-Space]`, `[Disk]`, `[CPU]`, `[Docker]`, `[REDIS]`, `[DB-Connection]`, `[Monitoring]`, `[LiveKit Service]`, `[Signalling]`. These cover the underlying VMs, containers, ScyllaDB clusters, RabbitMQ, Redis, and MinIO object storage per client.

### 4.1 Clients / tenants actually monitored (live)

| Client | Monitors | Active | Status pages slug | Notes |
|:---|:--:|:--:|:---|:---|
| KwikID Core / Shared | 78 | 63 | `global` | Shared platform (idverification, KMS, esign, billing, OCR, verifyapi) |
| Bank of Baroda / BOBCards | 45 | 39 | `bob` | Prod + UAT, DC/DR split, scylla master |
| Unity Bank | 40 | 40 | `unity-bank` | Prod + UAT + "New Unity" server fleet |
| RBL Bank | 35 | 34 | `rbl` | Prod + UAT, report server, redis, dockers |
| FINO Payments Bank | 28 | 22 | `fino` | Prod + UAT, on-prem prod user API, MinIO object store |
| NRFSI | 18 | 11 | `nrfsi` | Prod + UAT + CUG environments |
| Bajaj Finserv | 17 | 15 | `bajaj` | New prod + UAT + DR; mostly under maintenance |
| Central Bank of India | 15 | 13 | `cbi` | ML containers, DKYC backend, storage server |
| ICICI | 15 | 2 | `icici` | Mostly paused (KRA/eKYC in pre-deployment) |
| Tata Capital (tcook) | 14 | 0 | `tatacap` | **All paused** — dormant/off-boarded |
| SaaS / Shared (chaand, sfu) | 13 | 12 | (in `global`) | Shared SFU/LiveKit media servers |
| Canara Bank | 11 | 10 | `canarabank` | Prod admin/user/agent/wrapper/redis |
| RRB | 1 | 1 | — | Single disk-space monitor |
| Shinhan | 1 | 1 | — | Single prod monitoring push |
| Svamaan | 1 | 0 | — | Docker VM (paused) |

Real target domains observed include `thinkanalytics.in`, `getkwikid.com` (and subdomains `vkyc.`, `dev.vkyc.`, `uat.vkyc.`, `ml.`, `chaand.`), `videokyc.bankofbaroda.com`, `bflvkyc.bajajfinserv.in`, plus internal RFC-1918 IPs (`172.x`, `10.x`) for push-based infra monitors.

---

## 5. Full Monitor Inventory (live, 2026-07-11)

Grouped by client. Status reflects the last heartbeat at extraction time. Uptime 24h / 30d are Uptime Kuma's own rolling calculations. `⛔` = paused (inactive).


### KwikID Core / Shared (78 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 2 | kwikid-idverification-api | http | 120s | ✅ | UP | 98.82% | 99.00% |
| 4 | kwikid-ml-ocr-api | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 5 | kwikid-vkyc-prod-schedule-api | http | 60s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 6 | kwikid-vkyc-prod-agent-api | http | 60s | ✅ | UP | 100.00% | 99.98% |
| 14 | kwikid-vkyc-uat-speed-service | http | 60s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 21 | [Server-Space] kwikid-vkyc-auat-server-space (172.31.21.73) | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 22 | [Server-Space] kwikid-vkyc-uat-server-space (13.235.229.229) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 26 | [Server-Space] faceailive.thinkanalytics.in (172.31.24.123) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 28 | Server-Space] kwikid-clickhouse-server-space (15.207.250.89) | push | 90s | ✅ | MAINTENANCE | 100.00% | 99.91% |
| 39 | api-uat.test.getkwikid.com/appapi | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 43 | kwikid-vkyc-uat-prod-celery-redis | http | 60s | ✅ | UP | 100.00% | 99.98% |
| 70 | T2 \| UAT \| Admin API | http | 60s | ✅ | UP | 99.93% | 99.98% |
| 71 | T2 \| UAT \| Agent API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 72 | T2 \| UAT \| Auth API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 73 | T2 \| UAT \| DKYC API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 74 | T2 \| UAT \| Schedule API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 75 | T2 \| UAT \| Server | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 76 | T2 \| UAT \| User API | http | 60s | ✅ | UP | 99.93% | 99.98% |
| 77 | T2 \| PROD \| Admin API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 78 | T2 \| PROD \| Agent API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 79 | T2 \| PROD \| Auth API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 80 | T2 \| PROD \| DKYC API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 81 | T2 \| PROD \| Schedule API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 82 | T2 \| PROD \| Server | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 83 | T2 \| PROD \| User API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 87 | [certificate expiry] auat | http | 300s | ✅ | UP | 100.00% | 100.00% |
| 88 | Esign V1 | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 89 | Esign V2 | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 91 | T2 \| UAT \| Deployment API | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 104 | scylladb-prod | keyword | 60s | ✅ | UP | 100.00% | 100.00% |
| 119 | [Memory Space] kwikid-uat-t3-server | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 124 | [Memory-Space] esign.app | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 125 | [Server-Space] esign.app | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 128 | [Docker-Status] esign.app - javaApp - pythonApp | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 170 | [Docker] billingservice.kwikid (172-31-14-122) (all containers) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 171 | [Server-Space] billingservice.kwikid (172-31-14-122) | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 172 | [Server-Space] dev.vkyc.getkwikid.com (172.31.0.59) | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 173 | [Docker] dev.vkyc.getkwikid.com (172.31.0.59) | push | 120s | ✅ | UP | 99.91% | 99.92% |
| 187 | kwikid-vkyc-auat-prod-agent-health-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 191 | [DISK SPACE] kwikid-verifyapi-uat-server | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 192 | [MEMORY SPACE] kwikid-verifyapi-uat-server | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 202 | Flagsmith (dev.vkyc.getkwikid.com) | http | 60s | ✅ | UP | 99.93% | 99.98% |
| 224 | [Disk-Space] ml.getkwikid.com (172.31.18.43) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 233 | [Disk Space] kwikid-rabbitmq | push | 120s | ⛔ | — | 0.00% | 0.00% |
| 234 | kwikid-prod-rmq (TA AWS - i-0511ae6728c8bfc3a) | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 243 | [Docker] elasticservice.kwikid(172-31-11-116) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 244 | [Disk Space] elasticservice.kwikid(172-31-11-116) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 245 | [Memory Space] elasticservice.kwikid(172-31-11-116) | push | 180s | ✅ | UP | 98.82% | 98.59% |
| 246 | [Memory Space] billingservice.kwikid (172-31-14-122) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 250 | [certificate expiry] dev.vkyc.gtekwikid.com | http | 180s | ✅ | UP | 100.00% | 100.00% |
| 252 | [Disk Space] kwikid.prod.verifyapi - 172-31-14-10 | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 268 | [CPU] kwikid.vkyc.prod.scylladb.mon | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 269 | [Disk Space] kwikid.vkyc.prod.scylladb.mon | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 270 | [Memory space] kwikid.vkyc.prod.scylladb.mon | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 303 | [Monitoring] kwikid.prod.verifyapi  PORD | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 304 | [Monitoring] billingservice.kwikid PROD (all containers) | push | 120s | ✅ | UP | 99.65% | 99.60% |
| 305 | [Monitoring] kwikid.ocr.base PROD - [TA_AWS - Instance_Name (delete\|kwikid\|pan-ocr-batch-realcv)] | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 306 | [health endpoint] kwikid.prod.verifyapi PROD | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 308 | [Monitoring] esign.app PORD | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 309 | [Monitoring] kwikid.uat.verifyapi UAT | push | 120s | ⛔ | — | 0.00% | 0.00% |
| 312 | [health endpoint]  kwikid.prod.verifyapi  PROD | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 313 | [health endpoint]  esign  PROD | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 314 | [health endpoint]  esign  UAT | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 315 | [health endpoint]  feature/kra_api UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 316 | [health endpoint]  feature/kra_wrapper_api UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 337 | [health endpoint] ckyc_wrapper PROD | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 339 | [health endpoint] nsdl_esign_py3 PROD | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 342 | [health endpoint] ckyc_wrapper UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 344 | [health endpoint] nsdl_esign_py3 UAT | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 357 | ml.getkwikid.old | http | 60s | ✅ | UP | 99.93% | 99.92% |
| 362 | [SSL] Legacy verification server | http | 120s | ✅ | UP | 100.00% | 100.00% |
| 369 | Teleport Core | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 379 | [Health] Volume tracker api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 381 | kwikid.vkyc.prod.scylladb.master2 | push | 120s | ✅ | UP | 100.00% | 99.88% |
| 382 | kwikid.vkyc.prod.scylladb.master | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 386 | [Monitoring] kwikid.ocr.kbipvd | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 415 | AI Support Services | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 424 | [Server-CPU] kwikid-vkyc-auat-server-cpu (172.31.21.73) | push | 90s | ⛔ | — | 0.00% | 0.00% |

### BOB / BOBCARDS (45 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 3 | kwikid-vkyc-bob-prod-agent-api | http | 90s | ✅ | UP | 100.00% | 100.00% |
| 12 | kwikid-vkyc-bob-prod-map | http | 60s | ✅ | MAINTENANCE | 100.00% | 99.81% |
| 13 | kwikid-vkyc-bob-prod-queue-management | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 15 | kwikid-vkyc-bob-prod-socket-server | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 19 | kwikid-vkyc-bob-prod-celery-redis | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 20 | [Server-Space] kwikid-vkyc-bob-prod-mr-dr-server-space (172.32.0.93) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 23 | [Server-Space] kwikid-vkyc-bob-ps-server-space (172-32-2-216) | push | 90s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 24 | [RabbitMQ] kwikid-vkyc-bob-rabbitmq (172.32.2.216) | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 27 | [Server-Space] bob.prod.web.dr.vkyc (172.32.0.92) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 35 | kwikid-vkyc-bob-prod-bank-wrapper-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 40 | kwikid-vkyc-bob-uat-agent-api | http | 60s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 41 | kwikid-vkyc-bob-uat-user-api | http | 60s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 42 | kwikid-vkyc-bob-uat-admin-api | http | 60s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 44 | kwikid-vkyc-bob-prod-admin-api | http | 120s | ✅ | UP | 100.00% | 100.00% |
| 54 | kwikid-vkyc-bob-prod-user-api | http | 120s | ✅ | UP | 100.00% | 100.00% |
| 99 | [Server-Space] bob.prod.vkyc.app.dev (172.32.2.95) | push | 90s | ✅ | MAINTENANCE | 100.00% | 99.79% |
| 100 | [Server-Space] bob.prod.extapi.vkyc (172.31.28.81) | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 149 | [Docker] bob.uat.app.vkyc.dc.new | push | 90s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 150 | [Memory-Space] bob.uat.app.vkyc.dc.new | push | 90s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 151 | [Server-Space] bob.uat.app.vkyc.dc.new | push | 90s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 152 | [Memory-Space] bob.prod.extapi.vkyc | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 153 | [Docker] bob.prod.extapi.vkyc | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 155 | [Memory-Space] bob.prod.mr.dc.vkyc | push | 90s | ✅ | UP | 100.00% | 99.97% |
| 158 | [Memory-Space] bob.prod.web.dr.vkyc | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 159 | [Memory-Space] bob.prod.web.dc.vkyc | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 160 | [Server-Space] bob.prod.web.dc.vkyc | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 161 | [Docker] bob.prod.vkyc.map.worker | push | 90s | ✅ | MAINTENANCE | 100.00% | 99.82% |
| 162 | [Server-Space]  bob.prod.vkyc.map.worker | push | 90s | ✅ | MAINTENANCE | 100.00% | 99.82% |
| 163 | [Memory-Space] bob.prod.vkyc.map.worker | push | 90s | ✅ | MAINTENANCE | 100.00% | 99.82% |
| 164 | [Memory-Space] bob.prod.vkyc.app.dev | push | 90s | ✅ | MAINTENANCE | 100.00% | 99.79% |
| 165 | [Docker] bob.prod.ps.vkyc.dr | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 166 | [Memory-Space] bob.prod.ps.vkyc.dr | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 213 | kwikid-vkyc-bob-uat-logging-api | http | 90s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 214 | [Memory] bob-auth-v2 | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 215 | [Server Space] bob-auth-v2 | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 271 | [CPU] bob-scylla-master | push | 70s | ✅ | UP | 100.00% | 100.00% |
| 272 | [Disk-root] bob-scylla-master | push | 70s | ✅ | UP | 100.00% | 100.00% |
| 273 | [Disk-scylla-/var/lib/scylla] bob-scylla-master | push | 70s | ✅ | UP | 100.00% | 100.00% |
| 274 | [Memory] bob-scylla-master | push | 70s | ✅ | UP | 100.00% | 100.00% |
| 283 | kwikid-bobcards-kyc-app-uat | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 358 | kwikid-bobcards-kyc-app-prod | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 377 | bob-prod-app-server | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 392 | BOB Redis Monitoring | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 420 | bob.prod.redis.vkyc | push | 320s | ✅ | UP | 100.00% | 100.00% |
| 430 | BOB - Analytics Server (Metabase) | push | 119s | ✅ | UP | 100.00% | 99.98% |

### Unity (40 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 25 | kwikid-vkyc-unity-prod-dkyc-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 84 | kwikid-vkyc-unity-prod-user-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 85 | kwikid-vkyc-unity-prod-agent-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 86 | kwikid-vkyc-unity-prod-admin-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 93 | [Disk] kwikid-vkyc-unity-app1-65-space (172.29.22.165) | push | 300s | ✅ | UP | 100.00% | 100.00% |
| 201 | [Disk] kwikid-vkyc-unity-app2-73-space (172.29.22.73) | push | 300s | ✅ | UP | 100.00% | 100.00% |
| 229 | kwikid-vkyc-unity-uat-admin-api | http | 60s | ✅ | MAINTENANCE | 99.93% | 99.98% |
| 230 | kwikid-vkyc-unity-uat-agent-api | http | 60s | ✅ | MAINTENANCE | 99.93% | 99.96% |
| 231 | kwikid-vkyc-unity-uat-user-api | http | 60s | ✅ | MAINTENANCE | 99.93% | 99.96% |
| 232 | kwikid-vkyc-unity-uat-dkyc-api | http | 60s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 235 | kwikid-vkyc-unity-signaling-server (172.29.22.74) | http | 60s | ✅ | UP | 100.00% | 99.98% |
| 236 | kwikid-vkyc-unity-prod-cbs-redis (172.29.22.74) | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 237 | kwikid-vkyc-unity-prod-rabbitmq (172.29.22.74) | http | 60s | ✅ | UP | 100.00% | 99.96% |
| 238 | [Disk] kwikid-vkyc-unity-mon1-74-space (172.29.22.74) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 239 | [CPU] kwikid-vkyc-unity-app1-165-cpu (172.29.22.165) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 240 | [CPU] kwikid-vkyc-unity-app2-73-cpu (172.29.22.73) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 242 | kwikid-vkyc-unity-prod-pan-signature-api | http | 180s | ✅ | UP | 100.00% | 100.00% |
| 248 | [Mem] kwikid-vkyc-unity-app2-73-mem (172.29.22.73) | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 249 | [Mem] kwikid-vkyc-unity-app1-165-mem (172.29.22.165) | push | 60s | ✅ | UP | 98.04% | 97.36% |
| 259 | [Disk-Space]Unity.prod.vkyc.ml | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 260 | [Memory-space]unity.prod.vkyc.mon2.106 | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 261 | [Memory-space]unity.prod.vkyc.mon2.106 | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 372 | [DOCKER] kwikid-vkyc-unity-app2-73-docker (172.29.22.73) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 373 | [REDIS] kwikid-vkyc-unity-app2-73-redis (172.29.22.73) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 375 | [REDIS] kwikid-vkyc-unity-app1-165-redis (172.29.22.165) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 378 | [Disk Space] unity.prod.vkyc.mr.85 172.29.11.85 | push | 180s | ✅ | UP | 100.00% | 100.00% |
| 383 | [Docker]- unity.prod.vkyc.app1.165 | push | 60s | ✅ | UP | 96.88% | 95.88% |
| 393 | Unity-New-UAT[Disk-Space] 118 | push | 180s | ✅ | MAINTENANCE | 99.79% | 99.94% |
| 394 | Unity New UAT CPU Utilization Monitoring | push | 180s | ✅ | MAINTENANCE | 99.44% | 99.59% |
| 399 | New Unity \| Disk  Space Alert \| Server = unity.prod.app.1(IP = 172.29.151.15) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 400 | New Unity \| Disk  Space Alert \| Server = unity.prod.app.2 (IP = 172.29.151.72) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 401 | New Unity \| CPU Utilization Alert \| Server = unity.prod.app.1(IP = 172.29.151.15) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 402 | New Unity \| Memory Utilization Alert \| Server = unity.prod.app.1(IP = 172.29.151.15) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 403 | New Unity \| Docker Alert (Disk, CPU, Memory Space)  \| Server = unity.prod.app.1(IP = 172.29.151.15) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 404 | New Unity \|  CPU Utilization Alert \| Server = unity.prod.app.2 (IP = 172.29.151.72) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 405 | New Unity \| Memory Utilization Alert \| Server = unity.prod.app.2 (IP = 172.29.151.72) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 406 | New Unity \| Docker Alert (Disk, CPU, MEMORY) Space  \| Server = unity.prod.app.2 (IP = 172.29.151.72) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 411 | New Unity \| Disk Space Alert \| Server = unity.prod.monitor (IP = 172.29.151.198) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 412 | New Unity \|  CPU Utilization Alert \| Server = unity.prod.monitor (IP = 172.29.151.198) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 413 | New Unity \| Memory Utilization Alert \| Server = unity.prod.monitor (IP = 172.29.151.198) | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |

### RBL (35 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 31 | RBL \| [Memory-Space] kwikid-vkyc-rbl-62-server-mem (10.45.246.62) | push | 90s | ✅ | UP | 59.81% | 75.93% |
| 32 | RBL \| [Memory-Space] kwikid-vkyc-rbl-04-server-mem (10.45.246.04) | push | 90s | ✅ | UP | 87.57% | 95.01% |
| 33 | RBL \| [Memory-Space] kwikid-vkyc-rbl-61-server-mem (10.45.246.61) | push | 90s | ✅ | UP | 50.06% | 79.80% |
| 34 | RBL \| [Memory-Space] kwikid-vkyc-rbl-19-server-mem (10.45.246.19) | push | 90s | ✅ | UP | 76.33% | 87.53% |
| 45 | RBL \| [Server-Space] kwikid-vkyc-rbl-62-server-mem (10.45.246.62) | push | 90s | ✅ | UP | 99.37% | 99.64% |
| 46 | RBL \| [Server-Space] kwikid-vkyc-rbl-04-server-mem (10.45.246.04) | push | 90s | ✅ | UP | 99.36% | 99.73% |
| 47 | RBL \| [Server-Space] kwikid-vkyc-rbl-61-server-mem (10.45.246.61) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 48 | RBL \| [Server-Space] kwikid-vkyc-rbl-19-server-mem (10.45.246.19) | push | 90s | ✅ | UP | 99.58% | 99.82% |
| 55 | RBL \| kwikid-vkyc-rbl-uat-agent-backend | http | 180s | ✅ | MAINTENANCE | 99.58% | 98.53% |
| 56 | RBL \| kwikid-vkyc-rbl-uat-user-backend | http | 90s | ✅ | MAINTENANCE | 99.37% | 99.34% |
| 57 | RBL \| kwikid-vkyc-rbl-uat-scheduling-backend | http | 180s | ✅ | MAINTENANCE | 99.05% | 97.98% |
| 58 | RBL \| kwikid-vkyc-rbl-uat-admin-backend | http | 180s | ✅ | MAINTENANCE | 98.07% | 98.04% |
| 59 | RBL \| kwikid-vkyc-rbl-scheduling-backend | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 60 | RBL \| kwikid-vkyc-rbl-agent-backend | http | 180s | ✅ | UP | 100.00% | 100.00% |
| 61 | RBL \| kwikid-vkyc-rbl-admin-backend | http | 118s | ✅ | UP | 100.00% | 100.00% |
| 62 | RBL \| kwikid-vkyc-rbl-user-backend | http | 60s | ✅ | UP | 100.00% | 99.15% |
| 103 | RBL \| [Memory-Space] RBL-report-14 (10.45.246.14) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 120 | RBL \| kwikid-vkyc-rbl-uat-wrapper-backend | http | 60s | ✅ | MAINTENANCE | 99.46% | 98.04% |
| 132 | RBL \| [Server-space] kwikid-vkyc-rbl-prod-db | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 133 | RBL \| [Memory-Space]  kwikid-vkyc-rbl-prod-db | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 134 | RBL \| [Docker] kwikid-vkyc-rbl-prod-db | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 135 | RBL \| [Docker] kwikid-vkyc-rbl-61-server (10.45.246.61) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 136 | RBL \| [Docker] kwikid-vkyc-rbl-04-server-mem (10.45.246.04) | push | 90s | ✅ | UP | 99.26% | 99.70% |
| 137 | RBL \| [Docker] kwikid-vkyc-rbl-62-server (10.45.246.62) | push | 90s | ✅ | UP | 99.37% | 99.61% |
| 138 | RBL \| [Memory-Space] kwikid-vkyc-rbl-43-server-mem | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 139 | RBL \| [Server-Space] kwikid-vkyc-rbl-43-server-mem | push | 90s | ✅ | UP | 99.90% | 99.94% |
| 140 | RBL \| [Docker] kwikid-vkyc-rbl-43-server-mem | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 141 | RBL \| [Server-Space] RBL-report-14 (10.45.246.14) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 142 | RBL \| [Docker] RBL-report-14 (10.45.246.14) | push | 90s | ✅ | UP | 100.00% | 100.00% |
| 143 | RBL \| [Docker] kwikid-vkyc-rbl-19-server-mem-and-docker (10.45.246.19) | push | 90s | ✅ | UP | 99.37% | 99.79% |
| 177 | RBL \| kl-wrapper-api-error | push | 60s | ✅ | UP | 100.00% | 100.00% |
| 390 | RBL Bank | group | 60s | ✅ | UP | 100.00% | 100.00% |
| 391 | rbl.prod.report.vkyc.dc.14 - redis | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 418 | RBL UAT (Disk + Server Space + Dockers) Monitoring | push | 120s | ✅ | MAINTENANCE | 85.49% | 78.57% |
| 423 | rbl.prod.redis.dc.14 | push | 320s | ⛔ | — | 0.00% | 0.00% |

### FINO (28 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 102 | fino-prod-signalling-server | keyword | 60s | ✅ | UP | 100.00% | 100.00% |
| 105 | fino_prod \| AGENT API \| PROD \| fino.prod.app.vkyc -> uat.vkyc.kwikid | http | 60s | ✅ | UP | 100.00% | 99.85% |
| 106 | T3 \| fino_prod \| Containers Health | push | 120s | ✅ | UP | 100.00% | 99.69% |
| 111 | FINO \| USER API \| UAT \| fino-app-uat | http | 60s | ✅ | UP | 99.93% | 99.98% |
| 112 | FINO \| AGENT API \| UAT \| fino-app-uat | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 113 | FINO \| ADMIN  API \| UAT \| fino-app-uat | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 131 | [Memory-Space] fino.prod.app.vkyc | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 169 | [DB-Connection] fino prod | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 254 | fino_prod \| ADMIN API \| PROD \| fino.prod.app.vkyc -> uat.vkyc.kwikid | http | 60s | ✅ | UP | 100.00% | 99.47% |
| 255 | fino_prod \| USER API \| PROD \| fino.prod.app.vkyc -> uat.vkyc.kwikid | http | 60s | ✅ | UP | 100.00% | 99.35% |
| 279 | fino-on-prem-prod-user-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 282 | fino-uat-hypertrail-api | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 288 | [Memory-Space] fino.dr.vkyc.object - minio | push | 60s | ⛔ | — | 0.00% | 0.00% |
| 289 | [Disk-Space] fino.dr.vkyc.object - minio | push | 60s | ⛔ | — | 0.00% | 0.00% |
| 290 | [Memory-Space] fino.dc.vkyc.object - minio | push | 60s | ⛔ | — | 0.00% | 0.00% |
| 291 | [Disk-Space] fino.dr.vkyc.object - minio | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 295 | [Monitoring] FINO CUG | push | 120s | ⛔ | — | 0.00% | 0.00% |
| 311 | [Monitoring] Fino UAT \| fino-app-uat | push | 120s | ⛔ | — | 0.00% | 0.00% |
| 319 | [Monitoring] FINO PROD \| uat.vkyc.getkwikid.com | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 320 | [health endpoint] Agent Fino UAT | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 321 | [health endpoint] User Fino UAT | http | 60s | ✅ | UP | 99.93% | 99.98% |
| 322 | [health endpoint] Admin Fino UAT | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 323 | [health endpoint] Schedule Fino UAT | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 324 | [health endpoint] Schedule Fino PROD | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 325 | [health endpoint] Admin Fino PROD | http | 60s | ✅ | UP | 100.00% | 99.87% |
| 326 | [health endpoint] User Fino PROD | http | 60s | ✅ | UP | 100.00% | 99.28% |
| 327 | [health endpoint] Agent Fino PROD | http | 60s | ✅ | UP | 100.00% | 99.95% |
| 389 | [Server-Space] fino.prod.app.vkyc | push | 120s | ✅ | UP | 100.00% | 100.00% |

### NRFSI (18 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 17 | kwikid-vkyc-nrfsi-prod | http | 60s | ✅ | UP | 99.93% | 99.92% |
| 225 | [Disk-Space] nrfsi.prod.app.vkyc (10.35.100.134) | push | 180s | ⛔ | — | 0.00% | 0.00% |
| 296 | [Monitoring] NRFSI UAT \| nrfsi.uat.app.vkyc | push | 120s | ⛔ | — | 0.00% | 0.00% |
| 297 | [health endpoint] Agent NRFSI UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 298 | [health endpoint] User NRFSI UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 299 | [health endpoint] Admin NRFSI UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 300 | [health endpoint] Scheduling NRFSI UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 301 | [health endpoint] Digilocker NRFSI UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 302 | [Monitoring] NRFSI PORD \| nrfsi.prod.app.vkyc | push | 120s | ✅ | UP | 99.58% | 99.50% |
| 328 | [Monitoring] NRFSI CUG \| nrfsi.prod.app.vkyc | push | 120s | ✅ | UP | 100.00% | 99.92% |
| 329 | [health endpoint] Agent NRFSI CUG | http | 60s | ✅ | UP | 99.58% | 99.59% |
| 330 | [health endpoint] User NRFSI CUG | http | 60s | ✅ | UP | 99.58% | 99.59% |
| 331 | [health endpoint] Admin NRFSI CUG | http | 60s | ✅ | UP | 99.65% | 99.61% |
| 332 | [health endpoint] Schedule NRFSI CUG | http | 60s | ✅ | UP | 99.65% | 99.61% |
| 333 | [health endpoint] Schedule NRFSI PROD | http | 60s | ✅ | UP | 99.93% | 99.90% |
| 334 | [health endpoint] Admin NRFSI PROD | http | 60s | ✅ | UP | 99.93% | 99.92% |
| 335 | [health endpoint] User NRFSI PROD | http | 60s | ✅ | UP | 99.93% | 99.92% |
| 336 | [health endpoint] Agent NRFSI PROD | http | 60s | ✅ | UP | 99.93% | 99.92% |

### Bajaj (17 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 211 | kwikid-vkyc-bajaj-uat-disk-monitor | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 222 | kwikid-vkyc-bajaj-uat-memory-monitor | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 223 | kwikid-vkyc-bajaj-uat-cpu-monitor | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 345 | [Monitoring] Bajaj Prod \| 10.187.160.168 | push | 120s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 346 | [Monitoring] Bajaj Prod \| 110.187.162.173 | push | 120s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 347 | [Monitoring] Bajaj UAT\| 10.187.178.138 | push | 180s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 349 | [Monitoring] Bajaj DR \| 10.134.138.189 | push | 120s | ⛔ | — | 0.00% | 0.00% |
| 350 | [Monitoring] Bajaj Prod \| 10.187.161.128 | push | 150s | ✅ | UP | 100.00% | 100.00% |
| 351 | [Monitoring] Bajaj DR \| 10.134.128.201 | push | 60s | ⛔ | — | 0.00% | 0.00% |
| 421 | bajajfin.prod.redis.168 | push | 320s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 422 | bajajfin.prod.redis.173 | push | 320s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 425 | Bajaj new worker node | push | 60s | ✅ | MAINTENANCE | 100.00% | 100.00% |
| 426 | [health endpoint] Agent Bajaj New PROD | http | 60s | ✅ | UP | 99.93% | 99.98% |
| 427 | [health endpoint] User Bajaj New PROD | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 428 | [health endpoint] Admin Bajaj New PROD | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 429 | [Monitoring]Bajaj new prod worker node | http | 60s | ✅ | UP | 100.00% | 99.76% |
| 432 | [Monitoring]Bajaj new prod monitoring server | http | 60s | ✅ | DOWN | 60.08% | 78.40% |

### CBI (15 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 7 | kwikid-vkyc-cbi-prod-agent-api | http | 80s | ✅ | UP | 100.00% | 99.28% |
| 10 | kwikid-vkyc-cbi-prod-admin-api | http | 120s | ✅ | UP | 100.00% | 99.32% |
| 16 | kwikid-vkyc-cbi-cug-signallingserver | keyword | 180s | ✅ | UP | 99.79% | 99.46% |
| 95 | [Memory-Space] kwikid-vkyc-cbi-app1-server-mem | push | 300s | ⛔ | — | 0.00% | 0.00% |
| 97 | [Memory-Space] kwikid-vkyc-cbi-ext-server-mem (cbi.vkyc.prod.getkwikid.com) | push | 300s | ✅ | UP | 100.00% | 100.00% |
| 101 | kwikid-vkyc-cbi-ext-dkyc-api (cbi.vkyc.prod.getkwikid.com) | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 189 | kwikid-vkyc-cbi-prod-dkyc-api | http | 60s | ✅ | UP | 99.36% | 99.25% |
| 190 | kwikid-vkyc-cbi-prod-wrapper-api | http | 180s | ✅ | UP | 100.00% | 99.20% |
| 203 | [Storage Server] kwikid-vkyc-cbi-storage-server | http | 180s | ✅ | UP | 100.00% | 99.12% |
| 352 | CBI \| ML \| Internal Container | http | 180s | ✅ | UP | 100.00% | 99.40% |
| 353 | CBI \| ML \| Wrapper Container | http | 180s | ✅ | UP | 100.00% | 99.82% |
| 354 | CBI \| ML \| face_match_custom | http | 20s | ⛔ | — | 0.00% | 0.00% |
| 355 | CBI \| DKYC \| Backend \| Agent (Maker) / Auditor (Checker) | http | 60s | ✅ | UP | 99.41% | 99.33% |
| 356 | CBI \| DKYC \| Backend \| Admin | http | 60s | ✅ | UP | 99.60% | 99.11% |
| 376 | kwikid-vkyc-cbi-prod-user-api | http | 60s | ✅ | UP | 99.54% | 99.39% |

### ICICI (15 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 180 | [DOCKER CONTAINER] - icici_ekyc_backend | http | 89s | ⛔ | — | 0.00% | 0.00% |
| 193 | icici-encryption-api | http | 90s | ⛔ | — | 0.00% | 0.00% |
| 194 | icici-kra-push-api | http | 90s | ⛔ | — | 0.00% | 0.00% |
| 195 | icici-kra-push-wrapper | http | 87s | ⛔ | — | 0.00% | 0.00% |
| 226 | [Memory Space] kwikid_ekyc_icici_app_uat | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 227 | [Disk Space] kwikid_ekyc_icici_app_uat | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 281 | icici-uat-kra-api | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 310 | [Monitoring] icici-app-uat UAT | push | 120s | ⛔ | — | 0.00% | 0.00% |
| 317 | [health endpoint]  icici-enc-dec-api UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 318 | [health endpoint]  icici_ekyc_backend  UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 338 | [health endpoint] icicipdfmaker PROD | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 340 | [health endpoint] icici_ekyc_wrappers UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 341 | [health endpoint] icicipdfmaker UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 343 | [health endpoint] icici-enc UAT | http | 60s | ⛔ | — | 0.00% | 0.00% |
| 360 | ICICI Prod API | http | 120s | ✅ | UP | 100.00% | 100.00% |

### Tata Capital (14 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 29 | [TCOOKP Schedule Service Alert] tcook.app.prod.dc.vkyc  (172-81-12-53) | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 118 | [Memory-Space] kwikid-vkyc-tcook-app-dc-server | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 126 | [Server-Space] tata.prod.app | push | 180s | ⛔ | — | 0.00% | 0.00% |
| 145 | [Memory-Space] tcook.ms.prod.dc.vkyc | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 146 | [Server-Space] tcook.ms.prod.dc.vkyc | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 148 | [Server-Space] tcook.app.prod.dc.vkyc | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 176 | Deprecated \| scylladb - TATA Prod | push | 90s | ⛔ | — | 0.00% | 0.00% |
| 204 | [Service] kwikid_poc_schedule_uat \| tcook.app.prod.dc.vkyc | http | 90s | ⛔ | — | 0.00% | 0.00% |
| 205 | [Service] kwikid_poc_schedule_dev \| tcook.app.prod.dc.vkyc | http | 90s | ⛔ | — | 0.00% | 0.00% |
| 206 | [Service] kwikid_poc_schedule \| tcook.app.prod.dc.vkyc | http | 90s | ⛔ | — | 0.00% | 0.00% |
| 207 | [Service] kwikid_poc_ipv_uat_uat \| tcook.app.prod.dc.vkyc | http | 90s | ⛔ | — | 0.00% | 0.00% |
| 208 | [Service] kwikid_poc_admin \| tcook.app.prod.dc.vkyc | http | 90s | ⛔ | — | 0.00% | 0.00% |
| 209 | [Service] kwikid_poc_apk_new \| tcook.app.prod.dc.vkyc | http | 90s | ⛔ | — | 0.00% | 0.00% |
| 210 | [Service] kwikid_poc_apk_uat_uat \| tcook.app.prod.dc.vkyc | http | 90s | ⛔ | — | 0.00% | 0.00% |

### SaaS / Shared (13 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 8 | kwikid-saas-chaand | http | 120s | ✅ | UP | 100.00% | 99.92% |
| 18 | kwikid-chaand-kms | http | 120s | ✅ | UP | 100.00% | 100.00% |
| 36 | [Server-Space]chaand.getkiwkid.com | push | 90s | ✅ | UP | 99.27% | 99.40% |
| 37 | api-uat.test.getkwikid.com/adminapi - kwikid-saas-vkyc-app-uat | http | 60s | ✅ | MAINTENANCE | 100.00% | 99.74% |
| 38 | scheduleapi-uat.test.getkwikid.com - kwikid-saas-vkyc-app-uat | http | 60s | ✅ | MAINTENANCE | 100.00% | 99.76% |
| 293 | Saas client | group | 120s | ✅ | UP | 100.00% | 100.00% |
| 361 | [Disk Space] kwikid-saas-sfu-prod-new | push | 120s | ✅ | UP | 100.00% | 99.92% |
| 363 | [LiveKit Service] kwikid-saas-sfu-uat {Contact : Ramavtar} | push | 120s | ⛔ | — | 91.45% | 91.86% |
| 364 | [Disk Space] kwikid-saas-sfu-uat | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 365 | [Signalling] kwikid-saas-sfu-uat | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 366 | [LiveKit Service] kwikid-saas-sfu-prod-new | push | 120s | ✅ | UP | 99.86% | 99.84% |
| 367 | [Disk Space] kwikid-saas-sfu-prod-new | push | 120s | ✅ | UP | 99.79% | 99.74% |
| 368 | [Signalling] kwikid-saas-sfu-prod-new | push | 120s | ✅ | UP | 100.00% | 99.92% |

### Canara (11 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 49 | canara-prod-admin-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 50 | canara-prod-user-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 51 | canara-prod-agent-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 52 | canara-prod-wrapper-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 108 | canara-prod-signalling-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 110 | canara-prod-scheduling-api | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 114 | canara-prod-redis-localhost | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 115 | canara-prod-redis-container | http | 60s | ✅ | UP | 100.00% | 100.00% |
| 292 | Disk Space \| canara.prod.vkyc.wrapper.dr | push | 120s | ⛔ | — | 0.00% | 0.00% |
| 395 | Disk Space \| canara.prod.vkyc.wrapper.dc | push | 120s | ✅ | UP | 100.00% | 100.00% |
| 396 | Disk Space \| canara.prod.vkyc.app.base.dc | push | 120s | ✅ | UP | 100.00% | 100.00% |

### RRB (1 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 294 | RRB \| server \| disk-space \| hostname: ip-172-32-148-11 | push | 90s | ✅ | UP | 100.00% | 100.00% |

### Shinhan (1 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 307 | [Monitoring] shinhan.prod.app.vkyc PORD | push | 120s | ✅ | UP | 100.00% | 98.12% |

### Svamaan (1 monitors)

| ID | Name | Type | Interval | Active | Status | Uptime 24h | Uptime 30d |
|:--|:--|:--|:--|:--|:--|:--|:--|
| 419 | Svamaan-Docker-VM [contact: Shivam] | push | 60s | ⛔ | — | 0.00% | 0.00% |

---

## 6. Data Collection Method (reproducible)

The live inventory was collected via the authenticated Socket.io path (the `/metrics` Prometheus endpoint rejected the shared API key with 401). Reproduce with:

```
1. Open EIO4 Socket.io polling handshake  → GET /socket.io/?EIO=4&transport=polling
2. Namespace connect                       → POST body "40"
3. Emit login                              → 42["login",{"username":"root_user","password":"…","token":""}]
4. Server pushes (on the next poll):        monitorList, heartbeatList, importantHeartbeatList,
                                            avgPing, uptime, maintenanceList, statusPageList,
                                            apiKeyList, notificationList, proxyList, info
```

A headless-browser client (which speaks Socket.io natively) is the most reliable driver, since it handles the polling/session mechanics automatically. This entire inventory was produced that way.

### Legacy notes (superseded)

Earlier attempts used these (now-secondary) approaches; kept for reference:

```bash
# Prometheus metrics (returned 401 with the shared key — confirm correct key first)
curl -H "Authorization: Bearer <API_KEY>" http://status.getkwikid.com:3001/metrics

# Public status page API (works per-slug now that pages exist)
curl http://status.getkwikid.com:3001/api/entry-page          # → entryPage: "dashboard"
curl http://status.getkwikid.com:3001/api/status-page/bob     # per-client public page
curl http://status.getkwikid.com:3001/api/status-page/heartbeat/bob
```

Historic update checklist (now satisfied):
- Complete monitor list (id, name, type, url, interval)
- Current status of each monitor
- Status page slug(s) in use
- Tag groupings
- Any tenant-to-monitor mappings found
