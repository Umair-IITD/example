# Asana L2 Integration — Research Brief

Prepared for: Umair
Date: 2026-07-30
Scope: research only — no code changed as part of this document.

## TL;DR

The L2 workflow you described — self-resolvable issues get an automated reply and close; issues needing dev work get an Asana task, tracked until dev marks it done, then verified and closed — is **already about 80% built** in the codebase. There's a real `AsanaClient`, a full `EngineeringEscalationService`, and it's already wired into the ticket pipeline and the Freshdesk webhook handler. You don't need to design this from scratch.

Two things are actually missing:

1. **Asana credentials aren't configured.** `ASANA_API_KEY`, `ASANA_PROJECT_ID`, and `ASANA_WORKSPACE_ID` are commented out in both `.env` and `.env.example`. Until these are set, the system silently falls back to an in-memory mock — tickets are "created" in memory only, no real Asana task, no link posted to Freshdesk, nothing dev can see.
2. **The resolution loop isn't automatic.** There's a method (`sync_status()`) that checks whether an Asana task has been marked complete and closes the loop on the Freshdesk side — but nothing currently calls it. Right now, if dev marks a task done in Asana, the system has no way of finding out.

Everything else — creating the task, writing a structured description with root cause and evidence, posting the Asana link back to the customer, priority tagging — is done.

---

## 1. Asana, in plain terms

Since you mentioned having very little Asana knowledge, here's the minimum vocabulary needed to follow the rest of this document:

- **Workspace** — the top-level container for your whole organization's Asana data (e.g. "KwikID"). You're already a member of this, per your message.
- **Project** — a board/list within a workspace where related tasks live (e.g. "Engineering Escalations"). Dev work would land in a project like this.
- **Task** — a single work item inside a project. This is what gets created per escalation, and what gets marked "complete" by dev when they're done.
- **GID (Global ID)** — every object in Asana (workspace, project, task, user) has a unique ID string, e.g. `1201234567890`. The API and URLs are built entirely around these IDs, not names.
- **Personal Access Token (PAT)** — a long-lived credential you generate from your own Asana account that lets a script act with your permissions. This is what `ASANA_API_KEY` is expected to be.

A task's permalink looks like `https://app.asana.com/0/{project_gid}/{task_gid}` — that's literally the format the codebase already builds (see `asana/client.py::build_task_url`).

---

## 2. What already exists in the codebase

All paths below are relative to `kwikid-ai-ingest/ai_project/fumadocs_ingest_service/`.

### `asana/client.py` — the Asana API client

A working, synchronous Asana REST API v1 client:

- `AsanaConfig.from_env()` reads `ASANA_API_KEY`, `ASANA_PROJECT_ID`, `ASANA_WORKSPACE_ID`, `ASANA_TIMEOUT_S` from environment variables.
- `AsanaClient.create_task(title, description, priority)` — POSTs a new task into the configured project, tags it with a priority label (`P0-critical` … `P3-low`), returns `{"gid", "project_id"}`.
- `AsanaClient.get_task(task_gid)` — fetches a task by GID, including its `completed` boolean. This is the piece that would tell you dev marked it done.
- `AsanaClient.health()` — a lightweight connectivity check.
- `build_asana_client()` — factory that returns `None` gracefully if credentials aren't set, so the rest of the system degrades to mock mode instead of crashing.

### `case_engine/engineering/service.py` — `EngineeringEscalationService`

This is the L2 orchestration layer (maps directly to the `ASANACREATE` node in your flow diagram):

- `create_ticket(...)` — builds a structured description (root cause, investigation summary, SOP steps already tried), infers priority, calls `AsanaClient.create_task()` if a live client is injected, stores an `EngineeringTicket` record.
- `update_ticket(...)` / `resolve_ticket(...)` — status transitions (`PENDING → IN_PROGRESS → RESOLVED → CLOSED`, or `FAILED`).
- `sync_status(ticket_id)` — **this is the close-the-loop method.** If a live Asana client is present, it calls `get_task()` and, if `completed == True` and the local status isn't already `RESOLVED`, flips it to `RESOLVED`. This is exactly the check your workflow needs — it just isn't being called by anything yet (see §4).

### Wiring already in place

- `runtime/assembly.py` (around line 596) builds a live `AsanaClient` from env vars at startup and injects it into `EngineeringEscalationService`. If credentials are missing, it injects `None` and logs `asana_live=False` — the service still works, just in mock mode.
- `api/routes/webhooks/freshdesk.py` (around line 422, tagged "Sprint 2.5.8 / Blueprint §20A") already does the following automatically when a live Asana task is created:
  1. Builds the Asana task URL.
  2. Writes it into the Freshdesk custom field `cf_asana_ticket_link`.
  3. Sends the customer an automatic reply referencing the escalation and the Asana link.

So the "escalate to dev team with an Asana link" half of your workflow is functionally complete, contingent only on credentials being set.

---

## 3. The two real gaps

### Gap 1 — Credentials not configured

`.env.example` (line ~460) and `.env` both have `ASANA_API_KEY`, `ASANA_PROJECT_ID`, and `ASANA_WORKSPACE_ID` commented out. `SUPPORT_AGENT_MODE=PRODUCTION` is already set in your real `.env`, but without these three values, `build_asana_client()` returns `None` and every escalation is silently mocked — no real task, no link, no dev visibility.

To fix this you need, from your own Asana account:

