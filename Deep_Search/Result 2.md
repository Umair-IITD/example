# **Technical Report: Next-Generation Enterprise AI Support Agent Architecture for KwikID**

## **Executive Summary**

The landscape of enterprise customer support is undergoing a structural transition from passive, search-based text-generation systems to autonomous, stateful, and action-oriented agent architectures.1 First-generation Retrieval-Augmented Generation (RAG) pipelines, which are typically designed to parse static document indexes to answer queries, are fundamentally limited by their inability to persist execution state, plan multi-turn troubleshooting paths, safely execute transactional backend operations, or maintain end-to-end ticket ownership.3 For a compliance-sensitive B2B Software-as-a-Service (SaaS) onboarding platform like Think360.ai's KwikID—which serves verification agents, bank operations teams, and client administrators—support issues are inherently complex and transactional.4 Resolving errors in electronic Know Your Customer (eKYC) workflows, Video KYC (VKYC) routing, One-Time Password (OTP) delivery, or OCR document verification requires deep systems integration, strict compliance controls, and deterministic execution.4  
This report presents a detailed reverse-engineered analysis of the world's leading enterprise AI support architectures, including Sierra AI, Intercom Fin, Klarna AI Assistant, Zendesk AI, Salesforce Agentforce, Microsoft Copilot Studio, ServiceNow Now Assist, AWS Bedrock Agents, Anthropic, and OpenAI. Synthesizing these architectural patterns, this document outlines the blueprint for KwikID's Phase 2 evolution. By introducing graph-based state-machine orchestration, transactional checkpointing, dual-agent verification, out-of-band security validators, and local transformer-based escalation models, the proposed architecture transforms KwikID's support layer from a passive answering interface into an autonomous, secure, and resilient resolution system.

## **Reverse Engineering Findings**

### **Sierra AI: Constellation Orchestration and Declarative Skill Abstractions**

Sierra AI replaces single-model paradigms with a multi-agent "constellation" architecture.4 This design splits cognitive and execution duties among specialized, highly constrained models to prevent context overload and improve reliability.1 The system organizes these operations into three distinct agent classes:

* **Planner Agents:** These models parse unconstrained natural language inputs, resolve semantic ambiguity, and generate structured execution plans represented as Directed Acyclic Graphs (DAGs) rather than generating direct text responses.4  
* **Executor Agents:** Bound to sandboxed tool environments, these models execute specific system APIs, query databases, and write updates to external registries.4  
* **Validator Agents:** Operating as independent supervisor models, these agents audit proposed executor outputs against system rules and customer guidelines to prevent policy violations.4

                   
                          │  
                          ▼  
                  \[ Planner Agent \]  
                          │  
            (Generates Structured Task DAG)  
                          │  
                          ▼  
                  \[ Executor Agent \] \<───►  
                          │                 (APIs, CRMs, DBs)  
                  (Proposed Action)  
                          │  
                          ▼  
                 \[ Validator Agent \]  
                          │  
            (Audits against Policy Rules)  
                          │  
                          ▼  
                 \[ Executed Outcome \]

The execution layer is built on the Sierra Agent SDK, which uses a declarative programming language to define agent behaviors as composable "skills".7 These skills function as modular software components, allowing engineers to enforce deterministic control flows without writing brittle, imperative code.7 When executing high-risk transactions (e.g., account modifications or compliance disclosures), the Agent SDK bypasses the generative model entirely, outputting pre-validated language to ensure zero-hallucination execution.7  
To ensure safety in highly regulated fields, Sierra layers supervisor models directly over primary executors.7 If the primary agent has an error probability of ![][image1] and the supervisor has an error probability of ![][image2], the joint probability of an undetected execution failure, assuming error independence, is modeled as:  
![][image3]  
By pairing a primary executor operating at ![][image4] accuracy (![][image5]) with an independent supervisor operating at ![][image4] accuracy (![][image6]), the overall system error rate drops to ![][image7] (![][image8]), yielding a ![][image9] success rate in production.7

### **Intercom Fin: Purpose-Built Model Suites and ModernBERT Routing**

Intercom Fin avoids general-purpose LLM bottlenecks by deploying the Fin CX Model Suite—a pipeline of seven specialized, highly optimized models that handle discrete stages of the ticketing lifecycle 8:

* **Language Detector:** A RoBERTa-based classification model that identifies the user's language across 45 options, handling typos, short messages, and script mismatches.8  
* **Issue Summarizer:** A fine-tuned 14B model that extracts structured, concise summaries of core problems from unstructured conversation logs.8  
* **Fin Retrieval:** A bi-encoder model trained on production support data that retrieves relevant text chunks from integrated knowledge databases.8  
* **Fin Reranker:** A cross-encoder model that scores retrieved chunks and filters out outdated or low-confidence sources.8  
* **Fin Apex 1.0:** A post-trained model that generates grounded answers or decides if a query requires human intervention based on system policies.8  
* **Feedback Parser:** A model that classifies user sentiment, flags unresolved follow-ups, and detects interaction closure.8  
* **Escalation Router:** A sequence classifier built on a multi-task ModernBERT architecture.8

The **Escalation Router** evaluates conversation history against business rules with over ![][image10] accuracy, executing routing decisions ![][image11] seconds faster than general-purpose LLMs to minimize latency during traffic spikes.8

 ──\> ──\> ──\> ──\> \[ Apex 1.0 \] ──\>

Fin's core engine relies on a strict grounding framework.9 The model is constrained to cite only verified knowledge base sources, ensuring that if a query cannot be answered using the retrieved context, it executes a fallback pattern ("I don't know") and triggers the Escalation Router.9

### **Klarna AI Assistant: Stateful Graphs via LangGraph and LangSmith**

Klarna's enterprise support assistant, which handles over 2.3 million monthly conversations across 23 global markets, is built on a stateful multi-agent system using LangGraph and LangSmith.11 The system is organized around a Plan-and-Execute topology.13 A highly capable frontier model acts as the central planner, analyzing user intent and generating a sequence of tasks.13 These tasks are then distributed to cheaper, faster executor models that call transactional APIs (e.g., payment adjustments, refund triggers, or charge dispute updates).11

┌────────────────────────────────────────────────────────┐  
│                   LangGraph Runtime                    │  
│                                                        │  
│   ┌───────────────┐     Plan     ┌─────────────────┐   │  
│   │ Planner Node  │─────────────\>│ Executor Nodes  │   │  
│   └───────────────┘              └─────────────────┘   │  
│           ▲                               │            │  
│           │ Checkpoint                    │ Tool Call  │  
│           ▼                               ▼            │  
│   ┌───────────────┐              ┌─────────────────┐   │  
│   │ State DB      │\<─────────────│ External APIs   │   │  
│   │ (Postgres)    │   State Upd  └─────────────────┘   │  
│   └───────────────┘                                    │  
└────────────────────────────────────────────────────────┘

The defining features of Klarna's LangGraph implementation include:

* **Durable State Checkpointing:** The LangGraph execution engine automatically serializes the conversation state and transaction history into a persistent PostgreSQL database at every node transition.14 If a container restarts or an external API times out mid-workflow, the agent resumes execution from the exact saved checkpoint rather than restarting the entire sequence.14  
* **Stateful Reducers:** State schemas use reducer functions to govern how state variables are updated.15 This ensures that incremental updates from parallel tool calls are deterministically merged without overwriting critical session context.14  
* **Test-Driven Development with LangSmith:** Every agent execution path is traced, logged, and evaluated.12 Prompt engineering is optimized using meta-prompting models that evaluate prompt changes against historical gold-standard datasets to measure performance impact before production deployment.12

### **Zendesk AI: Native Triage Pipelines and REST Integration Nodes**

Zendesk's AI architecture focuses on embedding automated triage and server-to-server integration actions directly within the ticketing lifecycle.16 Rather than executing an unconstrained loop, Zendesk agents rely on custom conversation flow builders that combine generative AI replies with deterministic, scripted steps.17

                        
                                │  
                                ▼  
                     
               (Categorizes Intent & Sentiment)  
                                │  
                                ▼  
                     
              ┌─────────────────┴─────────────────┐  
              ▼                                   ▼  
     \[ Generative Node \]                 \[ Integration Node \]  
    (Answers via KB)                    (REST/OAuth API Call)

Key architectural characteristics include:

* **Integration Nodes:** When a conversation path requires external data, the runtime executes server-to-server HTTP requests (supporting REST, GraphQL, and OAuth 2.0 authentication).16  
* **Session-Bound State Management:** Data returned by these integration nodes is mapped to structured variables and stored in memory for the duration of the active chat session.16  
* **Intelligent Triage Engine:** Prior to agent activation, a classification pipeline reads incoming tickets and applies tags for customer intent, sentiment, and language.17 These tags are then used by the platform's core routing engine to allocate tickets to appropriate queues.17

### **Salesforce Agentforce: Atlas Reasoning Engine and Hyperforce**

Salesforce Agentforce is powered by the Atlas Reasoning Engine (ARE), a modular, pluggable reasoning orchestrator built on the Hyperforce framework.18 Atlas manages planning, tool utilization, memory access, and self-reflection cycles through an asynchronous, event-driven architecture.18

                      
                              │  
                              ▼  
                    
                              │  
                              ▼  
                   
                (Data Cloud Zero-Copy Retrieval)  
                              │  
                              ▼  
                      
              ┌───► (Thought-Act-Observe-Reflect) ───┐  
              │                                      │  
              └──────────────────────────────────────┘  
                              │  
                              ▼  
                     \[ Grounding Check \]  
                    (Einstein Trust Layer)  
                              │  
                              ▼  
                    \[ Execution Output \]

The Atlas execution sequence proceeds through five distinct stages 20:

* **Topic Classification:** ARE parses the incoming customer message and maps it to a predefined "Topic".20 Topics represent distinct semantic domains and constrain the agent's reasoning scope by injecting strict behavioral guidelines and allowed actions.20  
* **Context Assembly:** The engine retrieves relevant enterprise records using Salesforce Data Cloud.21 This architecture uses a "zero-copy" model.21 Instead of replicating or moving sensitive data to external vector databases, semantic searches are executed directly within the underlying data lake.21  
* **Reasoning Loop:** The agent runs a ReAct (Reason-Act-Observe) cycle.19 It generates chain-of-thought steps, selects and runs tools (e.g., Salesforce Flow, Apex Classes, or MuleSoft APIs), observes the results, and reflects on its progress.19  
* **Grounding Check:** Before exposing any response, the Einstein Trust Layer performs real-time verification.22 It checks that the generated response is strictly grounded in the retrieved source context, adheres to the topic guidelines, and does not violate PII masking or toxicity standards.20  
* **Execution and Side Effects:** The engine applies side effects by writing updates back to Salesforce core records via automated flows, logging conversation logs, or executing external transactional processes.18

### **Microsoft Copilot Studio: Orchestrator Routing and Dataverse Logging**

Microsoft Copilot Studio uses a multi-model orchestration framework that can route individual conversation turns through different models (e.g., routing a planning task to Claude Sonnet 4.6, a prompt execution task to GPT-4o mini, and a desktop interaction task to Claude Sonnet 4.5).24

                      \[ User Interaction \]  
                               │  
                               ▼  
                    \[ Copilot Orchestrator \]  
             ┌─────────────────┼─────────────────┐  
             ▼                 ▼                 ▼  
              
     (Plan / Route)       (Prompt Execution) (Computer Use)  
             │                 │                 │  
             └─────────────────┼─────────────────┘  
                               │  
                               ▼  
                    
                    (Plaintext Reasoning Log)

Key features of Copilot Studio's system design include:

* **Reasoning Trace Persistence:** The orchestrator writes its complete execution trace, including routing decisions, tool selection rationale, and safety evaluations, as plaintext to a Microsoft Dataverse ledger (conversationtranscripts.content.activities).24 This provides deep observability for developers but requires strict access controls to prevent data exposure.24  
* **Server-Side Request Forgery (SSRF) Protection:** The platform implements a strict validation layer for outbound HTTP integration nodes.24 Before executing a call, the validator performs a real-time DNS lookup, extracts the target IP address, and verifies that it does not point to local hosts (e.g., 127.0.0.1), cloud metadata services (e.g., 169.254.169.254), or private IP ranges.24  
* **Session Isolation:** The runtime uses unique browser-session conversation IDs to isolate active sessions, preventing cross-tenant context leaks during high-concurrency execution.24

