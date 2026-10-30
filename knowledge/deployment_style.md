# Deployment Style Knowledge Base

This file covers **deployment style** — how the system is decomposed into
deployable units and how those units are distributed, deployed, and scaled.
This is a separate decision from **internal structure pattern**
(see `structure_pattern.md`), which covers how code is organized *within*
a given deployable unit. A component's deployment style and its internal
structure pattern are chosen independently — e.g. a microservice can
internally use a hexagonal structure, or not.

This file is loaded into context when the tool is helping decide the
deployment style for the system or a given component. It should ask the
user questions until it can confidently recommend one option below, then
record the decision (with rationale and trade-offs accepted) in the
decision log.

## How to use this file (for the orchestrator/LLM)

Before recommending a style, gather enough signal on these decision drivers.
Don't ask all of them as a checklist — infer what you can from the project
goal and component description, and only ask what's still unclear:

- **Scale & load**: expected users/requests, growth trajectory, spiky vs steady traffic
- **Team size & structure**: solo/small team vs multiple independent teams
- **Deployment/ops maturity**: is there a platform team, CI/CD, container orchestration experience?
- **Consistency needs**: strong consistency required, or is eventual consistency acceptable?
- **Latency sensitivity**: real-time/low-latency vs tolerant of async processing
- **Domain complexity**: simple CRUD vs complex business rules/workflows
- **Change frequency**: how often will parts of the system need independent releases?
- **Budget/infra constraints**: cost sensitivity, cloud vs on-prem, existing infra
- **Decentralization requirement**: is a central authority/server acceptable, or explicitly undesired?

---

## Option: Monolithic

**Category:** Single-deployment
**Use when:** small team, early-stage product, simple domain, need to move fast and validate ideas, limited ops capacity
**Avoid when:** multiple teams need to deploy independently, parts of the system have very different scaling needs, codebase is expected to grow very large with many contributors

**Trade-offs:**
- Fast to build, simple to test end-to-end, single deployment unit, easy local dev
- Becomes harder to reason about and deploy safely as the codebase and team grow
- Scaling means scaling the whole app, even if only one part is the bottleneck

**Common pitfalls:**
- Letting module boundaries erode into a "big ball of mud" with no internal structure
- Delaying the decision to modularize until it's very expensive to untangle
- Treating "monolith" as an excuse to skip internal architecture discipline (see `structure_pattern.md`)

**Scaling notes:** Scales vertically well and horizontally via multiple identical instances behind a load balancer, as long as the app is stateless. Database usually becomes the bottleneck before the app tier does.

**Related decision drivers:** team size, domain complexity, deployment/ops maturity

---

## Option: Modular Monolith

**Category:** Single-deployment, internally structured
**Use when:** want monolith's operational simplicity but need clear internal boundaries, anticipate possibly extracting services later, team is growing but not yet large enough to justify separate deployments
**Avoid when:** you already have strong evidence that independent scaling or independent deployment is needed now

**Trade-offs:**
- Keeps deployment simple while enforcing separation of concerns via module/package boundaries
- Disciplined internal APIs make a future migration to microservices much cheaper if it's ever needed
- Requires team discipline to actually respect module boundaries — nothing enforces it at runtime

**Common pitfalls:**
- Modules that still share a database schema directly, undermining the whole point
- No ownership model per module, so boundaries drift over time

**Scaling notes:** Same physical scaling characteristics as a monolith; the benefit is organizational/maintainability, not runtime scaling.

**Related decision drivers:** team size, change frequency, future extraction plans

---

## Option: Microservices

**Category:** Distributed, independently deployable services
**Use when:** multiple teams need to release independently, different parts of the system have very different scaling/reliability needs, domain has clear bounded contexts, org has investment in ops/platform tooling
**Avoid when:** small team, unclear domain boundaries (premature decomposition is costly), limited ops maturity (no CI/CD, no container orchestration experience), early-stage product still finding product-market fit

**Trade-offs:**
- Independent deployability, independent scaling, technology flexibility per service, fault isolation
- Significant operational overhead: service discovery, distributed tracing, network reliability, versioned contracts
- Distributed transactions and cross-service consistency become genuinely hard problems
- Local development and end-to-end testing get harder

