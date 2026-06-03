# **Comprehensive Architectural Audit and Multi-Tenant Action Systems Design for KwikID Video KYC Customer Support**

## **Industry Blueprint**

Implementing autonomous, tool-calling support automation within highly regulated financial ecosystems, such as banks and non-banking financial companies (NBFCs), requires a transition from non-deterministic generation models to strictly bounded execution systems.1 While Large Language Models (LLMs) excel at processing conversational nuance, their integration into operational workflows must be governed by deterministic constraints, strict authorization gates, and formal execution parameters.2

### **State Machine vs. Autonomous Loops in Enterprise Support**

Leading industry platforms such as Sierra AI, Intercom Fin, and Klarna’s support core avoid completely autonomous, unconstrained loops (such as open-ended ReAct paradigms) when executing state-changing operations.2 Sierra AI employs a declarative "Agent OS" framework, which structures agent behavioral patterns using goal-driven configurations, behavioral constraints, and deterministic state transitions rather than giving models free-form tool-calling autonomy.6 This design philosophy structures conversations as directed graphs where nodes represent well-defined procedural states, and edges define explicit conditional logic.5  
In a regulated banking ecosystem—such as the Video KYC (VKYC) framework operated by KwikID—non-determinism poses severe compliance and operational risks under the Reserve Bank of India (RBI) guidelines.8 Production architectures mitigate this by decoupling the conversational interface from the execution layer.2 The LLM acts solely as a natural language parser and parameter extractor, while state transitions and tool-execution decisions are governed by a deterministic, code-enforced state machine.2 If a customer demands a transaction update, the agent cannot arbitrarily sequence API calls; it must follow a pre-validated, linear path defined in code, ensuring that the system fails safe and prevents infinite loops or conflicting actions.5  
Intercom Fin employs an orchestration paradigm known as "Autoflows," which uses natural-language business logic instead of rigid, hardcoded decision trees.1 However, these flows are executed under strict constraint boundaries where any deviation or critical action is routed immediately to a human-agent handover queue.1  
Klarna's support engine balances relationship value against transactional concession costs by using a centralized multi-objective reward function rather than a single-objective speed optimization metric.2 The reward function (![][image1]) is formalized as:  
![][image2]  
This multi-objective framework prevents the model from over-optimizing for resolution speed at the cost of relationship preservation or financial liability (such as giving away refunds indiscriminately to bad actors).2  
To enrich these execution paths with context, Klarna utilizes "Kiki," an internal enterprise knowledge graph powered by Neo4j.15 This system serves as the single source of truth, linking real-time customer transactional records and organizational policies directly to the model's prompt context.15 This design pattern enforces the rule that the conversational model handles dialogue, while compiled, validated software code handles operational consequences.2

### **Contextual Parameter Extraction**

Production-grade agents utilize Joint Intent Detection and Slot Filling models to perform semantic parsing in a single inference pass.16 This process maps unstructured user inputs (such as raw, unformatted emails or disjointed live chats) into structured, type-safe schemas.17 In environments processing telemetry and administrative data, parsing algorithms are optimized by converting nested payloads into hybrid column- and row-oriented representations.17 This prevents the extraction errors that frequently occur when LLMs process deeply nested JSON structures.17  
When mandatory parameters (referred to as slots) are missing from the user's input, production architectures transition to a stateful clarification loop rather than attempting to execute a tool with null or guessed values.16 This is modeled via a parameter-clarification state (e.g., CLARIFY\_PARAMETER), which preserves the existing conversation context in a Redis-backed session store and prompts the user with highly specific, single-question inquiries designed to fill the missing slot.16 The system enforces strict pattern-matching boundaries—using regex engines and Named Entity Recognition (NER) tokenizers—to validate extracted parameters (such as Indian mobile numbers ![][image3] or 12-digit Aadhaar patterns) before committing them to the tool execution context.10  
The structural impact of under-specified inputs is well-documented in enterprise studies.18 Experimental evaluations of tool-calling agents (such as the LAMINA materials science agent and the PathFinder genetic mapping assistant) show that prompt under-specification represents the primary driver of tool-calling instability.18  
In the LAMINA framework, vague user claims triggered an alternate-interpretation "flip rate of 0.43," causing the agent to execute completely different tool sequences for the same underlying request.18 Similarly, in the PathFinder system, the removal of dataset-level parameters caused slot-filling and selection F1-scores to collapse from 0.664 to 0.158.18  
To prevent these failure modes, enterprise gateways utilize strict parsing engines such as the nested natural language pattern extractor (NATEX), which enforces schema validation at the edge and blocks execution until all required slot constraints are fully resolved.19

### **Transient Failure Handling and Asynchronous Execution**

Robust execution of tool chains requires systematic fault tolerance. When a downstream system fails, production systems implement a log-based recovery and compensation paradigm (such as Robust Agent Compensation) to log every execution trace and safely rollback partial states.21 For asynchronous executions, transactions are managed via persistent message queues and evaluated using strict idempotency managers.11  
The prevention of race conditions and duplicate executions is achieved by asserting unique database constraints (e.g., idx\_tool\_executions\_idempotency) and employing distributed locks in Redis.22 When a transient failure occurs (such as a carrier SMS gateway timeout during a link-generation request), retry strategies are dynamically governed by the risk classification of the tool:

* **Low-Risk / Read-Only Tools**: Managed via automatic exponential backoff with jitter to absorb downstream network instability.22  
* **High-Risk / State-Changing Tools**: Bypassed entirely for automated retries. Instead, the transaction is marked as a partial failure, the state is persisted, and the event is routed to a human-in-the-loop escalation queue to prevent issues like duplicate link generation or duplicate outbound communication.22

These interactions are unified under emerging server-side orchestrators using the "Responses API" paradigm.3 This approach unifies model inference, tool execution, state tracking, and vector retrieval behind a single, transactional endpoint.3  
By centralizing execution on secure server-side infrastructure rather than relying on client-side state coordination, enterprise systems eliminate the risk of client-side bypass.3 This centralization provides clear enforcement points for rate-limiting policies, transactional rollbacks, and distributed circuit-breaking states, safeguarding the system's operational continuity.11

### **Strict Tenant and Risk Demarcation**

Enterprise multi-tenant environments demand absolute isolation at the retrieval, processing, and tool-execution layers to prevent cross-tenant data leakage.4 Naive architectures that rely on runtime prompt instructions to separate tenant data are highly vulnerable to prompt injections and context pollution.24 Production engines resolve this by implementing a three-layered isolation model:  
![][image4]  
At Layer 1 (Ingress), metadata tags are permanently attached to all document chunks.24 At Layer 2 (Retrieval), Attribute-Based Access Control (ABAC) filters are applied as hard metadata predicates directly inside the vector database (e.g., Supabase pgvector HNSW index search).24 This restricts the retrieval engine to search only within the authorized tenant space, entirely bypassing model-level decision making.24 At Layer 3 (Inference), the LLM is executed inside a shared infrastructure model, but the prompt's context window is strictly bounded by the prior ABAC-gated retrieval results, preventing data contamination across tenant lines.24  
This architecture addresses the relevance-authorization gap, which is a major security vulnerability in naive RAG systems.3 Because standard vector retrieval systems rank document chunks strictly by semantic similarity (relevance) rather than access permissions (authorization), a high-similarity query from Tenant A can easily retrieve sensitive documentation belonging to Tenant B.3  
By pushing down ABAC predicate filters to the database query layer, production gateways ensure that the search space is constrained before similarity matching is executed.3 This design pattern allows enterprise platforms to safely run shared inference models across multiple tenants, lowering operational costs from ![][image5] to ![][image6]—where ![][image7] represents the number of distinct tenants and ![][image8] represents the active model endpoints—without compromising data isolation.3

### **Dynamic and Synthetic Standard Operating Procedure (SOP) Scaling**

A major bottleneck in enterprise support automation is the lack of comprehensive, human-authored documentation for edge-case failures. Production architectures scale their knowledge systems dynamically by using synthetic SOP generation frameworks.27 These pipelines continuously monitor retrieval quality logs (such as tracking low-confidence vector matches or recurring human escalations).22  
When a knowledge gap is flagged, the system aggregates the historical logs, conversation transcripts, and resolution steps of the relevant memory episodes, clustering them by semantic similarity.22 An LLM-based offline generator then drafts a structured SOP document mapping the resolved paths to formal steps, schemas, and API definitions.27 To prevent the ingestion of hallucinations, these drafted SOPs are stored in a staging database (such as sop\_suggestions) and routed to senior operational officials for manual modification, verification, and formal ingestion.22  
This is illustrated by the "Flow-of-Action" framework, which integrates runtime SOP synthesis with multi-agent orchestration.27 When standard vector retrieval falls below a defined similarity threshold (![][image9]), the system triggers an on-demand generator to construct a new SOP based on structured system metrics, tracing logs, and contextual incident data.27  
To validate and execute these workflows safely, the system utilizes a specialized multi-agent stack 31:

* **ObAgent**: Aggregates multi-modal data and filters out noise.30  
* **ActionAgent**: Proposes appropriate action sets to the execution planner.31  
* **CodeAgent**: Programmatically translates the draft steps into executable script blocks.31  
* **JudgeAgent**: Monitors the execution state to determine when the issue has been successfully resolved.30