### **ServiceNow Now Assist & Otto: Spoke Generators and Workflow Playbooks**

ServiceNow's Now Assist platform and its autonomous agent, Otto, are built directly on top of ServiceNow's workflow and data platforms.25 This architecture specializes in translating natural language commands into structured enterprise workflows.25

                     \[ Natural Language \]  
                              │  
                              ▼  
                    \[ Now Assist Creator \]  
            ┌─────────────────┴─────────────────┐  
            ▼                                   ▼  
                    \[ Playbook Engine \]  
   (API Collection Ingestion)          (Flow Studio Orchestration)  
            │                                   │  
            ▼                                   ▼  
   \[ Executable Code \]                

The core technical components include:

* **Spoke Generator:** This service ingest OpenAPI specifications or Postman collections to generate integration connectors ("spokes").26 It converts external API schemas into structured actions that can be called by generative models.26  
* **Workflow Playbooks:** Agents do not interact directly with systems. Instead, they trigger "playbooks" inside ServiceNow Workflow Studio.26 Playbooks represent structured, automated processes consisting of task stages, conditional logic, loops, and human-in-the-loop exception reviews.15  
* **Now Assist Guardian:** A moderation layer that scans incoming queries and outgoing responses for sensitive data, policy violations, and toxic language.28

### **AWS Bedrock Agents: Amazon States Language and Lambda Tool Isolation**

AWS Bedrock Agents provide a fully managed orchestration framework that lets developers build multi-agent systems using foundation models, tools, APIs, and memory.2

                   
                               │  
                               ▼  
                   
                               │  
                               ▼  
                   
             ┌─────────────────┴─────────────────┐  
             ▼                                   ▼  
     \[ Pre-Processing \]                 \[ Orchestration Loop \]  
     (Input Sanitation)                  (ReAct Model Turn)  
             │                                   │  
             ▼                                   ▼  
     \[ Post-Processing \]                 \[ Lambda Executor \]  
     (Output Formatting)                 (Sandbox API Tool)

The architecture consists of two primary layers:

* **AWS Step Functions Orchestration:** High-level workflows are managed by a Step Functions state machine defined using Amazon States Language (ASL).31 Step Functions handle conditional routing (Choice states), parallel task execution (Parallel states), retries, error catching, and operational tracking via CloudWatch.31  
* **Bedrock Agentic Loop:** Task execution is managed by the Bedrock Agent, which runs a structured reasoning loop 30:  
  * *Pre-Processing:* Cleans, sanitizes, and structures the incoming input.30  
  * *Orchestration Loop:* Decides whether to retrieve context from a knowledge base, call an external API, ask for clarification, or finalize the response.30  
  * *Lambda Executor:* API calls are executed in isolated, sandboxed AWS Lambda functions. The agent passes a JSON payload, and the Lambda returns a structured JSON response.32  
  * *Post-Processing:* Formats the final execution output into natural language.30

This design enforces a clean separation of concerns. The Step Functions state machine manages process coordination, Bedrock Agents handle reasoning and planning, and Lambda functions execute system interactions with strict IAM permissions.30

### **Anthropic: Context Compaction Hooks and Programmatic Tool Calling**

Anthropic's agent framework introduces specialized primitives designed to optimize context window utilization and improve tool execution reliability.33

                       
                               │  
                               ▼  
                     
             ┌─────────────────┴─────────────────┐  
             ▼                                   ▼  
    \[ PreCompact Hook \]                
   (History Compression)              (Local Python Engine)  
             │                                   │  
             ▼                                   ▼  
    \[ Cleaned Context \]               \[ Final Output Only \]

Key features of Anthropic's SDK design include:

* **Context Window Compaction Hooks:** To manage long-running agent sessions, the SDK provides a PreCompact hook.34 When cumulative tool call responses approach the context limit, the SDK intercepts a stop\_reason: "compaction" event and automatically compresses historical logs into a concise summary, preventing context overflow.34  
* **Programmatic Tool Calling:** Instead of requiring the LLM to execute a separate reasoning turn for every single API call, the model writes lightweight orchestration code (e.g., Python scripts) that runs in a sandboxed server environment (server\_tool\_use).35 The local script calls multiple APIs, processes the raw outputs, and returns only the final, filtered result to the model's primary context, reducing token costs and latency.35  
* **Dynamic Tool Search:** The SDK supports a tool\_search tool that dynamically retrieves relevant API schemas on-demand based on context.34 This avoids the need to pre-load hundreds of tool definitions into the system prompt, which can degrade planning accuracy.34

### **OpenAI Assistant API: Thread Persistence and Code Interpreter Sandboxing**

The OpenAI Assistant framework abstracts low-level state and thread management, allowing developers to build conversational agents with persistence.11

                        
                                │  
                                ▼  
                    \[ Assistant API Engine \]  
             ┌──────────────────┴──────────────────┐  
             ▼                                     ▼  
                    \[ Action Execution \]  
     (Automated Context)                 (Code Interpreter / APIs)

The core architecture is built around:

* **Persistent Threads:** The Assistant API manages conversation history automatically.12 Developers send new messages to a unique thread\_id, and the platform handles context window optimization, message truncation, and retrieval injection behind the scenes.12  
* **Sandboxed Code Interpreter:** The assistant can write and execute Python code in a secure, sandboxed environment.33 This allows the model to process large data files, generate charts, and solve mathematical problems locally without exposing raw data to the primary LLM context window.33  
* **File Search Retrieval:** The platform includes an integrated vector search engine that automatically chunks documents, generates embeddings, builds vector indexes, and retrieves relevant passages to ground the assistant's responses.12

## **Architecture Comparison Matrix**

The following matrix provides a comparative analysis of the core enterprise AI support platforms:

| Architectural Dimension | Sierra AI | Intercom Fin | Klarna AI Assistant | Zendesk AI | Salesforce Agentforce | Microsoft Copilot Studio | ServiceNow Now Assist | AWS Bedrock Agents | Anthropic | OpenAI |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| **Core Memory Model** | Multi-channel state managed via Agent Data Platform (ADP).6 | Stateless session-bound context; histories retrieved via API.1 | Graph-state schemas with persistent PostgreSQL DB checkpointing.14 | Session-bound context memory, discarded post-session termination.16 | Short- and long-term memory persisted in Data Cloud profile states.18 | Plaintext chain-of-thought traces logged directly to Dataverse ledger.24 | Unified ServiceNow Knowledge Graph and user profiles.25 | Managed session state variables persistent across active agent turns.32 | Dynamic Context Compaction hooks (PreCompact) with history pruning.34 | Persistent Threads API abstracting historical state management.12 |
| **Planning Paradigm** | Multi-agent "constellation" split into Planner, Executor, Validator.4 | Strict linear RAG pipeline augmented by Custom Procedure flows.3 | Plan-and-Execute topology using centralized planner models.13 | Graph-driven script flow matched with intent classification.17 | ReAct (Reason-Act-Observe-Reflect) loop per Topic block.19 | Multi-model Turn-by-Turn Orchestration across model providers.24 | Standardized task playbooks and dynamic subflow triggers.26 | SFN Orchestration with Choice states driving agentic loops.32 | Programmatic Tool Calling via code execution schemas (server\_tool\_use).35 | Code Interpreter logic loops combined with parallel tool runs.33 |
| **Action Execution** | REST/gRPC API calls orchestrated via Agent SDK skills.7 | Workflow Studio procedures and standard REST API payloads.3 | Direct system-of-record API calls via specialized sub-agents.11 | Server-to-server HTTP integration nodes (REST/GraphQL).16 | Salesforce Flows, Apex execution classes, and MuleSoft routes.22 | Sandbox browser task loops (Computer Use) & REST nodes.24 | ServiceNow Spokes, automated tasks, and REST endpoints.26 | Safe API execution via isolated AWS Lambda functions.32 | Local tool run engines operating within sandboxed runtimes.33 | Secure code interpreter runtimes executing Python blocks.33 |
| **Retrieval Design** | Context-engineered multi-source search (FAQ, docs).6 | Proprietary fin-cx-retrieval model \+ fin-cx-reranker.8 | Integrated semantic retrieval across payments and transaction DBs.11 | Contextual search over Help Center and Knowledge bases.17 | Zero-copy retrieval from Data Cloud.21 | Semantically grounded search over SharePoint document drives.24 | Search indexing over unified Knowledge Graphs.25 | Semantic searches over Bedrock Vector Databases and S3.30 | Dynamic schemas retrieved via dedicated Tool Search tools.34 | File Search vector index engines managing document chunking.12 |
| **Workflow Engine** | SDK-driven declarative skills composition.7 | Platform-native visual ticket workflow builder.3 | LangGraph state machines with typed reducer schemas.14 | Visual Flow Builder interface with scripted branching.17 | Asynchronous, event-driven graph-based cooperative swarms.18 | Visual Canvas editors compiling state rules to Dataverse.24 | Workflow Studio engine managing multi-activity flows.26 | AWS Step Functions executing ASL state flow rules.31 | Custom Graph runtime layers managed by developer frameworks.34 | Linear Thread logic with parallel call batching.37 |
| **Human Handoff Pattern** | Direct ticket creation and routing with state payload.1 | ModernBERT Escalation Router with de-duplicated issue logs.8 | Automated edge-routing to specialized human queues.12 | Triage tagging combined with routing rules.17 | Concierge Orchestrator routing to Omni-Channel queues.18 | Seamless transfer to Dynamics 365 or Omnichannel queues.40 | Flow routing to IT Service Management (ITSM) agents.25 | Workflow routing to human queues via Amazon SQS messages.32 | Explicit pause-and-resume hook interfaces.34 | Direct escalation flags returned in status responses.12 |
| **Trust Governance** | Layered Validator Agents and hardcoded non-LLM overrides.7 | Verification of model grounding and citation matching.9 | Human-in-the-loop nodes and LangSmith validation.12 | Persona constraints and standard field redaction rules.17 | Einstein Trust Layer, PII masking, and schema validators.19 | Host DNS checks, SSRF blocks, and content filters.24 | Now Assist Guardian checking for toxicity and sensitivity.28 | IAM boundary roles and Step Function retry models.32 | Strict prompt instructions and sandboxed code execution.33 | Sandboxed execution runtimes and static system rules.33 |
| **Observability Tracing** | Audit logging of state transitions and tool executes.4 | Step-by-step reporting via Fin AI Analytics.9 | LangSmith tracing of node inputs, tool calls, and LLM steps.12 | Platform event logs and execution timing matrices.17 | Step-by-step reasoning logs and token telemetry.19 | Full reasoning trace logs saved to Dataverse logs.24 | Performance Analytics reporting on generative KPIs.28 | CloudWatch trace tracking of state steps and LLM calls.32 | API invocation hooks tracking execution steps.34 | Detailed thread execution logs and API event audits.12 |
| **Evaluation Engine** | LLM-as-a-judge monitoring.6 | Human verification of deflection accuracy metrics.9 | Continuous LangSmith unit tests and meta-prompt testing.12 | Performance metrics based on deflection and resolution rates.17 | Agentforce Testing Center with scenario replay engines.19 | Playback analysis and built-in model performance evals.40 | Performance benchmarks against golden datasets.28 | Step Functions testing frameworks and validation checks.32 | Standard test assertion suites executing on tool runs.37 | Thread evaluation suites tracing accuracy and recall.12 |

## **Industry Best Practices**

### **Context Engineering and Dynamic Tool Loading**

Enterprise support systems frequently provide agents with access to hundreds of backend databases, internal APIs, and Standard Operating Procedures (SOPs).34 Attempting to load every API schema and instruction set into the primary LLM system prompt introduces severe context bloat, inflates API token costs, and degrades decision-making accuracy.34

                                \[ Query Context \]  
                                        │  
                                        ▼  
                            ┌───────────────────────┐  
                            │   Tool Search Tool    │  
                            └───────────────────────┘  
                                        │  
                        (Dynamically Retrieves Schemas)  
                                        │  
                                        ▼  
                            ┌───────────────────────┐  
                            │  Anthropic Executor   │  
                            │ ┌───────────────────┐ │  
                            │ │ server\_tool\_use   │ │  
                            │ └───────────────────┘ │  
                            └───────────────────────┘

