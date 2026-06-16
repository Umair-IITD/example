---
title: "Global — First Response (Ticket Acknowledgment)"
query_type: "ESCALATION"
issue_area: "Support Triage"
clients: []
sop_id: "sop_global_first_response"
---

## Overview

Use this standard first response for any client ticket while investigation is in progress. Applies to all tenants unless a client-specific SOP provides an alternate template.

## Internal steps before replying

1. Create or update the internal support ticket (for example Freshdesk).
2. Inform the developer or team lead if the issue may need engineering involvement.
3. Collect session ID, user ID, mobile number, or deal ID from the client email.

## Standard holding reply (first response)

Copy and send:

Hi Team,

Please allow us some time to analyze this. We will check and get back to you with the details.

Please feel free to reach out to us if you have any further concerns or questions.

Best regards,

**Kwik.ID Support**

## Follow-up reply after analysis

After investigation, send a specific reply. Example structure:

Hi Team,

After analyzing, we have identified the root cause of the issue you reported.

[Insert specific finding here.]

Please feel free to reach out to us if you have any further concerns or questions.

Best regards,

**Kwik.ID Support**

## OCR / document quality issue

When the ticket is about unreadable Aadhaar, PAN, or other document images:

Hi Team,

We checked the images associated with the session ID you shared, and understood that the clarity of the image was compromised for the OCR (Optical Character Recognition) engine to recognize the text.

Please share the document image with a readable quality so that we can get accurate details captured in the session.

Best regards,

**Kwik.ID Support**

## Escalation criteria

Escalate to engineering or team lead when:

- Production outage or widespread agent/user login failure
- Data loss, missing video, or CBS (Core Banking System) push failure affecting multiple users
- Security concern (credential leak, unauthorized access)
- Issue reproduced but no documented fix in Stack Overflow Teams knowledge base