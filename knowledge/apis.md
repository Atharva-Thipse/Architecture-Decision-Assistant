# API Architecture Knowledge Base

This file covers **backend API decisions**: first the API architecture
style (REST, GraphQL, gRPC, SOAP, real-time/streaming, or async/event-based),
then the backend tech stack/framework used to implement it. This is the
communication and backend-implementation counterpart to `interfaces.md`
(which covers the user-facing interface and frontend stack) and
`deployment_style.md` (which covers how components are decomposed).

This decision is typically made **per component/service boundary** — a
system commonly mixes styles (e.g. REST for public API, gRPC internally
between services, Kafka for async events).

## How to use this file (for the orchestrator/LLM)

**Step 1** — determine the API style using the decision drivers below.
**Step 2** — once the style is settled, use the matching sub-section to
pick a backend language/framework.

Decision drivers to gather (infer where possible, ask only what's unclear):

- **Consumer type**: public third-party developers, first-party web/mobile clients, or internal service-to-service calls?
- **Sync vs async**: does the caller need an immediate response, or is fire-and-forget/eventual processing acceptable?
- **Real-time needs**: does the client need live/streaming updates, or is request-response sufficient?
- **Payload/query flexibility**: do consumers need to fetch varying, nested data shapes (favors GraphQL), or is a fixed set of well-known endpoints fine (favors REST)?
- **Performance/latency needs**: is this a high-throughput, low-latency internal path (favors gRPC), or is HTTP/JSON overhead a non-issue?
- **Contract strictness**: does the team want strongly-typed, enforced contracts (gRPC/protobuf, GraphQL schema) or a looser, more flexible contract (REST/JSON)?
- **Interoperability**: how many different languages/platforms will consume this API? (affects tooling maturity needs)
- **Public API/versioning needs**: is this a public API needing long-term stability, documentation, and versioning discipline?
- **Team familiarity & existing stack**: what does the team already know, and what's already in production?
- **Network reliability assumptions**: can consumers tolerate occasional message delays/retries (favors async messaging) or is that unacceptable?

---

## Category: Request/Response APIs

### Option: REST

**Use when:** default choice for most public and first-party APIs — need broad client compatibility, good caching support via HTTP semantics, human-readable and easy to debug, wide tooling and documentation ecosystem (OpenAPI/Swagger)
**Avoid when:** clients need highly flexible, nested queries with varying shapes (consider GraphQL), need very low-latency internal service-to-service calls with strict typed contracts (consider gRPC)

**Trade-offs:** Simple, widely understood, excellent tooling (OpenAPI, Postman, browser dev tools), leverages HTTP caching naturally. Over/under-fetching is common (clients often get more or less data than they need per endpoint); no built-in strict typing without extra tooling (OpenAPI schemas).

**Common pitfalls:** Inconsistent resource naming/versioning conventions across endpoints; not planning a versioning strategy (URI versioning, header versioning) before the first breaking change is needed.

**Notes:** The safest default for public APIs and most first-party client-facing APIs due to universal familiarity and tooling support.

---

### Option: GraphQL

**Use when:** clients (especially multiple different clients — web, mobile, etc.) need to fetch varying, nested data shapes efficiently, want to avoid over/under-fetching, want a single strongly-typed schema as the contract
**Avoid when:** API is simple with well-known fixed access patterns (REST is simpler), team lacks experience with GraphQL-specific concerns (query complexity limits, caching strategy, N+1 resolver problems), need HTTP-level caching simplicity

**Trade-offs:** Clients request exactly the data they need in one round trip, strongly-typed schema serves as self-documenting contract, great for multiple heterogeneous clients. Caching is more complex than REST's HTTP-native caching; poorly designed resolvers can cause N+1 query problems; query complexity needs to be bounded to prevent abuse.

**Common pitfalls:** Not implementing query depth/complexity limits, allowing expensive nested queries to hurt performance or enable abuse; unresolved N+1 database query problems in resolvers (needs dataloader-style batching).

**Notes:** Particularly valuable when multiple different client types (web, iOS, Android) have different data needs from the same underlying data.

---

### Option: gRPC

**Use when:** internal service-to-service communication where performance matters, need strict typed contracts via Protocol Buffers, need bidirectional streaming, polyglot microservices environment where a strongly-typed cross-language contract is valuable
**Avoid when:** consumers include browsers directly (gRPC-Web exists but adds complexity), need easy human-readable debugging without extra tooling, building a public API for broad third-party/developer consumption (REST/GraphQL are far more universally approachable)

**Trade-offs:** High performance (binary protocol, HTTP/2 multiplexing), strict typed contracts via `.proto` files generate client/server code across languages, supports streaming (unary, server-streaming, client-streaming, bidirectional). Not browser-native without a proxy layer (gRPC-Web), less human-readable than JSON for debugging, steeper learning curve than REST.

**Common pitfalls:** Trying to expose gRPC directly to browser/public clients without accounting for the gRPC-Web proxy requirement; underestimating the tooling investment (proto management, code generation pipeline) for a small team.

**Notes:** A very strong default for internal microservice-to-microservice communication specifically, less suited as the outward-facing API for a public product.

---

### Option: SOAP

**Use when:** integrating with legacy enterprise systems that require it (common in banking, insurance, government, healthcare/HL7 contexts), contractual/regulatory requirements mandate SOAP/WS-* standards, need built-in support for things like WS-Security in tightly regulated environments
**Avoid when:** building a new system with no legacy integration requirement — REST, GraphQL, or gRPC will almost always be a better fit for greenfield work

**Trade-offs:** Strong standardization (WS-* specifications) for security, transactions, and reliable messaging in enterprise contexts; strict contract via WSDL. Verbose (XML-based), heavier tooling, largely legacy at this point outside specific regulated industries.

**Common pitfalls:** Choosing SOAP for new development without a genuine legacy-integration requirement forcing the decision.

**Notes:** Almost always a constraint imposed by an existing system you must integrate with, rather than a proactive choice for new development.

---

## Category: Real-Time / Streaming APIs

**Use when:** clients need live, ongoing updates without repeatedly polling (chat apps, live dashboards, collaborative editing, live notifications)
**Avoid when:** occasional/periodic updates are fine — polling or simple request/response is much simpler to build and operate

### Option: WebSocket

**Use when:** need full-duplex, low-latency, bidirectional communication (chat, multiplayer/collaborative features, live trading data)
**Avoid when:** communication is genuinely one-directional (server-to-client only — Server-Sent Events is simpler), don't need persistent connections

**Trade-offs:** True bidirectional real-time communication, low latency once connected. Requires managing persistent connections (reconnection logic, scaling connection state across servers), more operational complexity than plain HTTP.

**Common pitfalls:** Not planning for reconnection/backoff logic on flaky client networks; not accounting for how to scale WebSocket connections horizontally (sticky sessions or a pub/sub backplane is often needed).

**Notes:** The default choice when true bidirectional real-time interaction is required.

---

### Option: Server-Sent Events (SSE)

**Use when:** only need one-directional server-to-client streaming (live feeds, notifications, progress updates), want something simpler than WebSocket that works over plain HTTP
**Avoid when:** need the client to also send data back over the same persistent connection — use WebSocket instead

**Trade-offs:** Simpler than WebSocket (built on plain HTTP, auto-reconnect built into the browser API), works well through standard HTTP infrastructure. One-directional only (server to client).

**Notes:** A good, underused option when only server-to-client push is actually needed — often simpler than reaching for WebSocket by default.

---

## Category: Asynchronous / Event-Based APIs

**Use when:** caller doesn't need an immediate synchronous response, want to decouple producers and consumers, need resilience to consumer downtime, workload is naturally event-driven (see `deployment_style.md`'s Event-Driven option, which this category directly supports)
**Avoid when:** caller genuinely needs an immediate response to proceed (use a request/response style instead)