To optimize performance, modern architectures use a dedicated **Tool Search** tool that queries a repository of available schemas and dynamically injects only the tool definitions required for the current task.34 In addition, platforms implement **Programmatic Tool Calling**.35 Instead of requiring the LLM to execute a separate reasoning turn for every single API call, the model writes lightweight orchestration scripts (e.g., Python code) that run in sandboxed server environments.33 This allows the agent to process raw database records locally and return only the final, filtered results to the LLM's primary context window.35

### **Transactional State Checkpointing and Durable Execution**

AI agent containers operating in cloud environments are transient and subject to unexpected terminations.14 Linear prompt chains cannot recover from mid-execution failures, leading to duplicate API requests and broken support workflows.1  
Durable execution frameworks resolve this by saving the entire execution graph state (including local variables, memory history, and next steps) to persistent storage at every node transition.14 If a process is interrupted, the orchestration engine deserializes the state from the last valid checkpoint and continues execution without repeating completed actions.14

### **Server-Side Request Forgery (SSRF) Defense**

When agents call external APIs or retrieve webhook definitions dynamically, they are highly susceptible to Server-Side Request Forgery (SSRF) and prompt injection exploits.24

                       
                                    │  
                                    ▼  
                        ┌──────────────────────┐  
                        │ DNS Resolution Layer │  
                        └──────────────────────┘  
                                    │  
                            (Extracts Target IP)  
                                    │  
                                    ▼  
                        ┌──────────────────────┐  
                        │ Subnet Policy Filter │  
                        └──────────────────────┘  
                         /                    \\  
                     
                       /                        \\  
                      ▼                          ▼  
                    

Robust architectures deploy dedicated validation layers that intercept outbound HTTP requests.24 Rather than relying on easily bypassed hostname blocklists, the validator performs a real-time DNS lookup, extracts the resolved IP address, and verifies it against a strict subnet filter.24 Outbound connections to local hosts (e.g., 127.0.0.1), private IP subnets (e.g., RFC 1918 ranges like 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), or link-local addresses (e.g., 169.254.169.254 for cloud metadata) are dropped immediately.24 The valid request is then executed directly against the verified IP address, preventing DNS rebinding vulnerabilities.24

### **Hierarchical Supervisory Frameworks**

To operate safely in highly regulated sectors like banking and KYC, support systems must deploy layered verification structures.7 A primary agent is paired with an independent supervisor model that evaluates the proposed action against strict compliance schemas before execution.7 If the supervisor detects a policy violation, the action is blocked, and the transaction is routed to a human operator.7

## **Support Agent Design Principles**

Implementing support agents in complex enterprise environments requires adhering to four architectural core principles:

1. **Strict State Machine Containment over Conversational Autonomy:** Agents must operate within explicit, graph-defined boundaries. Every edge transition must be governed by typed state variables, preventing models from straying into unconstrained execution paths.  
2. **Context Isolation and Least Privilege Access:** Agents must run under strict security scopes. Tool bindings must enforce Role-Based Access Control (RBAC), ensuring an agent cannot execute write operations or read sensitive records unless explicitly authorized by the user's active session permissions.  
3. **Complete Audit Trail Persistency:** Every decision, planning step, tool call payload, and verification response must be logged in a read-only audit ledger.19 This is critical for regulatory compliance and debugging production failures.  
4. **Decoupling Cognitive Orchestration from Execution Infrastructures:** The core planning loops and LLM configurations must remain decoupled from backend REST APIs, databases, and message brokers.18 This separation of concerns allows developers to swap planning models, upgrade tool schemas, or update API endpoints independently without breaking the system.18

## **KwikID-Specific Architecture**

### **High-Level Architectural Framework**

To transition Think360.ai's KwikID support platform from a passive RAG answering tool to a stateful resolution agent, the system must be redesigned into an event-driven, graph-based multi-agent architecture. This next-generation design integrates with the existing FastAPI backend, PostgreSQL/pgvector database, and Freshdesk ticket workflows.

                                 
                                          │  
                                          ▼  
                             ┌────────────────────────┐  
                             │    Ingestion Engine    │  
                             └────────────────────────┘  
                                          │  
                                          ▼  
                             ┌────────────────────────┐  
                             │  Topic Classification  │  
                             └────────────────────────┘  
                                          │  
                                          ▼  
                            ┌──────────────────────────┐  
                            │    Reasoning Engine      │  
                            │  ┌────────────────────┐  │  
                            │  │  Concierge Router  │  │  
                            │  └────────────────────┘  │  
                            │            │             │  
                            │            ▼             │  
                            │  ┌────────────────────┐  │  
                            │  │   Planner Agent    │  │  
                            │  └────────────────────┘  │  
                            └──────────────────────────┘  
                                         │  
                    ┌────────────────────┴────────────────────┐  
                    ▼                                         ▼  
         ┌─────────────────────┐                   ┌─────────────────────┐  
         │     RAG Engine      │                   │  API Executor Node  │  
         │  (Existing Stack)   │                   │ (FastAPI Services)  │  
         └─────────────────────┘                   └─────────────────────┘  
                    │                                         │  
                    └────────────────────┬────────────────────┘  
                                         ▼  
                             ┌────────────────────────┐  
                             │    Validator Agent     │  
                             └────────────────────────┘  
                                         │  
                                         ▼  
                             ┌────────────────────────┐  
                             │  State Checkpoint DB   │  
                             └────────────────────────┘  
                                         │  
                    ┌────────────────────┴────────────────────┐  
                    ▼                                         ▼  
       ┌────────────────────────┐                ┌────────────────────────┐  
       │   Response Generator   │                │   Escalation Router    │  
       │     (Freshdesk)        │                │  (ModernBERT Engine)   │  
       └────────────────────────┘                └────────────────────────┘

### **High-Level Component Layout**

* **Ingestion Engine (FastAPI):** Receives real-time Freshdesk webhook events triggered by new tickets or user responses. It normalizes payloads, sanitizes inputs, and places ticket events into an execution queue.  
* **Topic Classification Service:** A fine-tuned, high-speed classification model that maps the parsed ticket to a predefined, structured KwikID support topic (e.g., OTP\_DELIVERY\_FAILURE, VKYC\_ROUTING\_ERROR, OCR\_DOCUMENT\_MISMATCH, API\_TIMEOUT).  
* **Reasoning Engine (Concierge and Planner Agents):**  
  * *Concierge Router:* Extracts ticket history and active banking metadata, setting up the execution context.  
  * *Planner Agent:* Takes the mapped Topic and compiles a localized Directed Acyclic Graph (DAG) detailing the required troubleshooting steps.  
* **Action Execution Engine (API Executor Node):** A sandboxed execution environment that runs validated API integrations against KwikID systems (e.g., checking WebRTC logs, querying OCR confidence arrays, resending verification OTPs).  
* **RAG Engine (Existing Stack):** The current hybrid retrieval system (BM25 \+ pgvector \+ Rerank) repurposed as a read-only tool. The Planner calls this tool to retrieve Standard Operating Procedures (SOPs) or integration documentation.  
* **Validator Agent (Compliance Supervisor):** An independent model that runs policy checks on proposed action payloads and generated responses before they are executed or sent.  
* **State Checkpoint Database (PostgreSQL):** Stores the active graph state, transaction history, execution variables, and audit trails.  
* **Escalation Router (ModernBERT):** An automated classifier that detects integration failures, unresolved loops, or compliance violations, instantly routing the ticket to specialized human teams with a structured summary.

### **Component Relationship Schema**

The components of this system cooperate to ingest, analyze, and resolve incoming support requests:

| Component | Upstream Trigger | Downstream Action | Core Technology |
| :---- | :---- | :---- | :---- |
| **Ingestion Engine** | Freshdesk Webhook Event | Topic Classification Service | FastAPI, Redis Queue |
| **Topic Classification** | Normalized Ticket Payload | Ingestion Engine Queue | Fine-Tuned SetFit / BERT |
| **Reasoning Engine** | Classified Topic ID | Action Execution / RAG | LangGraph Runtime, GPT-4o |
| **API Executor Node** | Planner Graph Task | Validator Agent | Python Requests, SSRF Validator |
| **RAG Engine** | Planner Graph Query | Planner Context State | BM25, pgvector, Cross-Encoder |
| **Validator Agent** | Proposed Action / Response | State Checkpoint DB / Response | GPT-4o-mini, JSON Schema |
| **Escalation Router** | Validation Failure / Flag | Freshdesk API Assignee Update | ModernBERT Sequence Classifier |

### **Runtime Request Flow: Troubleshooting a WebRTC VKYC Routing Failure**

The system processes incoming support tickets through a structured execution sequence:

1. **Ingestion:** A bank operations agent submits a ticket through Freshdesk: *"Unity Bank customer VKYC sessions are failing. The interface loads, but video connections fail with a network error."*  
2. **Topic Classification:** The classification service analyzes the ticket and maps it to VKYC\_ROUTING\_ERROR with a confidence score of ![][image12].  
3. **Context Assembly:** The Concierge Router queries PostgreSQL and Data Cloud via zero-copy schemas. It compiles the customer profile, identifying the affected client as Unity Bank and extracting their custom gateway configuration.  
4. **Reasoning and Planning:** The Planner Agent is initialized with the custom guidelines for VKYC\_ROUTING\_ERROR. It compiles an execution plan:  
   * *Task 1:* Retrieve the VKYC SOP from the RAG database to identify the correct WebRTC gateway.  
   * *Task 2:* Query the KwikID WebRTC server status API to check gateway health.  
   * *Task 3:* Extract the latest session connection logs for Unity Bank over the past ![][image13] minutes.  
5. **Execution & RAG Processing:**  
   * The agent calls the RAG Engine. It retrieves the Unity Bank integration guide, which specifies that Unity Bank routes VKYC traffic through a custom coturn server (turn:turn.unitybank.co.in:3478).  
   * The agent calls the KwikID API Executor. It pings the Unity Bank TURN server and retrieves connection logs. The logs reveal a series of socket timeout errors: Connection timed out to turn:turn.unitybank.co.in.  
6. **Self-Correction & Refinement:** The Planner analyzes these observations, notes that the bank's custom WebRTC TURN server is offline, and updates its plan to draft a targeted notification.  
7. **Validator Supervision:** The Validator Agent evaluates the planned response against the Unity Bank service agreement and compliance policies. It verifies that the drafted message contains no customer PII and confirms the diagnosis is accurate.  
8. **Handoff / Closure Execution:** The agent posts an update to the Freshdesk ticket: *"Investigated the VKYC connection logs for Unity Bank. We identified socket connection failures to your custom TURN server at turn:turn.unitybank.co.in:3478. Please verify your infrastructure routing and port status."* The ticket status is updated to Pending Customer Response via the Freshdesk API, and the execution trace is saved to PostgreSQL.

### **State and Memory System Architecture**

Memory is split into two isolated, transactional layers to guarantee persistence and prevent context drift:

                  ┌─────────────────────────────────┐  
                  │       Active Ticket Flow        │  
                  └─────────────────────────────────┘  
                                   │  
                    ┌──────────────┴──────────────┐  
                    ▼                             ▼  
      ┌──────────────────────────┐   ┌──────────────────────────┐  
      │    Short-Term Memory     │   │     Long-Term Memory     │  
      │   (Transaction State)    │   │ (Enterprise Context)     │  
      ├──────────────────────────┤   ├──────────────────────────┤  
      │ • Typed State Schema     │   │ • Historical Ticket Logs │  
      │ • Active Session ID      │   │ • Banking SOPs           │  
      │ • Token Usage Metrics    │   │ • Client Config Profiles │  
      ├──────────────────────────┤   ├──────────────────────────┤  
      │   Postgres Checkpoints   │   │     pgvector Search      │  
      └──────────────────────────┘   └──────────────────────────┘

#### **Short-Term Memory**

Stores the active transaction state using a structured schema. This schema tracks the ticket status, extracted parameters (e.g., bank ID, customer tax identifier), execution steps, and intermediate API results. It is backed by a transactional PostgreSQL checkpoint repository. Every state change writes an immutable checkpoint record:

SQL  
CREATE TABLE state\_checkpoints (  
    checkpoint\_id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),  
    session\_id UUID NOT NULL,  
    ticket\_id VARCHAR(50) NOT NULL,  
    topic\_id VARCHAR(100) NOT NULL,  
    current\_node VARCHAR(100) NOT NULL,  
    state\_payload JSONB NOT NULL,  
    version INT NOT NULL,  
    created\_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT\_TIMESTAMP  
);  
CREATE INDEX idx\_state\_checkpoints\_session ON state\_checkpoints(session\_id);