**Common pitfalls:**
- Splitting services along technical layers instead of business capabilities (bounded contexts)
- Chatty inter-service communication that recreates a "distributed monolith" with all the latency and none of the benefits
- Underestimating the ops investment required (observability, deployment pipelines, on-call)
- Shared databases between services, defeating the purpose of independent deployability

**Scaling notes:** Each service scales independently based on its own load profile. Requires investment in service mesh/load balancing, distributed tracing, and centralized logging to stay operable at scale.

**Related decision drivers:** team structure, deployment/ops maturity, domain complexity, change frequency

---

## Option: Service-Oriented Architecture (SOA)

**Category:** Distributed, services with shared enterprise middleware
**Use when:** large enterprise with many existing systems that need integration, need centralized governance/contracts (e.g. via an ESB), reuse of coarse-grained business services across the org
**Avoid when:** team wants microservices-style independence (SOA's central ESB often becomes a bottleneck/coupling point); simpler point-to-point integration would suffice

**Trade-offs:**
- Good for integrating heterogeneous legacy systems under common contracts
- Centralized ESB (Enterprise Service Bus) can become a single point of failure and a governance bottleneck
- Heavier standards (SOAP/WS-* in older implementations) than typical modern REST/gRPC microservices

**Common pitfalls:**
- Over-centralizing logic into the ESB, making it a monolith in disguise
- Governance process becomes so heavy that it slows delivery down

**Scaling notes:** Scaling is often constrained by the central middleware/ESB capacity, unlike microservices where each service scales independently.

**Related decision drivers:** existing legacy landscape, org governance requirements

---

## Option: Event-Driven Architecture (EDA)

**Category:** Asynchronous, message/event-based communication
**Use when:** components need to react to state changes without tight coupling, workloads are naturally asynchronous (notifications, workflows, data pipelines), need to decouple producers from consumers, need audit trail of what happened

**Avoid when:** strong immediate consistency is required across components; team lacks experience debugging asynchronous, eventually-consistent systems; simple synchronous request/response would be clearer

**Trade-offs:**
- Strong decoupling between producers and consumers; consumers can be added without changing producers
- Naturally resilient to consumer downtime (events queue up)
- Harder to trace a single logical operation across the system ("what happened, and in what order?")
- Debugging and testing require different tooling (event replay, distributed tracing)

**Common pitfalls:**
- No clear event schema/contract ownership, leading to breaking changes downstream
- Treating events as a replacement for synchronous APIs everywhere, even where request/response is a better fit
- No dead-letter/retry strategy for failed event processing

**Scaling notes:** Scales very well horizontally — add more consumers to a topic/queue. Throughput is often bounded by the message broker (Kafka, RabbitMQ, etc.), which should be sized deliberately.

**Related decision drivers:** consistency needs, latency sensitivity, change frequency

**Note:** If chosen, consider whether the Event Sourcing + CQRS pattern
(`structure_pattern.md`) is also appropriate for components that need a
full state-change history — they're commonly paired but are separate
decisions.

---

## Option: Serverless / Function-as-a-Service (FaaS)

**Category:** Managed, event-triggered compute
**Use when:** workloads are spiky or unpredictable, want to minimize ops overhead, cost should scale to near-zero when idle, individual operations are short-lived and stateless

**Avoid when:** workloads are long-running or need consistent low latency (cold starts), need fine-grained control over runtime/infrastructure, costs at sustained high volume would exceed dedicated infrastructure

**Trade-offs:**
- No server management, automatic scaling, pay-per-execution
- Cold-start latency can be an issue for latency-sensitive paths
- Vendor lock-in tends to be higher (provider-specific triggers, limits, tooling)
- Harder to test/debug locally compared to a normal service

**Common pitfalls:**
- Using FaaS for long-running or stateful workloads it isn't suited for
- Not accounting for cold starts in latency-sensitive user-facing paths
- Cost surprises at high sustained volume compared to reserved/dedicated infrastructure

**Scaling notes:** Scales automatically and near-instantly with load (subject to provider concurrency limits). Best suited to bursty, event-triggered workloads rather than steady high-throughput traffic.

**Related decision drivers:** scale & load pattern, budget/infra constraints, ops maturity

---

## Option: Space-Based Architecture (Cloud/In-Memory Data Grid)

**Category:** Distributed, high-throughput, shared-nothing
**Use when:** extreme scalability and elasticity are required, need to avoid a central database bottleneck under very high load (e.g. flash-sale/ticketing systems)
**Avoid when:** load is moderate and predictable — the complexity isn't justified; strong consistency across all data is required

**Trade-offs:**
- Removes the central DB bottleneck by keeping data in a distributed in-memory grid, replicated across processing units
- Excellent elasticity for variable, high-volume load
- Complex to operate; eventual consistency and data replication semantics need careful design
- Fewer teams have deep operational experience with this pattern than with microservices/EDA

**Common pitfalls:**
- Adopting it "for scale" when the actual load doesn't come close to needing it
- Underestimating the operational expertise required to run an in-memory data grid reliably

**Scaling notes:** Designed specifically for near-linear horizontal scaling under very high, bursty load by eliminating the central data-store bottleneck.

**Related decision drivers:** scale & load pattern, consistency needs, ops maturity

---

## Option: Client-Server

**Category:** Fundamental structural pattern
**Use when:** this is the default baseline for most systems — a client (web/mobile/desktop) talking to a server over a network. Nearly always the starting assumption; the real decision is usually which style layered on top (monolith, microservices, etc.)
**Avoid when:** truly decentralized, no-central-authority use cases (see Peer-to-Peer)

**Trade-offs:**
- Simple mental model, centralizes data and logic, easier to secure and update
- Server is a natural bottleneck and single point of failure unless deliberately made redundant

**Common pitfalls:**
- Treating "client-server" as a complete deployment decision on its own, rather than the baseline that other options refine

**Scaling notes:** Server-side scaling depends entirely on which style is layered on top (monolith vs microservices vs serverless, etc.).

**Related decision drivers:** almost always the starting point; drives which other options are relevant

---

## Option: Peer-to-Peer (P2P)

**Category:** Decentralized
**Use when:** no central authority/server is desired or feasible, need resilience to any single node going down, workload is naturally distributable among peers (file sharing, some blockchain/distributed-ledger use cases)
**Avoid when:** centralized control, easy auditability, or straightforward consistency guarantees are needed — which is most typical business systems

**Trade-offs:**
- No single point of failure, scales with the number of peers, no central infrastructure cost
- Much harder to secure, coordinate consistency, and reason about system-wide state
- Debugging and monitoring a fully decentralized system is significantly harder

**Common pitfalls:**
- Choosing P2P for its novelty rather than a genuine requirement for decentralization
- Underestimating the difficulty of achieving consistency/coordination without central control

**Scaling notes:** Scales naturally as peers join, but total system reliability/performance depends on peer availability and network conditions, which are outside your control.

**Related decision drivers:** decentralization requirement, trust model, regulatory/compliance constraints

---

## Decision Summary Guidance (for the orchestrator)

When enough decision drivers are known, use this rough mapping as a sanity
check before finalizing (not a rigid rule — the LLM should still reason
about the specific project):

| Signal | Leans toward |
|---|---|
| Small team, early-stage, simple domain | Monolithic or Modular Monolith |
| Multiple teams, independent releases, mature ops | Microservices |
| Spiky/unpredictable load, minimal ops desired | Serverless |
| Need decoupling + async reactions to state changes | Event-Driven |
| Extreme elastic scale, central DB is the bottleneck | Space-Based |
| Legacy system integration across the enterprise | SOA |
| No central authority desired/feasible | Peer-to-Peer |

Once a deployment style is chosen, proceed to `structure_pattern.md` to
decide (per component, if relevant) how code within each deployable unit
should be internally organized. Also consult `database.md`, `apis.md`,
`interfaces.md`, and `hardware.md` — this decision constrains, but does
not fully determine, those choices (see `category_dependencies.yaml`).
