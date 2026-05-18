---
title: "OTP Delivery Failure - Resolution Procedure"
query_type: "OTP_ISSUE"
issue_area: "Authentication"
---

## Overview

This procedure covers diagnosis and resolution of OTP (One-Time Password) delivery failures during KYC, login, and transaction verification flows. OTP failures are one of the most frequent support escalations in the KwikID platform.

## Step 1: Triage — Identify the OTP Delivery Channel

Determine which OTP delivery method was in use:

- **SMS OTP**: Sent via registered mobile number
- **Email OTP**: Sent to registered email address
- **TOTP (Authenticator App)**: Time-based codes from apps like Google Authenticator
- **WhatsApp OTP**: Sent via WhatsApp Business API

Ask the customer: "Which method were you expecting the OTP on?"

If the customer is unsure, check their profile in the admin panel under: **Customer > Authentication Settings**.

## Step 2: Verify Mobile Number Registration

For SMS OTP failures:

1. Open the customer's profile in Freshdesk and note the mobile number on record.
2. Ask the customer to confirm the last 4 digits of their registered mobile number.
3. If there is a mismatch, the customer must update their mobile number via the KYC re-verification flow before OTPs can be resent.
4. If the number matches, proceed to Step 3.

**Common issue**: Customer changed their SIM or phone number without updating the platform record. Resolution requires re-KYC with valid ID proof.

## Step 3: Check OTP Gateway Status

Before resending an OTP, verify the SMS gateway is operational:

1. Check the internal status dashboard: **Ops > Integrations > SMS Gateway**.
2. If gateway shows degraded or down: escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.
3. If gateway is operational: proceed to Step 4.

Note: SMS delivery can take up to 5 minutes during peak hours. Ask the customer to wait before concluding the OTP was not delivered.

## Step 4: Resend OTP (Agent-Initiated)

If the customer confirms no OTP was received and the gateway is operational:

1. Navigate to: **Customer Profile > Security > Resend OTP**.
2. Select the correct delivery channel.
3. Set a note in Freshdesk: "OTP resent by agent at [timestamp]".
4. Inform the customer to check:
   - SMS inbox and spam folder
   - If using SMS: ensure the phone is not in DND (Do Not Disturb) mode
   - If using email: check the spam/promotions folder

Maximum resend attempts per hour: **3**. If this limit is reached, the account enters a 60-minute cooldown. Do not attempt further resends.

## Step 5: OTP Expiry Handling

OTPs expire after **10 minutes** for SMS/email and **30 seconds** for TOTP.

If the customer received the OTP but it expired:

1. Instruct the customer to request a new OTP from the login or verification screen.
2. If the customer is unable to trigger the OTP from the app, use Step 4 to resend.
3. Remind the customer to enter the OTP immediately after receiving it.

## Step 6: TOTP / Authenticator App Issues

If the customer uses a TOTP authenticator app and codes are rejected:

1. **Clock sync issue**: The most common cause. The device clock must be synchronized to the correct time. Ask the customer to:
   - Android: Settings > General management > Date and time > Enable Automatic date and time
   - iOS: Settings > General > Date & Time > Set Automatically
2. After re-syncing, the TOTP should generate valid codes immediately.
3. If the app was deleted or the device was reset: the customer must go through re-enrollment. This requires re-KYC.

## Step 7: Escalation Criteria

Escalate to Tier-2 support if:

- SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number
- The customer's account shows "OTP_BLOCKED" status in the admin panel
- Gateway is operational but the customer insists on non-receipt (possible carrier-level block)
- The customer reports receiving OTPs for a different account (cross-delivery incident — treat as security incident)

**For cross-delivery incidents**: Immediately flag in Freshdesk with tag `security-incident` and notify the Security team at security@think360.ai.

## Resolution Confirmation

Before closing the ticket:

1. Confirm the customer successfully completed their intended action (login, verification, etc.)
2. Ask: "Were you able to complete the process successfully?"
3. If yes: resolve ticket with label **AUTO_REPLY** / Resolution: OTP delivered successfully.
4. If no: escalate per Step 7 criteria.