#### **Long-Term Memory**

Maintains historical ticket profiles, client configurations, and regulatory rules across active sessions. When a ticket is initialized, the pgvector database is queried to find similar past tickets resolved for that specific bank client, injecting the historical resolution patterns directly into the prompt context.

### **Action System and Secure API Gateway**

To prevent prompt injection attacks from triggering malicious actions, the API Executor Node enforces strict boundaries:

* **Explicit Action Declarations:** The agent cannot execute raw, arbitrary shell commands or database queries. It can only call tools declared in a JSON schema:

JSON  
{  
  "name": "check\_user\_otp\_status",  
  "description": "Queries the KwikID OTP gateway to retrieve delivery logs for a given mobile number and bank ID.",  
  "parameters": {  
    "type": "object",  
    "properties": {  
      "mobile\_number": { "type": "string", "pattern": "^\[6-9\]\\\\d{9}$" },  
      "bank\_id": { "type": "string" }  
    },  
    "required": \["mobile\_number", "bank\_id"\]  
  }  
}

* **Outbound Validation and SSRF Guards:** The API client intercepts all outbound HTTP calls. It resolves the target hostname using Python's standard socket library, extracts the destination IP, and validates it against RFC 1918 (private IPs) and loopback ranges. If the IP is valid, the HTTP request is executed directly against that IP address while passing the target host in the header, preventing DNS-rebinding attacks.24

### **Decision and Planning Architecture**

The Reasoning Engine uses a Plan-and-Execute paradigm.13 Rather than relying on the LLM to recursively decide the next step, planning is structured as a deterministic state machine:

                    ────\> \[ Parse Intent \]  
                                           │  
                                           ▼  
                                   
                                           │  
                                           ▼  
                                   \[ Generate Plan \]  
                                           │  
                                           ▼  
                                    
                                           │  
                                           ▼  
                                  { Check Status }  
                                   /            \\  
                                \[ Failed \]  
                             /                    \\  
                            ▼                      ▼  
                    \[ Progress Plan \]        
                            │                      │  
                            ▼                      ▼  
                     { Tasks Done? }         { Limit Reached? }  
                     /             \\         /              \\  
                   Yes              No     Yes               No  
                   /                 \\     /                  \\  
                  ▼                   ▼   ▼                    ▼  
             \[ Validate \]        \[ Loop \]\[ Escalate \]      

Every execution path includes a **Step Limiter**.19 If an agent enters a loop or fails to resolve a task within a maximum of ![][image14] iterations, the execution halts, and the Escalation Router transfers the ticket to a human queue with a full trace of the failed steps.19

### **Escalation and Human Handoff Orchestration**

When handoff is required, the system uses a fine-tuned, local ModernBERT multi-task model to classify and route the ticket.8 It extracts the execution state from PostgreSQL, compiles a structured transition payload, and calls the Freshdesk API to assign the ticket to the correct human engineering queue.

JSON  
{  
  "ticket\_id": "FD-90211",  
  "bank\_id": "Central\_Bank\_Of\_India",  
  "assigned\_queue": "L3\_Integrations\_Support",  
  "summary": "Agentic automated resolution halted. Step limit reached.",  
  "root\_cause": "Timeout on target banking system endpoint during OCR verification lookup.",  
  "attempted\_remedies":,  
  "agent\_state\_snapshot": {  
    "session\_id": "7b8e1f54-5c94-4d8b-90f7-33a7e0f21d30",  
    "last\_executed\_node": "execute\_ocr\_verification",  
    "step\_count": 10  
  }  
}

### **Analytics and Tracing Architecture**

The analytics engine records every LLM call, token exchange, database retrieval, and API execution. This instrumentation is vital for debugging agent trajectories, monitoring operational health, and tracking performance over time:  
![][image15]  
Every state transition and API call is instrumented using OpenTelemetry and logged to an Elasticsearch indexing service.

### **Data Model Recommendations**

To support transactional checkpointing and granular auditing, the PostgreSQL database must implement the following physical database schema:

SQL  
\-- 1\. Conversation Sessions  
CREATE TABLE conversation\_sessions (  
    session\_id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),  
    ticket\_id VARCHAR(50) UNIQUE NOT NULL,  
    bank\_id VARCHAR(100) NOT NULL,  
    status VARCHAR(50) CHECK (status IN ('ACTIVE', 'PENDING\_HUMAN', 'RESOLVED', 'CLOSED')) DEFAULT 'ACTIVE',  
    created\_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT\_TIMESTAMP,  
    updated\_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT\_TIMESTAMP  
);

\-- 2\. State Checkpoint Ledger  
CREATE TABLE state\_checkpoints (  
    checkpoint\_id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),  
    session\_id UUID NOT NULL REFERENCES conversation\_sessions(session\_id) ON DELETE CASCADE,  
    current\_node VARCHAR(100) NOT NULL,  
    state\_payload JSONB NOT NULL,  
    version INT NOT NULL,  
    created\_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT\_TIMESTAMP  
);

\-- 3\. Operational Audit Ledger  
CREATE TABLE action\_audit\_ledger (  
    audit\_id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),  
    session\_id UUID NOT NULL REFERENCES conversation\_sessions(session\_id) ON DELETE CASCADE,  
    tool\_name VARCHAR(100) NOT NULL,  
    input\_payload JSONB NOT NULL,  
    output\_payload JSONB,  
    execution\_latency\_ms INT NOT NULL,  
    status VARCHAR(50) CHECK (status IN ('SUCCESS', 'FAILURE', 'TIMEOUT')),  
    created\_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT\_TIMESTAMP  
);

\-- 4\. Unified System Topics  
CREATE TABLE agent\_topics (  
    topic\_id VARCHAR(100) PRIMARY KEY,  
    name VARCHAR(100) NOT NULL,  
    description TEXT NOT NULL,  
    is\_active BOOLEAN DEFAULT TRUE,  
    compliance\_rules TEXT NOT NULL,  
    created\_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT\_TIMESTAMP  
);

## **Recommended Big Phase 2 Roadmap**

Month 1: MVP Setup ────────\> Month 2-3: Core Planning ────────\> Month 4-5: Enterprise Scale

### **Phase 1: MVP Setup (Months 1–2)**

Focus on replacing the linear RAG framework with a stateful Graph Orchestrator and deploying three high-impact, low-risk Topics.

* **Goal:** Establish stateful execution and basic system tool usage.  
* **Target Channels:** Freshdesk webhooks for email tickets.  
* **Active Topics:** OTP\_DELIVERY\_FAILURE, OCR\_VERIFICATION\_ERROR, USER\_PORTAL\_LOCKOUT.  
* **Integrations:** Write-capabilities restricted to Freshdesk ticket tagging and status updates. Read-only access to KwikID system APIs.  
* **Safety Guardrails:** Manual approval required in Freshdesk before any outbound response is sent to a user.

### **Phase 2: Production Scale (Months 3–4)**

Connect the agent to the core banking APIs and transition validation from human-in-the-loop to an automated supervisor model.

* **Goal:** Enable autonomous issue resolution and direct backend API interactions.  
* **Target Channels:** Freshdesk Webhooks, Banking Web Console APIs.  
* **Active Topics:** Mapped coverage expanded to ![][image16] distinct Topics, including VKYC\_ROUTING\_ERROR, API\_TIMEOUT, and KYC\_WORKFLOW\_FAILURE.  
* **Integrations:** Active write-integrations with KwikID core servers and bank verification portals.  
* **Safety Guardrails:** Layered Validator Agent audits all proposed API call payloads. Output response validation checks for PII leaks and correctness before sending.

### **Phase 3: Enterprise Operations (Months 5–6)**

Deploy cross-session customer profiles, automate model evaluations, and implement multi-tenant compliance engines.

* **Goal:** Maximize containment rates and scale compliance controls.  
* **Target Channels:** Omni-channel support (Email, Live Chat, Slack portals for bank administrators).  
* **Active Topics:** Complete semantic coverage of all KwikID support domains.  
* **Integrations:** Deep zero-copy retrieval integrations with customer data warehouses.  
* **Safety Guardrails:** Automated validation testing suites using LangSmith. Real-time, localized compliance templates dynamically swapped based on the tenant bank (e.g., Central Bank of India vs. Unity Bank rules).

## **Risks and Anti-Patterns**

### **Incorrect Operational Assumptions**

* **Assuming Banking Support is Conversational:** Developers often assume that support agents must use conversational, empathetic language. However, when a bank operations agent encounters an API timeout during a live customer KYC verification, they want immediate, deterministic, and structured technical data. Conversational fluff increases latency, bloats tokens, and frustrates enterprise users.  
* **Underestimating Multi-Tenant Compliance Drift:** System engineers frequently assume a single prompt template can govern all customer interactions. In reality, each partner bank operates under distinct regulatory and security rules. CBI may require strict data retention policies, while Unity Bank may enforce different parameters. Treating compliance configurations as a monolith is a critical operational risk.

### **Unnecessary System Elements**

* **Generative Chat User Interfaces:** Since users submit support requests through Freshdesk tickets asynchronously, building a dedicated chat frontend is an unnecessary distraction. The focus should remain on integrating directly with the Freshdesk Ticket and Conversation APIs.  
* **Unconstrained Autonomous Planning:** Giving an agent unstructured access to tools is highly dangerous. Unconstrained planners frequently enter infinite reasoning loops or generate invalid API payloads when encountering edge cases, leading to high token costs and potential database corruption.

### **Mandatory Components to Implement Immediately**

* **PostgreSQL Transactional State Checkpointing:** Implementing state persistence is critical. Without durable checkpointing, the system cannot recover from transient server restarts or API timeouts mid-workflow, leading to duplicate transactions and failed resolutions.14  
* **A Dedicated Validator / Supervisor Layer:** A dual-agent verification system is mandatory. Outbound API payloads and customer-facing responses must be audited by an independent model to prevent compliance violations and hallucinations.7

### **System Components to Delay**

* **Complex Reinforcement Learning from Human Feedback (RLHF):** Fine-tuning proprietary models on user feedback should be delayed. In B2B support, absolute precision is achieved through deterministic workflows and clear prompt engineering, not stochastic behavior models.  
* **Zero-Copy Multi-Tenant Data Virtualization:** While Salesforce-style zero-copy architectures are elegant, implementing federated queries across multiple external banking databases is highly complex. In the early stages, the system should rely on standard, secure API integrations.

### **Common Structural Mistakes in Support AI Roadmap**

* **Treating Phase 2 as a "Better RAG" System:** Linear RAG architectures only answer static questions. Evolving into a true support agent requires state management, transactional tools, and complete ticket ownership.3  
* **Embedding Raw API Payloads into the LLM Context Window:** Passing large, raw JSON payloads from APIs directly into the prompt context introduces severe token bloat and degrades task performance.35 Systems must use programmatic tools to process data locally, returning only the final, structured results to the LLM.35

### **Critical Expert Reviews**

#### **Architectural Criticisms from Sierra AI Engineers**

Sierra Engineers would identify a critical vulnerability in the lack of a dedicated Validator agent.4  
"The existing RAG pipeline and proposed state graphs lack independent, parallel validation.4 Relying on a single model to retrieve customer details, draft an API update, and execute it violates basic safety standards.7 If the executor model hallucinates an invalid parameter, the action will fail or pollute the system of record.7 KwikID must deploy a constellation pattern: separate the Executor from the Validator.4 The Validator must be an independent, highly constrained model that reviews proposed payloads against strict schemas before execution, preventing compliance failures.7"

#### **Architectural Criticisms from Intercom Fin Engineers**

Intercom Fin Engineers would critique the escalation latency and routing mechanism.8  
"Using a general-purpose LLM to decide when to hand off a ticket is too slow and expensive.8 When a banking gateway is offline, dozens of identical tickets are generated.38 Running a complex reasoning loop on every ticket will crash the API budget.8 KwikID needs a high-speed, local classification model like ModernBERT for escalation routing.8 It runs in milliseconds, accurately evaluates business rules, and routes tickets to human teams ![][image11] seconds faster than general LLMs, preventing queue backlogs.8"

#### **Architectural Criticisms from Salesforce Agentforce Engineers**