1. **A Personal Access Token.** Generate one from the Asana Developer Console (My Settings → Apps → Developer apps, or directly at `app.asana.com` under your profile settings → Apps). Give it a clear description like "KwikID L2 automation" so it's identifiable later. Treat it like a password — set it in `.env`, never commit it.
2. **A workspace GID.** With your PAT, visit `https://app.asana.com/api/1.0/workspaces` while logged in (or hit that endpoint with the token) to list your workspace(s) and their GIDs.
3. **A project GID.** This requires a decision on your end first: which Asana project should escalation tasks land in? If one doesn't exist yet, create a project in your KwikID workspace dedicated to these escalations (e.g. "Support Escalations" or similar), then copy its GID out of the browser URL — `https://app.asana.com/0/{project_gid}/...`.

Once you have all three, filling them into `.env` and restarting the service is enough to flip `asana_live=True` — no code changes needed for this part.

### Gap 2 — No automatic resolution loop

`sync_status()` exists and does the right thing, but nothing calls it. There are two standard ways to close this loop, and they trade off differently:

**Option A — Polling.** Something (a scheduled job) periodically calls `sync_status()` for every ticket that's still `PENDING`/`IN_PROGRESS`. Simple, no public endpoint needed, no Asana-side configuration. Downside: resolution is only detected on the next poll, so there's a delay (e.g. every 5–15 minutes) rather than instant. I didn't find an existing scheduler (no APScheduler/Celery beat) in this codebase, so this would be a new small piece of infrastructure — though a simple timer loop or cron-triggered script would suffice at this scale.

**Option B — Asana webhooks.** Asana can push an event to your server the moment a task's `completed` field changes. This is real-time and doesn't need polling, but has more setup:

- You register a webhook against the project (`POST /webhooks` with a `target` URL and `resource` = the project GID).
- Asana immediately does a **handshake**: it POSTs a test request to your target URL with an `X-Hook-Secret` header, and your endpoint must echo that same header back with a `200`/`204` within its request — until this succeeds, the webhook registration itself doesn't complete.
- After that, every event delivery includes an `X-Hook-Signature` header (HMAC-SHA256 over the raw body, keyed with the secret from the handshake) that you must verify before trusting the payload.
- A "task marked complete" event arrives as a `changed` action on a `task` resource with `change.field == "completed"` and `new_value == true`.
- This needs a publicly reachable HTTPS endpoint (same category of thing as your existing Freshdesk webhook receiver).

The good news: you already have the exact scaffolding this needs. `freshdesk/verifier.py` implements constant-time HMAC signature verification, and `freshdesk/idempotency.py` implements duplicate-event protection with the same "receive → verify → dedupe → process" shape Asana's webhook model expects. An Asana webhook receiver could mirror both of those almost directly rather than inventing a new pattern.

**My read:** given this is a single internal project with modest task volume, Option A (polling every few minutes) is the lower-effort, lower-risk way to get the loop closed first — no new public endpoint, no handshake/signature code to write and secure. Option B is the "proper" long-term answer if the volume or latency requirements justify it later, and it can reuse your existing webhook-verification pattern almost as-is when you get there.

---

## 4. Asana MCP connector (separate from the production integration)

There's an official Asana MCP server available in the connector registry (`mcp.asana.com`, tools include `create_task_preview`, `search_tasks_preview`, `get_project`, `get_portfolio`, and more). This is a good option for you personally — you could connect it and ask me to browse your Asana workspace, look up project/workspace GIDs, or even create/inspect tasks conversationally, without touching code.

It is **not** a replacement for the production `AsanaClient` in the codebase — the pipeline needs a server-side Python client making authenticated calls as part of an automated backend flow, which is exactly what already exists. The MCP is useful for you to explore Asana and pull GIDs quickly; the escalation pipeline should keep using the existing `asana/client.py`.

If you'd like, I can connect this for you now so you can find your workspace/project GIDs conversationally instead of digging through the Asana UI or hitting the API by hand.

---

## 5. Suggested path forward

1. Decide (or create) the Asana project that escalation tasks should land in.
2. Generate a Personal Access Token from your Asana account.
3. Get the workspace GID and the project GID (either via the MCP connector above, or the raw API endpoints listed in §3).
4. Set `ASANA_API_KEY`, `ASANA_PROJECT_ID`, `ASANA_WORKSPACE_ID` in `.env`. Restart — this alone activates real task creation, the Freshdesk link update, and the escalation reply, since all of that is already built.
5. Decide polling vs. webhook for the resolution loop (my recommendation: start with polling — it's a small, self-contained addition).
6. Build the chosen resolution-loop mechanism, which calls `EngineeringEscalationService.sync_status()` and, on a transition to `RESOLVED`, triggers the verify → reply-to-customer → close-ticket steps your workflow describes (this last connective step — resolved-in-Asana leading all the way to Freshdesk ticket close — doesn't exist yet either, and would need to be written alongside the polling/webhook trigger).

Nothing in this document changes any code — this is purely the research and current-state picture you asked for. Happy to help implement any of the steps above once you've made the project/credential decisions.

---

Sources:
- [Personal access token — Asana Developers](https://developers.asana.com/docs/personal-access-token)
- [Quick start guide — Asana Developers](https://developers.asana.com/docs/quick-start)
- [How to find your personal access token in Asana](https://www.merge.dev/blog/asana-personal-access-token)
- [Webhooks — Asana Developers](https://developers.asana.com/docs/webhooks-guide)
- [Establish a webhook — Asana Developers](https://developers.asana.com/reference/createwebhook)
- [Guide to Asana Webhooks: Features and Best Practices — Hookdeck](https://hookdeck.com/webhooks/platforms/guide-to-asana-webhooks-features-and-best-practices)
