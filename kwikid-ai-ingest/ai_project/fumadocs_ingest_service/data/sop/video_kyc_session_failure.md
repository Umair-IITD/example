---
title: "Video KYC Session Failure - Resolution Procedure"
query_type: "VIDEO_KYC"
issue_area: "KYC_VERIFICATION"
clients: "unity_bank"
---

## Overview

This procedure applies to Video KYC (V-CIP — Video Customer Identification Process) session failures for Unity Bank customers. V-CIP is a regulated process governed by RBI guidelines. Session failures must be resolved promptly as failed sessions count against the customer's daily verification attempts.

**Maximum V-CIP attempts per day**: 3. After 3 failures, the customer must wait 24 hours before retrying.

## Step 1: Identify the Stage of Failure

Collect the following from the customer:

1. **At what point did the session drop?**
   - Before connecting to an agent (pre-session failure)
   - During document verification (mid-session failure)
   - During liveness check / biometric capture (biometric failure)
   - After document capture but before agent sign-off (agent-side failure)

2. **What error message, if any, was displayed?**
   - "Session ended unexpectedly" → network issue
   - "Camera not detected" → device/permission issue
   - "Face not detected" → lighting or positioning issue
   - "Agent disconnected" → agent-side issue

3. **Was the session ID visible on screen?** (Format: VCIP-XXXXXXXX). If yes, note it for retrieval from the session log.

## Step 2: Check Network Conditions

V-CIP requires a stable internet connection. Minimum requirements:
- Download: 2 Mbps
- Upload: 1 Mbps
- Latency: < 300ms

Ask the customer:
- Are they on WiFi or mobile data?
- Are other video calls or streaming services working?
- Is VPN active? (VPN can cause session instability — ask them to disable it)

If the customer is on a poor network: advise them to find a stable WiFi connection and retry. **Do not count this as a platform failure.**

## Step 3: Camera and Microphone Permission Verification

Camera and microphone access are mandatory for V-CIP.

**Android:**
1. Settings > Apps > [KwikID App] > Permissions
2. Ensure Camera and Microphone are set to "Allow"

**iOS:**
1. Settings > Privacy & Security > Camera → Enable for KwikID
2. Settings > Privacy & Security > Microphone → Enable for KwikID

If permissions were just enabled, the customer must restart the app and begin a new session — the current session cannot be resumed.

## Step 4: Device Compatibility Check

V-CIP requires:
- Front-facing camera (minimum 5 MP recommended)
- OS: Android 8.0+ or iOS 12.0+
- Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions

If the device is incompatible: inform the customer to use a different device. Do not attempt to work around device incompatibility — the session quality will fail regulatory requirements.

## Step 5: Recover a Dropped Mid-Session

If the session dropped after document capture was completed:

1. Retrieve the session log using the session ID (from Step 1) via: **Admin > V-CIP Sessions > Search by Session ID**.
2. Check the session status:
   - **DOCS_CAPTURED / PENDING_AGENT_REVIEW**: The documents were saved. An agent can resume review from the admin panel without requiring the customer to restart from scratch.
   - **INCOMPLETE / NO_DATA**: The session must be restarted from the beginning.
3. If the session is in DOCS_CAPTURED status: notify the V-CIP operations team at vcip-ops@unity_bank.com to manually assign an agent for review.

## Step 6: Lighting and Liveness Failure

"Face not detected" errors are typically caused by poor lighting or incorrect positioning.

Resolution:
1. Ask the customer to move to a well-lit area (natural light preferred; avoid backlighting from windows).
2. Ensure the face is centered in the camera frame with no obstruction (glasses are acceptable; masks are not for V-CIP).
3. Remove filters or beauty modes if using the camera through a third-party app.
4. For liveness check: ensure the customer moves naturally and doesn't hold unnaturally still. The system expects head movement.

## Step 7: Agent-Side Disconnection

If the agent disconnected from the session (agent-side failure):

1. The session enters "PENDING" status for 5 minutes, during which a new agent can join.
2. Check: **Admin > V-CIP Sessions > PENDING** for the session.
3. If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.
4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.

## Step 8: Escalation Criteria

Escalate to Platform Engineering (#vcip-issues in Slack) if:

- The same session ID shows FAILED status despite all permissions and network being correct
- Customers report V-CIP failures during a specific time window (possible regional outage)
- Liveness check fails repeatedly for a customer who is clearly visible (possible SDK bug)
- Session count shows a customer has been charged an attempt for a platform-initiated failure (requires attempt count reset — requires L2 action)

## Resolution Confirmation

1. Verify the customer successfully completed the V-CIP session and received confirmation.
2. Check the session shows status **COMPLETED** or **APPROVED** in the admin panel.
3. If approved: resolve ticket. Label: **AUTO_REPLY**. Note: "V-CIP session completed successfully."
4. If the customer exhausted attempts due to platform fault: request attempt count reset from L2 and note the fault.