Agentforce Engineers would focus on data isolation and retrieval integrity.21  
"The proposed architecture moves sensitive banking data and KYC profiles into external storage vectors for search indexation.22 This violates data compliance rules in banking.38 Additionally, retrieving stale database records introduces context mismatch during live verification flows.22 KwikID must implement zero-copy data retrieval.21 The agent must query the live banking CRM and database records on-demand using secure API nodes, ensuring that decisions are grounded in real-time, accurate context without risking data leaks.21"

## **Final Architecture Recommendation**

For Think360.ai's KwikID platform, the optimal blueprint is a **Stateful, Plan-and-Execute Multi-Agent Architecture built on LangGraph, with Transactional PostgreSQL Checkpoint Storage and Independent Dual-Agent Supervision**.7

┌────────────────────────────────────────────────────────┐  
│               Unified Ingestion Gateway                │  
│             (FastAPI / Freshdesk Webhooks)             │  
└────────────────────────────────────────────────────────┘  
                            │  
                            ▼  
┌────────────────────────────────────────────────────────┐  
│               Topic Classification Node                │  
│         (Sets behavioral scope & rules)                │  
└────────────────────────────────────────────────────────┘  
                            │  
                            ▼  
┌────────────────────────────────────────────────────────┐  
│             LangGraph Stateful Orchestrator            │  
│  ┌──────────────────────────────────────────────────┐  │  
│  │ Planner Node (Compiles Localized Exec Graph)     │  │  
│  └──────────────────────────────────────────────────┘  │  
│                           │                            │  
│                           ▼                            │  
│  ┌──────────────────────────────────────────────────┐  │  
│  │ Action Executor (Calls RAG & API Secure Nodes)   │  │  
│  └──────────────────────────────────────────────────┘  │  
│                           │                            │  
│                           ▼                            │  
│  ┌──────────────────────────────────────────────────┐  │  
│  │ State Saver (Durable Checkpoint Persistence)     │  │  
│  └──────────────────────────────────────────────────┘  │  
└────────────────────────────────────────────────────────┘  
                            │  
                            ▼  
┌────────────────────────────────────────────────────────┐  
│                 Validator Super Block                  │  
│       (PII Scrubbing & Outbound Action Audit)          │  
└────────────────────────────────────────────────────────┘  
                            │  
                    ┌───────┴───────┐  
                    ▼               ▼  
         \[ Verification Passed \]   
                    │               │  
                    ▼               ▼  
         ┌─────────────────────┐  ┌─────────────────────┐  
         │ Response Generator  │  │ ModernBERT Router   │  
         │  (Freshdesk Reply)  │  │  (Escalate to L3)   │  
         └─────────────────────┘  └─────────────────────┘

This design guarantees absolute security and reliability by wrapping conversational tools in a deterministic, state-saved execution graph.14 Short-term variables are written directly to PostgreSQL checkpoints, ensuring fault-tolerant, resume-on-failure operations.14  
Compliance and safety are guaranteed through a dual-agent configuration.7 The executor agent initiates actions, but the validator supervisor model must approve them before execution.7 Finally, a ModernBERT-based Escalation Router processes edge cases and system failures swiftly, ensuring a seamless handoff to human engineering teams.8

## **Build Priority Ranking**

The following prioritized roadmap outlines the sequence for implementing the Phase 2 components:

| Priority | Component | Core Operational Responsibility | Technical Stack | Estimated Effort (Person-Weeks) | Implementation Complexity | Upstream Dependencies |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| **1** | **Stateful Ingest & Triage Node** | Ingests webhooks, classifies topics, and initializes sessions in Postgres.20 | FastAPI, PostgreSQL, SetFit | 2 | Low | Freshdesk webhook configurations |
| **2** | **Durable Checkpoint Store** | Persists active conversation graphs, state variables, and transaction logs.14 | PostgreSQL JSONB Schema | 1 | Medium | Stateful Ingest Node |
| **3** | **LangGraph Orchestrator** | Coordinates execution flow and handles task planning using a state machine.15 | LangGraph Engine | 4 | High | Checkpoint Store, Ingest Node |
| **4** | **Secure API Gateway & Executor** | Runs validated external APIs, enforcing SSRF and DNS security boundaries.24 | Python Requests, Outbound Validator | 2 | Medium | LangGraph Orchestrator |
| **5** | **RAG Tool Binding** | Connects the existing RAG stack to the executor as a read-only data tool.37 | pgvector Retrieval API | 1 | Low | API Executor Node |
| **6** | **Validator Supervisor Block** | Audits planned API payloads and generated responses before execution.7 | PII Scrubbing Service | 3 | High | LangGraph Orchestrator |
| **7** | **ModernBERT Escalation Router** | Classifies escalations and routes tickets to human queues via API.8 | ModernBERT Classifier | 3 | High | Validator Block, Checkpoint Store |
| **8** | **Unified Topic Manager** | Manages custom guidelines and compliance rules dynamically.20 | Postgres SQL Schema, Admin UI | 2 | Low | LangGraph Orchestrator |
| **9** | **Audit Logging & Telemetry Hub** | Tracks execution traces, latency, and token consumption per step.19 | OpenTelemetry, Prometheus | 2 | Medium | API Executor Node |
| **10** | **Evaluations Suite** | Runs automated tests on datasets to evaluate model performance and prompts.12 | LangSmith Platform API | 3 | Medium | Telemetry Hub |

#### **Works cited**

