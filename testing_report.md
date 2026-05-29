## Query 1:
```
    {
    "query_text": "I did not receive the VKYC link on SMS — what should I do?",
    "client": "unity_bank"
    }
```
## Response 1:
```
    {
    "session_id": "5a0f5e44-ceee-4f13-983c-a37e12d7877d",
    "message_id": "69ba46d2-7127-476e-9b80-44ab6fa1571c",
    "answer": "To address the issue of not receiving the VKYC link via SMS, follow these steps:\n\n1. Open the customer's profile in Freshdesk and verify the mobile number on record.\n2. Ask the customer to confirm the last 4 digits of their registered mobile number.\n - If there is a mismatch, instruct the customer to update their mobile number via the KYC re-verification flow before OTPs can be resent.\n - If the number matches, proceed to the next step.\n3. Check the internal status dashboard under Ops > Integrations > SMS Gateway to ensure the SMS gateway is operational.\n - If the gateway shows degraded or down, escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n - If the gateway is operational, proceed to resend the OTP.\n4. Navigate to Customer Profile > Security > Resend OTP.\n5. Select the correct delivery channel and set a note in Freshdesk: \"OTP resent by agent at [timestamp]\".\n6. Inform the customer to check their SMS inbox and spam folder. If using SMS, ensure their phone is not in DND (Do Not Disturb) mode. If using email, check the spam/promotions folder.\n7. Remember, the maximum resend attempts per hour is 3. If this limit is reached, the account enters a 60-minute cooldown, and no further resends should be attempted.\n\nIf the customer confirms no OTP was received after following these steps:\n- Escalate to Tier-2 support if:\n - SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number.\n - The customer's account shows \"OTP_BLOCKED\" status in the admin panel.\n - The gateway is operational but the customer insists on non-receipt (possible carrier-level block).\n - The customer reports receiving OTPs for a different account (cross-delivery incident — treat as a security incident). For cross-delivery incidents, immediately flag in Freshdesk with tag `security-incident` and notify the Security team at security@think360.ai.",
    "confidence": "high",
    "confidence_score": 0.89,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 3,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 4,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "0e67ff63-82cc-58d8-9b37-883783a938c2",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4642,
            "boosted_score": 0.6142,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "For SMS OTP failures:\n\n1. Open the customer's profile in Freshdesk and note the mobile number on record.\n2. Ask the customer to confirm the last 4 digits of their registered mobile number.\n3. If there is a mismatch, the customer must update their mobile number via the KYC re-verification flow before OTPs can be resent.\n4. If the number matches, proceed to Step 3.\n\n**Common issue**: Customer changed their SIM or phone number without updating the platform record. Resolution requires re-KYC with valid ID proof.",
            "retrieval_rank": 1,
            "rerank_score": 0.6142
        },
        {
            "chunk_id": "21841437-140f-5add-b84e-fe4327234e7b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4429,
            "boosted_score": 0.5929,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Before resending an OTP, verify the SMS gateway is operational:\n\n1. Check the internal status dashboard: **Ops > Integrations > SMS Gateway**.\n2. If gateway shows degraded or down: escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n3. If gateway is operational: proceed to Step 4.\n\nNote: SMS delivery can take up to 5 minutes during peak hours. Ask the customer to wait before concluding the OTP was not delivered.",
            "retrieval_rank": 2,
            "rerank_score": 0.5929
        },
        {
            "chunk_id": "2ef4a012-175c-5d67-940a-6f788190b50a",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4223,
            "boosted_score": 0.5723,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer confirms no OTP was received and the gateway is operational:\n\n1. Navigate to: **Customer Profile > Security > Resend OTP**.\n2. Select the correct delivery channel.\n3. Set a note in Freshdesk: \"OTP resent by agent at [timestamp]\".\n4. Inform the customer to check:\n   - SMS inbox and spam folder\n   - If using SMS: ensure the phone is not in DND (Do Not Disturb) mode\n   - If using email: check the spam/promotions folder\n\nMaximum resend attempts per hour: **3**. If this limit is reached, the account enters a 60-minute cooldown. Do not attempt further resends.",
            "retrieval_rank": 3,
            "rerank_score": 0.5723
        },
        {
            "chunk_id": "1dac802b-e598-56b2-a0e4-d776e7f6b103",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.396,
            "boosted_score": 0.546,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Escalate to Tier-2 support if:\n\n- SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number\n- The customer's account shows \"OTP_BLOCKED\" status in the admin panel\n- Gateway is operational but the customer insists on non-receipt (possible carrier-level block)\n- The customer reports receiving OTPs for a different account (cross-delivery incident — treat as security incident)\n\n**For cross-delivery incidents**: Immediately flag in Freshdesk with tag `security-incident` and notify the Security team at security@think360.ai.",
            "retrieval_rank": 4,
            "rerank_score": 0.546
        },
        {
            "chunk_id": "a94f4f0b-0bda-525c-8b40-560ba35d50c6",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3956,
            "boosted_score": 0.5456,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Determine which OTP delivery method was in use:\n\n- **SMS OTP**: Sent via registered mobile number\n- **Email OTP**: Sent to registered email address\n- **TOTP (Authenticator App)**: Time-based codes from apps like Google Authenticator\n- **WhatsApp OTP**: Sent via WhatsApp Business API\n\nAsk the customer: \"Which method were you expecting the OTP on?\"\n\nIf the customer is unsure, check their profile in the admin panel under: **Customer > Authentication Settings**.",
            "retrieval_rank": 5,
            "rerank_score": 0.5456
        },
        {
            "chunk_id": "0ff13a4f-7971-5cb7-b82b-505423c4133b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3782,
            "boosted_score": 0.5282,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "OTPs expire after **10 minutes** for SMS/email and **30 seconds** for TOTP.\n\nIf the customer received the OTP but it expired:\n\n1. Instruct the customer to request a new OTP from the login or verification screen.\n2. If the customer is unable to trigger the OTP from the app, use Step 4 to resend.\n3. Remind the customer to enter the OTP immediately after receiving it.",
            "retrieval_rank": 6,
            "rerank_score": 0.5282
        },
        {
            "chunk_id": "7c8f0a5d-2110-5842-bed2-9d5f2579fc3a",
            "ticket_id": "187726",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.5101,
            "boosted_score": 0.5101,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nHi Team, Kindly check—the user mentioned below is unable to initiate VKYC and is encountering the following error. Mob:9615346125 request_payload request_body response {\"traceId\": \"SC202604281203497002\", \"password\": \"unity_uat\", \"sourceId\": \"SC\", \"username\": \"unity_uat\"} {} {\"Status\":\"Fail\",\"StatusCode\":400,\"ErrorReason\":\"Bad Request\",\"FailedAt\":\"invoke-backendvkyc\",\"ErrorName\":\"OperationError\",\"ErrorCode\":\"USFB400\",\"ErrorMessage\":{\"type\":\"Buffer\",\"data\":[60,104,116,109,108,62,13,10,60,104,101,97,100,62,60,116,105,116,108,101,62,52,48,48,32,84,104,101,32,112,108,97,105,110,32,72,84,84,80,32,114,101,113,117,101,115,116,32,119,97,115,32,115,101,110,116,32,116,111,32,72,84,84,80,83,32,112,111,114,116,60,47,116,105,116,108,101,62,60,47,104,101,97,100,62,13,10,60,98,111,100,121…",
            "retrieval_rank": 7,
            "rerank_score": 0.5101
        },
        {
            "chunk_id": "92b4b4a2-79e9-5f67-9c45-f7cdf3c5bab1",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3599,
            "boosted_score": 0.5099,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "This procedure covers diagnosis and resolution of OTP (One-Time Password) delivery failures during KYC, login, and transaction verification flows. OTP failures are one of the most frequent support escalations in the KwikID platform.",
            "retrieval_rank": 8,
            "rerank_score": 0.5099
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2736.0,
        "total_latency_ms": 5279.4,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.5101,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 2125.0,
        "keyword_latency_ms": 417.7,
        "fusion_latency_ms": 0.1,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.6142,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1816,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "POLICY_COMPLIANCE",
        "routing_confidence": 0.25,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "KNOWLEDGE",
                "SOP_STEPS"
            ]
        }
    }
}
```

---

## Query 2:
```
    {
    "query_text": "The link in SMS is expired — can I get a new link?",
    "client": "unity_bank"
    }
```
## Response 2:
```
    {
    "session_id": "b47bb5fe-af6d-43b7-b03b-a6e271b9e2de",
    "message_id": "bdb89b8e-06e9-47f3-9e75-320e18e6ac41",
    "answer": "To request a new OTP link due to expiration:\n1. Instruct the customer to request a new OTP from the login or verification screen.\n2. If the customer is unable to trigger the OTP from the app, verify the SMS gateway is operational:\n - Check the internal status dashboard: Ops > Integrations > SMS Gateway.\n - If the gateway shows degraded or down, escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n - If the gateway is operational, proceed to resend the OTP.\n3. If the customer confirms no OTP was received and the gateway is operational:\n - Navigate to: Customer Profile > Security > Resend OTP.\n - Select the correct delivery channel.\n - Set a note in Freshdesk: \"OTP resent by agent at [timestamp].\"\n - Inform the customer to check their SMS inbox and spam folder, ensure their phone is not in DND mode, and check their email spam/promotions folder.\n - Maximum resend attempts per hour: 3. If this limit is reached, the account enters a 60-minute cooldown. Do not attempt further resends.\n4. If the customer changed their SIM or phone number without updating the platform record, they must re-KYC with valid ID proof before OTPs can be resent.\n5. Escalate to Tier-2 support if:\n - SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number.\n - The customer's account shows \"OTP_BLOCKED\" status in the admin panel.\n - The gateway is operational but the customer insists on non-receipt (possible carrier-level block).\n - The customer reports receiving OTPs for a different account (cross-delivery incident — treat as a security incident). Flag in Freshdesk with tag `security-incident` and notify the Security team at security@think360.ai.",
    "confidence": "high",
    "confidence_score": 0.92,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 4,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 3,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 5,
            "chunk_type": "SOP",
            "source_id": "b7819368-0f46-5a7b-a7a3-97892afbef67"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "0ff13a4f-7971-5cb7-b82b-505423c4133b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4201,
            "boosted_score": 0.5701,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "OTPs expire after **10 minutes** for SMS/email and **30 seconds** for TOTP.\n\nIf the customer received the OTP but it expired:\n\n1. Instruct the customer to request a new OTP from the login or verification screen.\n2. If the customer is unable to trigger the OTP from the app, use Step 4 to resend.\n3. Remind the customer to enter the OTP immediately after receiving it.",
            "retrieval_rank": 1,
            "rerank_score": 0.5701
        },
        {
            "chunk_id": "21841437-140f-5add-b84e-fe4327234e7b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3992,
            "boosted_score": 0.5492,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Before resending an OTP, verify the SMS gateway is operational:\n\n1. Check the internal status dashboard: **Ops > Integrations > SMS Gateway**.\n2. If gateway shows degraded or down: escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n3. If gateway is operational: proceed to Step 4.\n\nNote: SMS delivery can take up to 5 minutes during peak hours. Ask the customer to wait before concluding the OTP was not delivered.",
            "retrieval_rank": 2,
            "rerank_score": 0.5492
        },
        {
            "chunk_id": "0e67ff63-82cc-58d8-9b37-883783a938c2",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3824,
            "boosted_score": 0.5324,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "For SMS OTP failures:\n\n1. Open the customer's profile in Freshdesk and note the mobile number on record.\n2. Ask the customer to confirm the last 4 digits of their registered mobile number.\n3. If there is a mismatch, the customer must update their mobile number via the KYC re-verification flow before OTPs can be resent.\n4. If the number matches, proceed to Step 3.\n\n**Common issue**: Customer changed their SIM or phone number without updating the platform record. Resolution requires re-KYC with valid ID proof.",
            "retrieval_rank": 3,
            "rerank_score": 0.5324
        },
        {
            "chunk_id": "2ef4a012-175c-5d67-940a-6f788190b50a",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.38,
            "boosted_score": 0.53,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer confirms no OTP was received and the gateway is operational:\n\n1. Navigate to: **Customer Profile > Security > Resend OTP**.\n2. Select the correct delivery channel.\n3. Set a note in Freshdesk: \"OTP resent by agent at [timestamp]\".\n4. Inform the customer to check:\n   - SMS inbox and spam folder\n   - If using SMS: ensure the phone is not in DND (Do Not Disturb) mode\n   - If using email: check the spam/promotions folder\n\nMaximum resend attempts per hour: **3**. If this limit is reached, the account enters a 60-minute cooldown. Do not attempt further resends.",
            "retrieval_rank": 4,
            "rerank_score": 0.53
        },
        {
            "chunk_id": "1dac802b-e598-56b2-a0e4-d776e7f6b103",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3329,
            "boosted_score": 0.4829,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Escalate to Tier-2 support if:\n\n- SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number\n- The customer's account shows \"OTP_BLOCKED\" status in the admin panel\n- Gateway is operational but the customer insists on non-receipt (possible carrier-level block)\n- The customer reports receiving OTPs for a different account (cross-delivery incident — treat as security incident)\n\n**For cross-delivery incidents**: Immediately flag in Freshdesk with tag `security-incident` and notify the Security team at security@think360.ai.",
            "retrieval_rank": 5,
            "rerank_score": 0.4829
        },
        {
            "chunk_id": "a94f4f0b-0bda-525c-8b40-560ba35d50c6",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2805,
            "boosted_score": 0.4305,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Determine which OTP delivery method was in use:\n\n- **SMS OTP**: Sent via registered mobile number\n- **Email OTP**: Sent to registered email address\n- **TOTP (Authenticator App)**: Time-based codes from apps like Google Authenticator\n- **WhatsApp OTP**: Sent via WhatsApp Business API\n\nAsk the customer: \"Which method were you expecting the OTP on?\"\n\nIf the customer is unsure, check their profile in the admin panel under: **Customer > Authentication Settings**.",
            "retrieval_rank": 6,
            "rerank_score": 0.4305
        },
        {
            "chunk_id": "852e4a9c-5358-5672-86b5-4338434e9ee8",
            "ticket_id": "179989",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4247,
            "boosted_score": 0.4247,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 1 | Handling time: 45 minutes\n\n[ROOT CAUSE AND FIX]\nAs discussed with you regarding this issue, we have identified that HOTFOOT is sending an expiry duration of three days in the SendLink API. Because of this, once a session is created, it remains in the waiting state from the time of creation until the AGENT either accepts or rejects it. Since the session expiry is set to three days, the session continues to appear as waiting during this entire period. We also checked with our team and confirmed that we do not have any information on why a three-day expiry duration is being used. Hence, please check with the respective team regarding the logic behind setting the expiry duration to three days. The logs are attac…",
            "retrieval_rank": 7,
            "rerank_score": 0.4247
        },
        {
            "chunk_id": "bde7f9a0-7beb-5ef4-9523-74873471f437",
            "ticket_id": "171486",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.3706,
            "boosted_score": 0.3706,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 1 | Handling time: 1 minutes\n\n[ROOT CAUSE AND FIX]\nFor this we have did a deplyement to store the logs of sendlink",
            "retrieval_rank": 8,
            "rerank_score": 0.3706
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2353.3,
        "total_latency_ms": 3504.0,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.4247,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 728.9,
        "keyword_latency_ms": 421.6,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.5701,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1366,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 3:
```
    {
    "query_text": "The link in SMS is expired — can I get a new link?",
    "client": "unity_bank"
    }
```
## Response espected:
```
    {
    "response_type": "SOP_RESPONSE",
    "response": "I understand your link has expired. Let me help you get a new one.\n\nI will first check the available OTP channels for your account. Which method were you expecting the OTP on?\n\n* SMS\n* Email\n* TOTP (Authenticator App)\n* WhatsApp\n\nOnce I know this, I can guide you through the resend process or escalate if needed."
    }
```

---

## Query 4:
```
{
    "query_text": "OTP is not coming on my mobile number.",
    "client": "unity_bank"
}
```
## Response expected:
```
    {
        "response_type": "SOP_RESPONSE",
        "response": "I'm sorry to hear you're not receiving the OTP. Let me help you troubleshoot this.

First, let's identify the OTP method. Are you expecting:

* SMS OTP
* Email OTP
* TOTP (Authenticator App)
* WhatsApp OTP

Let me know which one, and I'll guide you through the next steps."
    }