Furthermore, advanced implementations use "SOPRAG," a Mixture-of-Experts (MoE) graph-retrieval framework that replaces flat text chunking with specialized Entity, Causal, and Flow graph experts.33 This preserves structural operational dependencies and conditional branching logic, achieving superior retrieval accuracy on complex operational workflows.33

| Architectural Dimension | Sierra AI Blueprint | Intercom Fin Blueprint | Klarna Support Core Blueprint |
| :---- | :---- | :---- | :---- |
| **Orchestration Paradigm** | Bounded Agent OS with declarative goal configurations and strict guardrails.6 | Conversational parsing combined with deterministic helpdesk routing and handoffs.7 | Multi-agent network governed by centralized internal knowledge graph (Neo4j "Kiki").2 |
| **State Management** | Multi-channel state preservation across asynchronous communication loops.6 | Stateless session processing with handoffs directly into target ticketing channels.7 | Stateful recursive optimization minimizing user-facing friction and operational latency.14 |
| **Isolation and Security** | Explicit enterprise tenant boundary mapping with isolated CRM/ERP data pipes.6 | Strict API gateway controls linked to target billing and secure platform records.7 | Compliance-grade internal infrastructure with end-to-end encrypted local domain servers.2 |
| **Error and Fault Recovery** | Proactive signal monitoring, automated human-in-the-loop escalation alerts.6 | Direct fallback to standard live agent inbox upon tool/API execution failure.7 | Compensation loops with recursive error-handling and automated system alerts.2 |
| **Knowledge Scaling** | Self-optimizing pipeline analyzing flagged transcripts to suggest updates.35 | Static knowledge base ingestion paired with real-time semantic query testing.1 | Dynamic graph updates linking customer history to local business policies.2 |

## **Gap Analysis of Proposed Phase 2**

The proposed Phase 2 evolution plans for the KwikID automated agent contain several critical architectural gaps when evaluated against the compliance, security, and operational standards of banking ecosystems.8

       \[ Proposed Phase 2 Pipeline with Critical Architectural Vulnerabilities \]  
         
    Incoming Request  
          │  
          ▼  
   ┌──────────────┐  
   │ Ingress API  │ ──►  
   └──────────────┘  
          │  
          ▼  
   ┌──────────────┐  
   │ pgvector RAG │ ──►  
          │  
          ▼  
   ┌──────────────┐  
   │ LLM Parser   │ ──►  
          │  
          ▼  
   ┌──────────────┐  
   │Tool Execution│ ──►

### **1\. The Relevance-Authorization Gap in Supabase Retrieval**

The Phase 2 documentation outlines a unified Supabase PostgreSQL schema where tables such as rag\_sop\_chunks and rag\_knowledge\_articles are shared among multiple bank tenants, utilizing a tenant\_context lookup table to govern the application layer.22 However, the retrieval execution path relies on a global hybrid search (pgvector \+ BM25) without enforcing tenant-level isolation predicates during database scanning.3  
This creates a high-risk security vulnerability.24 If a customer or agent from Bank A submits a query containing terminology highly similar to a proprietary internal SOP of Bank B, the RAG engine will retrieve and surface those chunks based on cosine similarity.3 This bypasses security boundaries because authorization logic is deferred to post-retrieval processing.24 The retrieval engine must apply hard tenant-filtering predicates directly to the database query.24

### **2\. Under-Engineered Slot Extraction and Clarification Flow**

The proposed ActionPlanner and WorkflowDecomposer assume that incoming customer requests contain well-formed parameters required for tool invocation.22 In reality, banking customer queries are highly underspecified and unstructured.18  
The current Phase 2 architecture lacks an intermediate, schema-validated slot extraction layer.22 If a user emails "VKYC link expired," the system cannot execute regenerate\_vkyc\_link because it lacks the vital session\_id and phone\_number parameters.22 In the current design, this either triggers a system exception or generates an invalid tool execution attempt that is sent to the human approval queue, violating the SLA latency mandate and causing ticket gridlock.22 The architecture must implement a formal slot validation state machine that intercepts requests, detects missing parameters, and suspends execution to trigger single-topic clarification responses.16

### **3\. Risk of Synchronous Thread Exhaustion Under Network Timeout**

The RETRY\_POLICY defined in the Phase 2 action systems implements static retry counts (e.g., max 2 retries with a 1.0s backoff for medium-risk tools).22 Downstream core banking endpoints and carrier SMS gateways in India are highly volatile, frequently exhibiting prolonged gateway latency and high error rates.10  
Running synchronous, nested API retries inside the request-handling thread of FastAPI workers will block those workers during downstream gateway outages (e.g., a 504 Gateway Timeout).11 This blocks the execution loop, leading to rapid pool starvation, worker process crashes, and complete service denial.11 The system requires an asynchronous execution model protected by a stateful circuit breaker.11

### **4\. Regulatory Logging and Anti-Forgery Compliance Gaps (RBI V-CIP Directions)**

KwikID is deployed as an RBI-compliant V-CIP solution, meaning the automated agent handling support requests is subject to stringent financial compliance audits.8 Under the RBI Master Directions, the platform must log all security anomalies and attempt logs, including spoofing and liveness failures, and report them as cyber events.9  
The current Phase 2 Action Layer logs executions to a standard tool\_executions database table for operational monitoring.22 However, it lacks a dedicated, tamper-proof security auditing path that flags identity anomalies, deepfake detections, and location mismatches (such as connections originating from IP addresses outside India).9 These events must be written to an immutable audit trail and routed directly to the bank's internal Security Operations Center (SOC).9

| Architectural Vulnerability | Current Phase 2 Specification | Industry Best-Practice Requirement | Critical System Impact |
| :---- | :---- | :---- | :---- |
| **Multi-Tenant Retrieval** | Post-retrieval validation via tenant\_allowlist.22 | Predicate pushdown inside the database search query.24 | High risk of cross-tenant data leakage via high-similarity vector lookups.24 |
| **Parameter Handling** | Direct passing of unstructured extracted parameters to ActionPlanner.22 | Joint Slot-Filling with stateful CLARIFY\_PARAMETER loop.16 | Invalid tool invocations, execution failures, and ticket gridlock.18 |
| **Fault Isolation** | Static, blocking retry logic on downstream HTTP failures.22 | Non-blocking Redis-backed circuit breaking with worker separation.11 | Thread exhaustion, worker pool starvation, and global service denial.11 |
| **Regulatory Auditing** | Volatile database log entry in standard tool\_executions.22 | Cert-In compliant immutable logging with real-time incident reporting.9 | Compliance failures, audit penalties, and unflagged security breaches.8 |

## **Direct Codebase Modifications**

To resolve the identified architectural gaps, the following direct codebase modifications must be implemented. These modifications are additive and maintain complete backward compatibility with the Phase 1 pipeline.22

### **1\. Tenant-Isolated Attribute-Based Access Control (ABAC) Hybrid Retriever**

This class wraps the existing vector database search to enforce strict metadata filtering at the database level, preventing cross-tenant leakage.

Python  
import uuid  
from typing import List, Dict, Any, Optional  
from pydantic import BaseModel, Field  
from supabase import Client

class RetrievalContext(BaseModel):  
    tenant\_id: str \= Field(..., description="Unique UUID of the bank tenant")  
    user\_role: str \= Field(..., description="Role of the invoking user (agent or customer)")  
    is\_internal: bool \= Field(default=False, description="Whether the request originates internally")

class ABACGatedRetriever:  
    """  
    Implements a Layer 2 retrieval-time gating system using Attribute-Based  
    Access Control (ABAC) predicates pushed down to the Supabase pgvector search query.  
    Ensures absolute cross-tenant data isolation.  
    """  
    def \_\_init\_\_(self, supabase\_client: Client):  
        self.client \= supabase\_client

    async def retrieve\_isolated\_chunks(  
        self,   
        query\_vector: List\[float\],   
        retrieval\_ctx: RetrievalContext,   
        top\_k: int \= 5,   
        min\_similarity: float \= 0.75  
    ) \-\> List\]:  
        """  
        Executes a database-level gated search by appending hard filtering predicates  
        on tenant\_id and access permissions prior to similarity matching.  
        """  
        try:  
            \# Enforce strict policy-aware database filters  
            \# Pushes the tenant\_id constraint directly into the PostgreSQL execution plan  
            response \= self.client.rpc(  
                "match\_gated\_sop\_chunks",  
                {  
                    "query\_embedding": query\_vector,  
                    "match\_threshold": min\_similarity,  
                    "match\_count": top\_k,  
                    "filter\_tenant\_id": retrieval\_ctx.tenant\_id,  
                    "filter\_requires\_internal": not retrieval\_ctx.is\_internal  
                }  
            ).execute()  
              
            if not response.data:  
                return  
                  
            return response.data  
        except Exception as e:  
            \# Fallback safe behavior: log exception internally, return empty list to prevent exposure  
            \# Ensures zero cross-tenant leakage under database driver failure  
            return

### **2\. Stateful Slot-Filling Processor and Clarification Router**

This component handles contextual parameter extraction, validates input schemas, and manages stateful clarification loops in Redis when parameters are missing.

Python  
import re  
import json  
from typing import Dict, Any, Tuple, Optional  
from pydantic import BaseModel, ValidationError, Field  
import redis

