# Internal Structure Pattern Knowledge Base

This file covers **internal structure pattern** — how code is organized
*within* a given deployable unit (a monolith, a single microservice, a
serverless function, etc.). This is a separate decision from
**deployment style** (see `deployment_style.md`), which covers how the
system is decomposed and distributed at the macro level.

A structure pattern decision is typically made **per component**, after
(or alongside) that component's deployment style is known. Not every
component needs a special pattern — for simple components, "No Special
Pattern" is a legitimate, often correct, choice.

## How to use this file (for the orchestrator/LLM)

Gather signal on these decision drivers before recommending a pattern.
Infer what you can from context; only ask what's still unclear:

- **Domain complexity**: simple CRUD vs complex business rules/workflows
- **Testability priority**: how important is it to unit-test business logic in total isolation from frameworks/infra?
- **Infra volatility**: how likely is the team to swap databases, frameworks, or external services over the component's lifetime?
- **Extensibility needs**: does this component need runtime-pluggable features (e.g. third-party plugins, customer-specific extensions)?
- **Audit/history needs**: does the component need a full history of state changes, or the ability to reconstruct past state?
- **Read vs write asymmetry**: are the read and write access patterns for this component's data meaningfully different?
- **Team familiarity**: has the team used this pattern before? Unfamiliar patterns carry real ramp-up cost.
- **Expected component lifespan**: short-lived/throwaway vs long-lived core system

---

## Option: No Special Pattern (Simple/Direct)

**Category:** Baseline
**Use when:** component is simple (basic CRUD, small utility service, short-lived/throwaway component), domain logic is minimal, extra structure would add ceremony without benefit
**Avoid when:** domain logic is non-trivial, or the component is expected to grow substantially over time — retrofitting structure later is more expensive than adopting it early

**Trade-offs:**
- Fastest to build, easiest to understand for a small scope, minimal indirection
- Doesn't hold up well as business logic complexity grows — logic tends to leak into request handlers/controllers

**Common pitfalls:**
- Sticking with "no pattern" well past the point where the component has outgrown it
- Underestimating how much a component will grow before it's built

**Scaling notes:** Not applicable — this is an internal-code decision, not a runtime-scaling one.

**Related decision drivers:** domain complexity, expected component lifespan

---

## Option: Layered (N-Tier)

**Category:** Structural pattern — separation by technical responsibility
**Use when:** clear separation between presentation, business logic, and data access is needed; team is used to traditional enterprise patterns; domain logic is moderately complex
**Avoid when:** the domain is simple enough that layering adds ceremony without benefit; component needs to be highly decoupled from infrastructure (consider Hexagonal instead)

**Trade-offs:**
- Easy to understand, well-known pattern, good tooling support
- Can lead to "anemic" layers that just pass data through with little added value
- Changes that cut across layers (common in practice) touch multiple files/layers

**Common pitfalls:**
- Business logic leaking into the presentation or data-access layer
- Over-layering trivial CRUD operations, adding indirection without value

**Scaling notes:** Not applicable — internal-code decision only.

**Related decision drivers:** domain complexity, team familiarity with the pattern

---

## Option: Hexagonal / Clean Architecture (Ports & Adapters)

**Category:** Structural pattern — dependency direction / testability focus
**Use when:** want business logic fully decoupled from frameworks, databases, and delivery mechanisms; expect to swap infrastructure (DB, external services) over time; want high testability of core logic in isolation; component is expected to be long-lived and core to the system
**Avoid when:** the component is small/short-lived enough that the extra abstraction layers aren't worth the overhead

**Trade-offs:**
- Core domain logic has zero dependency on frameworks or infrastructure, making it easy to unit test
- Infrastructure (DB, message broker, web framework) can be swapped with minimal impact on business logic
- More upfront structure and indirection (ports, adapters, DI wiring) than a straightforward layered approach

**Common pitfalls:**
- Applying it dogmatically to trivial CRUD components where the ceremony outweighs the benefit
- Leaky abstractions where infrastructure concerns sneak into the "pure" domain layer anyway

**Scaling notes:** Not applicable — internal-code decision only.

**Related decision drivers:** domain complexity, expected lifespan of the component, testability requirements, infra volatility

---

## Option: Microkernel / Plugin Architecture

**Category:** Structural pattern — extensible core + pluggable modules
**Use when:** component needs a stable core with variable/optional features (e.g. IDEs, CMS platforms, product lines with customer-specific extensions)
**Avoid when:** the component doesn't genuinely need runtime extensibility — added indirection for no benefit

**Trade-offs:**
- Core stays stable while features are added/removed as plugins without touching the core
- Good fit for product lines needing customer-specific customization
- Plugin API becomes a long-term contract that's hard to change once third parties depend on it
- Debugging issues that cross the core/plugin boundary can be harder

**Common pitfalls:**
- Designing the plugin API too early, before enough real plugins exist to validate it
- Plugins reaching into core internals instead of using the defined extension points

**Scaling notes:** Not applicable — internal-code decision only.

**Related decision drivers:** extensibility needs, third-party/customer customization needs

---

## Option: Event Sourcing + CQRS

**Category:** Structural/data pattern — separates write model from read model, persists state as an event log
**Use when:** need a full audit trail of every state change, complex domain where read and write models genuinely differ, need to reconstruct past state or support temporal queries
**Avoid when:** domain is simple CRUD; team is not prepared for the added complexity of eventual consistency between write and read models; no real need for historical replay

**Trade-offs:**
- Complete history of changes, natural audit log, can rebuild state/read models from scratch
- Read and write models can be optimized independently
- Significant complexity: eventual consistency between write/read sides, event schema evolution, replay tooling
- Steeper learning curve for the team

**Common pitfalls:**
- Applying event sourcing to the whole system instead of just the components that actually need it
- Underestimating the effort of versioning events as the schema evolves over time

**Scaling notes:** The read side (query models) can be scaled and denormalized independently of the write side — one of the pattern's main practical benefits, though it's a data/query concern rather than a deployment-scaling one.

**Related decision drivers:** domain complexity, audit/history needs, read vs write asymmetry, consistency needs

**Note:** This pattern pairs naturally with Event-Driven deployment style
(`deployment_style.md`) but the two are independent decisions — a
component can use Event Sourcing + CQRS internally regardless of how it
communicates externally, though EDA does make propagating those events
to other components easier.

---

## Decision Summary Guidance (for the orchestrator)

When enough decision drivers are known, use this rough mapping as a sanity
check before finalizing (not a rigid rule — the LLM should still reason
about the specific component):

| Signal | Leans toward |
|---|---|
| Simple CRUD, short-lived, minimal logic | No Special Pattern |
| Moderate domain complexity, team knows the pattern | Layered |
| Need infra independence + high testability, long-lived core component | Hexagonal/Clean |
| Need runtime-pluggable/customer-specific features | Microkernel |
| Need full change history, complex read/write asymmetry | Event Sourcing + CQRS |

This decision is made independently for each component and does not need
to be uniform across the whole system — e.g. a simple notification
service might use "No Special Pattern" while the core order-management
component uses Hexagonal. Record the pattern decision per component in
the decision log, alongside its deployment style.