### Option: Message Queue (point-to-point)

**Use when:** need reliable task/work distribution to one consumer per message (job queues, background processing), need guaranteed delivery and retry semantics
**Avoid when:** need the same message delivered to multiple independent consumers (use pub/sub instead)

**Tech examples:** RabbitMQ, Amazon SQS, Azure Service Bus

**Trade-offs:** Reliable delivery, natural load leveling/back-pressure handling, built-in retry/dead-letter support. Adds infrastructure to operate and monitor; introduces eventual consistency into the system.

**Common pitfalls:** No dead-letter queue strategy for messages that repeatedly fail processing; not monitoring queue depth/consumer lag.

---

### Option: Pub/Sub / Event Streaming

**Use when:** multiple independent consumers need to react to the same event, need an ordered, replayable log of events (supports Event Sourcing — see `structure_pattern.md`), building a broader event-driven architecture across many services
**Avoid when:** the workload doesn't need multiple independent consumers or replay — a simpler point-to-point queue may suffice

**Tech examples:** Apache Kafka, Amazon SNS/EventBridge, Google Pub/Sub

**Trade-offs:** Strong decoupling, supports many independent consumers, Kafka specifically offers durable, replayable, ordered event logs. Significant operational complexity (especially self-hosted Kafka); requires careful schema/contract management as the number of producers/consumers grows.

