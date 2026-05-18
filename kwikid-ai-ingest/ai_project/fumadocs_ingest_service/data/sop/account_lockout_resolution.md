---
title: "Account Lockout - Resolution Procedure"
query_type: "AUTH_ISSUE"
issue_area: "Authentication"
---

## Overview

This procedure covers resolution of account lockouts caused by repeated failed login attempts, suspicious activity flags, or admin-initiated freezes. Account lockouts are a security control — resolution requires identity verification before access is restored.

**Never unlock an account without verifying customer identity.** Failure to do so constitutes a security violation.

## Step 1: Identify the Lockout Type

Account lockouts fall into three categories with different resolution paths:

| Lockout Type | Cause | Resolution |
|---|---|---|
| **Soft Lock** | 3–5 failed login attempts | Auto-unlocks after 30 minutes; or agent can reset |
| **Hard Lock** | 6+ failed attempts OR OTP failure abuse | Requires agent-assisted unlock + MFA re-enrollment |
| **Security Freeze** | Suspicious activity or admin action | Requires security team clearance |

Check the customer's status in the admin panel: **Customer > Account > Security Status**.

## Step 2: Verify Customer Identity

Before taking any action on a locked account, verify the customer's identity using **two** of the following:

1. Full name (exact match on record)
2. Date of birth
3. Registered mobile number (last 4 digits)
4. Registered email address
5. Last 4 digits of PAN / Aadhaar (if KYC completed)

**If only one factor can be verified**: Do not unlock. Ask the customer to visit the nearest branch with original ID documents.

**If zero factors match**: Close the ticket and mark as suspected account takeover attempt. Notify the Security team.

Log the identity verification in Freshdesk: "Identity verified via [Factor 1] and [Factor 2] at [timestamp]."

## Step 3: Resolve a Soft Lock

For soft-locked accounts (3–5 failed attempts):

**Option A — Wait for auto-unlock (preferred for low-urgency cases):**
- Inform the customer the account auto-unlocks in 30 minutes from the time of the last failed attempt.
- Advise the customer to use "Forgot Password" if they are unsure of their credentials.

**Option B — Agent-initiated unlock:**
1. Navigate to: **Admin > Customer Management > [Customer ID] > Security > Unlock Account**.
2. Select reason: "Agent-assisted unlock after identity verification".
3. Set a note with the verification method used.
4. Send confirmation to the customer via the ticket.

After unlock: instruct the customer to use the "Forgot Password" flow to set a new password immediately. A soft-locked account that is unlocked without a password reset remains vulnerable.

## Step 4: Resolve a Hard Lock

Hard locks require additional steps beyond simple unlock:

1. Complete identity verification (Step 2).
2. Unlock via: **Admin > Customer Management > [Customer ID] > Security > Force Unlock (Hard Lock)**.
   - This action is logged in the security audit trail.
3. **Mandatory**: Trigger MFA re-enrollment:
   - Navigate to: **Customer > Security > Reset MFA**.
   - The customer will be prompted to set up a new authenticator on next login.
4. Advise the customer to update their password immediately after unlocking.
5. If the customer denies initiating the failed attempts: escalate to the Security team to investigate potential unauthorized access.

Hard lock resolution must be documented with the agent name, timestamp, verification factors used, and reason for unlock.

## Step 5: Security Freeze — Escalation Required

Security freezes are NOT resolvable by front-line agents. Steps:

1. Inform the customer: "Your account has been temporarily secured pending a review. This typically takes 24–48 business hours."
2. Create a high-priority ticket in Freshdesk tagged `security-freeze` and assign to the Security team.
3. Do NOT provide the customer any details about why the freeze was triggered.
4. Do NOT attempt to unlock via the admin panel — this will be rejected at the database level.

The Security team will contact the customer directly with next steps.

## Step 6: Post-Unlock Checklist

After any account unlock (Soft or Hard lock):

- [ ] Identity verification logged in ticket notes
- [ ] Unlock action logged in admin panel with reason
- [ ] Customer advised to reset password via "Forgot Password"
- [ ] MFA re-enrollment triggered (Hard lock only)
- [ ] Customer confirmed successful login before ticket closure

## Step 7: Escalation Criteria

Escalate to Security team if:

- Customer denies responsibility for the failed attempts
- Account shows login attempts from foreign IP addresses or unusual devices
- Customer reports receiving lockout notifications for attempts they did not make
- Multiple accounts with the same mobile number or email are locked simultaneously (credential stuffing pattern)
- Security Freeze status (as per Step 5)

Escalations must include: account ID, lockout timestamp, IP addresses from the failed attempts log (available in **Admin > Security Logs**), and any information the customer provided about their location at the time.

## Resolution Confirmation

1. Confirm the customer successfully logged in.
2. Verify the account status shows **ACTIVE** in the admin panel.
3. Resolve the ticket with: label **AUTO_REPLY**, resolution note: "Account unlocked after identity verification. Customer confirmed successful login."
4. If escalated to Security: do not resolve the ticket — leave open for Security team to close.