1. Sierra AI Alternatives: Enterprise Conversational AI Platforms Compared (2026) \- Tandem, accessed June 1, 2026, [https://usetandem.ai/blog/sierra-ai-alternatives-enterprise-conversational-ai-platforms-compared-(2026)](https://usetandem.ai/blog/sierra-ai-alternatives-enterprise-conversational-ai-platforms-compared-\(2026\))  
2. Agentic AI vs Generative AI: 12 Key Differences (2026) | Uvik Software, accessed June 1, 2026, [https://uvik.net/blog/agentic-ai-vs-generative-ai/](https://uvik.net/blog/agentic-ai-vs-generative-ai/)  
3. The 7 AI Customer Support Tools Every CX Leader Should Know for Tier 1 Automation \[2026 Guide\] | Fini Labs, accessed June 1, 2026, [https://www.usefini.com/guides/ai-tools-automate-tier-1-customer-support](https://www.usefini.com/guides/ai-tools-automate-tier-1-customer-support)  
4. Sierra AI Review 2026 \- CallBotics, accessed June 1, 2026, [https://callbotics.ai/blog/sierra-review](https://callbotics.ai/blog/sierra-review)  
5. Sierra AI Explained: Features, Use Cases & Enterprise Alternatives \- PixieBrix, accessed June 1, 2026, [https://www.pixiebrix.com/tool/sierra](https://www.pixiebrix.com/tool/sierra)  
6. The Sierra blog \- Engineering, accessed June 1, 2026, [https://sierra.ai/blog/engineering](https://sierra.ai/blog/engineering)  
7. Meet the AI agent engineer | Sierra, accessed June 1, 2026, [https://sierra.ai/jp/blog/meet-the-ai-agent-engineer](https://sierra.ai/jp/blog/meet-the-ai-agent-engineer)  
8. Fin Apex 1.0 — The best-performing model for customer service, accessed June 1, 2026, [https://fin.ai/cx-models](https://fin.ai/cx-models)  
9. The Fin AI Engine™: Powering Next-Gen AI Support | Intercom, accessed June 1, 2026, [https://fin.ai/ai-engine](https://fin.ai/ai-engine)  
10. A complete guide to Fin, the AI bot transforming customer service \- Intercom, accessed June 1, 2026, [https://www.intercom.com/blog/fin-ai-bot-customer-service/](https://www.intercom.com/blog/fin-ai-bot-customer-service/)  
11. Klarna: AI Assistant for Global Customer Service Automation \- ZenML LLMOps Database, accessed June 1, 2026, [https://www.zenml.io/llmops-database/ai-assistant-for-global-customer-service-automation](https://www.zenml.io/llmops-database/ai-assistant-for-global-customer-service-automation)  
12. How Klarna's AI assistant redefined customer support at scale for 85 ..., accessed June 1, 2026, [https://www.langchain.com/blog/customers-klarna](https://www.langchain.com/blog/customers-klarna)  
13. The Multi-Agent Trap | Towards Data Science, accessed June 1, 2026, [https://towardsdatascience.com/the-multi-agent-trap/](https://towardsdatascience.com/the-multi-agent-trap/)  
14. LangGraph Agents in Production: Architecture, Costs & Real-World Outcomes \- AlphaBOLD, accessed June 1, 2026, [https://www.alphabold.com/langgraph-agents-in-production/](https://www.alphabold.com/langgraph-agents-in-production/)  
15. Your Agents Need a Contract | Nidhi Vichare, accessed June 1, 2026, [https://www.nidhivichare.com/blog/langgraph-agents-contract](https://www.nidhivichare.com/blog/langgraph-agents-contract)  
16. Technical requirements for integrations with AI agents \- Zendesk help, accessed June 1, 2026, [https://support.zendesk.com/hc/en-us/articles/8357749781274-Technical-requirements-for-integrations-with-AI-agents](https://support.zendesk.com/hc/en-us/articles/8357749781274-Technical-requirements-for-integrations-with-AI-agents)  
17. Zendesk AI Agent Features Teardown for 2026 \- Kustomer, accessed June 1, 2026, [https://www.kustomer.com/resources/blog/zendesk-ai-agents-features/](https://www.kustomer.com/resources/blog/zendesk-ai-agents-features/)  
18. Inside Agentforce: Revealing the Atlas Reasoning Engine, accessed June 1, 2026, [https://engineering.salesforce.com/inside-the-brain-of-agentforce-revealing-the-atlas-reasoning-engine/](https://engineering.salesforce.com/inside-the-brain-of-agentforce-revealing-the-atlas-reasoning-engine/)  
19. Atlas Reasoning Engine: The Intelligent Core Driving Salesforce Agentforce \- Accelirate, accessed June 1, 2026, [https://www.accelirate.com/salesforce-agentforce-atlas-reasoning-engine/](https://www.accelirate.com/salesforce-agentforce-atlas-reasoning-engine/)  
20. Understanding the Atlas Reasoning Engine Workflow \- Salesforce Help, accessed June 1, 2026, [https://help.salesforce.com/s/articleView?id=005134880\&language=en\_US\&type=1](https://help.salesforce.com/s/articleView?id=005134880&language=en_US&type=1)  
21. Salesforce Atlas Explained: How the AI Reasoning Engine Works | Cirra, accessed June 1, 2026, [https://cirra.ai/articles/salesforce-atlas-ai-reasoning-engine](https://cirra.ai/articles/salesforce-atlas-ai-reasoning-engine)  
22. What Is the Salesforce Agentforce Architecture? How Slack, Data, and AI Agents Work Together | MindStudio, accessed June 1, 2026, [https://www.mindstudio.ai/blog/salesforce-agentforce-architecture-slack-data-agents](https://www.mindstudio.ai/blog/salesforce-agentforce-architecture-slack-data-agents)  
23. Under the Hood: How the Atlas Reasoning Engine Works Within Salesforce Agentforce, accessed June 1, 2026, [https://ceptes.com/blogs/under-the-hood-how-the-atlas-reasoning-engine-works-within-salesforce-agentforce/](https://ceptes.com/blogs/under-the-hood-how-the-atlas-reasoning-engine-works-within-salesforce-agentforce/)  
24. Inside Copilot Studio: How Microsoft's AI Agent Platform Works \- Pluto Security, accessed June 1, 2026, [https://pluto.security/blog/inside-copilot-studio-how-microsofts-citizen-developer-agent-platform-actually-works/](https://pluto.security/blog/inside-copilot-studio-how-microsofts-citizen-developer-agent-platform-actually-works/)  
25. The Unified Conversational AI Experience \- ServiceNow Otto, accessed June 1, 2026, [https://www.servicenow.com/platform/otto.html](https://www.servicenow.com/platform/otto.html)  
26. Now Assist for Creator \- ServiceNow, accessed June 1, 2026, [https://www.servicenow.com/docs/r/yokohama/build-workflows/now-assist-for-creator/now-assist-for-creator-landing.html](https://www.servicenow.com/docs/r/yokohama/build-workflows/now-assist-for-creator/now-assist-for-creator-landing.html)  
27. Now Assist for Creator \- ServiceNow, accessed June 1, 2026, [https://www.servicenow.com/docs/r/xanadu/build-workflows/now-assist-for-creator/now-assist-for-creator-landing.html](https://www.servicenow.com/docs/r/xanadu/build-workflows/now-assist-for-creator/now-assist-for-creator-landing.html)  
28. Exploring Now Assist \- ServiceNow, accessed June 1, 2026, [https://www.servicenow.com/docs/r/xanadu/intelligent-experiences/exploring-now-assist-platform.html](https://www.servicenow.com/docs/r/xanadu/intelligent-experiences/exploring-now-assist-platform.html)  
29. Now Assist \- ServiceNow, accessed June 1, 2026, [https://www.servicenow.com/docs/r/intelligent-experiences/platform-now-assist-landing.html](https://www.servicenow.com/docs/r/intelligent-experiences/platform-now-assist-landing.html)  
30. How to Build AWS Bedrock Agents Step by Step? \- ProjectPro, accessed June 1, 2026, [https://www.projectpro.io/article/aws-bedrock-agents/1141](https://www.projectpro.io/article/aws-bedrock-agents/1141)  
31. Using AWS Step Functions for Orchestrating Web Scraping Workflows \- Bright Data, accessed June 1, 2026, [https://brightdata.com/blog/web-data/aws-step-functions-with-bright-data](https://brightdata.com/blog/web-data/aws-step-functions-with-bright-data)  
32. Beyond Chatbots: Building Autonomous Multi-Agent Workflows with ..., accessed June 1, 2026, [https://dev.to/aws-builders/beyond-chatbots-building-autonomous-multi-agent-workflows-with-amazon-bedrock-and-step-functions-472d](https://dev.to/aws-builders/beyond-chatbots-building-autonomous-multi-agent-workflows-with-amazon-bedrock-and-step-functions-472d)  
33. What Is Anthropic's Managed Agents? How to Deploy AI Agents Without Infrastructure, accessed June 1, 2026, [https://www.mindstudio.ai/blog/anthropic-managed-agents-deploy-without-infrastructure](https://www.mindstudio.ai/blog/anthropic-managed-agents-deploy-without-infrastructure)  
34. Anthropic Agent SDK: What It Ships vs. What It Leaves to You | Augment Code, accessed June 1, 2026, [https://www.augmentcode.com/guides/anthropic-agent-sdk-what-ships-vs-what-you-build](https://www.augmentcode.com/guides/anthropic-agent-sdk-what-ships-vs-what-you-build)  
35. Introducing advanced tool use on the Claude Developer Platform \- Anthropic, accessed June 1, 2026, [https://www.anthropic.com/engineering/advanced-tool-use](https://www.anthropic.com/engineering/advanced-tool-use)  
36. Fin over email: How we built a multichannel AI agent \- The Intercom Blog, accessed June 1, 2026, [https://www.intercom.com/blog/fin-over-email-how-we-built/](https://www.intercom.com/blog/fin-over-email-how-we-built/)  
37. Building Effective AI Agents \- Anthropic, accessed June 1, 2026, [https://www.anthropic.com/research/building-effective-agents](https://www.anthropic.com/research/building-effective-agents)  
38. Transform fintech customer service with Fin \- The Intercom Blog, accessed June 1, 2026, [https://www.intercom.com/blog/fintech-customer-service-fin-ai-agent/](https://www.intercom.com/blog/fintech-customer-service-fin-ai-agent/)  
39. Sierra AI: Complete Guide to Features, Pricing & Limitations (2026) \- My AskAI, accessed June 1, 2026, [https://myaskai.com/blog/sierra-ai-complete-guide-2026](https://myaskai.com/blog/sierra-ai-complete-guide-2026)  
40. Microsoft Copilot Studio, accessed June 1, 2026, [https://adoption.microsoft.com/en-us/ai-agents/copilot-studio/](https://adoption.microsoft.com/en-us/ai-agents/copilot-studio/)  
41. Trustworthy agents in practice \- Anthropic, accessed June 1, 2026, [https://www.anthropic.com/research/trustworthy-agents](https://www.anthropic.com/research/trustworthy-agents)  
42. Operator: A look under the hood \- The Intercom Blog, accessed June 1, 2026, [https://www.intercom.com/blog/operator-a-look-under-the-hood/](https://www.intercom.com/blog/operator-a-look-under-the-hood/)  
43. AI for Customer Service & Support | Zendesk AI Platform, accessed June 1, 2026, [https://www.zendesk.com/service/ai/](https://www.zendesk.com/service/ai/)  
44. I built a production-ready template for AI Agents using LangGraph and Clean Architecture (Open Source) : r/LangChain \- Reddit, accessed June 1, 2026, [https://www.reddit.com/r/LangChain/comments/1ss1r93/i\_built\_a\_productionready\_template\_for\_ai\_agents/](https://www.reddit.com/r/LangChain/comments/1ss1r93/i_built_a_productionready_template_for_ai_agents/)

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAGIAAAAaCAYAAABM1ImiAAAD8ElEQVR4Xu2ZS8hNURTHlzwi74gUeSsllFeJklAGSh4ZMGfAxICiZGKgkDxKIhl5pEwoA+lGIUSkTCjkMUIpCnmsn7V3d9/l3HPP+b7P57uf+6t/d5+1z9n3nrX23mvvfUVatGjRotszWHXeG/9D7qjWemNZhqhGOfWtuaM+V1XHvfE/ZK7qrTeWYYCY41+rfqomhms0Vqxx7JQ9q1VLvTFAIGnjptjz9JY00CtUu0LdofBMs9NPbGQM8xVl+CzmFM801QfVCVWvxD5C9Vjsy/O4K9buUF8R+KHa7o1NzHdpx/v0EHPWK1+hTBAbFS/EenLkimTf76HdrABHHqnmeGMTQxDy3jcXeisPX/YVyhKxXntbLDFHPqkuJNf1oN2PznYgKd9TTU2um50Fqm9SO3sUZr2Ys3G6542YM3s7Ozbm+TxmiN3HtBbZpnqWXHc3+ostYGb5iiLgKKaZ0c4+XcyRZ52dLyOnNJpSCDDP0+tZWSGui4ykZua0NO6kf0CvZepInYU2qsZVb6uBXOFzhofpjkSN0kRN4mf4tpfDYquurgh5onTCjr12la/IoUggGJrkEb80va8aGcosEuYndWWgbX5DV6RNgYjLy4G+IocigTgl1m5eIt4tbUxqXZw2BYKeVXa5NVz1TvKd3Gj/MEn1PJTZ/C1SzRNrc43YaAF2/WPF8tcUsfzUU2x/szzcw6Z0mdizs1Urg502aCv9nTzLaGXRkCZUNmGTxd4NO4uTuClFtBWv2UPlsUe11RvziPsHnFqWRqsm6usFeJBY3ebEVlE9SK6/qA6GMo65KOas/WKjaIfUTk0sNp6EMqOb5xeGaxwTR+8ZqS6nCWbac3mfvaoxqi3BdkRq90vrknIWpVZNBICzkU1iDiGB0hvoWUXhOZzhoR16IfWspxeJOQE7PfWY2IqLlxtvj/ymEhRhwxjPbngpP9S5TgNBOeYj3uOGVKdb7o2BoFczygAbK5wIgfCdK+4LgH1UzG/1oJ7lOSOrU2CDV2RnXZRKUAQHETAgEN5BWYGIwSIQlfAJaSA4KX4ZylmByOrJjFxG11FfkQHfVW8m+Cvw4zryCytBEQIdp5qODAQb1zglxkDEjWy9QDByr6lO+ooMKmJncJ3KBql/+lqWiuqpmGMuic3FTKEch3wVCzq7fCBZxhyEE+MUi/hN70OZz1uhTBvcRw5gpJEr2IdgZxo7J3Zgh7Ic+VAaT0scgF6X2qOgToGVRbuPfQOVIObvRi/cXsgTcbT0keoKLY+d3pABnYfc+0/oiH/ocAwrJlT0D6nOgNHGMnSxNN7vdMg/dP+amVI9WqHcVWDEM7Xt8xUtWrRo0U35Bea710ytd1WvAAAAAElFTkSuQmCC>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHAAAAAaCAYAAABvj9h3AAAEdUlEQVR4Xu2ZX6iURRTAT5igWGopSlTc2x+DUFMQhSsV96FEiULKQNAnXxIR31QQEkF6rEB7EFGkhyhT8OESSPmwtyCiQiv0SQKDVAhCDJQstM6PM4edPe5++317db2y3w8OfHNmvplvz5k5c2ZWpKampqZmkjBL5fOorCnFNpVHo7IXZqs8FmRaS4vOnFI5GJU1pZgqZj8WQc88JOawiyr/qTyTysiQyuWk5znylsqrUZlgAtDHt2Lvv53KLq+r7E51+9I7gwj2+zcqe+G6mDEji1SuqBxSeTDTz1M5qzI907XjB7F+H4kViVsqO6NygMB+YypPxooqPCBm5N9jhfK02Cr8TWzlOCelffsI/babGM4vKsujcsB4XOU7mUAoZXVg5C9ihfKK2CqJA1xTOZ6VO0G/fwXdB9nzjyrPZ+VBhMh2Q2UkVpRlg5iTcFbkkpgT2HBz0LGPFbFErB3h19mh8mtWrjGw08dRWRYMTDh8IuhfEOv4s6CfIbZndgt9TAzeZ5WRqSKUy6zcQYOI1ojKMrBKCHG5kZF3VIabzVpgL4x7YoSwTAKD5AkMCdGLWXmyQl6wSW6f1HcL7IlUxlfJm7GigDIOXCY2q+IR4bTK/PSMkVZmdZMJjlcN6b5N3Cl6dqCn+Q/HigLKOPCIWL9FCcoeaT2aDDI9O5BVUpTmt2Ouyp9S7Jxu579nVS5k5efEwjZ9siq5HRoSS7GByTKankmoVqTysMpalSmpzmFreFeak8z7IyQyFvs4/fjFAu97mQgRx6fuZZU1Yn07rmel0qfr/PtWiY3XDY5qlZM7P//hjKp0y0Kp7zQxZorVbU1lDOYJEQ58Pz2j8zF81TtkbH9kZbJlDsTAxcBT6fkjlZ/TM2H9hMocsTFY/XwDKbyzV8wukI8/Ls0oxUQDLjPy9htVFqZnvo8bltdUfkq6IrAH75SCAZkhm8VeJLFYIBb3y8J7u6JSrJ91YvX8gFEx46NnpRwQy2DJet3IrGhudTAAs9cNhcGLHJiXcRTj0dc5ad7lsrcTZYD+4q2P3zQBDqXs5OPfVPlUbGV63zgyP3rR/r30HL+vG9jLJ0Zf4GBf5iamLBhlVOWqmDOhigNxjO+5RJR2e2veX85qlTdUtgR9bE+kOCY2DouABC3PqGnPEYmx+b5GVlcEYboh1RbQhCH0dAqRVcE5/vEviYVDyA3I3pVPmOhAVgcribtFrvnyvdf3rOgQB8d8qfJJ0OftfeXiOMIm37teWrN3nOnbQhUHMnmIhn2HkNfp34gq4ECSHmY3xwwSBWAm47TDYuH6m1ReLGYgHHZU7L3t0npbdF6s/VcqS8Wu7/4Rm3Q+QXKYDCNZ2dsTOj9UOSP2bYyFs539qW5cmhcUfJ/nAFyCeHLTDuz3d1T2Cwz2vVhSMBE8g2R/idmk/y3FzM/H8RXISmh3nPH2sb9OsO/yTif4rfTFN0b4xl7D39dyj/9P5YK73//IY8wxsSy0V8NNBu7YP/L3G8PSvPLzcFtTU1NTU9ON/wEV9OQh4DK6ywAAAABJRU5ErkJggg==>

[image3]: <data:image/png;base64,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>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACYAAAAYCAYAAACWTY9zAAACMElEQVR4Xu2VP0hWURiH36igKIgsKtEoXSIaQsKEZkEcbMiEBoe2HJqLmloaamgQMYgWh7Zoa6kGXXUtlEgwCBokWtQt7fd4zvl87+ney20IGu4DD9933nPud9/znj+fWUtLJUflZ3lBLsi5Yvcu++RtOZTFK5mQs3JSHsn6EvvltHwhz2Z9MCVvxe9X5KZ8JM/H2EH5Tu5YSLCWcflLHovtcxZmzafng/zp2vfktu29oFt+lb2dEWaP3ffEqjWsFtkzM8+o/Cb7YpukGXejM8LsgHwt78d2v/xuIcFE6gMmwGRSAWrhx8sSYxmIX8/aJOxhD72Vh+RxuSQvxz4SeRW/w1UL1WoEm7UusbR5mTljiHuI/5AXY5vlZ8mpCvvweYyT5PsYb0xZYlSGeJpxXWJ5/IvcsJDgmRijWvhXpFOSOCEXY4w9BGUJ1MU9DywkmVi3sPxIVStJp3DEQsmfyTfWfCnL4h72VaoWW4dJM3mWdT7GGzNs4SrgE8bklhzsjAhwp/Hi01kcmCTV8vvqoRVPKhMqe3YXLr2uLHZHLtveQ5w0KkiCHn8qc4bsz1PIeJ8Yd56/Xgqw5ryUMgM/yPKc6owIMGbGtXvkmrzkYgmqlU6nJ68YK3DStQt8suKpZL/5Gz7Bi/w49iLJlv21MLmBPGhha7x07bsW7tJK2IyckKcW/g+roI8xjOWZMqgIJ72Kj3LFQrWfFLv+LexD/3+Zc1jelNesvggtLS3/Db8BOf5yOWRW21UAAAAASUVORK5CYII=>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAKIAAAAaCAYAAAA0a4cDAAAFPklEQVR4Xu2ae+hlUxTHlwyRdzSaovEu5TETZkrUJMQfSh4h/vMHCX9Qoygx5Q9C8iiJxvzlkfIHSpJOFEJkGqmJGvL4C1GUkcf6zNq7u+9y9r7n/O6Ze+f+Zn9qdc9Z+5x979n7e9dee+8jUqlUKpVKZRlzmNrL3rkX8pHa1d5Z6cfhaqucHTB2RZ631Z7xzr2QdWo/emelOweLCe97tX/VTgzn2GqxxsXPsedKtYu8M4CQqeN9sfuJFqnQL1O7N5Q9Hu5ZdA4Ui4xH+oIZwyi1U6xdn1D7K/i6crTar2rf+ILAPmJ1vqV2u5hG1oxdMQV/iInCc5raL2rPqq1I/CvVtok1fomPxeo9whcE/lG7yzsXmL9lvs+zn9gIRZCIcIyPshKni/1++gvLCdHXR5DaHj6nAoXzxd/5AuUEMcXzo4hkkTel/XpPfKgcW9XO8c4FBhGWnnd384K0fz++zd5ZICfEa8XKDnF+IuQO5+sN0YrK3/AFyoViUetDGQ/vv6u9kpznoN7fnO/R5PgTtVOT80XnPLFOSUePWbJD8kL8wjsL5ISImCk7yPnRAzqZiuvFKkF0nh/EvtiHdXzkeSXOFLuOYT2yUe3r5Hy5QQcxgTvLF8wI2jsnxD5CyQkRX1v9OX8vEArD7DHOf4ZY5S86P41NTjlpSEXg3E/UI6fAOO8SSReZLTL5T0o6xKTGr1aUjInlJEpCbPPnmLkQiVoMnalYsJvUjhtdNgaN4nNGD8M9ExUsnagw8WH4mhZmg8y690TIE+c1YckJLufPMXMhxqh1hS8o0EWIDE3kDX5p5lOx5QEgKpyblPWButsaak+gCnEJxOUVPwsq0UWIm8XqLU1E7pP5JfW7k3kKkT9omyDw+UljiZwQG7EygkjKT8G/ZHI/vMRRYl9cEtmk9cOTZDTdZ/F7g9p6sTqvktGDsuuzWix/PUUsP91XbH3z0nANudPFYveerXZ58FMHdaW/k3uJ1kya0gkF+drJYs+Gn8lZXJTHqCues4Za4gG1O7zTcajaq2rf9rBNu+4sw0SwrT/xDTFrZj5Bmc9Xp5o107hUiqj6wn2lhJzytgYBOoGyWxNfo/ZZcv6n2mPhGGHQaYjlEbEoereMNxSTrS/DMdGd+88P5wgjRm/W2WJkQMxp5OJ5HlQ7Vu224HtSxtdLr0mO25j3rPkSMUGkGw2IhoXqdBfsNbE+WJv4UnJCJK2inWm7FK6P/dUZBMje6M1iFTCBIBp4lZfgPsTgoR6iEOWsp20QEwF+ItXTYjNuOvd4u2UXTbAIC+Zx75ZO9UMd52lDcRzzUZ7jPRmlG1wbhUhUI8oCvi3hGBCi/3PFdUFgHTXmtzkoJyoRWedB3Fm5TqyfsVuCL12Ci5EzfV5Gi7T/+DMjOJ+CsbPyvIzEzoR3kJ2VpcACd5edla40wSIIBMECQvQCaRNiFCtCbMInpELkTSGGOWgTYlskI3ITXZ/yBS3wXbmRYJaQxtyvdqdYhB8aRqcb1R4SS6nmBp0zZIM3wSIIPQ61QwqRYSumBFGIcSE/J0Qi9ztqz/mCFhqxPfjKDLlB8m/f9KVR+0pMGK+L5WIMK2wH7hQTPbs8wGQh5qCIKKYYGL/p53DM5wfhmDq4jhyQSEuuyDokfobxl8TyKKxNSJ/L5GGZoepd6femS2UAyDmGeu2pCUb+NqnDp4U8MUbL/eX/SxFt3OMdLfDnIfeuzIEh3tBGGMyYsa4v5M4Coi3LMBfI5PXO+ob2MoCXKuPW4mAvWA4AEZ+h/WFfUKlUKpVKpbLM+A/ZtFLyUx9y8gAAAABJRU5ErkJggg==>

[image6]: <data:image/png;base64,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>

[image7]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABsAAAAZCAYAAADAHFVeAAABcklEQVR4Xu2UsStFcRTHj6LIk5SSkvInSLHIpEwmBmUzWhX1stkNdiHZ7BZxy8ikLMqgLAxMyiK+X+f3e/e83/35vXsng/upT+/+fue9c+5553evSM0f8Ab3YQM+wbX28A/98BgOhoEYXXAu3AR9ogUm3HoLfsEVyRPz8xG+u3WUXfgKP0UTMFHIIjwRvRkyBC/ycItMOnQ1A5fhuOidxYpx78is+VdmZk2mRXOVYlR+LzYLz0VnQsbgTR6WTSkWT5IqNgLv4LBbc1aXeVgepEJXJFWMrMMP+AK3YY/oDNlVck4xOhUjvaLf83BO92bN7g5FT7Q/TFHKFLMwGefou5qCO26fRW/dfpSqxZiQnXn24Ly77oanJlagSjF2k0n+Vw3AK9HuPMk8VYrx9Nmu/G9tsVVzXaBsMXbFE2gPQKyzprluweR8TYWysD15hHNiVzEm4YHoI7EEr9vD1eArig/yRhgw8Dk8g89wIYjV1PxHvgF8nEVTGpZZcAAAAABJRU5ErkJggg==>

[image8]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAKAAAAAaCAYAAAAwnlc+AAAFEklEQVR4Xu2aSYglRRCGo1FBGcWVERfsdjsIg8KIwohHFcUFcUFBxYOCHjyIIMocvMx9YObggAjiQVzwIuKCeHjoQVFBD4oieFB0ZAZUFBQUXOIjKudFxcusfvWmul5j5wfB6xeVmVUV+ecS+VqkUqlUKpXK/4CT1V6Jzi3A2Wofqa3EC5X+nKJ2VrDjWyXKvKv2THSOAM/Hc96sdkO4NhZXqt0enZV+nCjWkT+o/at2YfMdW1X7sfHzd4TgX+u+H6N2rtqjYnW+kmlbF6vdqvZrc4379uEbsXq7mu/U55nwfZsKLYHXxWbCzcCNap+qPaD2uVis5+V3adctDeo1tb1qE+nfh538IdaZkR1qv6g9q3as828Xe9ATnC9xuVhbk+AHRPO12hnxwjokAdK2Z9kCZAD+HZ1LgNmYfmJLBHyyOuFfD8pQ1telLV/3GrWDYvHmfScyoADZx9Dw9/GCcoHYLEgnM5Ml3pZ8eegSINBmFNKiLFuA8ITa3dE5Mn/KbH8QZ2a2rlizolCXlctDn1M3x/MysABPFevIN+IFMeX/o/ahTEcI8HCvuu+enABPk+lyfZKUp/i+bAYBXq32orRXiLEhDqwSHiYM/A8Fv4fBQ5kzg5+Y5lZEGFyA94iJDLFF0rR7XPDjIwHIkRMg98gJBVG/pPac2v1qh9X+EttPJlJ7WBzNUYCpnJ+xCVjyp6ClAGP3ic0eDCr/TleI7aP2NZ+lPdU2WX+m2Uh4pxhv789NLImJtOMS/blkdHABsr+jA+I0fKnYQyAQDwFnz0gH5UiC+UBMBLT7neQFyExIWUYirIrtER8+UsLEv0dskMROjgIk2Xm58SUBkuV/LO1As4e9q/H9pLZT7Tex4ELaU13UfF8Tm2FKeyraKQ3IBIMqJWXz2jysJ8Do90ykW4DRD4MK8DKxwH8idpySjGl7bVqsBYHxHRyJAsRKAlxRu0raMywdGdunzdwsEwUIufppqfFBSx30gvMl8DPIPKwQiDAuV+AH0diUhFbyeyYyG5cuPwwqQJZGbnRbvNDBvAKcOB/tR6FEmKnuEHvB2P5GCnC38yXws+TyPMlog8HKoI1UAS5IWppIDOZlEQH6JMSzKnacwzHA+Y0PAZGFkcUlNlKAOeHk2u2i1M5YcP/3g4+jLvxssUogplz/f9H4cwwqQDq1dKMSvBj7pkvihYacAEu8I1bWZ9gIKIqtrwBj2UUEyGw3L5R/LDoDaS/cx+YlFwcGIP6uLJhnpkycTGirpIvBBLgidhPE1BfqlTbdfQRIJ/sX5ZlIOJKAeFnoI8Bc2S+lnwAPiF3zAwPelNnD92VnwcC5LEma5xaxX2r883KIzHulIyOuUff6IyUMylA3x1ELkE4mmyPT5EZke2SPfRqkXtw7keXR7lNi1w823+Po8vCSlE0ZOJkp9Qgmh7uPiB0FPNn4uGd6Vj6pS+efJ9NEBtFQNgUVgfwsVvZxsZmbLPimxvea2jnSToRo4y2xBOV0sZhx3pfLgklKWLL6/rozJClrZ6sDxPOzxu/hfeNApAzLt68bfwkhNrwnOQPvSh89KNa3uaOaDYeD6XjyfjTskPYLEyCSkkVBMAgnBYigYn0GGfijE3826WEGZbbZDNyp9rTadTJ7dtsFg5S6+8XqbnqYmfzSuVVB0BOxGbUyMvdKPrPdSrCFeC86K+PAFM+/IrHUbVUOyew+qzIibNbrf0RXKpVKpVKpVCqVIv8BzvdyPSVcYFIAAAAASUVORK5CYII=>

[image9]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACYAAAAYCAYAAACWTY9zAAACNUlEQVR4Xu2VS8hOURSGl1xKlFxyKXKZyEgSYiyXAROUYmDGwJgYKRmQDAx+JSUDM5kpIf0MmRKJopSBZMLQ5X3stX7rbGd/fQbK4Dz19p219v7OXnuttfcxGxhoMld6Ja2VHkk3usO/mCYdkbZU/iYHpAnpsDSnGgumSxekq9KKagyOSQf9eaP0VTojrXLfTOme9MNKgCPZJ32T5rm90squ+c3gY6GA4PhfsEx6Jy1PvnPpOXhjY2aL6POCsFt6L612e6uVeSenZpgtsbJIZGiN9MFKgEGeT4ZO2O8EjGSG9QdGGfDvdfuQ23kh+mlSum5l0fnSU2m9j+O76c+w2cpGxoKXjwosmpeAWoFRvsgS5X9gJSuU+or7CfK++8emLzBKiT92HHZfKSl57qvX0hcrAS51H9lCf0WckmCh9MR9t5If+26yz7qPvqK/WpyyEmTwUbrjIqtNSD8nbqeVlF+Sblu3lMBL8M22cm08lJ5Zt5R9kNXIFuVn02yesk66f2y2S9/9t8Ui6bl0uR5w2CTZyn112rrtQC/TEr1w6S2ofEelF9b9E3OYG6yTPkm7ki/DXVWfQiqQA6M3m9mm5pSINAMv5DAsnpphNsv+PCSPrXvBZshWnM5MnbFNVjLfC+XIC9Jvn5MdENhFf6bPsLkw+2BzG2qnlda4luzjVu7SJjQjzc13kMbugzLusPI95bvagoxw0ltwYF5Kb6Xz3aF/yx7r3ms1ZHu/tM3aSRgYGPiv+AkKn3Qhv1igfwAAAABJRU5ErkJggg==>

[image10]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACYAAAAYCAYAAACWTY9zAAACRElEQVR4Xu2VP2gUQRSHn2hAMRCiohECSUQUsRCRKKQMgpEQGw0KWthpYa1oZWOhhYhFBLERsQvpYqEp1FJbgyEoJCBYiIiijeCf3+fM3L2dvV3OQrDYDz5u9+3d3Myb92bNGhoq6ZXLcrd8Ju8XH/9hjTwrD2XxSqbljDwjN2bPEmvlbXlX7rLwJ57z8lS8PiC/yatyOMZ65GP5y8q/LXFc/pB98X7Iwqr59CzIe+5+TH6y8GewXa7KwdY3zK6568Rb6zJbzJ6VeY7Kd3LExRhwm7uHqSjskO8tTDBxyV2ToYvWTkAt66zzxNgG4sdcjHsG9pyWh+N1v3wp98V7JvIwXsNBC4vrCoq1bmK+eNk2Yp/lSflAfnDPge1ny8kKdXgnxpnkkxjvmk4TYyuJ+xVPWvgecXxkxXpKvJFfLUxwIMbIFv4VqUsSm+WLGJt18XELGbohv8fnmIq/issWJplgjPkoWa2E9NOFRyyk/Kacs/JWMpk0EJlKE+OIqIO6StmidFg0i2dbn8Z411DQP+Mn0AS+wxIT8qPckz+wsEiy5evqihXHoZbzTm/BVmzKYufka2v/iMHSseBZL79YuxM9nFV5F7IDfmJk3h8vBdhztoQ0AwNS5Ftb3zDbK59buat2yltWPsW5T93pyTM2Kre4+wKLVuxK6o2jwcMf5V3Kaplsp85kcfvzoIXS8G+PCxbO0kooRgqbjuN9WAUZqHtXAhmh06t4JZfkirxefPRvoRY7ZTGxQZ6w8K6tS0JDQ8N/w281Y3Fcl0BDgQAAAABJRU5ErkJggg==>

[image11]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABoAAAAZCAYAAAAv3j5gAAABSElEQVR4Xu2UPytGYRjGb8UgSkrKZpCVImVhkxIGWRgNFruy+RIGmXwFKYOkLDL4BpJBBoPJQP5d13met/c+1/s8x1nl/OrXOed67nPfb+85zzFr+GtswUO4DvtkLcckHJBsFS5IVjADX6x9A4/nMf+NbfgtzpcqHGfwS7JFeAJ7JVeW4ZOFAddwo7xchkV3ko3EnL+4Cg7q1zAHG15KNhjzI8mV2oNYlBqUyxUOuofHcBPewB3Y5YtIrmEuVzhozV0Pw0/JCnINc3kdWm9fCe6XVMPWoFPJ65AcRBheSTYU86qXYRo+x6MnO+jNwl7wTMBXOOWyMTjnrnctNORz8jBjzw5SG3bFOjcsHzKbdMfrWfjQXi5gPWt4b5IlCzftxSP/EuUWvkvGz9UHvICPFoaM+oIU/JgeWPgg9shaFeNwP8rzhob/xA/qV090T2n/RAAAAABJRU5ErkJggg==>

[image12]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACUAAAAZCAYAAAC2JufVAAACJElEQVR4Xu2VPWgVQRSFj0QLiSCihWIagwhpVBCtFCyCGEQLQRQsU8TCSkHBKilsLEUsRAg2omAjIWghsmATsA0ERAtFsBAJFgoi/pzjncm7e3fmpbLbDw7vzZm/uzt37wA9Pf+XaeoedY4aDX3D2I7B3NPUpnZ3i0PULWqO2hv6WhyhVqmtqa3fF8kfxgbqGjWPQSBxrcxB6gM1kdpT1Fdqx9qIwHPqd/BOUgvU5uB7jlE/qKPOU6B3qMvOE8uwsZmG+kPtc14Ldb4L3q7kzwTf8xo2RmM9OsJvsKMSu9FdS8Fvc+0OmtAETxPk3w++5z3qQck/m9pnYCcxSY3AcnAoW1AOquZ7lHeloK4k/wa1kXpCfadewtJBb0l59yZPiNQ2r/meWdiYcedpw/nkP4Ct06S2fjPKOXnq71DbvOZH9LUpH/en9kPqEWzudQyC+on2B6F8U94V00P1qLR5Dmox+CWewcZK52E5pP85p/TGfOKLHJRSoFgTtcCr4Kl+yC8+yTroK/uCQU26iXpQDSpHqPrxKXgH0F1Ir9/XlT3UR+qS81Q0l6gLztM4PaAKZuYwLPmrJadUPHUEvngqgbXwr7URwPHU1vFkdHxayxfdPHfWeReTN+a8DqdgdUefsX4/t7v/oSdbcW1tdhVWRHXvNbAc2enGZHTNvKWeUo9hV4x/c1V0Ed+lTmD4pRpRYApqDhZoDa15G7ZHMbl7enp6An8BRIKAR4vfE9cAAAAASUVORK5CYII=>

[image13]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABUAAAAZCAYAAADe1WXtAAABTElEQVR4Xu2UL0sFQRTFr2hQ9GkRLQ8Eg/CaIAqCFoPoB3jJaLDYBZtfQcQgFj+CWEQM262KSVCwGURBQcQ/57w5s8zOjIYHr+2BH8M99+7d3Zm7a1ar1+oDc+AI7EU5r1GwCQ5BO8olmgV3oKV4HdyA8bLCbAFcgjHFXJ/lJ5oA12BZ8QgowBOYkTcEzsGaYq9vcBZ5Hd2Dn8jjVvQH8Y65msnAox7kJ6LJO1JsFDbzOjFXx7cIdSW/ogGZ7+YOh6+5ZG6vplTjtyPXNOszoBkntsGFvK6bfoamudGif2xdNKVovkWeb8oRGtaau7iQPxj5/zYtFPuDavgCibNMP9GjpYl5eVuKF8EHmC4rnF4tfaCODixtugFuQVPxX8PP67LDT/Ez/QKn5sbrpZouxafiwO9qXammU62CfXM/Ch5OTvSZZx3ra9XqtX4BAnZSfpdI9HkAAAAASUVORK5CYII=>

[image14]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABUAAAAZCAYAAADe1WXtAAAA5klEQVR4XmNgGAX0AIxAbIcuiAT4gDgZiKcBcSiaHAroA+J3QPwXiP8DcTmqNByYAfEeIOaH8kH0e6g4BjAH4hAglgPihwzYDeUE4h1A7IEm/g+IN6OJoQBJBtyGgsRAvhBHEwepB4njBPgMXcgA0cyDJn4aKo4T4DIUZNABBuyG4hKHg6FvKDcDJClh03wAKs6BJg4HuAwFAVhE8aKJX4WK4wT4DLUE4p9ArIQm/gmIv6KJoQB8huJK/CBXYk38sISNjkEWgCxCBiBXgcSroLQTqjR5ABRpoIJkEhC7ocmNglFACwAA6ElDZPfVY6QAAAAASUVORK5CYII=>

[image15]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmwAAABsCAYAAADXCu4MAAAap0lEQVR4Xu2dC6htW1nHx6WCXpppdYysc7WUtCPawy63bnkplUR7YIJGUkaZEhcjxUoROiJSFhGGdKOMS0WUdk3DrLDQSYVZSQ/JFFM4hRoltygsumWP9btjfq5vf2vMtdZ+nH3O3uf3g8He85tjjjlec4z/Go85WxMRERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERGRa8Cnr9zDV+5bVu4Ty7lrAfH53OI++YCP0+cBrcfj6St3Szl30tzU9rvXKJ9wshvK81LrebzNdj1DWV9cuSfXE9cB1OH7VeOKT2v9HH8zS/7PGp+wcp9ajW0zvSJyzqAR+7+V+6fWOxHcb862X0m2f12518zXAP/j53KybYOO6vWtX4MIOC7HFVfE4SEr90utxymECJ3pS2fb73/c9+nwoJV7TOv3/sVy7qSh3BFqu+61lE+RV//Ven15cFxwRvnAyt27crfWE0eE/KU8/6z1fFuynTbUr39buQ/WEyse2HocM4g12oG/K/bTYFc7Efl528r9y8r9+8o9YbbFOY6x075RZ7GddRBsn7Nyz1y5/2697h41bbTLx21LrwdGacj9lci5gEaRDuSzk+2HWu9QvjHZbm4HO/YQbPjdlwh3V0O8CxrjHLfjEHGqvK91Ox3WabNLRJ0k+95rKZ8Qc9ipQ2cZOj069i+vJ44JeVvzbWQ7LUKwXSl2eFMbP5s8a9dCsO3btiBWiB+ujv7mc+cN6ip1dir2w4AYP6m29Fqx1B8o2OTcQYP2uGIbCTaY2rhB35eTEmyM7NS4HZUlIRL2qdhPg31F1Emw772W8gn4pc+5p9UTMhRnI9u1hk5vauNn81oItgtNwbaLkxBsjEyeVFt6rTjJ/kDkuoYRkkcW25Jge1frDelROQnBRsfy8rYZt6OyJEQUbAdZyif4vNZHbN6xcp9x8NQNz0icjWzXmi9q15dge1ZTsO3iuIKNtnTUzp8lTro/EDlzLAm2YGr9PK42qm+f7T/X+nD7X6dzWbBFYzMK53Ur978r95LWr3/lbH90W/sPRxjB963cPa2P+HAdYYw6oMySEIkp0doBwPtX7lUr95Otr+3LC7JJ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R3hq6+ntXCYiIiLnjhBqI5eFQ8DifDYZfLD1EZ4XzbYMu0F5FQWv7YiREeB1EITLFB4jL7wmg7VO2NgZOYrLiOonHJ17td0+sAWPmo+JD2ni6wMZ0lbTGiC0arjkVz5GcOCybRrYCKvGHX+3D/zBS1vfFJHP4fYRufXe2Y1GCsOFeIKoA+zOpIzZnQoxApldLdNcp0bfos5Qh7iGOnRxtrG54mWt35dXn+AnCz8RERGZiY988y3lJfje8YW2+aF2RnMutfVrGiD/f9pwbzZALI0y7ZPW04SRJETOc5KNOMbo52HXsh0VyvXxbVPkHRbqAm4EdYjwcx2iTsFJfsdbRERE5ERhXd3dbXMKkGPsdd2diIiIiJwyjDYxwvbsYufFvdjriKaIiIiIXCNYpM+nqFjTxd+YKhQREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREZGj8//Kw/gvVyUKbwAAAABJRU5ErkJggg==>

[image16]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABUAAAAZCAYAAADe1WXtAAAA10lEQVR4XmNgGAX0AIxAbIcuCAUGQMyPJuYPxG5oYmDQB8TvgPgvEP8H4nJUaThIZ4DII2N7FBVIwByIQ4BYDogfMuA21BeInzNADDsBxFGo0tiBJANhQ3nQBQmBATP0PhAvAuJoID4FxDkMkMjFCYgxNBiJL8YAiVxkMQxAyFBsAJYKcAK6G2oCxK+hNDKgyFCQGEgzKFyRAUjsJ5oYCsBnqCUDRA4ZcDJADN2MJg4GMFegY5AhIItgAJTv/wDxPiB+ClWjgCRPNlAD4kYoBrFHwSigNQAApEs7cIQkA38AAAAASUVORK5CYII=>