**Common pitfalls:** No schema registry/contract discipline, leading to breaking changes propagating silently to consumers; underestimating Kafka's operational overhead for a small team (managed offerings like Confluent Cloud or AWS MSK mitigate this).

**Notes:** This is the backbone technology choice when `deployment_style.md`'s Event-Driven Architecture option is selected.

---

### Option: Webhooks

**Use when:** need to notify external third-party systems of events (integrations, "call me back when X happens"), consumer is an external system rather than an internal component
**Avoid when:** communicating between your own internal services — use a proper message queue/event stream instead for better reliability guarantees

**Trade-offs:** Simple to implement and integrate with third parties, no persistent connection needed. Delivery reliability depends on the receiving endpoint being available; typically needs retry logic and signature verification (for security) built by the sender.

**Common pitfalls:** No retry/backoff strategy when the receiving endpoint is temporarily down; missing payload signature verification, creating a spoofing risk for receivers.

---

## Backend Tech Stack / Framework (Step 2)

Once the API style is chosen, match it to a backend language/framework.
Weigh team familiarity, existing stack, and ecosystem maturity for the
chosen API style.

| Language | Common frameworks | Notes |
|---|---|---|
| **Python** | FastAPI, Django REST Framework, Flask | FastAPI has strong native async and OpenAPI support, good for REST/GraphQL; Django REST Framework suits teams already on Django with rapid admin/CRUD needs |
| **Node.js / TypeScript** | Express, Fastify, NestJS | NestJS gives more structure (DI, modules) for larger teams; Express/Fastify are lighter-weight for smaller services |
| **Java** | Spring Boot | Very mature ecosystem, strong enterprise tooling, good fit for large teams and REST/gRPC/SOAP alike |
| **Go** | Gin, Echo, standard `net/http` | Excellent performance and low resource usage, strong fit for gRPC and high-throughput services |
| **C# / .NET** | ASP.NET Core | Strong performance, first-class gRPC support, natural fit for teams on the Microsoft stack |
| **Ruby** | Ruby on Rails (API mode) | Fast development speed for CRUD-heavy REST APIs, smaller ecosystem for gRPC/streaming |
| **Rust** | Actix-web, Axum | Best-in-class performance and safety, steeper learning curve, growing but smaller ecosystem |

**Common pitfalls at this step:** picking a framework the team has no experience with purely for its reputation, without weighing the ramp-up cost against the project timeline; ignoring an already-established stack elsewhere in the system when a new component could reasonably reuse it (reduces the number of languages/frameworks the team has to operate).

---

## A note on API Gateways / Backend-for-Frontend (BFF)

Not a separate style choice, but worth the orchestrator being aware of:
when `deployment_style.md`'s Microservices option is chosen, an API
Gateway (single entry point routing to backend services) or a
Backend-for-Frontend (a tailored API layer per client type) often becomes
necessary to avoid exposing internal service topology directly to
clients. If microservices is the deployment style and there are multiple
client types (web + mobile), flag this as a likely follow-up decision
rather than a separate top-level category.

---

## Decision Summary Guidance (for the orchestrator)

**Step 1 — API style:**

| Signal | Leans toward |
|---|---|
| Public API, broad client compatibility needed | REST |
| Multiple client types with different/nested data needs | GraphQL |
| Internal service-to-service, performance-critical, polyglot | gRPC |
| Legacy enterprise integration requirement | SOAP |
| Need live bidirectional updates | WebSocket |
| Need live one-directional server push only | Server-Sent Events |
| Fire-and-forget task distribution, guaranteed delivery | Message Queue |
| Multiple consumers reacting to the same event, need replay | Pub/Sub / Event Streaming |
| Notifying external third-party systems | Webhooks |

**Step 2 — backend stack**: match to the table above, weighted heavily
by existing team stack and familiarity.

Remember this decision is typically made **per component/service
boundary** — record which component/boundary each API decision applies
to in the decision log. A single system very often uses more than one
style (e.g. REST at the public edge, gRPC internally, Kafka for async
events).