```
## Response generated:
```
{
    "session_id": "112ec8c0-2390-48bd-81aa-6f4b66434d9b",
    "message_id": "6c2cb832-fb79-442a-896c-98b4806eccc3",
    "answer": "To address the issue of not receiving the OTP:\n\n1. Open the customer's profile in Freshdesk and confirm the mobile number on record.\n2. Ask the customer to verify the last 4 digits of their registered mobile number.\n - If there is a mismatch, instruct the customer to update their mobile number via the KYC re-verification flow before OTPs can be resent.\n3. If the number matches, check which OTP delivery method was expected (SMS, email, TOTP, or WhatsApp).\n4. If the customer confirms no OTP was received and the gateway is operational:\n - Navigate to Customer Profile > Security > Resend OTP.\n - Select the correct delivery channel.\n - Set a note in Freshdesk: \"OTP resent by agent at [timestamp].\"\n - Remind the customer to check their SMS inbox and spam folder, and ensure their phone is not in DND mode if using SMS.\n - If using email, advise them to check the spam/promotions folder.\n5. Maximum resend attempts per hour is 3. If this limit is reached, the account enters a 60-minute cooldown, and no further resends should be attempted.\n6. Before resending an OTP, verify the SMS gateway is operational:\n - Check the internal status dashboard: Ops > Integrations > SMS Gateway.\n - If the gateway shows degraded or down, escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n - If the gateway is operational, proceed to resend the OTP.\n7. If SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number, escalate to Tier-2 support. Also escalate if the customer's account shows \"OTP_BLOCKED\" status or if the customer insists on non-receipt despite the gateway being operational.",
    "confidence": "high",
    "confidence_score": 0.89,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 3,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 4,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 7,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 8,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "0e67ff63-82cc-58d8-9b37-883783a938c2",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5505,
            "boosted_score": 0.7005,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "For SMS OTP failures:\n\n1. Open the customer's profile in Freshdesk and note the mobile number on record.\n2. Ask the customer to confirm the last 4 digits of their registered mobile number.\n3. If there is a mismatch, the customer must update their mobile number via the KYC re-verification flow before OTPs can be resent.\n4. If the number matches, proceed to Step 3.\n\n**Common issue**: Customer changed their SIM or phone number without updating the platform record. Resolution requires re-KYC with valid ID proof.",
            "retrieval_rank": 1,
            "rerank_score": 0.7005
        },
        {
            "chunk_id": "0ff13a4f-7971-5cb7-b82b-505423c4133b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4686,
            "boosted_score": 0.6186,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "OTPs expire after **10 minutes** for SMS/email and **30 seconds** for TOTP.\n\nIf the customer received the OTP but it expired:\n\n1. Instruct the customer to request a new OTP from the login or verification screen.\n2. If the customer is unable to trigger the OTP from the app, use Step 4 to resend.\n3. Remind the customer to enter the OTP immediately after receiving it.",
            "retrieval_rank": 2,
            "rerank_score": 0.6186
        },
        {
            "chunk_id": "a94f4f0b-0bda-525c-8b40-560ba35d50c6",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4535,
            "boosted_score": 0.6035,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Determine which OTP delivery method was in use:\n\n- **SMS OTP**: Sent via registered mobile number\n- **Email OTP**: Sent to registered email address\n- **TOTP (Authenticator App)**: Time-based codes from apps like Google Authenticator\n- **WhatsApp OTP**: Sent via WhatsApp Business API\n\nAsk the customer: \"Which method were you expecting the OTP on?\"\n\nIf the customer is unsure, check their profile in the admin panel under: **Customer > Authentication Settings**.",
            "retrieval_rank": 3,
            "rerank_score": 0.6035
        },
        {
            "chunk_id": "2ef4a012-175c-5d67-940a-6f788190b50a",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4437,
            "boosted_score": 0.5937,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer confirms no OTP was received and the gateway is operational:\n\n1. Navigate to: **Customer Profile > Security > Resend OTP**.\n2. Select the correct delivery channel.\n3. Set a note in Freshdesk: \"OTP resent by agent at [timestamp]\".\n4. Inform the customer to check:\n   - SMS inbox and spam folder\n   - If using SMS: ensure the phone is not in DND (Do Not Disturb) mode\n   - If using email: check the spam/promotions folder\n\nMaximum resend attempts per hour: **3**. If this limit is reached, the account enters a 60-minute cooldown. Do not attempt further resends.",
            "retrieval_rank": 4,
            "rerank_score": 0.5937
        },
        {
            "chunk_id": "92b4b4a2-79e9-5f67-9c45-f7cdf3c5bab1",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4386,
            "boosted_score": 0.5886,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "This procedure covers diagnosis and resolution of OTP (One-Time Password) delivery failures during KYC, login, and transaction verification flows. OTP failures are one of the most frequent support escalations in the KwikID platform.",
            "retrieval_rank": 5,
            "rerank_score": 0.5886
        },
        {
            "chunk_id": "7b5ac63a-3d2b-503e-bc0e-443360894344",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.432,
            "boosted_score": 0.582,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer uses a TOTP authenticator app and codes are rejected:\n\n1. **Clock sync issue**: The most common cause. The device clock must be synchronized to the correct time. Ask the customer to:\n   - Android: Settings > General management > Date and time > Enable Automatic date and time\n   - iOS: Settings > General > Date & Time > Set Automatically\n2. After re-syncing, the TOTP should generate valid codes immediately.\n3. If the app was deleted or the device was reset: the customer must go through re-enrollment. This requires re-KYC.",
            "retrieval_rank": 6,
            "rerank_score": 0.582
        },
        {
            "chunk_id": "21841437-140f-5add-b84e-fe4327234e7b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4134,
            "boosted_score": 0.5634,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Before resending an OTP, verify the SMS gateway is operational:\n\n1. Check the internal status dashboard: **Ops > Integrations > SMS Gateway**.\n2. If gateway shows degraded or down: escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n3. If gateway is operational: proceed to Step 4.\n\nNote: SMS delivery can take up to 5 minutes during peak hours. Ask the customer to wait before concluding the OTP was not delivered.",
            "retrieval_rank": 7,
            "rerank_score": 0.5634
        },
        {
            "chunk_id": "1dac802b-e598-56b2-a0e4-d776e7f6b103",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4066,
            "boosted_score": 0.5566,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Escalate to Tier-2 support if:\n\n- SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number\n- The customer's account shows \"OTP_BLOCKED\" status in the admin panel\n- Gateway is operational but the customer insists on non-receipt (possible carrier-level block)\n- The customer reports receiving OTPs for a different account (cross-delivery incident — treat as security incident)\n\n**For cross-delivery incidents**: Immediately flag in Freshdesk with tag `security-incident` and notify the Security team at security@think360.ai.",
            "retrieval_rank": 8,
            "rerank_score": 0.5566
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 3994.6,
        "total_latency_ms": 6452.2,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.5505,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 1820.5,
        "keyword_latency_ms": 636.2,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.7005,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1179,
        "context_skipped_chunks": 0,
        "branch_completeness_warnings": [
            "SOP has denial/restriction branches — answer missing restriction language"
        ],
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 5:
```
{
    "query_text": "I entered OTP but it says invalid or expired.",
    "client": "unity_bank"
}
```
## Response:
```
{
    "session_id": "3afca658-c69a-4af4-a207-50bf95ebf356",
    "message_id": "5fa0e45b-f432-4c24-8130-5c25f0473b5c",
    "answer": "To address the invalid or expired OTP issue:\n1. Confirm with the customer which OTP delivery method they were expecting (SMS, email, TOTP, or WhatsApp).\n2. If the OTP was received but expired, instruct the customer to request a new OTP from the login or verification screen. Remind them to enter the OTP immediately after receiving it.\n3. If the customer is using a TOTP authenticator app and codes are rejected, check for a clock sync issue. Ask them to:\n - Android: Settings > General management > Date and time > Enable Automatic date and time\n - iOS: Settings > General > Date & Time > Set Automatically\n4. If the customer confirms no OTP was received and the gateway is operational:\n - Navigate to Customer Profile > Security > Resend OTP.\n - Select the correct delivery channel and note in Freshdesk: \"OTP resent by agent at [timestamp].\"\n - Advise the customer to check their SMS inbox, spam folder, and ensure their phone is not in DND mode if using SMS.\n5. Maximum resend attempts per hour is 3. If this limit is reached, the account enters a 60-minute cooldown.\n6. Before resending an OTP, verify the SMS gateway is operational:\n - Check the internal status dashboard: Ops > Integrations > SMS Gateway.\n - If the gateway shows degraded or down, escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n7. If SMS delivery fails across 3+ consecutive resend attempts, escalate to Tier-2 support. Also escalate if the customer reports receiving OTPs for a different account, flagging it as a security incident.",
    "confidence": "high",
    "confidence_score": 0.89,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 4,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 5,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 7,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 8,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "0ff13a4f-7971-5cb7-b82b-505423c4133b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5947,
            "boosted_score": 0.7447,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "OTPs expire after **10 minutes** for SMS/email and **30 seconds** for TOTP.\n\nIf the customer received the OTP but it expired:\n\n1. Instruct the customer to request a new OTP from the login or verification screen.\n2. If the customer is unable to trigger the OTP from the app, use Step 4 to resend.\n3. Remind the customer to enter the OTP immediately after receiving it.",
            "retrieval_rank": 1,
            "rerank_score": 0.7447
        },
        {
            "chunk_id": "92b4b4a2-79e9-5f67-9c45-f7cdf3c5bab1",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5123,
            "boosted_score": 0.6623,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "This procedure covers diagnosis and resolution of OTP (One-Time Password) delivery failures during KYC, login, and transaction verification flows. OTP failures are one of the most frequent support escalations in the KwikID platform.",
            "retrieval_rank": 2,
            "rerank_score": 0.6623
        },
        {
            "chunk_id": "0e67ff63-82cc-58d8-9b37-883783a938c2",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5116,
            "boosted_score": 0.6616,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "For SMS OTP failures:\n\n1. Open the customer's profile in Freshdesk and note the mobile number on record.\n2. Ask the customer to confirm the last 4 digits of their registered mobile number.\n3. If there is a mismatch, the customer must update their mobile number via the KYC re-verification flow before OTPs can be resent.\n4. If the number matches, proceed to Step 3.\n\n**Common issue**: Customer changed their SIM or phone number without updating the platform record. Resolution requires re-KYC with valid ID proof.",
            "retrieval_rank": 3,
            "rerank_score": 0.6616
        },
        {
            "chunk_id": "7b5ac63a-3d2b-503e-bc0e-443360894344",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5058,
            "boosted_score": 0.6558,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer uses a TOTP authenticator app and codes are rejected:\n\n1. **Clock sync issue**: The most common cause. The device clock must be synchronized to the correct time. Ask the customer to:\n   - Android: Settings > General management > Date and time > Enable Automatic date and time\n   - iOS: Settings > General > Date & Time > Set Automatically\n2. After re-syncing, the TOTP should generate valid codes immediately.\n3. If the app was deleted or the device was reset: the customer must go through re-enrollment. This requires re-KYC.",
            "retrieval_rank": 4,
            "rerank_score": 0.6558
        },
        {
            "chunk_id": "2ef4a012-175c-5d67-940a-6f788190b50a",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4921,
            "boosted_score": 0.6421,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer confirms no OTP was received and the gateway is operational:\n\n1. Navigate to: **Customer Profile > Security > Resend OTP**.\n2. Select the correct delivery channel.\n3. Set a note in Freshdesk: \"OTP resent by agent at [timestamp]\".\n4. Inform the customer to check:\n   - SMS inbox and spam folder\n   - If using SMS: ensure the phone is not in DND (Do Not Disturb) mode\n   - If using email: check the spam/promotions folder\n\nMaximum resend attempts per hour: **3**. If this limit is reached, the account enters a 60-minute cooldown. Do not attempt further resends.",
            "retrieval_rank": 5,
            "rerank_score": 0.6421
        },
        {
            "chunk_id": "a94f4f0b-0bda-525c-8b40-560ba35d50c6",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4691,
            "boosted_score": 0.6191,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Determine which OTP delivery method was in use:\n\n- **SMS OTP**: Sent via registered mobile number\n- **Email OTP**: Sent to registered email address\n- **TOTP (Authenticator App)**: Time-based codes from apps like Google Authenticator\n- **WhatsApp OTP**: Sent via WhatsApp Business API\n\nAsk the customer: \"Which method were you expecting the OTP on?\"\n\nIf the customer is unsure, check their profile in the admin panel under: **Customer > Authentication Settings**.",
            "retrieval_rank": 6,
            "rerank_score": 0.6191
        },
        {
            "chunk_id": "21841437-140f-5add-b84e-fe4327234e7b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4621,
            "boosted_score": 0.6121,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Before resending an OTP, verify the SMS gateway is operational:\n\n1. Check the internal status dashboard: **Ops > Integrations > SMS Gateway**.\n2. If gateway shows degraded or down: escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n3. If gateway is operational: proceed to Step 4.\n\nNote: SMS delivery can take up to 5 minutes during peak hours. Ask the customer to wait before concluding the OTP was not delivered.",
            "retrieval_rank": 7,
            "rerank_score": 0.6121
        },
        {
            "chunk_id": "1dac802b-e598-56b2-a0e4-d776e7f6b103",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3837,
            "boosted_score": 0.5337,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Escalate to Tier-2 support if:\n\n- SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number\n- The customer's account shows \"OTP_BLOCKED\" status in the admin panel\n- Gateway is operational but the customer insists on non-receipt (possible carrier-level block)\n- The customer reports receiving OTPs for a different account (cross-delivery incident — treat as security incident)\n\n**For cross-delivery incidents**: Immediately flag in Freshdesk with tag `security-incident` and notify the Security team at security@think360.ai.",
            "retrieval_rank": 8,
            "rerank_score": 0.5337
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 63,
        "semantic_latency_ms": 2058.8,
        "total_latency_ms": 3695.1,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.5947,
        "retrieval_mode": "hybrid",
        "used_fallback": false,
        "embedding_latency_ms": 1134.0,
        "keyword_latency_ms": 482.9,
        "fusion_latency_ms": 18.4,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 32,
        "fused_candidates": 63,
        "selected_rrf_k": 60,
        "overlap_count": 1,
        "overlap_ratio": 0.0159,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.7447,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1179,
        "context_skipped_chunks": 0,
        "branch_completeness_warnings": [
            "SOP has denial/restriction branches — answer missing restriction language"
        ],
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 6:
```
    {
    "query_text": "The page says “session expired” before I could finish.",
    "client": "unity_bank"
    }
```
## Response expected:
```
{
    "best_answers": [
        {
            "answer": "When a page expires, it means the system automatically logged you out for security reasons. This is normal behavior for financial applications. You can usually resolve this by simply **refreshing the page** or **logging in again**. If the issue continues, please let me know and I can escalate it for further investigation.",
            "confidence_score": 0.7836563664674389,
            "reasoning": "The query 'session expired' is matched to the SOP section 'Session Expiry Management' which explains that session expiry is a normal security feature and can be resolved by refreshing or logging in again. The answer is constructed directly from this SOP content without any fallbacks or heuristics. The confidence score is high because the retrieved SOP provides a direct and relevant solution to the user's problem.",
            "knowledge_type": "SOP",
            "relevant_sop_ids": [
                "3c9ed15c-f60d-50d9-bf2b-b989b565cc6b"
            ],
            "relevant_knowledge_ids": [],
            "fallback_used": false,
            "heuristics_applied": [],
            "grounding_source": "SOP: Session Expiry Management (3c9ed15c-f60d-50d9-bf2b-b989b565cc6b)",
            "confidence_rationale": "The answer is directly extracted from the SOP section that defines session expiry and provides a solution, making it highly reliable.",
            "branch_context": {
                "type": "SOP",
                "node_id": "node-5",
                "node_label": "SessionExpiryManagement",
                "sop_id": "3c9ed15c-f60d-50d9-bf2b-b989b565cc6b"
            },
            "contains_restrictions": false,
            "is_restricted": false,
            "escalation_reason": null,
            "metadata": {
                "confidence_components": {
                    "similarity": 0.275,
                    "grounding": 1,
                    "coverage": 1,
                    "novelty": 0.6758
                },
                "branch_context": {
                    "type": "SOP",
                    "node_id": "node-5",
                    "node_label": "SessionExpiryManagement",
                    "sop_id": "3c9ed15c-f60d-50d9-bf2b-b989b565cc6b"
                }
            },
            "workflow_match_type": "exact_match",
            "workflow_id": "workflow-1",
            "workflow_execution_id": "01c4885f-d1f7-7c30-a16c-72ea55768988",
            "workflow_step_id": "1"
        }
    ],
    "heuristics_data": {
        "top_matches": [
            {
                "id": "3c9ed15c-f60d-50d9-bf2b-b989b565cc6b:SessionExpiryManagement",
                "type": "SOP",
                "title": "Session Expiry Management",
                "score": 0.27500000000000007,
                "weight": 0.5,
                "chunks": [
                    {
                        "chunk_id": "e9b4b801-91e9-5861-990c-126fc76fa634",
                        "ticket_id": null,
                        "sop_id": "3c9ed15c-f60d-50d9-bf2b-b989b565cc6b",
                        "chunk_type": "SOP_STEPS",
                        "similarity": 0.275,
                        "boosted_score": 0.275,
                        "source_table": "rag_sop_chunks",
                        "has_rca": false,
                        "has_sop": true,
                        "content_preview": "Sessions expire automatically after a period of inactivity to protect customer data. When a session expires, users are redirected to the login page and must re-authenticate to continue. This is a standard security measure for financial applications.",
                        "retrieval_rank": 1,
                        "rerank_score": 0.275
                    }
                ]
            }
        ]
    },
    "diagnostic_summary": {
        "query_text": "The page says “session expired” before I could finish.",
        "embedding_latency_ms": 578.2,
        "retrieval_latency_ms": 3435.8,
        "total_latency_ms": 4830.9,
        "index_version": "v1",
        "returned_count": 1,
        "total_candidates": 8,
        "best_similarity": 0.275,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "semantic_candidates": 1,
        "keyword_candidates": 7,
        "threshold": 0.27,
        "top_k": 1,
        "v2_hnsw_used": true,
        "workflow_execution_id": "01c4885f-d1f7-7c30-a16c-72ea55768988",
        "workflow_step_id": "1",
        "workflow_match_type": "exact_match",
        "automation_safe": true,
        "automation_block_reason": null,
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY"
            ]
        }
    }
}
```
## Response generated:
```
{
    "session_id": "87f4e423-51fb-498f-889b-13b398906fa1",
    "message_id": "cbb9ccac-fb8a-4ec5-b542-11fe6775f7f6",
    "answer": "I wasn't able to find a dedicated workflow for handling session expiration, but based on the existing procedures, here's the closest applicable guidance:\n\n1. Determine at what point the session dropped:\n - Before connecting to an agent (pre-session failure)\n - During document verification (mid-session failure)\n - During liveness check / biometric capture (biometric failure)\n - After document capture but before agent sign-off (agent-side failure)\n\n2. Check for any error messages displayed, such as:\n - \"Session ended unexpectedly\" (network issue)\n - \"Camera not detected\" (device/permission issue)\n - \"Face not detected\" (lighting or positioning issue)\n - \"Agent disconnected\" (agent-side issue)\n\n3. If the session dropped after document capture:\n - Retrieve the session log using the session ID (if visible) via: **Admin > V-CIP Sessions > Search by Session ID**.\n - Check the session status:\n - **DOCS_CAPTURED / PENDING_AGENT_REVIEW**: Documents were saved; an agent can resume review without requiring the customer to restart.\n - **INCOMPLETE / NO_DATA**: The session must be restarted from the beginning.\n - If the session is in DOCS_CAPTURED status, notify the V-CIP operations team at vcip-ops@unity_bank.com to manually assign an agent for review.",
    "confidence": "medium",
    "confidence_score": 0.6,
    "requires_human": true,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 3,
            "chunk_type": "SOP",
            "source_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "fb36c86a-f6d7-598e-a28a-8f267f63eab7",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2976,
            "boosted_score": 0.4476,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Collect the following from the customer:\n\n1. **At what point did the session drop?**\n   - Before connecting to an agent (pre-session failure)\n   - During document verification (mid-session failure)\n   - During liveness check / biometric capture (biometric failure)\n   - After document capture but before agent sign-off (agent-side failure)\n\n2. **What error message, if any, was displayed?**\n   - \"Session ended unexpectedly\" → network issue\n   - \"Camera not detected\" → device/permission issue\n   - \"Face not detected\" → lighting or positioning issue\n   - \"Agent disconnected\" → agent-side issue\n\n3. **Was the session ID visible on screen?** (Format: VCIP-XXXXXXXX). If yes, note it for retrieval from the session log.",
            "retrieval_rank": 1,
            "rerank_score": 0.4476
        },
        {
            "chunk_id": "e88bbe31-cda1-5600-8957-e69cdeae29d9",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2907,
            "boosted_score": 0.4407,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the session dropped after document capture was completed:\n\n1. Retrieve the session log using the session ID (from Step 1) via: **Admin > V-CIP Sessions > Search by Session ID**.\n2. Check the session status:\n   - **DOCS_CAPTURED / PENDING_AGENT_REVIEW**: The documents were saved. An agent can resume review from the admin panel without requiring the customer to restart from scratch.\n   - **INCOMPLETE / NO_DATA**: The session must be restarted from the beginning.\n3. If the session is in DOCS_CAPTURED status: notify the V-CIP operations team at vcip-ops@unity_bank.com to manually assign an agent for review.",
            "retrieval_rank": 2,
            "rerank_score": 0.4407
        },
        {
            "chunk_id": "c192aa3b-f87d-58ff-9f85-aa13b3acb5c4",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2834,
            "boosted_score": 0.4334,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the agent disconnected from the session (agent-side failure):\n\n1. The session enters \"PENDING\" status for 5 minutes, during which a new agent can join.\n2. Check: **Admin > V-CIP Sessions > PENDING** for the session.\n3. If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.\n4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.",
            "retrieval_rank": 3,
            "rerank_score": 0.4334
        },
        {
            "chunk_id": "1e8bb084-f88a-573d-af40-fb88b484076f",
            "ticket_id": "164095",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4296,
            "boosted_score": 0.4296,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 164095\nSubject: session status is expiring\nCategory: Others > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 4,
            "rerank_score": 0.4296
        },
        {
            "chunk_id": "80e23a47-e4e1-59a5-a575-d599125214d3",
            "ticket_id": "161819",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.3968,
            "boosted_score": 0.3968,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 161819\nSubject: Session Expire\nClient: Unity Bank | Environment: Unknown | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 5,
            "rerank_score": 0.3968
        },
        {
            "chunk_id": "51a4d0f0-3ca3-5f16-a4b4-6ffcfee08599",
            "ticket_id": "172191",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.389,
            "boosted_score": 0.389,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 1 | Handling time: 2 minutes\n\n[ROOT CAUSE AND FIX]\nIncorrect session status in database.",
            "retrieval_rank": 6,
            "rerank_score": 0.389
        },
        {
            "chunk_id": "e0f3b8d2-3d32-5c44-8fdb-212b4f52b278",
            "ticket_id": "172437",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.3674,
            "boosted_score": 0.3674,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 2 | Handling time: 45 minutes\n\n[ROOT CAUSE AND FIX]\nAs per the logs, we have checked and found that the expired sessions occurred because users are not come for the vkyc with that session id. Hence, those sessions automatically expired. For the user abandonment cases, most of the calls were not connected to the agents or exited before connecting to the agents.",
            "retrieval_rank": 7,
            "rerank_score": 0.3674
        },
        {
            "chunk_id": "5c428205-10c0-53af-b673-83f8c56ba93a",
            "ticket_id": "153830",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.3654,
            "boosted_score": 0.3654,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 153830\nSubject: Sudden Spike in Session Expired Status - Roarbank\nClient: Unity Bank | Environment: Unknown | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 8,
            "rerank_score": 0.3654
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2065.8,
        "total_latency_ms": 3239.9,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.4296,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 843.1,
        "keyword_latency_ms": 330.7,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "related_match",
        "best_sop_score": 0.4476,
        "grounding_confidence": "medium",
        "exact_sop_match": false,
        "partial_match_detected": true,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1012,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "weak_workflow_coverage",
        "automation_safe": false,
        "automation_block_reason": "partial_workflow_coverage",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 7:
```
    {
    "query_text": "I cannot open the VKYC link on my phone.",
    "client": "unity_bank"
    }
```
## Response expected:
```
{
    "session_id": "3c9cf499-e64c-4f85-88b0-65070e3bbdf4",
    "message_id": "f028d95a-5d33-4572-918c-cfd0b4b8981f",
    "answer": "Please verify that you're using the correct link sent by the bank. You can also access the VKYC portal directly by going to [URL] and entering the session ID provided by the bank.",
    "confidence": "medium",
    "confidence_score": 0.5,
    "requires_human": true,
    "citations": [],
    "follow_up_question": "Which device are you using to access the link (Android/iOS/Desktop)?",
    "insufficient_context": false,
    "chunks": []
}
```
## Response generated:
```
{
    "session_id": "bdc035e2-626f-451e-a5bb-8f8f2b7f40a3",
    "message_id": "ec983278-5d49-4171-b1a9-6e656ea38992",
    "answer": "I wasn't able to find a dedicated procedure for troubleshooting VKYC link issues, but based on the V-CIP requirements, here are some steps to follow:\n\n1. Ensure that the device meets the minimum requirements for V-CIP:\n - Front-facing camera (minimum 5 MP recommended)\n - OS: Android 8.0+ or iOS 12.0+\n - If using a web browser, ensure it's Chrome 90+ or Safari 14+ with camera permissions.\n\n2. Check camera and microphone permissions:\n - **Android:** Go to Settings > Apps > [KwikID App] > Permissions and ensure Camera and Microphone are set to \"Allow\".\n - **iOS:** Go to Settings > Privacy & Security > Camera and enable for KwikID; then do the same for Microphone.\n\n3. If permissions were just enabled, the customer must restart the app and begin a new session, as the current session cannot be resumed.\n\n4. Verify the internet connection:\n - Ensure a stable connection with a minimum of 2 Mbps download and 1 Mbps upload speed.\n - Ask if the customer is on WiFi or mobile data, and if other video calls or streaming services are working.\n - If using a VPN, advise them to disable it as it can cause session instability.\n\nIf the device is incompatible or if issues persist, inform the customer to use a different device, as workarounds for device incompatibility are not permitted.",
    "confidence": "medium",
    "confidence_score": 0.57,
    "requires_human": true,
    "citations": [
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 4,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "77b1779d-fee9-5417-80a6-ac5804a0328b",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3441,
            "boosted_score": 0.4941,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Camera and microphone access are mandatory for V-CIP.\n\n**Android:**\n1. Settings > Apps > [KwikID App] > Permissions\n2. Ensure Camera and Microphone are set to \"Allow\"\n\n**iOS:**\n1. Settings > Privacy & Security > Camera → Enable for KwikID\n2. Settings > Privacy & Security > Microphone → Enable for KwikID\n\nIf permissions were just enabled, the customer must restart the app and begin a new session — the current session cannot be resumed.",
            "retrieval_rank": 1,
            "rerank_score": 0.4941
        },
        {
            "chunk_id": "29d78999-5ee1-5e0f-aee8-e762772239a0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3295,
            "boosted_score": 0.4795,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires:\n- Front-facing camera (minimum 5 MP recommended)\n- OS: Android 8.0+ or iOS 12.0+\n- Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions\n\nIf the device is incompatible: inform the customer to use a different device. Do not attempt to work around device incompatibility — the session quality will fail regulatory requirements.",
            "retrieval_rank": 2,
            "rerank_score": 0.4795
        },
        {
            "chunk_id": "3cc9eb4d-2b6d-50da-9ccd-4bf01ad82de0",
            "ticket_id": "128931",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.47,
            "boosted_score": 0.47,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 128931\nSubject: RE: Unable to do VKYC - browser not supported for MOTOROLA DEVICE USERS\nClient: Unity Bank | Environment: Unknown | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 3,
            "rerank_score": 0.47
        },
        {
            "chunk_id": "fe14a00e-e01b-5f6f-bac7-f6999d42720f",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3186,
            "boosted_score": 0.4686,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "This procedure applies to Video KYC (V-CIP — Video Customer Identification Process) session failures for Unity Bank customers. V-CIP is a regulated process governed by RBI guidelines. Session failures must be resolved promptly as failed sessions count against the customer's daily verification attempts.\n\n**Maximum V-CIP attempts per day**: 3. After 3 failures, the customer must wait 24 hours before retrying.",
            "retrieval_rank": 4,
            "rerank_score": 0.4686
        },
        {
            "chunk_id": "bfe1c6f9-3cef-5cc3-8c06-947a5da732e1",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2992,
            "boosted_score": 0.4492,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires a stable internet connection. Minimum requirements:\n- Download: 2 Mbps\n- Upload: 1 Mbps\n- Latency: < 300ms\n\nAsk the customer:\n- Are they on WiFi or mobile data?\n- Are other video calls or streaming services working?\n- Is VPN active? (VPN can cause session instability — ask them to disable it)\n\nIf the customer is on a poor network: advise them to find a stable WiFi connection and retry. **Do not count this as a platform failure.**",
            "retrieval_rank": 5,
            "rerank_score": 0.4492
        },
        {
            "chunk_id": "33658d4f-fa67-56cd-8d2c-2805c39995a8",
            "ticket_id": "139669",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4467,
            "boosted_score": 0.4467,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 139669\nSubject: RE: VKYC function is not working\nCategory: Connectivity Issue > Backend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 6,
            "rerank_score": 0.4467
        },
        {
            "chunk_id": "0e67ff63-82cc-58d8-9b37-883783a938c2",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2887,
            "boosted_score": 0.4387,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "For SMS OTP failures:\n\n1. Open the customer's profile in Freshdesk and note the mobile number on record.\n2. Ask the customer to confirm the last 4 digits of their registered mobile number.\n3. If there is a mismatch, the customer must update their mobile number via the KYC re-verification flow before OTPs can be resent.\n4. If the number matches, proceed to Step 3.\n\n**Common issue**: Customer changed their SIM or phone number without updating the platform record. Resolution requires re-KYC with valid ID proof.",
            "retrieval_rank": 7,
            "rerank_score": 0.4387
        },
        {
            "chunk_id": "f02774b5-6e8e-59dd-b053-7773a3f58e75",
            "ticket_id": "159211",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4363,
            "boosted_score": 0.4363,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 159211\nSubject: Unable to connected VKYC - b4525fb5-b605-40d7-99dc-ba59ff693ee8\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 8,
            "rerank_score": 0.4363
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2214.1,
        "total_latency_ms": 3217.4,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.47,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 701.6,
        "keyword_latency_ms": 301.4,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "related_match",
        "best_sop_score": 0.4941,
        "grounding_confidence": "medium",
        "exact_sop_match": false,
        "partial_match_detected": true,
        "sop_branch_flags": {
            "has_escalation_branches": false,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1014,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "weak_workflow_coverage",
        "automation_safe": false,
        "automation_block_reason": "partial_workflow_coverage",
        "query_route": "POLICY_COMPLIANCE",
        "routing_confidence": 0.25,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "KNOWLEDGE",
                "SOP_STEPS"
            ]
        }
    }
}
```

---

## Query 8:
```
{
    "query_text": "Camera permission is denied — how do I enable it?",
    "client": "unity_bank"
}
```
## Response:
```
{
    "session_id": "b92d99ad-0d84-4bd7-89c6-166d12827066",
    "message_id": "8f9a9265-df36-4533-a9cd-8e97e835b253",
    "answer": "To enable camera permissions for the KwikID app:\n1. For Android:\n - Go to Settings > Apps > [KwikID App] > Permissions.\n - Ensure both Camera and Microphone are set to \"Allow.\"\n\n2. For iOS:\n - Go to Settings > Privacy & Security > Camera and enable access for KwikID.\n - Go to Settings > Privacy & Security > Microphone and enable access for KwikID.\n\nIf permissions were just enabled, the customer must restart the app and begin a new session, as the current session cannot be resumed.",
    "confidence": "high",
    "confidence_score": 0.92,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "77b1779d-fee9-5417-80a6-ac5804a0328b",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.556,
            "boosted_score": 0.706,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Camera and microphone access are mandatory for V-CIP.\n\n**Android:**\n1. Settings > Apps > [KwikID App] > Permissions\n2. Ensure Camera and Microphone are set to \"Allow\"\n\n**iOS:**\n1. Settings > Privacy & Security > Camera → Enable for KwikID\n2. Settings > Privacy & Security > Microphone → Enable for KwikID\n\nIf permissions were just enabled, the customer must restart the app and begin a new session — the current session cannot be resumed.",
            "retrieval_rank": 1,
            "rerank_score": 0.706
        },
        {
            "chunk_id": "888b4546-23a3-56eb-af6c-e909f284b6f0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3457,
            "boosted_score": 0.4957,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "\"Face not detected\" errors are typically caused by poor lighting or incorrect positioning.\n\nResolution:\n1. Ask the customer to move to a well-lit area (natural light preferred; avoid backlighting from windows).\n2. Ensure the face is centered in the camera frame with no obstruction (glasses are acceptable; masks are not for V-CIP).\n3. Remove filters or beauty modes if using the camera through a third-party app.\n4. For liveness check: ensure the customer moves naturally and doesn't hold unnaturally still. The system expects head movement.",
            "retrieval_rank": 2,
            "rerank_score": 0.4957
        },
        {
            "chunk_id": "29d78999-5ee1-5e0f-aee8-e762772239a0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.343,
            "boosted_score": 0.493,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires:\n- Front-facing camera (minimum 5 MP recommended)\n- OS: Android 8.0+ or iOS 12.0+\n- Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions\n\nIf the device is incompatible: inform the customer to use a different device. Do not attempt to work around device incompatibility — the session quality will fail regulatory requirements.",
            "retrieval_rank": 3,
            "rerank_score": 0.493
        },
        {
            "chunk_id": "8db65382-a08a-5584-b6fa-b6acb0a8b7e1",
            "ticket_id": "128738",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.3392,
            "boosted_score": 0.3392,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nINTERNAL Hi Team, User ID- 7506137035 Product code - ONEFIN Reason- Unable to capture PAN card image & also back camera is not accessible  Thanks & Regards, [Unity Small Finance Bank Limited]  Juilee Chavan Program Manager - VKYC ________________________________ +91 9819836442 Unity Small Finance Bank Limited [Facebook] [Linkedin] [Twitter] [Instagram]  ________________________________ Disclaimer: This e-mail message is legally privileged, confidential and intended for the addressee only If you are not the intended receiver, please do not disclose, copy, circulate or in any other way use the information contained in this transmission. Such unauthorised use may be unlawful. Unintended recipients of this email are prohibited from disseminating, distributing, copying or using…",
            "retrieval_rank": 4,
            "rerank_score": 0.3392
        },
        {
            "chunk_id": "f0ef02dc-5271-5f47-a84f-b18d33b9066f",
            "ticket_id": "127728",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.3151,
            "boosted_score": 0.3151,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nINTERNAL Hi Team, Below are the sample cases wherein issue was being faced during VKYC sessions where users are unable to switch to back camera. This is impacting the customer experience , kindly help in resolving this at the earliest Thanks & Regards, [Unity Small Finance Bank Limited]  Juilee Chavan Program Manager - VKYC ________________________________ +91 9819836442 Unity Small Finance Bank Limited [Facebook] [Linkedin] [Twitter] [Instagram]  ________________________________ From: Swapnil Shreeram Dalvi   Sent: 10 June 2025 14:57 To: Juilee Sambhaji Chavan   Cc: VCIP Team Coach   Subject: Think 360 Issues Hi Juilee Maam, Issue we are facing on Think 360 Platform User id Reason 8977748222 Unable to switch back camera 7769941618 Unable to switch back camera 9140724079 U…",
            "retrieval_rank": 5,
            "rerank_score": 0.3151
        },
        {
            "chunk_id": "811c5dfd-fb3a-547c-816c-0e0aadd04769",
            "ticket_id": "125900",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.2751,
            "boosted_score": 0.2751,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nINTERNAL Hi Team, We have come across an issue in Think360 call recording for Auditor is not available in admin access. We have 2 user who has taken VKYC for 1 call only recording is available for 0.32 second and 2 call unable to view recording, but we can see user & customer recording. Request you to please check and assist on urgent basis. Session id 3eb0dd92-8356-4327-876f-4d0f6ab49d4b Session id 45bb151b-0d6f-43fd-a448-71bd8c78c5c1 Ovaies Merchant | Team Lead Disclaimer- This e-mail is classified as INTERNAL for the Banks internal classification purposes only. Irrespective of any such classification mentioned in the e-mail, all the contents of the email is confidential information of the Bank and you should not utilise or share any information in the e-mail with any th…",
            "retrieval_rank": 6,
            "rerank_score": 0.2751
        },
        {
            "chunk_id": "9e8814de-3ea3-56a9-b558-4477e4ab5a2c",
            "ticket_id": "133881",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.2726,
            "boosted_score": 0.2726,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nHi Rohit, For production support please mail at @KwikID Support Regards, Pintu Bhattacharya From: Rohit Kantilal Gindra   Sent: Thursday, July 10, 2025 2:30:09 PM To: Mohit Sawardekar  ; Pintu Bhattacharya  ; Swapnil Sabale   Cc: Ovaies Merchant  ; Prince Hasmukhbhai Jagda  ; Swarup Patro   Subject: RE: Admin access request Dear Team, I am unable to login into think360 as still showing error not a registered admin. I will be moving to turbhe location in next 2 days and need an access on same. Request to solve this issue on high priority. Warm Regards, Rohit Gindra Team Leader Roar Bank by USFB From: Rohit Kantilal Gindra Sent: Wednesday, July 9, 2025 10:22 AM To: mohit.sawardekar@think360.ai; Pintu Bhattacharya  ; swapnil.sabale@think360.ai Cc: Ovaies Merchant  ; Prince Ha…",
            "retrieval_rank": 7,
            "rerank_score": 0.2726
        },
        {
            "chunk_id": "c4021846-b2a2-5bf2-bbb2-2adad85afc6e",
            "ticket_id": "175595",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.2708,
            "boosted_score": 0.2708,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nDear team, Please provide Admin Access and remove Agent Access for the below user, Employee ID Name Mobile Number Email ID 1012803 Ramesh Gupta 9987480766 ramesh.gupta@unitybank.co.in  Thanks & Regards, G Bhuvana sri Mob: 8639613303 Disclaimer: This e-mail message is legally privileged, confidential and intended for the addressee only If you are not the intended receiver, please do not disclose, copy, circulate or in any other way use the information contained in this transmission. Such unauthorised use may be unlawful. Unintended recipients of this email are prohibited from disseminating, distributing, copying or using its contents. If you have received this message in error, please notify us immediately by email and delete the message. Internet communications may not be …",
            "retrieval_rank": 8,
            "rerank_score": 0.2708
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 8,
        "semantic_latency_ms": 2200.4,
        "total_latency_ms": 3054.6,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.556,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 588.3,
        "keyword_latency_ms": 265.7,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 8,
        "keyword_candidates": 0,
        "fused_candidates": 8,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.706,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": false,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 2720,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "SOP",
        "routing_confidence": 0.1667,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": true,
            "boost_knowledge": false,
            "chunk_types_hint": [
                "SOP_STEPS"
            ]
        }
    }
}
```

---

## Query 8:
```
{
    "query_text": "My camera is not working during the video call.",
    "client": "unity_bank"
}
```
## Response Expected:
```
{
    "session_id": "db61630a-4d1b-418b-8f8c-d213784c45e3",
    "message_id": "9a44c27e-6b86-4441-a403-7217c11215e3",
    "answer": "If the camera is not working during the video call, the most common reason is that permissions were denied when the app was first opened.\n\nTo fix this:\n1. Check if you accidentally tapped \"Don't allow\" for camera access.\n2. Go to your device settings and ensure both Camera and Microphone permissions are granted to the KwikID app.\n3. Restart the V-CIP process from the beginning.\n\nWould you like guidance on how to check permissions on your device?",
    "confidence": "high",
    "confidence_score": 0.89,
    "requires_human": false,
    "citations": [],
    "follow_up_question": "Would you like guidance on how to check permissions on your device?",
    "insufficient_context": false,
    "chunks": []
}
```
## Response Generated:
```
{
    "session_id": "bd9d143c-6c66-442c-9642-9ae88f220d2e",
    "message_id": "d05d34b0-859c-4f1d-b160-103c171933c3",
    "answer": "To troubleshoot the camera issue during the video call:\n1. Verify that the camera and microphone permissions are enabled:\n - **Android**: Go to Settings > Apps > [KwikID App] > Permissions and ensure Camera and Microphone are set to \"Allow\".\n - **iOS**: Go to Settings > Privacy & Security > Camera and enable for KwikID; then do the same for Microphone.\n2. If permissions were just enabled, the customer must restart the app and begin a new session.\n3. Check for \"Face not detected\" errors:\n - Ask the customer to move to a well-lit area (natural light preferred; avoid backlighting).\n - Ensure their face is centered in the camera frame with no obstructions.\n - Remove filters or beauty modes if using a third-party app.\n - For liveness checks, ensure the customer moves naturally.\n4. Confirm that the device meets V-CIP requirements:\n - Front-facing camera (minimum 5 MP recommended).\n - OS: Android 8.0+ or iOS 12.0+.\n - If using a browser, ensure it is Chrome 90+ or Safari 14+ with camera permissions.\n If the device is incompatible, inform the customer to use a different device.\n5. Check the internet connection:\n - Ask if they are on WiFi or mobile data, and if other video calls or streaming services are working.\n - Confirm if a VPN is active, as it can cause session instability.\n If the customer is on a poor network, advise them to find a stable WiFi connection and retry.\n6. If the session dropped after document capture:\n - Retrieve the session log using the session ID and check the session status. If in DOCS_CAPTURED status, notify the V-CIP operations team to manually assign an agent for review.",
    "confidence": "high",
    "confidence_score": 0.92,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 3,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 5,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 6,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "77b1779d-fee9-5417-80a6-ac5804a0328b",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4011,
            "boosted_score": 0.5511,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Camera and microphone access are mandatory for V-CIP.\n\n**Android:**\n1. Settings > Apps > [KwikID App] > Permissions\n2. Ensure Camera and Microphone are set to \"Allow\"\n\n**iOS:**\n1. Settings > Privacy & Security > Camera → Enable for KwikID\n2. Settings > Privacy & Security > Microphone → Enable for KwikID\n\nIf permissions were just enabled, the customer must restart the app and begin a new session — the current session cannot be resumed.",
            "retrieval_rank": 1,
            "rerank_score": 0.5511
        },
        {
            "chunk_id": "888b4546-23a3-56eb-af6c-e909f284b6f0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3915,
            "boosted_score": 0.5415,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "\"Face not detected\" errors are typically caused by poor lighting or incorrect positioning.\n\nResolution:\n1. Ask the customer to move to a well-lit area (natural light preferred; avoid backlighting from windows).\n2. Ensure the face is centered in the camera frame with no obstruction (glasses are acceptable; masks are not for V-CIP).\n3. Remove filters or beauty modes if using the camera through a third-party app.\n4. For liveness check: ensure the customer moves naturally and doesn't hold unnaturally still. The system expects head movement.",
            "retrieval_rank": 2,
            "rerank_score": 0.5415
        },
        {
            "chunk_id": "29d78999-5ee1-5e0f-aee8-e762772239a0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3524,
            "boosted_score": 0.5024,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires:\n- Front-facing camera (minimum 5 MP recommended)\n- OS: Android 8.0+ or iOS 12.0+\n- Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions\n\nIf the device is incompatible: inform the customer to use a different device. Do not attempt to work around device incompatibility — the session quality will fail regulatory requirements.",
            "retrieval_rank": 3,
            "rerank_score": 0.5024
        },
        {
            "chunk_id": "fb36c86a-f6d7-598e-a28a-8f267f63eab7",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3491,
            "boosted_score": 0.4991,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Collect the following from the customer:\n\n1. **At what point did the session drop?**\n   - Before connecting to an agent (pre-session failure)\n   - During document verification (mid-session failure)\n   - During liveness check / biometric capture (biometric failure)\n   - After document capture but before agent sign-off (agent-side failure)\n\n2. **What error message, if any, was displayed?**\n   - \"Session ended unexpectedly\" → network issue\n   - \"Camera not detected\" → device/permission issue\n   - \"Face not detected\" → lighting or positioning issue\n   - \"Agent disconnected\" → agent-side issue\n\n3. **Was the session ID visible on screen?** (Format: VCIP-XXXXXXXX). If yes, note it for retrieval from the session log.",
            "retrieval_rank": 4,
            "rerank_score": 0.4991
        },
        {
            "chunk_id": "bfe1c6f9-3cef-5cc3-8c06-947a5da732e1",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.331,
            "boosted_score": 0.481,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires a stable internet connection. Minimum requirements:\n- Download: 2 Mbps\n- Upload: 1 Mbps\n- Latency: < 300ms\n\nAsk the customer:\n- Are they on WiFi or mobile data?\n- Are other video calls or streaming services working?\n- Is VPN active? (VPN can cause session instability — ask them to disable it)\n\nIf the customer is on a poor network: advise them to find a stable WiFi connection and retry. **Do not count this as a platform failure.**",
            "retrieval_rank": 5,
            "rerank_score": 0.481
        },
        {
            "chunk_id": "e8b477c6-7643-5016-a2e3-9568a95e3e26",
            "ticket_id": "175572",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4587,
            "boosted_score": 0.4587,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 2\n\n[ROOT CAUSE AND FIX]\nas the video recording was not done correctly, resulting in a black screen. Also, please ask the agent pooja.varma@unitybank.co.in to restart the laptop or get it updated by the IT team.",
            "retrieval_rank": 6,
            "rerank_score": 0.4587
        },
        {
            "chunk_id": "e88bbe31-cda1-5600-8957-e69cdeae29d9",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3051,
            "boosted_score": 0.4551,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the session dropped after document capture was completed:\n\n1. Retrieve the session log using the session ID (from Step 1) via: **Admin > V-CIP Sessions > Search by Session ID**.\n2. Check the session status:\n   - **DOCS_CAPTURED / PENDING_AGENT_REVIEW**: The documents were saved. An agent can resume review from the admin panel without requiring the customer to restart from scratch.\n   - **INCOMPLETE / NO_DATA**: The session must be restarted from the beginning.\n3. If the session is in DOCS_CAPTURED status: notify the V-CIP operations team at vcip-ops@unity_bank.com to manually assign an agent for review.",
            "retrieval_rank": 7,
            "rerank_score": 0.4551
        },
        {
            "chunk_id": "fe14a00e-e01b-5f6f-bac7-f6999d42720f",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3037,
            "boosted_score": 0.4537,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "This procedure applies to Video KYC (V-CIP — Video Customer Identification Process) session failures for Unity Bank customers. V-CIP is a regulated process governed by RBI guidelines. Session failures must be resolved promptly as failed sessions count against the customer's daily verification attempts.\n\n**Maximum V-CIP attempts per day**: 3. After 3 failures, the customer must wait 24 hours before retrying.",
            "retrieval_rank": 8,
            "rerank_score": 0.4537
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 1676.6,
        "total_latency_ms": 3799.2,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.4587,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 1647.9,
        "keyword_latency_ms": 474.4,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.5511,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": false,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1226,
        "context_skipped_chunks": 0,
        "branch_completeness_warnings": [
            "SOP has denial/restriction branches — answer missing restriction language"
        ],
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "TROUBLESHOOTING",
        "routing_confidence": 0.125,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": true,
            "boost_knowledge": false,
            "chunk_types_hint": [
                "QUERY_BODY",
                "SOP_STEPS"
            ]
        }
    }
}
```

---

## Query 9:
```
{
    "query_text": "Microphone is not working — agent cannot hear me.",
    "client": "unity_bank"
}
```
## Response expected:
```
{
    "session_id": "2777371d-11b7-4826-bf96-ae3a1cdfa5a5",
    "message_id": "580ff670-0517-4727-8d20-b718771f49ef",
    "answer": "No dedicated SOP or procedure exists for addressing microphone issues where the agent cannot be heard. Please escalate this matter to a human agent for further assistance.",
    "confidence": "low",
    "confidence_score": 0.23,
    "requires_human": true,
    "citations": [],
    "follow_up_question": "What specific troubleshooting steps have already been attempted for the microphone issue?",
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "ee551313-d44e-5110-9b2c-61cc33f8af5e",
            "ticket_id": "125120",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5929,
            "boosted_score": 0.5929,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 125120\nSubject: Agent audio not audible in agent screen recording video - UAT\nCategory: Others > Frontend\nClient: Unity Bank | Environment: UAT | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 1,
            "rerank_score": 0.5929
        },
        {
            "chunk_id": "a3a0d014-2bfd-5917-853a-e567abdcb42a",
            "ticket_id": "154084",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5593,
            "boosted_score": 0.5593,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 154084\nSubject: Re: Agent not audible (T360)\nCategory: Audio Related > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 2,
            "rerank_score": 0.5593
        },
        {
            "chunk_id": "163b1875-5b8e-513b-8f9c-168b4e9c30dc",
            "ticket_id": "132012",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5507,
            "boosted_score": 0.5507,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 132012\nSubject: Re: Agent not audible (T360)\nCategory: Connectivity Issue > Frontend\nClient: Unity Bank | Environment: Production | Priority: Medium\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 3,
            "rerank_score": 0.5507
        },
        {
            "chunk_id": "e0cab2a8-a440-5a50-a08d-ac6183f7d843",
            "ticket_id": "160591",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5471,
            "boosted_score": 0.5471,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 160591\nSubject: Re: Agent is not audible\nCategory: Audio Related > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Auto-resolvable — SOP-backed, recurring, within SLA\nSOP Available: No | Recurring Issue: Yes",
            "retrieval_rank": 4,
            "rerank_score": 0.5471
        },
        {
            "chunk_id": "91efed15-25b1-5958-a4d4-c166f4bd4645",
            "ticket_id": "160590",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5464,
            "boosted_score": 0.5464,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 160590\nSubject: Re: Agent is not audible\nCategory: Audio Related > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Auto-resolvable — SOP-backed, recurring, within SLA\nSOP Available: No | Recurring Issue: Yes",
            "retrieval_rank": 5,
            "rerank_score": 0.5464
        },
        {
            "chunk_id": "a74683d9-2933-54c3-8a08-7ce4bad75aac",
            "ticket_id": "154408",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5438,
            "boosted_score": 0.5438,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 154408\nSubject: Re: Agent not audible (T360)\nCategory: Video Related > Backend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 6,
            "rerank_score": 0.5438
        },
        {
            "chunk_id": "de6e48a7-611e-5a94-8c63-1a96f83dc73a",
            "ticket_id": "161400",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5433,
            "boosted_score": 0.5433,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 161400\nSubject: RE: Agent is not audible\nCategory: Audio Related > Backend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Auto-resolvable — SOP-backed, recurring, within SLA\nSOP Available: No | Recurring Issue: Yes",
            "retrieval_rank": 7,
            "rerank_score": 0.5433
        },
        {
            "chunk_id": "ab07655a-7be8-5bff-8fe7-85433238197e",
            "ticket_id": "153574",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.542,
            "boosted_score": 0.542,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 153574\nSubject: Re: Agent not audible (tT360)\nCategory: Others > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 8,
            "rerank_score": 0.542
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2321.2,
        "total_latency_ms": 3715.4,
        "has_sop_context": false,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.5929,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 1007.7,
        "keyword_latency_ms": 386.2,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "weak_match",
        "best_sop_score": 0.0,
        "grounding_confidence": "medium",
        "exact_sop_match": false,
        "partial_match_detected": false,
        "context_tokens": 758,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "llm_flag",
        "automation_safe": false,
        "automation_block_reason": "llm_requested_human_review",
        "query_route": "TROUBLESHOOTING",
        "routing_confidence": 0.25,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": true,
            "boost_knowledge": false,
            "chunk_types_hint": [
                "QUERY_BODY",
                "SOP_STEPS"
            ]
        }
    }
}
```

---

## Query 10:
```
{
    "query_text": "Video call is not connecting at all.",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "4253b682-a0b7-453a-9178-cf64ae845a3b",
    "message_id": "eb2e65bd-0eba-4f3c-a5ad-ea831cef215c",
    "answer": "{\n \"answer\": \"To troubleshoot the video call connection issue, follow these steps:\\n\\n1. **Check Internet Connection:** Ensure the customer has a stable internet connection. The minimum requirements are:\\n - Download speed: 2 Mbps\\n - Upload speed: 1 Mbps\\n - Latency: less than 300 ms\\n\\n2. **Network Type:** Ask the customer if they are using WiFi or mobile data. If they are on a poor network, advise them to switch to a stable WiFi connection and retry. Do not count this as a platform failure.\\n\\n3. **Check Other Services:** Inquire if other video calls or streaming services are working properly.\\n\\n4. **VPN Status:** Ask if a VPN is active, as it can cause session instability. If so, instruct them to disable it.\\n\\n5. **Device Compatibility:** Confirm that the customer is using a compatible device:\\n - Front-facing camera: minimum 5 MP recommended\\n - OS: Android 8.0+ or iOS 12.0+\\n - Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions\\n If the device is incompatible, inform the customer to use a different device.\\n\\n6. **Session Details:** Collect the following information from the customer:\\n - At what point did the session drop? (pre-session failure, mid-session failure, biometric failure, agent-side failure)\\n - What error message, if any, was displayed? (e.g., \\\"Session ended unexpectedly\\\" indicates a network issue)\\n - Was the session ID visible on screen? (Format: VCIP-XXXXXXXX)\\n\\n7. **Camera and Microphone Access:** Ensure that camera and microphone permissions are enabled for the KwikID app. If permissions were just enabled, the customer must restart the app and begin a new session.\\n\\n8. **Lighting and Positioning:** If the error is \\\"Face not detected,\\\" advise the customer to:\\n - Move to a well-lit area (natural light preferred)\\n - Ensure their face is centered in the camera frame with no obstruction\\n - Remove filters or beauty modes if using a third-party app\\n - For liveness checks, ensure they move naturally and do not hold still.\\n\\n9. **Session Attempts:** Remind the customer that they can attempt the V-CIP process a maximum of 3 times per day. After 3 failures, they must wait 24 hours before retrying.\",\n \"confidence\": \"high\",\n \"citations\": [\n {\"chunk_num\": 1, \"chunk_type\": \"SOP\", \"source_id\": \"\n {\"chunk_num\": 2, \"chunk_type\": \"SOP\", \"source_id\": \"\n {\"chunk_num\": 3, \"chunk_type\": \"SOP\", \"source_id\": \"\n {\"chunk_num\": 4, \"chunk_type\": \"SOP\", \"source_id\": \"\n {\"chunk_num\": 5, \"chunk_type\": \"SOP\", \"source_id\": \"\n {\"chunk_num\": 6, \"chunk_type\": \"",
    "confidence": "low",
    "confidence_score": 0.25,
    "requires_human": true,
    "citations": [],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "bfe1c6f9-3cef-5cc3-8c06-947a5da732e1",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4055,
            "boosted_score": 0.5555,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires a stable internet connection. Minimum requirements:\n- Download: 2 Mbps\n- Upload: 1 Mbps\n- Latency: < 300ms\n\nAsk the customer:\n- Are they on WiFi or mobile data?\n- Are other video calls or streaming services working?\n- Is VPN active? (VPN can cause session instability — ask them to disable it)\n\nIf the customer is on a poor network: advise them to find a stable WiFi connection and retry. **Do not count this as a platform failure.**",
            "retrieval_rank": 1,
            "rerank_score": 0.5555
        },
        {
            "chunk_id": "29d78999-5ee1-5e0f-aee8-e762772239a0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3113,
            "boosted_score": 0.4613,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires:\n- Front-facing camera (minimum 5 MP recommended)\n- OS: Android 8.0+ or iOS 12.0+\n- Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions\n\nIf the device is incompatible: inform the customer to use a different device. Do not attempt to work around device incompatibility — the session quality will fail regulatory requirements.",
            "retrieval_rank": 2,
            "rerank_score": 0.4613
        },
        {
            "chunk_id": "fb36c86a-f6d7-598e-a28a-8f267f63eab7",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3072,
            "boosted_score": 0.4572,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Collect the following from the customer:\n\n1. **At what point did the session drop?**\n   - Before connecting to an agent (pre-session failure)\n   - During document verification (mid-session failure)\n   - During liveness check / biometric capture (biometric failure)\n   - After document capture but before agent sign-off (agent-side failure)\n\n2. **What error message, if any, was displayed?**\n   - \"Session ended unexpectedly\" → network issue\n   - \"Camera not detected\" → device/permission issue\n   - \"Face not detected\" → lighting or positioning issue\n   - \"Agent disconnected\" → agent-side issue\n\n3. **Was the session ID visible on screen?** (Format: VCIP-XXXXXXXX). If yes, note it for retrieval from the session log.",
            "retrieval_rank": 3,
            "rerank_score": 0.4572
        },
        {
            "chunk_id": "888b4546-23a3-56eb-af6c-e909f284b6f0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3025,
            "boosted_score": 0.4525,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "\"Face not detected\" errors are typically caused by poor lighting or incorrect positioning.\n\nResolution:\n1. Ask the customer to move to a well-lit area (natural light preferred; avoid backlighting from windows).\n2. Ensure the face is centered in the camera frame with no obstruction (glasses are acceptable; masks are not for V-CIP).\n3. Remove filters or beauty modes if using the camera through a third-party app.\n4. For liveness check: ensure the customer moves naturally and doesn't hold unnaturally still. The system expects head movement.",
            "retrieval_rank": 4,
            "rerank_score": 0.4525
        },
        {
            "chunk_id": "fe14a00e-e01b-5f6f-bac7-f6999d42720f",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2935,
            "boosted_score": 0.4435,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "This procedure applies to Video KYC (V-CIP — Video Customer Identification Process) session failures for Unity Bank customers. V-CIP is a regulated process governed by RBI guidelines. Session failures must be resolved promptly as failed sessions count against the customer's daily verification attempts.\n\n**Maximum V-CIP attempts per day**: 3. After 3 failures, the customer must wait 24 hours before retrying.",
            "retrieval_rank": 5,
            "rerank_score": 0.4435
        },
        {
            "chunk_id": "77b1779d-fee9-5417-80a6-ac5804a0328b",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.282,
            "boosted_score": 0.432,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Camera and microphone access are mandatory for V-CIP.\n\n**Android:**\n1. Settings > Apps > [KwikID App] > Permissions\n2. Ensure Camera and Microphone are set to \"Allow\"\n\n**iOS:**\n1. Settings > Privacy & Security > Camera → Enable for KwikID\n2. Settings > Privacy & Security > Microphone → Enable for KwikID\n\nIf permissions were just enabled, the customer must restart the app and begin a new session — the current session cannot be resumed.",
            "retrieval_rank": 6,
            "rerank_score": 0.432
        },
        {
            "chunk_id": "81ca07a6-d94d-565f-82ae-5c1735d1a8c2",
            "ticket_id": "169121",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.3923,
            "boosted_score": 0.3923,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nHi Team, Please check\n 405 Not Allowed\n\n nginx                  curl ^\"https://vkyc360.unitybank.co.in/hypertrail/e/^\" ^ -H ^\"accept: */*^\" ^ -H ^\"accept-language: en-GB,en-US;q=0.9,en;q=0.8^\" ^ -H ^\"auth: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1bml0eSIsImF1ZCI6InVzciIsImlhdCI6MTc2MTU2NDk4MywiZXhwIjoxNzYyMTY5NzgzLCJpc3MiOiJpcHZfYXBpX0lvbmljQXBwIn0.fe_Q3cUsWN6g3aBI08sqMjAhPTWr1yPj7S_624Nx4VM^\" ^ -H ^\"cache-control: no-cache^\" ^ -H ^\"content-type: application/json^\" ^ -H ^\"origin: https://vkyc360.unitybank.co.in^\" ^ -H ^\"pragma: no-cache^\" ^ -H ^\"priority: u=1, i^\" ^ -H ^\"referer: https://vkyc360.unitybank.co.in/instructions?stepKey=VKYC^\" ^ -H ^\"sec-ch-ua: ^\\^\"Google Chrome^\\^\";v=^\\^\"141^\\^\", ^\\^\"Not?A_Brand^\\^\";v=^\\^\"8^\\^\", ^\\^\"Chromium^\\^\";v=^\\^\"141^\\^\"^\" ^ -H ^\"…",
            "retrieval_rank": 7,
            "rerank_score": 0.3923
        },
        {
            "chunk_id": "8161ab1e-2416-5db8-9504-c3d0bb8032e1",
            "ticket_id": "170222",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.3914,
            "boosted_score": 0.3914,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": " connected 4ff7d140-04b6-41d9-95b7-35dc3b99cd85 Any Other Technical Issues Call not connected 9500a43f-3f7d-4b47-94d8-b5d53744b9cd Any Other Technical Issues Call not connected 8115b035-2244-4945-8147-d776350159c0 Any Other Technical Issues Call not connected 8eb54c28-d946-4b58-bbd5-e6a940edaf50 Any Other Technical Issues Call not connected d4a94107-e766-4f3e-8fb8-8e3ed56ffe2b Any Other Technical Issues Call not connected 67fc8502-51a2-4509-8eb9-75de743026a1 Any Other Technical Issues Call not connected 7bb9f7ab-000d-460a-ac0a-22efc2272406 Any Other Technical Issues Call not connected 242b7bc1-3fa0-4df1-b633-c78cc37dc7a1 Any Other Technical Issues Call not connected 211e9b51-0e8e-4180-8dee-b8609d3e551a Any Other Technical Issues Call not connected 10c41db4-e37d-4866-9b49-0eefe55364ee Any O…",
            "retrieval_rank": 8,
            "rerank_score": 0.3914
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2716.5,
        "total_latency_ms": 3712.2,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.4055,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 636.7,
        "keyword_latency_ms": 358.5,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.5555,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": false,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 3384,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "llm_flag",
        "automation_safe": false,
        "automation_block_reason": "llm_requested_human_review",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 11:
```
{
    "query_text": "Call keeps disconnecting in the middle.",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "b02a5feb-cb65-4c39-8292-fe0460afbe33",
    "message_id": "6df56dab-9335-4d0a-82f5-e045d3264446",
    "answer": "To address the call disconnection issue, follow these steps:\n\n1. **Check the session status:** If the agent disconnected from the session, it will enter a \"PENDING\" status for 5 minutes. During this time, check for agent availability.\n - Navigate to **Admin > V-CIP Sessions > PENDING** to see if a new agent can join.\n - If no agent is available within 5 minutes, the session will be marked as FAILED, and the customer must restart.\n - Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.\n\n2. **Verify the customer's internet connection:** Ensure the customer has a stable internet connection. Minimum requirements are:\n - Download: 2 Mbps\n - Upload: 1 Mbps\n - Latency: < 300ms\n - Ask the customer:\n - Are they on WiFi or mobile data?\n - Are other video calls or streaming services working?\n - Is a VPN active? (VPN can cause session instability — advise them to disable it.)\n - If the customer is on a poor network, advise them to find a stable WiFi connection and retry. **Do not count this as a platform failure.**\n\n3. **Collect detailed information from the customer:**\n - At what point did the session drop? (pre-session failure, mid-session failure, biometric failure, agent-side failure)\n - What error message, if any, was displayed? (e.g., \"Session ended unexpectedly\" indicates a network issue)\n - Was the session ID visible on screen? (Format: VCIP-XXXXXXXX). If yes, note it for retrieval from the session log.\n\n4. **Escalation conditions:** If the same session ID shows FAILED status despite all permissions and network being correct, or if customers report V-CIP failures during a specific time window, escalate to Platform Engineering (#vcip-issues in Slack).",
    "confidence": "high",
    "confidence_score": 0.92,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 4,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 3,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "c192aa3b-f87d-58ff-9f85-aa13b3acb5c4",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.4062,
            "boosted_score": 0.5562,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the agent disconnected from the session (agent-side failure):\n\n1. The session enters \"PENDING\" status for 5 minutes, during which a new agent can join.\n2. Check: **Admin > V-CIP Sessions > PENDING** for the session.\n3. If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.\n4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.",
            "retrieval_rank": 1,
            "rerank_score": 0.5562
        },
        {
            "chunk_id": "bfe1c6f9-3cef-5cc3-8c06-947a5da732e1",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3552,
            "boosted_score": 0.5052,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires a stable internet connection. Minimum requirements:\n- Download: 2 Mbps\n- Upload: 1 Mbps\n- Latency: < 300ms\n\nAsk the customer:\n- Are they on WiFi or mobile data?\n- Are other video calls or streaming services working?\n- Is VPN active? (VPN can cause session instability — ask them to disable it)\n\nIf the customer is on a poor network: advise them to find a stable WiFi connection and retry. **Do not count this as a platform failure.**",
            "retrieval_rank": 2,
            "rerank_score": 0.5052
        },
        {
            "chunk_id": "5fd65b69-6f66-5f5f-bfb5-dc25bd088ee9",
            "ticket_id": "170222",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4966,
            "boosted_score": 0.4966,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 170222\nSubject: Call not connected\nCategory: Call Connected to Multiple Agents > Integration\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 3,
            "rerank_score": 0.4966
        },
        {
            "chunk_id": "5b8bb3ff-bdab-5e18-81f2-e44514929a25",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3137,
            "boosted_score": 0.4637,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Escalate to Platform Engineering (#vcip-issues in Slack) if:\n\n- The same session ID shows FAILED status despite all permissions and network being correct\n- Customers report V-CIP failures during a specific time window (possible regional outage)\n- Liveness check fails repeatedly for a customer who is clearly visible (possible SDK bug)\n- Session count shows a customer has been charged an attempt for a platform-initiated failure (requires attempt count reset — requires L2 action)",
            "retrieval_rank": 4,
            "rerank_score": 0.4637
        },
        {
            "chunk_id": "fb36c86a-f6d7-598e-a28a-8f267f63eab7",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3117,
            "boosted_score": 0.4617,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Collect the following from the customer:\n\n1. **At what point did the session drop?**\n   - Before connecting to an agent (pre-session failure)\n   - During document verification (mid-session failure)\n   - During liveness check / biometric capture (biometric failure)\n   - After document capture but before agent sign-off (agent-side failure)\n\n2. **What error message, if any, was displayed?**\n   - \"Session ended unexpectedly\" → network issue\n   - \"Camera not detected\" → device/permission issue\n   - \"Face not detected\" → lighting or positioning issue\n   - \"Agent disconnected\" → agent-side issue\n\n3. **Was the session ID visible on screen?** (Format: VCIP-XXXXXXXX). If yes, note it for retrieval from the session log.",
            "retrieval_rank": 5,
            "rerank_score": 0.4617
        },
        {
            "chunk_id": "25d3512f-a4c1-5591-b864-2aaeee9403e0",
            "ticket_id": "156540",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.4543,
            "boosted_score": 0.4543,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nDear Team, This is to inform you that calls are being getting disconnected during the interval between 35 seconds and 50 seconds, from the customers end as in Think360 it is showing error as User Disconnected on call and this has happened for one of the officers, customer said that the application crashing repeatedly, please find some of the sample cases for your reference and solve on priority. Session ID Session status user id agent id agent feedback type Auditor Name Auditor Feedback Type Auditor Feedback Comment Product name start time End Time Duration f1a033c8-e064-451d-8e7a-65ec73cf834e kyc_result_rejected 8897287803 firdos.khan@unitybank.co.in  Reopen Customer Dropped Customer dropped the call FINTECHFARM 9/11/2025 17:22 9/11/2025 17:25 0:02:16 bdb6a3ac-b2a5-42ff-b…",
            "retrieval_rank": 6,
            "rerank_score": 0.4543
        },
        {
            "chunk_id": "734f5e19-5e03-564c-b60c-ea6b87446f97",
            "ticket_id": "156540",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.452,
            "boosted_score": 0.452,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 156540\nSubject: Think360 issue/ call getting disconnected\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Auto-resolvable — SOP-backed, recurring, within SLA\nSOP Available: No | Recurring Issue: Yes",
            "retrieval_rank": 7,
            "rerank_score": 0.452
        },
        {
            "chunk_id": "9de7d73c-7b71-561c-b8f4-c42f3a5f8612",
            "ticket_id": "180245",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4498,
            "boosted_score": 0.4498,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 180245\nSubject: After accepting calls, something went wrong - FF\nCategory: Connectivity Issue > Backend\nClient: Unity Bank | Environment: Production | Priority: Medium\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 8,
            "rerank_score": 0.4498
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 1800.3,
        "total_latency_ms": 2648.2,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.4966,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 602.2,
        "keyword_latency_ms": 245.3,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.5562,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1704,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 11:
```
{
    "query_text": "Screen is black during video call.",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "a96085e7-c387-4526-9846-642b9f3a90f4",
    "message_id": "57571097-8a6e-42e9-9f7b-9eb35018505b",
    "answer": "I wasn't able to find a dedicated procedure for resolving a black screen during a video call, but based on the existing guidance for video call issues, here are the steps you can take:\n\n1. **Check Camera Permissions:** Ensure that the customer has granted camera and microphone permissions for the KwikID app. For Android, navigate to Settings > Apps > [KwikID App] > Permissions and set both to 'Allow'. For iOS, go to Settings > Privacy & Security > Camera and Microphone, and enable access for KwikID.\n2. **Restart the App:** If permissions were just enabled, instruct the customer to restart the app and start a new session, as the current session cannot be resumed.\n3. **Verify Internet Connection:** Confirm that the customer has a stable internet connection. Ask if they are using WiFi or mobile data, and if other video calls or streaming services are functioning properly. If they are on a poor network, advise them to switch to a stable WiFi connection.\n4. **Lighting and Positioning:** If the issue persists, ask the customer to move to a well-lit area and ensure their face is centered in the camera frame with no obstructions. Avoid backlighting from windows.\n5. **Device Compatibility:** Ensure the customer is using a front-facing camera (minimum 5 MP recommended) and that their device meets the OS requirements (Android 8.0+ or iOS 12.0+). If the device is incompatible, inform them to use a different device.\n6. **Session Details:** Collect information about when the session dropped and any error messages displayed. This can help in diagnosing the issue further.",
    "confidence": "medium",
    "confidence_score": 0.6,
    "requires_human": true,
    "citations": [
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 3,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 4,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 5,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "e8b477c6-7643-5016-a2e3-9568a95e3e26",
            "ticket_id": "175572",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.537,
            "boosted_score": 0.537,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 2\n\n[ROOT CAUSE AND FIX]\nas the video recording was not done correctly, resulting in a black screen. Also, please ask the agent pooja.varma@unitybank.co.in to restart the laptop or get it updated by the IT team.",
            "retrieval_rank": 1,
            "rerank_score": 0.537
        },
        {
            "chunk_id": "888b4546-23a3-56eb-af6c-e909f284b6f0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3751,
            "boosted_score": 0.5251,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "\"Face not detected\" errors are typically caused by poor lighting or incorrect positioning.\n\nResolution:\n1. Ask the customer to move to a well-lit area (natural light preferred; avoid backlighting from windows).\n2. Ensure the face is centered in the camera frame with no obstruction (glasses are acceptable; masks are not for V-CIP).\n3. Remove filters or beauty modes if using the camera through a third-party app.\n4. For liveness check: ensure the customer moves naturally and doesn't hold unnaturally still. The system expects head movement.",
            "retrieval_rank": 2,
            "rerank_score": 0.5251
        },
        {
            "chunk_id": "77b1779d-fee9-5417-80a6-ac5804a0328b",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3657,
            "boosted_score": 0.5157,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Camera and microphone access are mandatory for V-CIP.\n\n**Android:**\n1. Settings > Apps > [KwikID App] > Permissions\n2. Ensure Camera and Microphone are set to \"Allow\"\n\n**iOS:**\n1. Settings > Privacy & Security > Camera → Enable for KwikID\n2. Settings > Privacy & Security > Microphone → Enable for KwikID\n\nIf permissions were just enabled, the customer must restart the app and begin a new session — the current session cannot be resumed.",
            "retrieval_rank": 3,
            "rerank_score": 0.5157
        },
        {
            "chunk_id": "bfe1c6f9-3cef-5cc3-8c06-947a5da732e1",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3509,
            "boosted_score": 0.5009,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires a stable internet connection. Minimum requirements:\n- Download: 2 Mbps\n- Upload: 1 Mbps\n- Latency: < 300ms\n\nAsk the customer:\n- Are they on WiFi or mobile data?\n- Are other video calls or streaming services working?\n- Is VPN active? (VPN can cause session instability — ask them to disable it)\n\nIf the customer is on a poor network: advise them to find a stable WiFi connection and retry. **Do not count this as a platform failure.**",
            "retrieval_rank": 4,
            "rerank_score": 0.5009
        },
        {
            "chunk_id": "29d78999-5ee1-5e0f-aee8-e762772239a0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3498,
            "boosted_score": 0.4998,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires:\n- Front-facing camera (minimum 5 MP recommended)\n- OS: Android 8.0+ or iOS 12.0+\n- Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions\n\nIf the device is incompatible: inform the customer to use a different device. Do not attempt to work around device incompatibility — the session quality will fail regulatory requirements.",
            "retrieval_rank": 5,
            "rerank_score": 0.4998
        },
        {
            "chunk_id": "fb36c86a-f6d7-598e-a28a-8f267f63eab7",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3218,
            "boosted_score": 0.4718,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Collect the following from the customer:\n\n1. **At what point did the session drop?**\n   - Before connecting to an agent (pre-session failure)\n   - During document verification (mid-session failure)\n   - During liveness check / biometric capture (biometric failure)\n   - After document capture but before agent sign-off (agent-side failure)\n\n2. **What error message, if any, was displayed?**\n   - \"Session ended unexpectedly\" → network issue\n   - \"Camera not detected\" → device/permission issue\n   - \"Face not detected\" → lighting or positioning issue\n   - \"Agent disconnected\" → agent-side issue\n\n3. **Was the session ID visible on screen?** (Format: VCIP-XXXXXXXX). If yes, note it for retrieval from the session log.",
            "retrieval_rank": 6,
            "rerank_score": 0.4718
        },
        {
            "chunk_id": "c4fe0aa0-abc8-5bbe-af47-8af335147c42",
            "ticket_id": "173766",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4637,
            "boosted_score": 0.4637,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 2 | Handling time: 35 minutes\n\n[ROOT CAUSE AND FIX]\nAs discussed, this issue occurred due to a network problem at that particular time, which resulted in a white screen as shown in the attached screenshot. This caused the agent to get confused and close the tab directly, which led to the video not being uploaded to the server. Hence, the video is not available. Please reinitiate the VKYC journey for the mentioned user.",
            "retrieval_rank": 7,
            "rerank_score": 0.4637
        },
        {
            "chunk_id": "7b743a17-fdb6-5af5-b853-4e8d0b3b9e21",
            "ticket_id": "176870",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4563,
            "boosted_score": 0.4563,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 2 | Handling time: 15 minutes\n\n[ROOT CAUSE AND FIX]\nThe video is not available for this session ID due to a hard reload performed while the call disposition was taking time. As a result, the video is not available. Please redo the VKYC for this customer.",
            "retrieval_rank": 8,
            "rerank_score": 0.4563
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 3054.4,
        "total_latency_ms": 5074.7,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.537,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 756.5,
        "keyword_latency_ms": 1263.6,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "related_match",
        "best_sop_score": 0.5251,
        "grounding_confidence": "medium",
        "exact_sop_match": false,
        "partial_match_detected": true,
        "sop_branch_flags": {
            "has_escalation_branches": false,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1172,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "weak_workflow_coverage",
        "automation_safe": false,
        "automation_block_reason": "partial_workflow_coverage",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 12:
```
{
    "query_text": "Only audio works, no video.",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "610286cb-11f4-47f3-8aa4-db41b669162c",
    "message_id": "11ec35fc-1b37-4d4e-8241-8af500f309a3",
    "answer": "No dedicated SOP or procedure exists for addressing the issue of audio working without video. This coverage gap indicates that specific troubleshooting steps or resolutions for this scenario are not available in the current knowledge base. A human agent should handle this situation to determine the appropriate next steps.",
    "confidence": "low",
    "confidence_score": 0.2,
    "requires_human": true,
    "citations": [],
    "follow_up_question": "What specific details can you provide about the environment or any recent changes that might have affected video functionality?",
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "ef2622b9-de78-577b-8d07-92179ce2070e",
            "ticket_id": "187594",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4197,
            "boosted_score": 0.4197,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 187594\nSubject: video is not visible in audior bucket\nCategory: Video Recovery > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: Yes | Recurring Issue: No",
            "retrieval_rank": 1,
            "rerank_score": 0.4197
        },
        {
            "chunk_id": "c2b3fbfb-c93d-5416-acc2-3a9c490e8dec",
            "ticket_id": "155827",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.3869,
            "boosted_score": 0.3869,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 155827\nSubject: RE: Audio not available (FINTECHFARM)\nCategory: Video Related > Backend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Auto-resolvable — SOP-backed, recurring, within SLA\nSOP Available: No | Recurring Issue: Yes",
            "retrieval_rank": 2,
            "rerank_score": 0.3869
        },
        {
            "chunk_id": "ee551313-d44e-5110-9b2c-61cc33f8af5e",
            "ticket_id": "125120",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.3856,
            "boosted_score": 0.3856,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 125120\nSubject: Agent audio not audible in agent screen recording video - UAT\nCategory: Others > Frontend\nClient: Unity Bank | Environment: UAT | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 3,
            "rerank_score": 0.3856
        },
        {
            "chunk_id": "c7218680-f4f8-55d3-a553-acf0f2148a6c",
            "ticket_id": "187696",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.3817,
            "boosted_score": 0.3817,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 187696\nSubject: test video not avialable\nCategory: Video Recovery > Frontend\nClient: Unity Bank | Environment: Unknown | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: Yes | Recurring Issue: No",
            "retrieval_rank": 4,
            "rerank_score": 0.3817
        },
        {
            "chunk_id": "31422932-ac26-5c54-a93a-a7a97815e19a",
            "ticket_id": "186634",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.3739,
            "boosted_score": 0.3739,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 186634\nSubject: Re: Audit pending Tech issue/ Customer audio not available (T360)\nCategory: Video Recovery > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: Yes | Recurring Issue: No",
            "retrieval_rank": 5,
            "rerank_score": 0.3739
        },
        {
            "chunk_id": "13e439e2-927a-543a-920e-d474373e9eaa",
            "ticket_id": "186633",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.366,
            "boosted_score": 0.366,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 186633\nSubject: Re: Audit pending Tech issue/ Customer audio not available (T360)\nCategory: Video Related > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: Yes | Recurring Issue: No",
            "retrieval_rank": 6,
            "rerank_score": 0.366
        },
        {
            "chunk_id": "2e8cf385-f391-5c92-beee-70a551baf856",
            "ticket_id": "179160",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.363,
            "boosted_score": 0.363,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 179160\nSubject: Re: Audit pending Tech issue Customer/Agent Audio not available (360)\nCategory: Video Recovery > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 7,
            "rerank_score": 0.363
        },
        {
            "chunk_id": "73972fbc-78a0-51bb-b6bc-b960aebe953c",
            "ticket_id": "186636",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.3616,
            "boosted_score": 0.3616,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 186636\nSubject: Re: Audit pending Tech issue/ Customer audio not available (T360)\nCategory: Video Recovery > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Auto-resolvable — SOP-backed, recurring, within SLA\nSOP Available: Yes | Recurring Issue: Yes",
            "retrieval_rank": 8,
            "rerank_score": 0.3616
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2206.7,
        "total_latency_ms": 4061.7,
        "has_sop_context": false,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.4197,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 1480.5,
        "keyword_latency_ms": 374.2,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "weak_match",
        "best_sop_score": 0.0,
        "grounding_confidence": "medium",
        "exact_sop_match": false,
        "partial_match_detected": false,
        "context_tokens": 780,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "llm_flag",
        "automation_safe": false,
        "automation_block_reason": "llm_requested_human_review",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 13:
```
{
    "query_text": "The app says “waiting for agent” for a long time.",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "69e68a27-41c0-4dcf-8f68-721f4338bb4f",
    "message_id": "5d7d53ac-8907-4149-acf5-f19232830e24",
    "answer": "If the app shows 'waiting for agent' for an extended period, follow these steps:\n\n1. Check if the session has entered 'PENDING' status. This occurs if the agent disconnected from the session. The session will remain in this status for 5 minutes, allowing a new agent to join.\n2. Navigate to **Admin > V-CIP Sessions > PENDING** to verify the session status.\n3. If no agent is available within 5 minutes, the session will be marked as FAILED, and the customer must restart the process.\n4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.\n\nIf the customer reports a security freeze:\n- Inform them that their account has been temporarily secured pending a review, which typically takes 24–48 business hours.\n- Create a high-priority ticket in Freshdesk tagged `security-freeze` and assign it to the Security team. Do not provide any details about why the freeze was triggered, and do not attempt to unlock the account via the admin panel, as this will be rejected at the database level. The Security team will contact the customer directly with next steps.",
    "confidence": "high",
    "confidence_score": 0.92,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "sop_id=1f99f2f4-dace-50dd-9b62-da4a1316d268"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "c192aa3b-f87d-58ff-9f85-aa13b3acb5c4",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5114,
            "boosted_score": 0.6614,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the agent disconnected from the session (agent-side failure):\n\n1. The session enters \"PENDING\" status for 5 minutes, during which a new agent can join.\n2. Check: **Admin > V-CIP Sessions > PENDING** for the session.\n3. If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.\n4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.",
            "retrieval_rank": 1,
            "rerank_score": 0.6614
        },
        {
            "chunk_id": "c9eef100-245c-548d-b877-4f0118633a81",
            "ticket_id": "161823",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5436,
            "boosted_score": 0.5436,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 161823\nSubject: Waitinf for Agents- 1faa2b14-9bc9-4fb0-adc1-7ac645480f12\nClient: Unity Bank | Environment: Unknown | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 2,
            "rerank_score": 0.5436
        },
        {
            "chunk_id": "2ea1ec0f-99f8-596f-b12a-3cfbd7dbf266",
            "ticket_id": null,
            "sop_id": "1f99f2f4-dace-50dd-9b62-da4a1316d268",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3471,
            "boosted_score": 0.4971,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Security freezes are NOT resolvable by front-line agents. Steps:\n\n1. Inform the customer: \"Your account has been temporarily secured pending a review. This typically takes 24–48 business hours.\"\n2. Create a high-priority ticket in Freshdesk tagged `security-freeze` and assign to the Security team.\n3. Do NOT provide the customer any details about why the freeze was triggered.\n4. Do NOT attempt to unlock via the admin panel — this will be rejected at the database level.\n\nThe Security team will contact the customer directly with next steps.",
            "retrieval_rank": 3,
            "rerank_score": 0.4971
        },
        {
            "chunk_id": "7ad584d4-b481-540e-bc56-cf8f1295b970",
            "ticket_id": "176843",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4959,
            "boosted_score": 0.4959,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 0 | Handling time: 120 minutes\n\n[ROOT CAUSE AND FIX]\ngiven new feature report for agent productivity",
            "retrieval_rank": 4,
            "rerank_score": 0.4959
        },
        {
            "chunk_id": "21841437-140f-5add-b84e-fe4327234e7b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3437,
            "boosted_score": 0.4937,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Before resending an OTP, verify the SMS gateway is operational:\n\n1. Check the internal status dashboard: **Ops > Integrations > SMS Gateway**.\n2. If gateway shows degraded or down: escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n3. If gateway is operational: proceed to Step 4.\n\nNote: SMS delivery can take up to 5 minutes during peak hours. Ask the customer to wait before concluding the OTP was not delivered.",
            "retrieval_rank": 5,
            "rerank_score": 0.4937
        },
        {
            "chunk_id": "7b5ac63a-3d2b-503e-bc0e-443360894344",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3408,
            "boosted_score": 0.4908,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer uses a TOTP authenticator app and codes are rejected:\n\n1. **Clock sync issue**: The most common cause. The device clock must be synchronized to the correct time. Ask the customer to:\n   - Android: Settings > General management > Date and time > Enable Automatic date and time\n   - iOS: Settings > General > Date & Time > Set Automatically\n2. After re-syncing, the TOTP should generate valid codes immediately.\n3. If the app was deleted or the device was reset: the customer must go through re-enrollment. This requires re-KYC.",
            "retrieval_rank": 6,
            "rerank_score": 0.4908
        },
        {
            "chunk_id": "48a6079b-4838-549e-9bcc-ec1a9378e9ca",
            "ticket_id": "181253",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4853,
            "boosted_score": 0.4853,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 181253\nSubject: Re: Agent screen not available for verification\nCategory: Video Related > Frontend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 7,
            "rerank_score": 0.4853
        },
        {
            "chunk_id": "cab262c1-72f1-5765-aecd-9a8113ab9707",
            "ticket_id": "165369",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4853,
            "boosted_score": 0.4853,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 2\n\n[ROOT CAUSE AND FIX]\nWhen agent is inactive log time in disposition state then this issue get occurs",
            "retrieval_rank": 8,
            "rerank_score": 0.4853
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2034.5,
        "total_latency_ms": 3874.0,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.5436,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 1556.9,
        "keyword_latency_ms": 282.2,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.6614,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": true,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 988,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 14:
```
{
    "query_text": "No agent joined even after waiting 10+ minutes.",
    "client": "unity_bank"
}
```
## Response generatd:
```
{
    "session_id": "0a7682a9-39cd-4166-80f3-d661825418a5",
    "message_id": "d9339458-5e19-49d8-a664-4f92b1937665",
    "answer": "If the agent disconnected from the session (agent-side failure):\n1. The session enters 'PENDING' status for 5 minutes, during which a new agent can join.\n2. Check: Admin > V-CIP Sessions > PENDING for the session.\n3. If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.\n4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.",
    "confidence": "high",
    "confidence_score": 0.92,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "c192aa3b-f87d-58ff-9f85-aa13b3acb5c4",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5507,
            "boosted_score": 0.7007,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the agent disconnected from the session (agent-side failure):\n\n1. The session enters \"PENDING\" status for 5 minutes, during which a new agent can join.\n2. Check: **Admin > V-CIP Sessions > PENDING** for the session.\n3. If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.\n4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.",
            "retrieval_rank": 1,
            "rerank_score": 0.7007
        },
        {
            "chunk_id": "b911bd95-49d5-5523-98bb-9758fd75db0b",
            "ticket_id": "175352",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.5109,
            "boosted_score": 0.5109,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": true,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 2 | Handling time: 45 minutes\n\n[ROOT CAUSE AND FIX]\nused script https://stackoverflowteams.com/c/kwikid/questions/1397 to remove agents ids",
            "retrieval_rank": 2,
            "rerank_score": 0.5109
        },
        {
            "chunk_id": "24dc2213-adcf-51ac-ace8-9bc0e29744f4",
            "ticket_id": "179178",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.508,
            "boosted_score": 0.508,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 3 | Handling time: 40 minutes\n\n[ROOT CAUSE AND FIX]\nupdating the required fields from the admin panel as per the new option of the edit agents",
            "retrieval_rank": 3,
            "rerank_score": 0.508
        },
        {
            "chunk_id": "7ad584d4-b481-540e-bc56-cf8f1295b970",
            "ticket_id": "176843",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.5045,
            "boosted_score": 0.5045,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 0 | Handling time: 120 minutes\n\n[ROOT CAUSE AND FIX]\ngiven new feature report for agent productivity",
            "retrieval_rank": 4,
            "rerank_score": 0.5045
        },
        {
            "chunk_id": "cab262c1-72f1-5765-aecd-9a8113ab9707",
            "ticket_id": "165369",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4974,
            "boosted_score": 0.4974,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 2\n\n[ROOT CAUSE AND FIX]\nWhen agent is inactive log time in disposition state then this issue get occurs",
            "retrieval_rank": 5,
            "rerank_score": 0.4974
        },
        {
            "chunk_id": "c9eef100-245c-548d-b877-4f0118633a81",
            "ticket_id": "161823",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4947,
            "boosted_score": 0.4947,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 161823\nSubject: Waitinf for Agents- 1faa2b14-9bc9-4fb0-adc1-7ac645480f12\nClient: Unity Bank | Environment: Unknown | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 6,
            "rerank_score": 0.4947
        },
        {
            "chunk_id": "2fc521dc-0667-5dca-9305-6894136f13d1",
            "ticket_id": "174032",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4885,
            "boosted_score": 0.4885,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 3 | Handling time: 50 minutes\n\n[ROOT CAUSE AND FIX]\n​As per the logs checked, we found that the issue occurred because the agent did not wait for the complete video to upload to the server, and therefore the video is not available. Please ask the user to connect with me on Teams along with the TL, Sujata, so that we can verify whether the video is available on the agent’s laptop. If the video is not available, the VKYC will need to be reinitiated.",
            "retrieval_rank": 7,
            "rerank_score": 0.4885
        },
        {
            "chunk_id": "9ab8d8c8-02e7-5341-b616-f52285846f6a",
            "ticket_id": "180110",
            "sop_id": null,
            "chunk_type": "RESOLUTION_RCA",
            "similarity": 0.4827,
            "boosted_score": 0.4827,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": true,
            "content_preview": "[TROUBLESHOOTING AND RESOLUTION]\nStatus: Within SLA | Interactions to resolve: 0 | Handling time: 35 minutes\n\n[ROOT CAUSE AND FIX]\nAgent productivity report bug improvements",
            "retrieval_rank": 8,
            "rerank_score": 0.4827
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2641.8,
        "total_latency_ms": 3605.3,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.5507,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 693.1,
        "keyword_latency_ms": 270.1,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.7007,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 829,
        "context_skipped_chunks": 0,
        "branch_completeness_warnings": [
            "SOP has denial/restriction branches — answer missing restriction language"
        ],
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 15:
```
{
    "query_text": "I entered wrong mobile number — how to correct it?",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "d1a0e511-d3f0-46a1-a1f6-6d6b4e6ef9eb",
    "message_id": "c3c4b152-849e-4030-97dd-31dc5ac4ece4",
    "answer": "To correct the mobile number, follow these steps:\n\n1. Open the customer's profile in Freshdesk and note the current mobile number on record.\n2. Ask the customer to confirm the last 4 digits of their registered mobile number.\n3. If there is a mismatch, the customer must update their mobile number via the KYC re-verification flow before any OTPs can be resent. This requires valid ID proof.\n4. If the number matches, you can proceed with the next steps as needed.\n\n**Important:** If the customer denies responsibility for any failed attempts or if there are unusual login attempts, escalate to the Security team. If only one verification factor can be confirmed, do not unlock the account and instruct the customer to visit the nearest branch with original ID documents. If zero factors match, close the ticket and notify the Security team as a suspected account takeover attempt.\n\nAfter verifying the identity, log the verification details in Freshdesk: \"Identity verified via [Factor 1] and [Factor 2] at [timestamp].\"",
    "confidence": "high",
    "confidence_score": 0.89,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=b7819368-0f46-5a7b-a7a3-97892afbef67"
        },
        {
            "chunk_num": 8,
            "chunk_type": "SOP",
            "source_id": "sop_id=1f99f2f4-dace-50dd-9b62-da4a1316d268"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "0e67ff63-82cc-58d8-9b37-883783a938c2",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5186,
            "boosted_score": 0.6686,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "For SMS OTP failures:\n\n1. Open the customer's profile in Freshdesk and note the mobile number on record.\n2. Ask the customer to confirm the last 4 digits of their registered mobile number.\n3. If there is a mismatch, the customer must update their mobile number via the KYC re-verification flow before OTPs can be resent.\n4. If the number matches, proceed to Step 3.\n\n**Common issue**: Customer changed their SIM or phone number without updating the platform record. Resolution requires re-KYC with valid ID proof.",
            "retrieval_rank": 1,
            "rerank_score": 0.6686
        },
        {
            "chunk_id": "2ef4a012-175c-5d67-940a-6f788190b50a",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.38,
            "boosted_score": 0.53,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer confirms no OTP was received and the gateway is operational:\n\n1. Navigate to: **Customer Profile > Security > Resend OTP**.\n2. Select the correct delivery channel.\n3. Set a note in Freshdesk: \"OTP resent by agent at [timestamp]\".\n4. Inform the customer to check:\n   - SMS inbox and spam folder\n   - If using SMS: ensure the phone is not in DND (Do Not Disturb) mode\n   - If using email: check the spam/promotions folder\n\nMaximum resend attempts per hour: **3**. If this limit is reached, the account enters a 60-minute cooldown. Do not attempt further resends.",
            "retrieval_rank": 2,
            "rerank_score": 0.53
        },
        {
            "chunk_id": "1dac802b-e598-56b2-a0e4-d776e7f6b103",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3629,
            "boosted_score": 0.5129,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Escalate to Tier-2 support if:\n\n- SMS delivery fails across 3+ consecutive resend attempts for a verified mobile number\n- The customer's account shows \"OTP_BLOCKED\" status in the admin panel\n- Gateway is operational but the customer insists on non-receipt (possible carrier-level block)\n- The customer reports receiving OTPs for a different account (cross-delivery incident — treat as security incident)\n\n**For cross-delivery incidents**: Immediately flag in Freshdesk with tag `security-incident` and notify the Security team at security@think360.ai.",
            "retrieval_rank": 3,
            "rerank_score": 0.5129
        },
        {
            "chunk_id": "a94f4f0b-0bda-525c-8b40-560ba35d50c6",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3399,
            "boosted_score": 0.4899,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Determine which OTP delivery method was in use:\n\n- **SMS OTP**: Sent via registered mobile number\n- **Email OTP**: Sent to registered email address\n- **TOTP (Authenticator App)**: Time-based codes from apps like Google Authenticator\n- **WhatsApp OTP**: Sent via WhatsApp Business API\n\nAsk the customer: \"Which method were you expecting the OTP on?\"\n\nIf the customer is unsure, check their profile in the admin panel under: **Customer > Authentication Settings**.",
            "retrieval_rank": 4,
            "rerank_score": 0.4899
        },
        {
            "chunk_id": "21841437-140f-5add-b84e-fe4327234e7b",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3363,
            "boosted_score": 0.4863,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Before resending an OTP, verify the SMS gateway is operational:\n\n1. Check the internal status dashboard: **Ops > Integrations > SMS Gateway**.\n2. If gateway shows degraded or down: escalate to the Platform Engineering team on Slack (#infra-alerts) and inform the customer of a temporary delay.\n3. If gateway is operational: proceed to Step 4.\n\nNote: SMS delivery can take up to 5 minutes during peak hours. Ask the customer to wait before concluding the OTP was not delivered.",
            "retrieval_rank": 5,
            "rerank_score": 0.4863
        },
        {
            "chunk_id": "7b5ac63a-3d2b-503e-bc0e-443360894344",
            "ticket_id": null,
            "sop_id": "b7819368-0f46-5a7b-a7a3-97892afbef67",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3288,
            "boosted_score": 0.4788,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the customer uses a TOTP authenticator app and codes are rejected:\n\n1. **Clock sync issue**: The most common cause. The device clock must be synchronized to the correct time. Ask the customer to:\n   - Android: Settings > General management > Date and time > Enable Automatic date and time\n   - iOS: Settings > General > Date & Time > Set Automatically\n2. After re-syncing, the TOTP should generate valid codes immediately.\n3. If the app was deleted or the device was reset: the customer must go through re-enrollment. This requires re-KYC.",
            "retrieval_rank": 6,
            "rerank_score": 0.4788
        },
        {
            "chunk_id": "882d055e-fa6c-56a6-b76f-2f99e4f8a100",
            "ticket_id": null,
            "sop_id": "1f99f2f4-dace-50dd-9b62-da4a1316d268",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3046,
            "boosted_score": 0.4546,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Escalate to Security team if:\n\n- Customer denies responsibility for the failed attempts\n- Account shows login attempts from foreign IP addresses or unusual devices\n- Customer reports receiving lockout notifications for attempts they did not make\n- Multiple accounts with the same mobile number or email are locked simultaneously (credential stuffing pattern)\n- Security Freeze status (as per Step 5)\n\nEscalations must include: account ID, lockout timestamp, IP addresses from the failed attempts log (available in **Admin > Security Logs**), and any information the customer provided about their location at the time.",
            "retrieval_rank": 7,
            "rerank_score": 0.4546
        },
        {
            "chunk_id": "5d3b1932-7a5f-5fdb-b701-279e493efab5",
            "ticket_id": null,
            "sop_id": "1f99f2f4-dace-50dd-9b62-da4a1316d268",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3002,
            "boosted_score": 0.4502,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Before taking any action on a locked account, verify the customer's identity using **two** of the following:\n\n1. Full name (exact match on record)\n2. Date of birth\n3. Registered mobile number (last 4 digits)\n4. Registered email address\n5. Last 4 digits of PAN / Aadhaar (if KYC completed)\n\n**If only one factor can be verified**: Do not unlock. Ask the customer to visit the nearest branch with original ID documents.\n\n**If zero factors match**: Close the ticket and mark as suspected account takeover attempt. Notify the Security team.\n\nLog the identity verification in Freshdesk: \"Identity verified via [Factor 1] and [Factor 2] at [timestamp].\"",
            "retrieval_rank": 8,
            "rerank_score": 0.4502
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2501.3,
        "total_latency_ms": 3387.1,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.5186,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 711.3,
        "keyword_latency_ms": 174.3,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.6686,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": true,
            "has_post_resolution": true,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1328,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "SOP",
        "routing_confidence": 0.1667,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": true,
            "boost_knowledge": false,
            "chunk_types_hint": [
                "SOP_STEPS"
            ]
        }
    }
}
```

## Query 16:
```
{
    "query_text": "Call connected but agent screen is blank.",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "e840dddf-ac44-4e10-a36e-7988509c3fa2",
    "message_id": "440728b4-8d15-4837-91bd-7e737e97d713",
    "answer": "If the agent's screen is blank and the call is connected, follow these steps:\n1. Check if the agent disconnected from the session. If so, the session enters 'PENDING' status for 5 minutes, allowing a new agent to join.\n2. Navigate to Admin > V-CIP Sessions > PENDING to check for the session.\n3. If no agent is available within 5 minutes, the session will be marked as FAILED, and the customer must restart the session.\n4. If this issue occurs frequently, escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.\n5. If the session fails due to agent disconnection, do not proceed with any further actions until the issue is resolved.",
    "confidence": "high",
    "confidence_score": 0.89,
    "requires_human": false,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "c192aa3b-f87d-58ff-9f85-aa13b3acb5c4",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.5332,
            "boosted_score": 0.6832,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the agent disconnected from the session (agent-side failure):\n\n1. The session enters \"PENDING\" status for 5 minutes, during which a new agent can join.\n2. Check: **Admin > V-CIP Sessions > PENDING** for the session.\n3. If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.\n4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.",
            "retrieval_rank": 1,
            "rerank_score": 0.6832
        },
        {
            "chunk_id": "cce8c0aa-7128-5c14-8344-9debbe2ec140",
            "ticket_id": "167194",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5775,
            "boosted_score": 0.5775,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 167194\nSubject: Re: Agent screen is not visible (technical issue)\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 2,
            "rerank_score": 0.5775
        },
        {
            "chunk_id": "edd56458-a5cc-52b4-8d68-768b580a0e91",
            "ticket_id": "167352",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5774,
            "boosted_score": 0.5774,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 167352\nSubject: RE: Agent screen is not visible (technical issue)\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 3,
            "rerank_score": 0.5774
        },
        {
            "chunk_id": "7e4965d5-8361-5f92-8f55-7b99200a0aa6",
            "ticket_id": "172217",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5763,
            "boosted_score": 0.5763,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 172217\nSubject: RE: Agent screen is not visible (technical issue)\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 4,
            "rerank_score": 0.5763
        },
        {
            "chunk_id": "fb12708f-f080-5885-8bde-76d6343d498d",
            "ticket_id": "167420",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5754,
            "boosted_score": 0.5754,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 167420\nSubject: RE: Agent screen is not visible (technical issue)\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 5,
            "rerank_score": 0.5754
        },
        {
            "chunk_id": "8e039a8a-6179-5173-8731-e570d99b925a",
            "ticket_id": "171517",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5619,
            "boosted_score": 0.5619,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 171517\nSubject: RE: agent screen not available\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 6,
            "rerank_score": 0.5619
        },
        {
            "chunk_id": "fb5ae4a2-c561-5980-b1b4-f5dffba700ea",
            "ticket_id": "171858",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5619,
            "boosted_score": 0.5619,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 171858\nSubject: RE: agent screen not available\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 7,
            "rerank_score": 0.5619
        },
        {
            "chunk_id": "91b4c0b4-bdfc-5360-8647-fda4a4cc3497",
            "ticket_id": "172071",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.5619,
            "boosted_score": 0.5619,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 172071\nSubject: RE: agent screen not available\nCategory: Video Related > Other\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 8,
            "rerank_score": 0.5619
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 1075.6,
        "total_latency_ms": 1797.0,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.5775,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 557.2,
        "keyword_latency_ms": 163.9,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "exact_match",
        "best_sop_score": 0.6832,
        "grounding_confidence": "medium",
        "exact_sop_match": true,
        "partial_match_detected": false,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 792,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": null,
        "automation_safe": false,
        "automation_block_reason": "retrieval_confidence_is_medium",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 17:
```
{
    "query_text": "I was dropped from the call automatically.",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "b7e25cc9-8b84-453b-859a-3936f1c01664",
    "message_id": "908677b4-e7ea-4b4e-943f-1e175baf4576",
    "answer": "I wasn't able to find a dedicated workflow for handling dropped calls, but based on the procedures for session drops, here are the closest applicable steps:\n\n1. Collect information from the customer regarding the drop:\n - At what point did the session drop? (pre-session failure, mid-session failure, biometric failure, agent-side failure)\n - What error message, if any, was displayed? (e.g., \"Session ended unexpectedly\", \"Camera not detected\")\n - Was the session ID visible on screen? If yes, note it for retrieval.\n\n2. If the session dropped after document capture:\n - Retrieve the session log using the session ID via: **Admin > V-CIP Sessions > Search by Session ID**.\n - Check the session status:\n - **DOCS_CAPTURED / PENDING_AGENT_REVIEW**: Documents were saved; an agent can resume review.\n - **INCOMPLETE / NO_DATA**: The session must be restarted from the beginning.\n - If in DOCS_CAPTURED status, notify the V-CIP operations team at vcip-ops@unity_bank.com to manually assign an agent for review.\n\n3. If the agent disconnected from the session:\n - The session enters \"PENDING\" status for 5 minutes. Check **Admin > V-CIP Sessions > PENDING** for the session.\n - If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.\n - Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.",
    "confidence": "medium",
    "confidence_score": 0.6,
    "requires_human": true,
    "citations": [
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 3,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "e88bbe31-cda1-5600-8957-e69cdeae29d9",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3402,
            "boosted_score": 0.4902,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the session dropped after document capture was completed:\n\n1. Retrieve the session log using the session ID (from Step 1) via: **Admin > V-CIP Sessions > Search by Session ID**.\n2. Check the session status:\n   - **DOCS_CAPTURED / PENDING_AGENT_REVIEW**: The documents were saved. An agent can resume review from the admin panel without requiring the customer to restart from scratch.\n   - **INCOMPLETE / NO_DATA**: The session must be restarted from the beginning.\n3. If the session is in DOCS_CAPTURED status: notify the V-CIP operations team at vcip-ops@unity_bank.com to manually assign an agent for review.",
            "retrieval_rank": 1,
            "rerank_score": 0.4902
        },
        {
            "chunk_id": "fb36c86a-f6d7-598e-a28a-8f267f63eab7",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3335,
            "boosted_score": 0.4835,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Collect the following from the customer:\n\n1. **At what point did the session drop?**\n   - Before connecting to an agent (pre-session failure)\n   - During document verification (mid-session failure)\n   - During liveness check / biometric capture (biometric failure)\n   - After document capture but before agent sign-off (agent-side failure)\n\n2. **What error message, if any, was displayed?**\n   - \"Session ended unexpectedly\" → network issue\n   - \"Camera not detected\" → device/permission issue\n   - \"Face not detected\" → lighting or positioning issue\n   - \"Agent disconnected\" → agent-side issue\n\n3. **Was the session ID visible on screen?** (Format: VCIP-XXXXXXXX). If yes, note it for retrieval from the session log.",
            "retrieval_rank": 2,
            "rerank_score": 0.4835
        },
        {
            "chunk_id": "c192aa3b-f87d-58ff-9f85-aa13b3acb5c4",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3307,
            "boosted_score": 0.4807,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "If the agent disconnected from the session (agent-side failure):\n\n1. The session enters \"PENDING\" status for 5 minutes, during which a new agent can join.\n2. Check: **Admin > V-CIP Sessions > PENDING** for the session.\n3. If no agent is available within 5 minutes, the session is marked FAILED and the customer must restart.\n4. Escalate to the V-CIP operations supervisor to ensure agent availability during peak hours.",
            "retrieval_rank": 3,
            "rerank_score": 0.4807
        },
        {
            "chunk_id": "5b8bb3ff-bdab-5e18-81f2-e44514929a25",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2788,
            "boosted_score": 0.4288,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Escalate to Platform Engineering (#vcip-issues in Slack) if:\n\n- The same session ID shows FAILED status despite all permissions and network being correct\n- Customers report V-CIP failures during a specific time window (possible regional outage)\n- Liveness check fails repeatedly for a customer who is clearly visible (possible SDK bug)\n- Session count shows a customer has been charged an attempt for a platform-initiated failure (requires attempt count reset — requires L2 action)",
            "retrieval_rank": 4,
            "rerank_score": 0.4288
        },
        {
            "chunk_id": "9de7d73c-7b71-561c-b8f4-c42f3a5f8612",
            "ticket_id": "180245",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4277,
            "boosted_score": 0.4277,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 180245\nSubject: After accepting calls, something went wrong - FF\nCategory: Connectivity Issue > Backend\nClient: Unity Bank | Environment: Production | Priority: Medium\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 5,
            "rerank_score": 0.4277
        },
        {
            "chunk_id": "959506ca-1edf-54e3-bbd4-912000e29468",
            "ticket_id": "130612",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4162,
            "boosted_score": 0.4162,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 130612\nSubject: Call recording Stopped\nCategory: Video Related > Backend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Auto-resolvable — SOP-backed, recurring, within SLA\nSOP Available: No | Recurring Issue: Yes",
            "retrieval_rank": 6,
            "rerank_score": 0.4162
        },
        {
            "chunk_id": "e6f61fc1-7c33-58b8-85d3-021e19d98421",
            "ticket_id": "179310",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4144,
            "boosted_score": 0.4144,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 179310\nSubject: Unable to fetch complete call recording.\nCategory: Video Related > Backend\nClient: Unity Bank | Environment: Production | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: Yes",
            "retrieval_rank": 7,
            "rerank_score": 0.4144
        },
        {
            "chunk_id": "59e488c5-ec45-5aff-bb10-312e55ac9f92",
            "ticket_id": "165369",
            "sop_id": null,
            "chunk_type": "ISSUE_HEADER",
            "similarity": 0.4034,
            "boosted_score": 0.4034,
            "source_table": "rag_ticket_chunks",
            "has_rca": true,
            "has_sop": false,
            "content_preview": "[ISSUE SUMMARY]\nTicket ID: 165369\nSubject: Session logged out\nCategory: Others > Other\nClient: Unity Bank | Environment: Other | Priority: Low\nAutomation Classification: Requires agent review before posting AI draft\nSOP Available: No | Recurring Issue: No",
            "retrieval_rank": 8,
            "rerank_score": 0.4034
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 4437.2,
        "total_latency_ms": 7415.9,
        "has_sop_context": true,
        "has_rca_context": true,
        "has_knowledge_context": false,
        "best_similarity": 0.4277,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 1702.1,
        "keyword_latency_ms": 1276.3,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "related_match",
        "best_sop_score": 0.4902,
        "grounding_confidence": "medium",
        "exact_sop_match": false,
        "partial_match_detected": true,
        "sop_branch_flags": {
            "has_escalation_branches": true,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 1057,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "weak_workflow_coverage",
        "automation_safe": false,
        "automation_block_reason": "partial_workflow_coverage",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 18:
```
{
    "query_text": "Browser says “browser not supported” — which browser should I use?",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "e4d4693c-827f-4392-b36e-79c9c332d750",
    "message_id": "74dd2862-533c-403f-a013-21fbcddfe154",
    "answer": "I wasn't able to find a dedicated workflow for supported browsers, but based on the V-CIP requirements, the following browsers are recommended: Chrome version 90 or higher, or Safari version 14 or higher. Ensure that the customer has camera permissions enabled. If the device is incompatible, inform the customer to use a different device, as attempting to work around device incompatibility will fail regulatory requirements.",
    "confidence": "medium",
    "confidence_score": 0.55,
    "requires_human": true,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "29d78999-5ee1-5e0f-aee8-e762772239a0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3414,
            "boosted_score": 0.4914,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires:\n- Front-facing camera (minimum 5 MP recommended)\n- OS: Android 8.0+ or iOS 12.0+\n- Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions\n\nIf the device is incompatible: inform the customer to use a different device. Do not attempt to work around device incompatibility — the session quality will fail regulatory requirements.",
            "retrieval_rank": 1,
            "rerank_score": 0.4914
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 1,
        "total_candidates": 1,
        "semantic_latency_ms": 1739.8,
        "total_latency_ms": 2474.3,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.3414,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 516.8,
        "keyword_latency_ms": 217.7,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 1,
        "keyword_candidates": 0,
        "fused_candidates": 1,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "low",
        "workflow_match_type": "related_match",
        "best_sop_score": 0.4914,
        "grounding_confidence": "low",
        "exact_sop_match": false,
        "partial_match_detected": true,
        "sop_branch_flags": {
            "has_escalation_branches": false,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 133,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "weak_workflow_coverage",
        "automation_safe": false,
        "automation_block_reason": "partial_workflow_coverage",
        "query_route": "GENERAL_KNOWLEDGE",
        "routing_confidence": 0.0,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "QUERY_BODY",
                "KNOWLEDGE"
            ]
        }
    }
}
```

---

## Query 19:
```
{
    "query_text": "VKYC is not working on iPhone Safari.",
    "client": "unity_bank"
}
```
## Response generated:
```
{
    "session_id": "3fecc61f-826a-4796-9350-348007872140",
    "message_id": "44d599b3-3802-4897-a9b4-ab1e582966f2",
    "answer": "I wasn't able to find a dedicated workflow for VKYC issues on iPhone Safari, but based on the V-CIP requirements, here are the closest applicable steps: \n1. Ensure the iPhone is running iOS 12.0 or higher. \n2. Confirm that Safari is version 14 or higher. \n3. Check that camera and microphone permissions are enabled for the KwikID app: \n - Go to Settings > Privacy & Security > Camera and enable for KwikID. \n - Go to Settings > Privacy & Security > Microphone and enable for KwikID. \n4. If permissions were just enabled, the customer must restart the app and begin a new session, as the current session cannot be resumed. \n5. Verify the internet connection is stable, with a minimum download speed of 2 Mbps and upload speed of 1 Mbps. If the customer is on a poor network, advise them to switch to a stable WiFi connection and retry.",
    "confidence": "medium",
    "confidence_score": 0.57,
    "requires_human": true,
    "citations": [
        {
            "chunk_num": 1,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 2,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        },
        {
            "chunk_num": 4,
            "chunk_type": "SOP",
            "source_id": "sop_id=559db78c-a70f-5042-b39c-0b4a0c0d2935"
        }
    ],
    "follow_up_question": null,
    "insufficient_context": false,
    "chunks": [
        {
            "chunk_id": "29d78999-5ee1-5e0f-aee8-e762772239a0",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3718,
            "boosted_score": 0.5218,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires:\n- Front-facing camera (minimum 5 MP recommended)\n- OS: Android 8.0+ or iOS 12.0+\n- Browser (if web-based): Chrome 90+ or Safari 14+ with camera permissions\n\nIf the device is incompatible: inform the customer to use a different device. Do not attempt to work around device incompatibility — the session quality will fail regulatory requirements.",
            "retrieval_rank": 1,
            "rerank_score": 0.5218
        },
        {
            "chunk_id": "77b1779d-fee9-5417-80a6-ac5804a0328b",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3246,
            "boosted_score": 0.4746,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "Camera and microphone access are mandatory for V-CIP.\n\n**Android:**\n1. Settings > Apps > [KwikID App] > Permissions\n2. Ensure Camera and Microphone are set to \"Allow\"\n\n**iOS:**\n1. Settings > Privacy & Security > Camera → Enable for KwikID\n2. Settings > Privacy & Security > Microphone → Enable for KwikID\n\nIf permissions were just enabled, the customer must restart the app and begin a new session — the current session cannot be resumed.",
            "retrieval_rank": 2,
            "rerank_score": 0.4746
        },
        {
            "chunk_id": "fe14a00e-e01b-5f6f-bac7-f6999d42720f",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.3012,
            "boosted_score": 0.4512,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "This procedure applies to Video KYC (V-CIP — Video Customer Identification Process) session failures for Unity Bank customers. V-CIP is a regulated process governed by RBI guidelines. Session failures must be resolved promptly as failed sessions count against the customer's daily verification attempts.\n\n**Maximum V-CIP attempts per day**: 3. After 3 failures, the customer must wait 24 hours before retrying.",
            "retrieval_rank": 3,
            "rerank_score": 0.4512
        },
        {
            "chunk_id": "bfe1c6f9-3cef-5cc3-8c06-947a5da732e1",
            "ticket_id": null,
            "sop_id": "559db78c-a70f-5042-b39c-0b4a0c0d2935",
            "chunk_type": "SOP_STEPS",
            "similarity": 0.2842,
            "boosted_score": 0.4342,
            "source_table": "rag_sop_chunks",
            "has_rca": false,
            "has_sop": true,
            "content_preview": "V-CIP requires a stable internet connection. Minimum requirements:\n- Download: 2 Mbps\n- Upload: 1 Mbps\n- Latency: < 300ms\n\nAsk the customer:\n- Are they on WiFi or mobile data?\n- Are other video calls or streaming services working?\n- Is VPN active? (VPN can cause session instability — ask them to disable it)\n\nIf the customer is on a poor network: advise them to find a stable WiFi connection and retry. **Do not count this as a platform failure.**",
            "retrieval_rank": 4,
            "rerank_score": 0.4342
        },
        {
            "chunk_id": "81ca07a6-d94d-565f-82ae-5c1735d1a8c2",
            "ticket_id": "169121",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.4306,
            "boosted_score": 0.4306,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nHi Team, Please check\n 405 Not Allowed\n\n nginx                  curl ^\"https://vkyc360.unitybank.co.in/hypertrail/e/^\" ^ -H ^\"accept: */*^\" ^ -H ^\"accept-language: en-GB,en-US;q=0.9,en;q=0.8^\" ^ -H ^\"auth: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1bml0eSIsImF1ZCI6InVzciIsImlhdCI6MTc2MTU2NDk4MywiZXhwIjoxNzYyMTY5NzgzLCJpc3MiOiJpcHZfYXBpX0lvbmljQXBwIn0.fe_Q3cUsWN6g3aBI08sqMjAhPTWr1yPj7S_624Nx4VM^\" ^ -H ^\"cache-control: no-cache^\" ^ -H ^\"content-type: application/json^\" ^ -H ^\"origin: https://vkyc360.unitybank.co.in^\" ^ -H ^\"pragma: no-cache^\" ^ -H ^\"priority: u=1, i^\" ^ -H ^\"referer: https://vkyc360.unitybank.co.in/instructions?stepKey=VKYC^\" ^ -H ^\"sec-ch-ua: ^\\^\"Google Chrome^\\^\";v=^\\^\"141^\\^\", ^\\^\"Not?A_Brand^\\^\";v=^\\^\"8^\\^\", ^\\^\"Chromium^\\^\";v=^\\^\"141^\\^\"^\" ^ -H ^\"…",
            "retrieval_rank": 5,
            "rerank_score": 0.4306
        },
        {
            "chunk_id": "b2706481-e581-5dbe-b877-0efa29483da8",
            "ticket_id": "187010",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.3745,
            "boosted_score": 0.3745,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nHi Team, Not able to complete VKYC journey user stuck on agent screen Mob:8149724137 Session ID:73539fbf-2cdd-4dde-9202-0381f770cc9f  Regards, Gangadhar Solanke Quality Kiosk | Test Engineer This e-mail (including any attachments) is confidential and may also contain proprietary information of QualityKiosk. The email and the attachments, if any, are transferred for the exclusive attention of the intended addressees named above. If you have received this transmission in error, please immediately notify the sender by return e-mail and delete this message with its attachments. Unauthorized use, copying or further full or partial distribution of this e-mail or its attachments is strictly prohibited and may subject you to legal action. Although this e-mail and any attachments a…",
            "retrieval_rank": 6,
            "rerank_score": 0.3745
        },
        {
            "chunk_id": "e6ae51c9-cf60-5a29-88b6-e6eb0bae9c50",
            "ticket_id": "126049",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.3731,
            "boosted_score": 0.3731,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nINTERNAL Hi Team, Kindly review logs for below user id: 9411424707 User is unable to connect to VKYC platform, after verifying the Aadhar, customer is getting same screen ( retry VKYC) . The issue is been raised by the business team , kindly check and update on priority . Thanks & Regards, [Unity Small Finance Bank Limited]  Juilee Chavan Program Manager - VKYC ________________________________ +91 9819836442 Unity Small Finance Bank Limited [Facebook] [Linkedin] [Twitter] [Instagram]  ________________________________ Disclaimer: This e-mail message is legally privileged, confidential and intended for the addressee only If you are not the intended receiver, please do not disclose, copy, circulate or in any other way use the information contained in this transmission. Such u…",
            "retrieval_rank": 7,
            "rerank_score": 0.3731
        },
        {
            "chunk_id": "5efc31b9-b083-53ae-9372-84dc8106c50f",
            "ticket_id": "186613",
            "sop_id": null,
            "chunk_type": "QUERY_BODY",
            "similarity": 0.3721,
            "boosted_score": 0.3721,
            "source_table": "rag_ticket_chunks",
            "has_rca": false,
            "has_sop": false,
            "content_preview": "[CUSTOMER QUERY]\nHi Team, Kindly check the below case.  Swapnil Dalvi Banking Operations - VKYC ________________________________ +91 8082789806 Unity Small Finance Bank Limited [Facebook] [Linkedin] [Twitter] [Instagram]  ________________________________ From: Farheen Bano Zainul Haque Shaikh   Sent: Saturday, April 11, 2026 9:38 AM To: VCIP Team Coach  ; ext auditors of vkyc   Cc: Sushil Datt  ; Harshad Bade  ; Dharmesh Kamath   Subject: Audit pending Tech issue/Customer audio & agent video not available (T360) Dear All, Customer ID-9810183150 in which Customer audio & agent video not available after refreshing customer connection. Issue duration is (4:44 to 7:18) Thanks & regards, VKYC Auditor, Farheen Shaikh, NSB & Co. Disclaimer: This e-mail message is legally privileged, confidential …",
            "retrieval_rank": 8,
            "rerank_score": 0.3721
        }
    ],
    "diagnostics": {
        "index_version": "v1",
        "returned_count": 8,
        "total_candidates": 32,
        "semantic_latency_ms": 2462.0,
        "total_latency_ms": 3592.1,
        "has_sop_context": true,
        "has_rca_context": false,
        "has_knowledge_context": false,
        "best_similarity": 0.4306,
        "retrieval_mode": "semantic_only",
        "used_fallback": false,
        "embedding_latency_ms": 806.4,
        "keyword_latency_ms": 323.6,
        "fusion_latency_ms": 0.0,
        "threshold": 0.27,
        "top_k": 8,
        "v2_hnsw_used": true,
        "semantic_candidates": 32,
        "keyword_candidates": 0,
        "fused_candidates": 32,
        "selected_rrf_k": 60,
        "overlap_count": 0,
        "overlap_ratio": 0.0,
        "retrieval_confidence": "medium",
        "workflow_match_type": "related_match",
        "best_sop_score": 0.5218,
        "grounding_confidence": "medium",
        "exact_sop_match": false,
        "partial_match_detected": true,
        "sop_branch_flags": {
            "has_escalation_branches": false,
            "has_denial_branches": true,
            "has_security_freeze": false,
            "has_post_resolution": false,
            "has_mandatory_warnings": false
        },
        "context_tokens": 2851,
        "context_skipped_chunks": 0,
        "escalation_trigger_reason": "weak_workflow_coverage",
        "automation_safe": false,
        "automation_block_reason": "partial_workflow_coverage",
        "query_route": "POLICY_COMPLIANCE",
        "routing_confidence": 0.25,
        "retrieval_strategy": {
            "requires_human_review": false,
            "boost_sop": false,
            "boost_knowledge": true,
            "chunk_types_hint": [
                "KNOWLEDGE",
                "SOP_STEPS"
            ]
        }
    }
}
```