class VKYCParameters(BaseModel):  
    session\_id: str \= Field(..., description="Valid 8 to 16-character alphanumeric KwikID session string")  
    phone\_number: str \= Field(..., description="Valid 10-digit Indian mobile number")

    @classmethod  
    def clean\_phone(cls, v: str) \-\> str:  
        \# Normalizes raw string to 10-digit Indian standard  
        digits \= re.sub(r"\\D", "", v)  
        if len(digits) \== 10:  
            return digits  
        elif len(digits) \== 12 and digits.startswith("91"):  
            return digits\[2:\]  
        raise ValueError("Invalid Indian phone number format")

class SlotFillingProcessor:  
    """  
    Parses unstructured text, extracts parameters, validates them against schema definitions,  
    and manages stateful parameter-clarification workflows in Redis.  
    """  
    def \_\_init\_\_(self, redis\_client: redis.Redis):  
        self.redis \= redis\_client  
        \# Regex patterns for high-confidence fallback extraction  
        self.phone\_regex \= re.compile(r"(?:\\+91|91)?\[6-9\]\\d{9}")  
        self.session\_regex \= re.compile(r"KID-\[A-Z0-9\]{8,12}")

    def extract\_implicit\_entities(self, text: str) \-\> Dict\[str, Any\]:  
        """  
        Extracts structural tokens from raw, unformatted text blocks using high-confidence regex patterns.  
        """  
        extracted \= {}  
        phone\_match \= self.phone\_regex.search(text)  
        session\_match \= self.session\_regex.search(text)

        if phone\_match:  
            extracted\["phone\_number"\] \= phone\_match.group(0)  
        if session\_match:  
            extracted\["session\_id"\] \= session\_match.group(0)

        return extracted

    async def process\_slots(  
        self,   
        session\_key: str,   
        incoming\_text: str,   
        tenant\_id: str  
    ) \-\> Tuple, Optional\[str\]\]:  
        """  
        Validates the extracted parameters. Returns a boolean indicating if validation succeeded,  
        the validated parameters, and an optional clarification response if parameters are missing.  
        """  
        \# Retrieve existing state cache from Redis  
        cached\_state \= self.redis.get(f"slot\_state:{session\_key}")  
        state \= json.loads(cached\_state) if cached\_state else {}

        \# Merge newly extracted entities into the active state cache  
        new\_extractions \= self.extract\_implicit\_entities(incoming\_text)  
        state.update(new\_extractions)

        \# Write updated state back to Redis with a 30-minute expiration  
        self.redis.setex(f"slot\_state:{session\_key}", 1800, json.dumps(state))

        try:  
            \# Validate consolidated state against the target parameter schema  
            validated\_params \= VKYCParameters(  
                session\_id=state.get("session\_id", ""),  
                phone\_number=state.get("phone\_number", "")  
            )  
            \# Validation succeeded, clear state from Redis and signal readiness  
            self.redis.delete(f"slot\_state:{session\_key}")  
            return True, validated\_params, None

        except ValidationError as e:  
            \# Identify which parameters are missing and generate a targeted clarification response  
            missing\_fields \=  
            errors \= e.errors()  
            for error in errors:  
                missing\_fields.append(error\["loc"\])

            clarification\_payloads \= {  
                "session\_id": "Please provide your KwikID Session ID (e.g., KID-AB12CD34) to help us locate your session.",  
                "phone\_number": "Please provide the 10-digit mobile number registered with your Video KYC application."  
            }

            \# Return the first missing parameter's clarification message  
            target\_missing \= missing\_fields  
            clarification\_text \= clarification\_payloads.get(  
                target\_missing,   
                "We need additional details to process your request. Please provide your Session ID or registered phone number."  
            )  
            return False, None, clarification\_text

### **3\. Redis-Backed Circuit Breaker for Tool Execution Isolation**

This middleware wraps the external API client, implementing a stateful circuit breaker in Redis to prevent thread exhaustion during downstream outages.

Python  
import time  
from typing import Callable, Any, Dict  
import redis

class CircuitBreakerOpenException(Exception):  
    """Raised when the circuit breaker is open, blocking downstream calls."""  
    pass

class RedisCircuitBreaker:  
    """  
    Implements a distributed, stateful Circuit Breaker pattern in Redis.  
    Protects Gunicorn/FastAPI workers from thread exhaustion during downstream outages.  
    """  
    def \_\_init\_\_(  
        self,   
        redis\_client: redis.Redis,   
        tool\_name: str,   
        failure\_threshold: int \= 5,   
        recovery\_timeout: int \= 60  
    ):  
        self.redis \= redis\_client  
        self.tool\_name \= tool\_name  
        self.failure\_threshold \= failure\_threshold  
        self.recovery\_timeout \= recovery\_timeout  
        self.state\_key \= f"circuit\_breaker:state:{tool\_name}"  
        self.failures\_key \= f"circuit\_breaker:failures:{tool\_name}"

    def get\_state(self) \-\> str:  
        state \= self.redis.get(self.state\_key)  
        return state.decode("utf-8") if state else "CLOSED"

    def record\_success(self):  
        self.redis.delete(self.failures\_key)  
        self.redis.set(self.state\_key, "CLOSED")

    def record\_failure(self):  
        failures \= self.redis.incr(self.failures\_key)  
        if failures \>= self.failure\_threshold:  
            \# Open the circuit and set an expiration time (the recovery window)  
            self.redis.setex(self.state\_key, self.recovery\_timeout, "OPEN")

    async def execute(self, func: Callable\[..., Any\], \*args, \*\*kwargs) \-\> Any:  
        """  
        Executes the wrapped function if the circuit is CLOSED.  
        Raises CircuitBreakerOpenException if the circuit is OPEN.  
        """  
        current\_state \= self.get\_state()  
        if current\_state \== "OPEN":  
            raise CircuitBreakerOpenException(  
                f"Circuit breaker is OPEN for tool: {self.tool\_name}. Blocking execution."  
            )

        try:  
            \# Execute the synchronous API call  
            result \= func(\*args, \*\*kwargs)  
            self.record\_success()  
            return result  
        except Exception as e:  
            self.record\_failure()  
            \# Bubble up the exception to let the retry policy handle it  
            raise e

### **4\. Implementation of Database Migration Scripts**

To support the above codebase modifications, the following SQL migrations must be executed on the Supabase database. These define the gated match function and create tables for secure auditing.

SQL  
\-- Migration 1: Gated Chunk Retrieval Function with ABAC predicates  
CREATE OR REPLACE FUNCTION match\_gated\_sop\_chunks (  
  query\_embedding vector(1536),  
  match\_threshold float,  
  match\_count int,  
  filter\_tenant\_id uuid,  
  filter\_requires\_internal boolean  
)  
RETURNS TABLE (  
  id uuid,  
  tenant\_id uuid,  
  content text,  
  requires\_internal boolean,  
  similarity float  
)  
LANGUAGE plpgsql  
SECURITY DEFINER  
AS $$  
BEGIN  
  RETURN QUERY  
  SELECT   
    chunks.id,  
    chunks.tenant\_id,  
    chunks.content,  
    chunks.requires\_internal,  
    1 \- (chunks.embedding \<=\> query\_embedding) AS similarity  
  FROM rag\_sop\_chunks AS chunks  
  WHERE   
    \-- Enforce absolute tenant isolation  
    chunks.tenant\_id \= filter\_tenant\_id  
    \-- Enforce role-based access boundaries  
    AND (chunks.requires\_internal \= FALSE OR filter\_requires\_internal \= TRUE)  
    \-- Enforce similarity threshold  
    AND (1 \- (chunks.embedding \<=\> query\_embedding)) \> match\_threshold  
  ORDER BY chunks.embedding \<=\> query\_embedding ASC  
  LIMIT match\_count;  
END;  
$$;

\-- Migration 2: Compliance-Grade Anti-Forgery and Geo-Anomaly Audit Table  
CREATE TABLE security\_compliance\_audit (  
  id uuid PRIMARY KEY DEFAULT gen\_random\_uuid(),  
  event\_timestamp timestamptz DEFAULT current\_timestamp,  
  tenant\_id uuid NOT NULL,  
  session\_id varchar(64) NOT NULL,  
  source\_ip inet NOT NULL,  
  geolocation point,  
  is\_spoof\_detected boolean DEFAULT FALSE,  
  is\_geo\_fenced\_out boolean DEFAULT FALSE,  
  payload\_dump jsonb,  
  reported\_to\_soc boolean DEFAULT FALSE  
);

CREATE INDEX idx\_compliance\_tenant\_session ON security\_compliance\_audit(tenant\_id, session\_id);

## **Edge-Case Resolution Playbook**

           
            
      Incoming Request ("Link is expired", No Session ID)  
                              │  
                              ▼  
                ┌───────────────────────────┐  
                │   WorkflowClassifier      │  
                └───────────────────────────┘  
                              │  
                    (Identifies Link Issue)  
                              ▼  
                ┌───────────────────────────┐  
                │   SlotFillingProcessor    │  
                └───────────────────────────┘  
                              │  
                    (Detects Missing Slots)  
                              ▼  
                  
                              │  
                     (Persists State)  
                              ▼  
                    ┌──────────────────┐  
                    │  Redis Cache     │  
                    └──────────────────┘  
                              │  
                    (Generates Response)  
                              ▼  
             "Please provide your 10-digit mobile..."

### **Case A: Unstructured Email Processing Flow**

A bank agent or customer emails KwikID support stating: "The link is expired", without providing a session ID or mobile number.

1. **Ingress and Preprocessing**: The email webhook ingests the payload.22 The raw body is passed to the WorkflowClassifier and the SlotFillingProcessor.22  
2. **Intent Classification**: The WorkflowClassifier identifies the intent as LINK\_EXPIRED with a high confidence score of ![][image10].22  
3. **Slot Extraction Execution**:  
   * The SlotFillingProcessor executes extract\_implicit\_entities() on the email body.22  
   * No mobile numbers or KID session strings matching regex patterns are detected.22  
4. **State Evaluation and Suspension**:  
   * The processor invokes process\_slots(). It detects that both session\_id and phone\_number are missing.22  
   * Instead of generating a tool execution or routing to the ActionPlanner, the execution engine halts and sets the session state to CLARIFY\_PARAMETER.16  
5. **State Preservation**: The processor serializes the partial state and registers it under the user's email hash in Redis with a 30-minute Time-To-Live (TTL).19  
6. **Response Generation**: The system bypasses generation and outputs a structured clarification response: "To regenerate your Video KYC link, please reply with your registered 10-digit mobile number or your application reference ID."  
7. **Subsequent Turn Execution**:  
   * The customer replies: "My phone number is 9876543210." 29  
   * The webhook intercepts the response, loads the state from Redis, parses the phone number, validates it against the schema, and saves the updated state.19  
   * The system then prompts for the remaining session\_id parameter to complete the extraction.16 Once all slots are validated, the tool is queued for execution.16

           
            
      Execution Request for \`regenerate\_vkyc\_link\`  
                              │  
                              ▼  
                ┌───────────────────────────┐  
                │    IdempotencyManager     │  
                └───────────────────────────┘  
                              │  
                     (Generates Key Hash)  
                              ▼  
                ┌───────────────────────────┐  
                │   RedisCircuitBreaker     │  
                └───────────────────────────┘  
                              │  
                      (Checks CB State)  
                              ▼  
                   
                              │  
                   (Executes API Call)  
                              ▼  
                       
                              │  
                   (Intercepts Timeout)  
                              ▼  
                  
                              │  
                     (Aborts Retries)  
                              ▼  
                ┌───────────────────────────┐  
                │   ApprovalGate Escalation │  
                └───────────────────────────┘  
                              │  
                   (Generates Audit Log)  
                              ▼  
              Sends Alert to Telegram & Freshdesk

### **Case B: Unstable Banking API Endpoint Flow**

The system executes the regenerate\_vkyc\_link tool, but the downstream bank gateway returns a 504 Gateway Timeout.

1. **Action Evaluation**: The ActionPlanner evaluates the context and receives a high confidence score.22 It identifies the target tool as regenerate\_vkyc\_link.22  
2. **Risk and Policy Assessment**:  
   * The tool is flagged as ToolRisk.HIGH and is non-reversible.22  
   * The IdempotencyManager computes a SHA-256 hash using the validated customer parameter set (phone\_number, session\_id, tenant\_id) and verifies that no identical key is active in Redis.22  
3. **Circuit Breaker Guard**:  
   * The request enters the RedisCircuitBreaker wrapper.11  
   * The circuit state is verified as CLOSED.11  
4. **Downstream Failure Capture**:  
   * The execution thread calls the downstream bank gateway.22  
   * The gateway fails to respond within the 5.0-second timeout window, throwing an HTTP 504 Gateway Timeout exception.22  
5. **Circuit Breaker State Update**:  
   * The RedisCircuitBreaker catches the timeout, increments the failure count in Redis, and compares it against the threshold of 5 consecutive failures.11  
   * If the limit is exceeded, the circuit state is updated to OPEN in Redis with a 60-second recovery timeout, shielding the system from subsequent calls.11  
6. **State-Changing Retry Prevention**:  
   * The RETRY\_POLICY interceptor checks the tool's risk level.22  
   * Because the tool is classified as HIGH risk, the system enforces the zero-retry rule for state-changing actions, aborting further calls to prevent side effects (such as duplicate SMS transactions).22  
7. **Approval Gate Escalation and Rollback**:  
   * The transaction state is marked as FAILED. The ApprovalGate is invoked with a payload detailing the failure, automatically escalating the ticket.22  
   * The system logs a complete diagnostic payload to the approval\_requests table, setting the status to PENDING\_MANUAL\_REVIEW.22  
8. **Multi-Channel Auditing and Notification**:  
   * The system posts a private note to the parent Freshdesk ticket explaining the downtime, along with details of the failure trace.22  
   * It sends an alert to the banking operations team's Telegram channel: " Bank Gateway Timeout for Tool regenerate\_vkyc\_link. Session ID: KID-983174. Request ID: req-88912-z.".22  
   * The customer-facing thread receives a polite fallback message: "We are currently experiencing technical difficulties with our banking partner's integration. Our support team has been notified and is regenerating your link manually.".22

| State Dimension | Case A Lifecycle State | Case B Lifecycle State |
| :---- | :---- | :---- |
| **Initial Classification** | LINK\_EXPIRED (Confidence: ![][image10]).22 | REGENERATE\_LINK (Confidence: ![][image11]).22 |
| **Active Extraction Schema** | Pydantic validation: VKYCParameters.19 | Pydantic validation: VKYCParameters.19 |
| **Missing Parameter Mitigation** | Redirection to state: CLARIFY\_PARAMETER.16 | Bypassed; all parameters present at execution ingress.22 |
| **Context Storage Layer** | Redis serialization (TTL: 1800s).19 | Memory episode entry in tool\_executions.22 |
| **Execution Block Isolation** | Suspended; execution blocked at gateway ingress.11 | RedisCircuitBreaker checks circuit status.11 |
| **API Failure Code** | N/A (Client-side parameter hold-up).18 | HTTP 504 Gateway Timeout from banking gateway.22 |
| **Retry Sequence Policy** | N/A.22 | Zero retries permitted on state-changing high-risk actions.22 |
| **Downstream Escalation Trigger** | Interactive single-topic prompts sent to customer.16 | Entry logged to approval\_requests and Telegram notification.22 |

## **Strategic Technical Recommendations**

### **1\. Unified Event-Driven Architecture**

To ensure operational stability as the system scales to handle over 8,000 daily sessions, the integration of Phase 2B components should be managed using an asynchronous event-driven model.10 The FastAPI ingress layer should quickly write incoming webhook events to a Redis-backed queue.22  
A dedicated background task processor should then consume and process these tasks, decoupling the external chat interface from downstream API latencies. This architectural separation ensures that the system meets its SLA metric of a sub-6-second P50 latency, even during periods of heavy query volume or downstream banking service outages.22

                
                 
  Incoming Freshdesk Webhook  
              │  
              ▼  
    ┌───────────────────┐  
    │  FastAPI Ingress  │ (Resolves quickly, returns HTTP 202 Accepted)  
    └───────────────────┘  
              │  
              ▼  
    ┌───────────────────┐  
    │ Redis Stream/Queue│ (Task: KID-88219-Event)  
    └───────────────────┘  
              │  
      ┌───────┴───────┐  
      ▼               ▼  
┌───────────┐   ┌───────────┐  
│ Worker 1  │   │ Worker 2  │ (Decoupled Background Processors)  
└───────────┘   └───────────┘  
      │               │  
      ▼               ▼  
┌───────────────────────────┐  
│ Gated Retrieval & Tool    │ (External Downstream Call via Circuit Breaker)  
│ Execution (Circuit Closed)│  
└───────────────────────────┘

### **2\. Implementation of Semantic Chunk-Level Feedback Weights**

The proposed adaptive retrieval mechanism uses user feedback to apply a multiplicative weight to chunk rankings.22 This feedback loop should be structured using a Bayesian update strategy or the Wilson score lower bound.22 The adjustment modifier (![][image8]) is calculated as follows:  
![][image12]  
where ![][image13] is the positive feedback ratio, ![][image14] is the total feedback count (with a minimum activation threshold of ![][image15]), and ![][image16] representing a ![][image17] confidence interval.22 Applying this mathematical threshold prevents the retrieval engine from over-adjusting rankings based on a few outlying feedback signals, maintaining retrieval stability.

### **3\. Comprehensive Compliance and Security Auditing**

In accordance with Section 18 of the RBI Master Direction on V-CIP, the system must deploy real-time geo-fencing checks alongside its tool execution pipeline.9 Before the system executes a link-generation or database-modification tool, a middleware check must verify the user's IP-origin and coordinates.9  
If the client's connection originates from outside India or shows signs of location spoofing, the execution must fail safe immediately.9 This event must be flagged as an identity-anomaly alert in the security\_compliance\_audit table and routed directly to the bank's internal Security Operations Center (SOC).9

### **4\. Human-in-the-Loop Operational Workflows**

To ensure seamless coordination between automated and human agents, the pending approval queue (approval\_requests) must be tightly integrated with the bank's existing support workflows.22  
When an action is routed to human review, the automated agent should lock the ticket thread, post the diagnostic parameters, and set the ticket state to awaiting\_agent\_validation.22 If a support representative approves the action, the backend executes the corresponding tool using the previously validated parameters.22 If the action is rejected or expires under the 4-hour SLA window, the request is permanently deleted, the ticket is unlocked, and the thread is routed back to the manual support queue.22 This safeguards the system against unauthorized or unverified state mutations.

#### **Works cited**

1. Best AI Customer Support Platforms for Fintech in 2026 \- Lorikeet, accessed May 27, 2026, [https://www.lorikeetcx.ai/articles/ai-customer-support-fintech-2026](https://www.lorikeetcx.ai/articles/ai-customer-support-fintech-2026)  
2. Not prompt engineering not context engineering- this is how ai agents should be built now : r/AI\_Agents \- Reddit, accessed May 27, 2026, [https://www.reddit.com/r/AI\_Agents/comments/1sbjl67/not\_prompt\_engineering\_not\_context\_engineering/](https://www.reddit.com/r/AI_Agents/comments/1sbjl67/not_prompt_engineering_not_context_engineering/)  
3. Securing the Agent: Vendor-Neutral, Multitenant Enterprise Retrieval and Tool Use \- arXiv, accessed May 27, 2026, [https://arxiv.org/pdf/2605.05287](https://arxiv.org/pdf/2605.05287)  
4. Security Challenges of LLM Integration in Multi-Tenant SaaS: Threats, Vulnerabilities, and Mitigations \- Cybersecurity Journal, accessed May 27, 2026, [https://cybersecurityjournal.info/uploads/archivepdf/45572026.3121.pdf](https://cybersecurityjournal.info/uploads/archivepdf/45572026.3121.pdf)  
5. SOP-Agent Framework Overview \- Emergent Mind, accessed May 27, 2026, [https://www.emergentmind.com/topics/sop-agent-framework](https://www.emergentmind.com/topics/sop-agent-framework)  
6. Sierra AI Pricing vs Alternatives: Enterprise Cost Guide, accessed May 27, 2026, [https://www.nurix.ai/blogs/outcome-based-sierra-ai-pricing-models](https://www.nurix.ai/blogs/outcome-based-sierra-ai-pricing-models)  
7. Ecommerce Customer Service Automation with the \#1 AI Agent | Fin, accessed May 27, 2026, [https://fin.ai/solutions/ecommerce](https://fin.ai/solutions/ecommerce)  
8. KYC Outsourcing: When to Outsource KYC and How to Choose a Provider \- HyperVerge, accessed May 27, 2026, [https://hyperverge.co/blog/kyc-outsourcing/](https://hyperverge.co/blog/kyc-outsourcing/)  
9. RBI Video KYC Deepfake Guidelines: 2026 Compliance Guide \- HyperVerge, accessed May 27, 2026, [https://hyperverge.co/blog/rbi-video-kyc-deepfake-guidelines/](https://hyperverge.co/blog/rbi-video-kyc-deepfake-guidelines/)  
10. Kwik.ID: Best VCIP Digital Video KYC Solution Providers Tool for Banks, India, accessed May 27, 2026, [https://getkwikid.com/](https://getkwikid.com/)  
11. Building a Least-Privilege AI Agent Gateway for Infrastructure Automation with MCP, OPA, and Ephemeral Runners \- InfoQ, accessed May 27, 2026, [https://www.infoq.com/articles/building-ai-agent-gateway-mcp/](https://www.infoq.com/articles/building-ai-agent-gateway-mcp/)  
12. COPE: How to Ship Embedded AI Agents That Stay Grounded, Auditable, and Fast | by SprinklrAI, accessed May 27, 2026, [https://engineering.sprinklr.com/building-embedded-ai-agents-that-works-the-sprinklr-copilot-blueprint-bc2505f5c6bd](https://engineering.sprinklr.com/building-embedded-ai-agents-that-works-the-sprinklr-copilot-blueprint-bc2505f5c6bd)  
13. Conversational AI Glossary, Contact Center & AI Terms Explained | NiCE Cognigy, accessed May 27, 2026, [https://www.cognigy.com/glossary](https://www.cognigy.com/glossary)  
14. The Klarna Problem Has a Two-Sentence Solution | by Micheal Bee | Medium, accessed May 27, 2026, [https://medium.com/@mbonsign/the-klarna-problem-has-a-two-sentence-solution-40f0cfe56b3f](https://medium.com/@mbonsign/the-klarna-problem-has-a-two-sentence-solution-40f0cfe56b3f)  
15. What's Inside Klarna's AI Architecture? \- YouTube, accessed May 27, 2026, [https://www.youtube.com/shorts/IW8I9uEaEso](https://www.youtube.com/shorts/IW8I9uEaEso)  
16. Intent vs Context: The Dual Pillars of High-Performance AI Agents | by Pete Cleary | Medium, accessed May 27, 2026, [https://medium.com/@pete.cleary\_33484/intent-vs-context-the-dual-pillars-of-high-performance-ai-agents-ad618c0279fa](https://medium.com/@pete.cleary_33484/intent-vs-context-the-dual-pillars-of-high-performance-ai-agents-ad618c0279fa)  
17. HYVE: Hybrid Views for LLM Context Engineering over Machine Data \- arXiv, accessed May 27, 2026, [https://arxiv.org/html/2604.05400v1](https://arxiv.org/html/2604.05400v1)  
18. HOW UNDERSPECIFIED PROMPTS SHAPE TOOL-CALLING LLM AGENTS IN SCIENTIFIC WORKFLOWS Ahmed Osama Mohamed Muharram A THESIS in Comput, accessed May 27, 2026, [https://www.cis.upenn.edu/\~ccb/publications/masters-theses/Ahmed-Muharram-masters-thesis-2026.pdf](https://www.cis.upenn.edu/~ccb/publications/masters-theses/Ahmed-Muharram-masters-thesis-2026.pdf)  
19. Distribution Agreement In presenting this dissertation as a partial fulfillment of the requirements for an advanced degree from, accessed May 27, 2026, [https://etd.library.emory.edu/downloads/dr26xz98s?locale=en](https://etd.library.emory.edu/downloads/dr26xz98s?locale=en)  
20. Automating the Initial Development of Intent-Based Task-Oriented Dialog Systems Using Large Language Models \- Digibug, accessed May 27, 2026, [https://digibug.ugr.es/bitstream/handle/10481/112654/TSP\_CMC\_75777.pdf?sequence=1\&isAllowed=y](https://digibug.ugr.es/bitstream/handle/10481/112654/TSP_CMC_75777.pdf?sequence=1&isAllowed=y)  
21. ACM CAIS 2026 \- alphaXiv, accessed May 27, 2026, [https://www.alphaxiv.org/acm-cais](https://www.alphaxiv.org/acm-cais)  
22. BIG\_PHASE\_2\_MASTER\_ARCHITECTURE.md  
23. Securing the Agent: Vendor-Neutral, Multitenant Enterprise Retrieval and Tool Use \- arXiv, accessed May 27, 2026, [https://arxiv.org/abs/2605.05287](https://arxiv.org/abs/2605.05287)  
24. Securing the Agent: Vendor-Neutral, Multitenant Enterprise Retrieval and Tool Use \- arXiv, accessed May 27, 2026, [https://arxiv.org/html/2605.05287v1](https://arxiv.org/html/2605.05287v1)  
25. Are enterprise teams underestimating LLM security risks in production systems? \- Reddit, accessed May 27, 2026, [https://www.reddit.com/r/softwarearchitecture/comments/1tgkmo5/are\_enterprise\_teams\_underestimating\_llm\_security/](https://www.reddit.com/r/softwarearchitecture/comments/1tgkmo5/are_enterprise_teams_underestimating_llm_security/)  
26. What Is LLM (Large Language Model) Security? \- SentinelOne, accessed May 27, 2026, [https://www.sentinelone.com/cybersecurity-101/data-and-ai/llm-security/](https://www.sentinelone.com/cybersecurity-101/data-and-ai/llm-security/)  
27. Synthetic SOP Generation Framework \- Emergent Mind, accessed May 27, 2026, [https://www.emergentmind.com/topics/synthetic-sop-generation-framework](https://www.emergentmind.com/topics/synthetic-sop-generation-framework)  
28. SOP-Bench: Complex Industrial SOPs for Evaluating LLM Agents \- arXiv, accessed May 27, 2026, [https://arxiv.org/html/2506.08119v1](https://arxiv.org/html/2506.08119v1)  
29. Agent-S: LLM Agentic workflow to automate Standard Operating Procedures \- arXiv, accessed May 27, 2026, [https://arxiv.org/html/2503.15520v1](https://arxiv.org/html/2503.15520v1)  
30. (PDF) Flow-of-Action: SOP Enhanced LLM-Based Multi-Agent System for Root Cause Analysis \- ResearchGate, accessed May 27, 2026, [https://www.researchgate.net/publication/388955089\_Flow-of-Action\_SOP\_Enhanced\_LLM-Based\_Multi-Agent\_System\_for\_Root\_Cause\_Analysis](https://www.researchgate.net/publication/388955089_Flow-of-Action_SOP_Enhanced_LLM-Based_Multi-Agent_System_for_Root_Cause_Analysis)  
31. \[Literature Review\] Flow-of-Action: SOP Enhanced LLM-Based Multi-Agent System for Root Cause Analysis \- Moonlight, accessed May 27, 2026, [https://www.themoonlight.io/en/review/flow-of-action-sop-enhanced-llm-based-multi-agent-system-for-root-cause-analysis](https://www.themoonlight.io/en/review/flow-of-action-sop-enhanced-llm-based-multi-agent-system-for-root-cause-analysis)  
32. Flow-of-Action: SOP Enhanced LLM-Based Multi-Agent System for Root Cause Analysis, accessed May 27, 2026, [https://openreview.net/forum?id=X7dQuJqs8c](https://openreview.net/forum?id=X7dQuJqs8c)  
33. SOPRAG: Multi-view Graph Experts Retrieval for Industrial Standard Operating Procedures, accessed May 27, 2026, [https://arxiv.org/html/2602.01858v1](https://arxiv.org/html/2602.01858v1)  
34. Intercom Fin Pricing: Worth It? 10 Alternatives \- Coworker AI, accessed May 27, 2026, [https://coworker.ai/blog/intercom-fin-pricing](https://coworker.ai/blog/intercom-fin-pricing)  
35. Sierra: Better customer experiences, accessed May 27, 2026, [https://sierra.ai/](https://sierra.ai/)  
36. What is Agentic AI? How it works, use cases & future scope \- Salesmate, accessed May 27, 2026, [https://www.salesmate.io/blog/what-is-agentic-ai/](https://www.salesmate.io/blog/what-is-agentic-ai/)  
37. Best Video KYC Solutions in India 2026: Complete Buyer's Guide \- BASEKYC Blog, accessed May 27, 2026, [https://basekyc.in/blog/best-video-kyc-solution-india.html](https://basekyc.in/blog/best-video-kyc-solution-india.html)  
38. Verify Customer Identity | Video PD & KYC | KwikID \- Think360.ai, accessed May 27, 2026, [https://think360.ai/in/kwikid/](https://think360.ai/in/kwikid/)

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAaCAYAAAC+aNwHAAAA+UlEQVR4XmNgGAXogBWIxYFYEg0TDXgYIBqeAvF/IFaG8kHYCir2F4gZYRpwgW8MEMXooJMBIm6LLoEMQKaDFD1BlwCCIgaI3EJ0CWQgyABRtBVdAgjWMEDk0tElkEE0EP8DYhc0cX4GiOYraOIYYA4DxPkyDIhYyQLihwyQMOBGKMUE+kD8CYirkMSYGSAh/wtJDCcAOR/kTHTnz4eKC6OJY4DTDBCFHGjisMDTRBPHAF8ZsMf/XQaION5UiS/+QeIgDEqpIAByqTZMEqRRB4gzoIpuALE8A0IxCPyEyoHSCAhsBGJOhDRhALJEDYhnAXEtmtwoGAUMACmyMaKGKCzBAAAAAElFTkSuQmCC>

[image2]: <data:image/png;base64,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>

[image3]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAFsAAAAZCAYAAABeplL+AAADd0lEQVR4Xu2YW4hOURTHl1BELrkMucyUS4miXOZF8kZJyaUUSUneKZMHxYNXUaJcniQlb8qTh+GF8CShKQ9KCkUJKbms36y9mn32nG++s7/5TKM5v/o3c9Y++3L+Z++19/lEampqasYUE1Udqvmq3aoFxeKxwRTVHtVl1f6kLGWcalMarMha1TfVn6DtUVmX6rpqXhSrSpfqirRWt21MSwMl7FI9UE0P152qvvDXOav6pPolZlJPVNYKzOzU7Ksh1krbjeouFOtrREg7T1mqeqfal8QZ+IXoults2S9WvZHm7TajzOzZqpNiqywX6p6QwXXXyygy+7bYQ7O8Y3yppzDwf2V2u5mleiyjyOxeGRmzp6o2q1aKbZJlZvvGuSKKOdTfoVqiGq/apjojlqN9090gxbo3xPpoZDb3HhLbq3gxZf1m0cwUz3Wp2cQQm2FMrtnsA/dUp1WTQ4w8yh4Rm00/pClSWtw2RnLf8XDdJTYRSGvvxepjFHXjnM3LORBiq8XGzX30wzjuhGuH9oa9ypqZsldsQBuTuJvNoGNyzT4n1g6mxSwL8fgB6atXim1vVb0Ve0EO9W5G10Dd2Gyg7bKZ7c8QPxu5vrLZF8WObamelsTQHKvWT6fqs2qLWKecPL6LDTQl12za+JkGpTyNlJl9VKy/2DDqPYmuIcfsCWIvyyfUeRm8sodkOGbDNbFjHSYfk/blbNqgrZSqZpPjqe9mYBT1TvkNgRyzgVzvZrv86NsyVU1J+R2UMtJmA/fdFTPjiNgekBqTazbwAtlk3WxSXrpHZZEOPIVBP5JiR41mD+SaTXqirZSqZnNCqLLEhzLb6/eGGH2/CDGH+mm6yqaZKctVH8UG4htGt+q5am64jsk12zdIP4k4fLVWNZucmn6spJSZzQcNadHNvi92EPBniGfxJLF9YGYUy6aKKTtVr2Qgb78sFvdDO77cYlWZDfxkwL1fxV7sQ7Ezt7dB2z7TYwGnGNJZWobY0IExxPH4mdeEGP2uCzH66lM9E/sJ4oPqhww+MWVTxWzgcM/Gyazmo6HdMPNWqWaEa/+wKVs9DunskuqwFI3gw4axkv7S3F1GhxQnBG0tEpvZnLUpH7bRwPL4X+Fj5IuY6SnEKOOemjbAbLulOijF/MrKYyVS1pYZWTMASx1zXwfxP0u/pqampqamIX8Byarw5pRbQXcAAAAASUVORK5CYII=>

[image4]: <data:image/png;base64,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>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAFgAAAAaCAYAAAAzBZtTAAADpUlEQVR4Xu2Zy6vNURTHl1Dk/Yg86uaR8pgJETPCgAET5Q9gTBFRJDMDyUDyCMmApBAD5cZEDEyIibokBsIEJXmsz12/7ezfuvucuy+/e9Lx+9Q396y9z977t/daa6/fIVJTU1PTNoarjqiG+YYOYZTqhmqob/gThqh2qq6p7qj2l5uTnBTr62GsSapphUaXm3vBRr/BgsMP808tPrdihDT6j4/sXapd8hdrnaj6obrq7JzeT0lvICxVbffGAk58iuqQ2BhoS6mHbfDpom2FWP8qYfy1qk9ic5wqN5c4qPqg+qzaKOYc8YYeVb2JPmczV/VEtUfSYfBQbHH+9Eaqrot5RiuuqG6LjcG/ng2qHm+skPViz8f8N11bDGu7q7qvGuPaYKHqnQwwFY4Tm/ieb4hYqfqm2ubsx1VfnS3FU9UMaXixPygeeoezVQnjs3bmfunaAkThKrHnxHubQdsBb2wGD0r4fletcW0xi8XCxodXj+qRs6XgAcltz8Qecla5WZ6LHeJgEcZnbp7DQwTfksYhzC83l5iteiDmmP2CBzLgVt/gIITpd87Zse12Ns9kaWweG/ta+i6QFDKgsBsgYXzmZs2eE2KXH5GWavfQhz3plzAhp9KKvWL9jjk7tk3O5mFzSQ9AxHCoXKbrChue7VNPlcTjE21+A7lU8WAgPfj2FDmO1QsdcwZ8K31Dh9uZcCN9NAOvwXtiuBgZ62PxebWYl+cwT3VB+i+1Yhg/XMJEIHOHcnGZ6nLxN2ulrbv43Ar6+WhOkrvBoV8cxjkbzMZRgXjieQ9LfnogtfA9qoJciL4wfojEcKBsLpsM2GjzUZqi0g0mBdCHGjAmZ4OpDFJl0QFpPAwXUC54MN/J9eCZUh6fg2Fe1kzJFdfcrPW9tL7gApVtMAugfqSE87cmuY1cSgg2g/SQylWLxFIE32f8wYK1UbkEQjW0WXU+sodU1i3pt00Pe+YrqiTUfnROlWghV8YL9NCe2sDACzEvSrFc7PuDecF1i6WFwASxi463sXCxQajzW9W/Maw7q4og1Pgd4ZVqSWRfIJY7CYOxkd2DB170RrHX531iCzkjaa/gAL9Ied6qmCP2Ss78j1XTxSoYoo6UFV7t+dwl5r3BWYgu/yIUw08HuankN0xEOLHZ5DjewXO4JHlvcp0EXt4qaiuFWhYv/l/As8+KXZBtg/f3Zr+mdRpUUtwrbYVT5Y0wvjQ6FX5JowJpO/X/aNTU1NTU/DP8AlXXyBq1PLyiAAAAAElFTkSuQmCC>

[image6]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADYAAAAaCAYAAAD8K6+QAAACuklEQVR4Xu2XS6hOURTHl1Dk7YowUDLxmEjuiBnFgAGFYs7IgIFHGclM0s1IEgMpA5RMZPDFREzJSF1SogyJ5LF+1jnO2st5bN1zFX2/+ne/s9b69mN966y9r8iQIf8l01VnVdOioyeWqR6rpkTHn8CXj6puq+6rTqXuWi6KxdYxW7W00Ih0L26+VPEznH1Utds9Z7NQ9U11M9hnqb5L88KZ8FA0FrAJNnNabGzG2Z5EpAxUn1SPVKvFkuK5I/bLZbNK9VR1QjU1+IDBWFTM9kyxyZYEe+SS6pbYGAeDr4Sxr6jeqU6mrl9sVX2NxibmiU34IDocm1Rf5PdFXVB9DrY6nqnWi81zNfiABF1XnRGLWZ66E46p9kVjhCxRJmSBbDSxQfVBLPOecdWTYKuDmAViix6krp8cUJ1T3RWL8e9WhCSThNZGRcYZaH90BHZIfbaxkcE25kg1PvFvnA9Y4N7iM+8haoN3niST7EZei022MjoC1DxxY8GObVewRWgCCCjnj84HVArHBTDeeOVqhDiS3QgBqAuyTFy5QKBjdWZO0mS8lHS+42JHC5DcnAqAzrjcjZVxvq5zNkYZPnTPfPbzcbTQvGCLWBnyt4teNkapEXM+2HM2xnd9c6H5MBbf5ewrjw/eG85Jmkdb4yiZ8MYWi51vHAVlZktYQFeGKUPfSVkM821W3XN2ypByb12sgzGORKOHrBFU1+o5W/A9jw5HV+ZoThvdc3ke3pCqYUDO+VWS1RUZnHveK0kXsEbstkF7n+vsEX6xa9Eods06LLbYy1JdjWg+71Vri2fuhetUL8Ri96hWFL4muOVw4C+KjjooK0qKTVI+3O9y4KDMuXn0CRWyMxr7Zpt0H6h9wi8/EHv3Jx0aQdPtvm+4cLfdaXuFlk2T4D+Eyeat2Pv71/gn/oMeMmTIxPkB7NOYnKh2NV8AAAAASUVORK5CYII=>

[image7]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABMAAAAaCAYAAABVX2cEAAABCklEQVR4Xu2TsWrCUBSGf7GDguBSKmKX7j6Co0MfwM6OLu6Ckw/hK0iXri4+gZuTKLRTsXMnV/X/PYm5uSTeQKdCPviGnHtyck5OApT8lRptO1bTx1eeYHlBGrAiP/RM17SZyrBiA3qkY9g9d/mEFTrRV+9MqMDED2bxAEtUR+rul3ZTGUCftrxYJo+wZKFicnY7NaawhwYZIUmcISkY80z3znUuKvLhXGs8jali9Simrje3jDv06NaLaQFaxJxW6BLWfRAluZ0JLUKbPdAX+gV7aJAdkpfvoiIaVecLWIdBNGLHD8JujhdRaES9YK08j3dYMX06uej/e6Mr+k2HUcwnXkShEUtK/gUXx7orpVUl+1cAAAAASUVORK5CYII=>

[image8]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABYAAAAaCAYAAACzdqxAAAABTUlEQVR4Xu2TsUoDQRRFr6ighBAEUSQWkk5SiAh2dipYWGiTwi6lrYW1hT+QTwgkFtqJlXaW1jZWKoSAYCPYqvfmzbgzkY1jY7UHDuzO3Xm7+2YGKPhvxukcXXBOxfEPJuk8sufH4jhDhfRAj37QG1qKnoh5oZ/0EDZPHzaSB9ql93R2KPMs0XNY4cU4yqdFN+k7XRvKxBZt0z6scBJlekBXYJN243jAJa3D2iWT2KczsBeo8GkcY5VWYOuh/CqO81EbxARsYifIpumZu67B8uMszmcZtnAeTXwK7tUCv1D6k0da/U5HoDbcBfcq/Oqu9QcNd602qAXyt70+4Ba2cJ43WHEdmutgfMeNa+ckoTZoN3jUBhU4oUfBuNqg8aQ26DhqocJjeQErsBGMiaT9q0J7sNV+pk1kR1M7RAX8y/SF225MrsPaVFBQ8Be+AFFkPBDuAmWCAAAAAElFTkSuQmCC>

[image9]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAJMAAAAaCAYAAACzWm4FAAAFYElEQVR4Xu2aW6itUxSAhxAit5NbLhuR3AkPSAlH5FYochTy4CRJFPF0PEgiRaSkNg/ygpeTSB7+eBEKD1IutcmlCBFyyWV8xhzWWGP/c5211l57bTnzq9FZc4z53+Ycc4wx59kijUaj0Wg01pALVA7IyjmyvcoDKttlwxZ4UWXPrCzcLHVbY5W4TOXLrJwjONLjKq9kQ+AqMftRST/qOu6LbbdsaKwODPRmlZ2yYY58q7IxKwsnqHwqA4d4X+Wlgfkf1qtcnXQOtt+zsrE6MInnZeUcwYlxkH2yofCGyk+h3an8ENrAPT5UOTDpARuLpc/WmCE40Z9ZOUe2UXlE5dRsKJyk8qsM219T+Su0nSWV17OysL+YbZbp7kGVr7Jya2ZR1jYFMMlLKnskPVCIPyOW0mIK/kT6nelZMcfrg3tlp5yWI1SeV3lU1jDaXSQ2CMihKveJDdTnKm+K7aR4SQrhH0s/CsjIR2L1w/Uqz5X2KcH+lAyegdxR9FHXFR2QLmI7sq3KbyrfiRW5b6ncJhZNHFLQzyq3it3nYZWdg/1GlW9Uriy/iYJdsHNdn2PATWI2olMEXd8COF/q9wJsjM80MBZvi33rrsk2CcxxnIsof6ic+W/PLbCjyv1iF7KdvbvocRh0rMIrxCbrYJWPxSYhQr9YP/ACX4f27mIvjJ6+64qea2ifFnSA7unQdniH22WwE+LdXxBzjCNLn5PFUse+pQ08h29zeI9rQ3tRhp3pCak7AM/Ddp3K5UHQ9e08cTqev0s2FLB1WTkGl4jVdCzguFAmZUHlA1nuRC7vyoRHM/7BG5KemzFREVZRXkmcBR0b2h6JMgzoy2KTt5fKnTIcURyu9egVIWJiw4kcVuRh5fcNYvZcNF9a9IeUNr9flcGzOfM5vfyGToYXR4Tok7+N8UN3T9LDfmIpkH/7wIaMA07zvZgTEZVmAXMRMw3ZqC+9j407EykvwgDlD+1zJocBO1flPVk+4M6CmI1VXCs8a86EY9fuC9Qn2HMU8FSOUwF1Cm3SNlEup4hO6s7EdaSVCE5cq31m5Uy3iD33LJmdI2UYNzJC3wIfG3emvjogfyiOxKTFE2FqGPp6uPXIVDs1pt7Bvnc2FGrO5GmxRif9zuTfF+9JWuU7vA6MabmT0c7UhTZ1Jgsjp35nVs4EFPzs1GYZmSIsCmq8FTGpM3UymLATxfqRshx3pjypQDS6V2wrTQGdi3ngWormDJM2ypk8MuU055HJB+rsYAPqwHhf6qLa0QT9YmT2grzvW8GdLb+Tg43njwvjheMyLzUHngavP4/PhkmZNM11Mhg8dj4MCIPmsJviWlZjF/QUz5+FNn2IUpnabu4MsXTChiDCjoYUg6OStpjgiNdaDs88JrTPkWHnGbWbI9XitEA6oB/fVWO1d3OMNdE1p+pJwTF5l1iPTgwX3yU2mA+pHC7mKAw2N8fJDlLZQWzXRYGGg10o9jFHl34clgGheEnsfhw+viO2C7ym9HPnYSII2ej4EJ7rUHNFp3O4hmMA3on/EwMG8TEZpNQFsWd4FOJ9eBf6ODyTAtx5Uix1OO5cPC/DTu8LMRtOvUn6o6szyjEBG+llWniP41R+EduVT8uijH7PucEHUYPgEO7Z6Kb1cl8lNfqel2GCiYzxyMHx2g57Xy3jB5N9BTWwiEhbo5zIWZL+KAscjnZST5HTMO0JODviFae4/yJMFJGittubB0TVTVk5BUS4jVlZuFjqtpUwjpNvVRBRNsva/tUAW/GVTPZ6WV7fOdhITY05wZ+A1P6EYx74hsEPRSeBqEpdVosS2NjNNuZI+0vLRqPRaDQajcb/hr8Bg41ZuQu0pM0AAAAASUVORK5CYII=>

[image10]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAWCAYAAAChWZ5EAAABm0lEQVR4Xu2VvyuFURjHH6GIUkgpdUsyK8VkxKAs2GwSym71FxgNWOw2KWG4YTMwECmDMjHIQBn8+H49z3PvOce93FsGw/upT+97nnPOe5/zPuc9VyQj4x8xBk/hDDyHT3F3WWpEx97CTXgP+6IR3xmGb7DTAwPwEbZYm9cDi//GBDyU4twcvLZrObjADwkSeIV3hW6lGz7D/iQesiz6oHQMY+nznCG4LkkCbNx4w2An43NJPGRLSifAxBlPaYPHcEmCBJqtkS8MUzy+k8RD8lJdAlewR6pMII2HbEjpBBij3KAO98i03f9ZAhyzBxeDGF+zJ1BnsUHRjen8WQIkJ/oFjcImuAJfJC7BPpwP2lEChI2jQrfSbnG+5mp5N4kvpJxf8IYHSUglXwGpl7jWhPMu7Z5lmEzkucExszZGdqWYsTMOt2FjEOPpxYle2174IFomrpSw3jxoOqxdCp6YUQn8JGy1dhc8s3iIvzb/MZbpQvQYJ/zEuNnSeU6D6J45EX3OQtwtMgVX4Yjoq60U/n+sia6+NunLyPiRTy8ScfkD3mSMAAAAAElFTkSuQmCC>

[image11]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAWCAYAAAChWZ5EAAABoElEQVR4Xu2VzysFURTHv8JClEJKqVeSYqUU/4BYKBtZsbNg4Y+wsbGwkoWU7O2kJIuHnQ2KlLJQSljIgrLw43vevcc7c2Ymz055n/o2c7733rnn3jNzB6hS5Q8xRp1QM9Q59ZxszqUGoe8NtUk9UP2JHsAU9ZmjEoPUE9UcY7nuR/8nJqhDlMcWqKt4VRaRnjiRwBt1q0Gki3qhBpxvWUB4iO8jnn3eATVuYuVOb2TAtWkQOqI/63zLFrITkMS/V0eOqV4TC7JD03LThNC5aFuNv+N8SxGVJeCZo840+CkB71vWkZ2A1lde0CzuEVcv5E2U51ukzx41b7xWlBOoM74iyW7AJJc3UZ7vKSB8QaNUI7VMvSK7BDLpCjLeK+l85Ly26Ms2/5aPKM8wgu9LVppIDhJLJV+BUI90rWXcpfMEPQ9SCewinbF8t9tUg/HekaxtD/WIUCYpmTCEcJK2x9hSRBgvi0ugJ2FLjDup0+hb9OXSyaRMFwjHuNCNcAr6cYrscmYCyiS1So0gbG2lyP9jDWH1ta7N0kctebPK/+YLRXxuKVMXDpUAAAAASUVORK5CYII=>

[image12]: <data:image/png;base64,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>

[image13]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAaCAYAAACO5M0mAAAA7klEQVR4XtWSvQ5BQRCFj9ALkahErRCF6LRKjZ9e5wEkEq+hUElUOs+ATkIhCg3PQEWB+DlzZ+9aV1A7yZfcmT2Zn70L/K865EJCwQNXYahpQlr4YM6RhRMXAvGLIt9iKS/VoiaW9hmSsg6jNlmRMymTLVmSGxk4PsxJktzJgZSgXRom5y2UIEX1e8mR+RblyRG6lDePEIMam08fKibnF7LJNbSDrynUKEWsumSI1wveQY1WchWSdNumyZVUnZydZWMMcTImdQR+obQVY5+cDDPX4EvaykP4KakmG3/VHmqUwXt4fxRWNYds4Oyv9ACYszC8G6Wu4QAAAABJRU5ErkJggg==>

[image14]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAwAAAAbCAYAAABIpm7EAAAAwElEQVR4XmNgGAVDGnADsRsQs0L5zECsDMQOQMwBFYMDRiBuZoAo/g/E24HYCSruAcT/gFgYrhoIpIFYhwFiEkjDeyQ5YyD+CsSSSGJwK22A+DkQKyHJlTNADAE5GQOAJPcwoEruYIBowAAsQLwGiFvRxEHO+4kmBgYg5/xmQHUOPwPE9Byo+AUkOYYiqCQPkpg+A8TDII+DNC1HkmM4CcR/kQUYIM4EBSkoIDzR5BikgFgGXRAIBBjQgnQUDA4AAOUQHQGFuz9oAAAAAElFTkSuQmCC>

[image15]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADIAAAAaCAYAAAD1wA/qAAABz0lEQVR4Xu2WzysFURTHj1BEiIX82thRWAgbNkIs2LK2kIWVBWUhkYX8AYqFrJTslayxtJES9VhQJBGF8uP77c48d4739GbGW7mf+vSac2beu+fec+88EYfD4QjJLKzQwZgMwHs4BWfgJ9wK3JEFauE7XNeJGFzBYXXNYgqtWFZZgk+wA+aoXBg4aOqz4V0PWrGsUwQf4AnMVblMeYO31vW2mEJ6rFiAI7gP92ApPIYJMV/EwcRlBF7AUYneFlxZFsE9kxLesADzxdy4A7u9eD/8kL/ZxFwVrg4nJkwxfK4PrsEz2BZMf8NlKoY1YgoZVzkWUmXFosBJaYYvcFnlMoXfwdVI2yEF3mcnvIb1Vm5aTHHs96hwRg/EbP4SlQsLO4fj6dIJnzwxG2lRxTkDryqWCWxTf1/wMyo8neziW+EzvIMNVjwJ2yoBh1Sc1R+KWdZylUsH+/9G4p1WpFfM7+9aMb+QR9hixZNMinmIe8WHN/IhPjwBN61cKsbE/Cj3wl/Ag4ZjWrFifqvPWbEA/vlsw6Vja3Cjn8L2YPoHTToQE04qXwONVuxczDj5ikhJtZi/GJoyiX9ixaUOrnpWqpzjN+bhZYay/x0Oh+Mf8wWivV7TVlw/4wAAAABJRU5ErkJggg==>

[image16]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEoAAAAaCAYAAAAQXsqGAAACPklEQVR4Xu2XwUuVQRTFT6RQGZoVZSvbuBAlaRP+AeImaiGEiO1aFNFKqLCVBG0CNy0K2kSCtHRTUOJC/AOCIBLEjSIEbaKgFpXWOd0Zun7vvc83Yr0ezA8OfPfOvHnfnDdzZx6QyWQymcxf5hb1iTpVbCjhKLVFPacWw3Or7+BYpr5Sd2B9z21rbQLeUpvUD+on6jeqg1qgRkO8D2a2pGfPeWqJ6gpt16kX1AHfqVm4jTSjNmD9PW0hN+Vyz0Iucpiapx5RLS7fNKQa9R2VRgnlFl38BbYlPftRuepqonqgQeWs9vU16tW2Hv+WVKPUt5ZRMkdoxSheo2apb7Bt/iC078hZ/PkiSXVCS1n5WugXOAabSL3SMq+XVKM+o9KouPVUtEVniD9QV2AraQxmlmrcjhxyz93UGxc3ilSjLsImfNLlLsPG0LYUGktxceuNUx+p/kK+FJk0Ukw2gFSj4imnLdVODcJKh9960aj3IY5cCPmJQr4qQ9S7YrKBpBpVDR33GkNHv/A1yhONelrIV+U11eviIyi/V+hXm6PWE3T39yfrYzdG6Z09x2Fj3HC5MqP0nTXR5UsnnpZtH2wJn4EdqSnFd68pM+ogrE11JTIdcpq00Mn9BHZ6e17C+nnuwe5SpfNdoWaoVdgAUY260p+gemAvrve4CSuyfhIq2PE9I1dDHE+vyRAX/8JoXjK4O8QDsFOwdL4axA+k59NIuHz9Z+i4v089hl1dyrhEPaSGUWlmJpPJZDKZTGYP+AUiU4uJPIHR0QAAAABJRU5ErkJggg==>

[image17]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACYAAAAYCAYAAACWTY9zAAACPUlEQVR4Xu2VP0hVcRTHj5igJIQamSCYLhEiImZCOCrZYEsKDQ5CQw0uLUZOLg02NDQkhGANTYpbSza8VlsUtCIUDAQHkZacxPT77fzOu+fed+/zOQQN9wsf3v2d3/1zfuffE8mVK1P14Ae4Dj6Dd/Htv6oCE6A/Yc/UGHgNxsHFxB41Ca65dTXoArXO9hg8CNe94BDMSPRcDfgITkQdLKv74BhcCus20VPz1+tA9IUGn5ly+y3gJ2h1tufu2rQtFUaLH+HJvO6CXdDubGuiUchSB9gTddD01F0zQjyIBaCsLki6Y3SA9nvOdpZjDeAL6A5rOvI+2pZbotGqSCzWco754qVjN8Fb8B3cFq0zL6b/k2hU3oC5YKeTK8FesdIcYypp9yf+CtbBQzAa9hfdvmkL/BZ18GqwMVrkXLIuMTWB1WBbcvYh0OjWA+AIDDtbmp6JOmnaBx8CjGqmrAvviIb8JViW0lQmdUO0UxekfOuzrixaLB0emodnWgvBXrEGwZ/wS/ElHA9PindE46Eg+sGk6Cyj5etqWuKdylpuduuYOPR8iqhH4JtED9FBRpA1ZuK84khhmtIGMmdVsguZAe8Y3+HHS0zMOT9qp+YL2QxXinfoqTn5fcq45nOdzmbifdadXsmI9YHLbh3TpsS7kvX2y61NHJ6WWor3ML1p4uF6kkbR5+fdmofjLM0Ui5Ed8kJKZ5OX3fdKtATSxIiw07O0IToHd8BsfOvfakTi/5dJ1YnOwbQBnStXrv9Sp1Bpbn32pqG2AAAAAElFTkSuQmCC>