# Hardware & Infrastructure Knowledge Base

This file covers **where and how the system's compute actually runs**:
the hosting model and platform (on-prem vs cloud, which provider, VMs vs
containers vs PaaS vs serverless infra). This is a separate decision from
deployment *style* (`deployment_style.md`, e.g. monolith vs
microservices) — that's about how the system is decomposed; this file is
about the physical/virtual infrastructure it runs on.

This decision is made **once for the system as a whole**. Compute sizing
(instance types, CPU/RAM/GPU allocation, storage provisioning, scaling
thresholds) is intentionally out of scope for this knowledge base — it's
a configuration/capacity-planning concern best handled with real load
data and cost tooling, not an architecture decision to interview about.

## How to use this file (for the orchestrator/LLM)

**Step 1** — determine the hosting model (on-prem/cloud/hybrid). **Step
2** — if cloud, determine the provider. **Step 3** — determine the
compute platform (VMs, containers, PaaS, serverless, bare-metal).

Decision drivers to gather (infer where possible, ask only what's unclear):

- **Existing infrastructure/vendor relationships**: does the org already have cloud accounts, on-prem data centers, or vendor agreements in place?
- **Budget model**: capex-preferred (buy hardware) vs opex-preferred (pay-as-you-go cloud)?
- **Compliance/data residency**: regulatory requirements on where data physically lives, industry-specific certifications needed?
- **Team ops expertise**: does the team have Kubernetes/cloud experience, or would a more managed platform reduce risk?
- **Scaling needs**: steady predictable load vs highly variable/bursty load?
- **Latency to users**: do users need infrastructure in specific geographic regions?
- **Workload type**: general-purpose, compute-intensive, or has specialized hardware needs (e.g. GPU-dependent ML/rendering) that would push toward a specific compute platform?
- **Disaster recovery / high-availability needs**: what level of redundancy and failover is required?
- **Security requirements**: any need for network isolation, dedicated hardware, or specific certifications (HIPAA, FedRAMP, SOC 2, etc.)?

---

## Category: Hosting Model

### Option: On-Premises

**Use when:** strict data residency/compliance requirements mandate physical control of hardware, org already has data center investment and ops staff, extremely predictable steady-state load where owning hardware is more cost-effective long-term, need for specialized hardware not easily available in the cloud
**Avoid when:** load is variable/unpredictable, org doesn't already have data center infrastructure and ops expertise, need to move fast without large upfront capital investment

**Trade-offs:** Full control over hardware and data location, potentially lower long-term cost for very stable, predictable workloads, no dependency on a cloud vendor. High upfront capital cost, need in-house ops expertise for hardware maintenance/networking/power/cooling, much slower to scale up or down than cloud.

**Common pitfalls:** Underestimating the true total cost of ownership (power, cooling, physical security, staff, hardware refresh cycles) versus cloud; provisioning for peak load year-round instead of the cloud's elastic model.

**Notes:** Increasingly a niche choice outside specific regulatory/legacy contexts; most new systems default to cloud unless there's a specific driving requirement for on-prem.

---

### Option: Public Cloud

**Use when:** default choice for most new systems — need elasticity to scale up/down with demand, want to avoid large upfront capital investment, want access to managed services (databases, queues, ML infra) without operating them yourself, need to move fast
**Avoid when:** strict data residency/compliance rules make cloud infeasible for the specific workload, an unusually stable and predictable workload makes on-prem genuinely cheaper long-term (rare in practice for most teams)

**Trade-offs:** Elastic scaling, pay-as-you-go pricing, access to a huge range of managed services that reduce operational burden, global reach for latency-sensitive geographic distribution. Costs can grow unpredictably without active cost management (FinOps); some loss of control compared to owning hardware; potential vendor lock-in depending on which managed services are used.

**Common pitfalls:** Not setting up cost monitoring/alerts early, leading to surprise bills; over-relying on provider-specific managed services without considering the lock-in trade-off if portability matters to the org.

**Notes:** The default recommendation for most new systems unless a specific driver (compliance, existing on-prem investment, highly specialized hardware) points elsewhere.

---

### Option: Hybrid Cloud

**Use when:** need to keep some workloads/data on-prem for compliance or legacy reasons while still getting cloud elasticity for other workloads, gradual migration from on-prem to cloud is underway, need to burst to cloud capacity during peak demand while running baseline load on-prem
**Avoid when:** the added complexity of managing two environments isn't justified by an actual requirement to keep something on-prem — pure cloud is simpler if there's no specific reason to stay hybrid

**Trade-offs:** Flexibility to keep sensitive workloads on-prem while getting cloud benefits elsewhere, supports gradual migration paths. Meaningfully more operational complexity — networking, identity, and monitoring must span both environments coherently.

**Common pitfalls:** Treating hybrid as a permanent end-state rather than a deliberate, time-boxed strategy (e.g. migration in progress) or a genuinely required split (e.g. specific data must stay on-prem) — otherwise the complexity tends to outweigh the benefit.

**Notes:** Common in large enterprises with existing data center investment and gradual cloud adoption strategies, or in regulated industries with partial data residency constraints.

---

## Category: Cloud Provider

*(Applicable if Public Cloud or Hybrid Cloud was chosen above.)*

### Option: Amazon Web Services (AWS)

**Use when:** want the broadest range of services and the largest market share/community, no strong existing preference for another provider, need mature, battle-tested managed services across nearly every category
**Avoid when:** org already has a strong Microsoft or Google ecosystem investment that would make another provider a better integration fit

**Trade-offs:** Broadest service catalog, largest community/talent pool, most third-party tooling support. Pricing and service sprawl can be complex to navigate; no strong differentiator over Azure/GCP for most general workloads beyond breadth and market maturity.

**Notes:** Often the default when there's no other organizational reason to prefer a specific provider.

---

### Option: Microsoft Azure

**Use when:** org is already standardized on Microsoft tooling (Active Directory, .NET, Office 365), need tight integration with existing enterprise Microsoft agreements
**Avoid when:** no existing Microsoft ecosystem investment and no specific Azure-only service is needed

**Trade-offs:** Excellent integration with Microsoft enterprise tooling and identity systems, strong enterprise support agreements. Ecosystem/tooling outside the Microsoft-centric world is somewhat less broad than AWS.

**Notes:** A strong default specifically for organizations already deep in the Microsoft enterprise stack.

---

### Option: Google Cloud Platform (GCP)

**Use when:** workload is data/analytics/ML-heavy (GCP's data and AI tooling is generally considered a strength — BigQuery, Vertex AI), org values GCP's networking and Kubernetes heritage (GKE, originators of Kubernetes)
**Avoid when:** need the broadest general-purpose service catalog (AWS is broader) or deep Microsoft ecosystem integration (Azure fits better)

**Trade-offs:** Strong data analytics and ML/AI tooling, excellent Kubernetes support (GKE), generally praised networking. Smaller market share than AWS/Azure, meaning a somewhat smaller talent pool and third-party tooling ecosystem.

**Notes:** Worth strong consideration specifically for data-intensive or ML-heavy systems.

---

### Option: Smaller/Specialized Providers

**Use when:** cost predictability and simplicity matter more than a huge managed-service catalog, workload is straightforward (VMs, simple managed databases) and doesn't need the breadth of AWS/Azure/GCP, want simpler and more predictable pricing
**Avoid when:** need the depth of managed services (advanced ML infra, huge range of specialized databases, extensive compliance certifications) that the major providers offer

**Trade-offs:** Often simpler, more predictable pricing and a simpler control panel/experience. Smaller service catalog, fewer regions, and generally less depth in managed/enterprise services than the major three providers.

**Examples:** DigitalOcean, Linode (Akamai), Hetzner, Vultr

**Notes:** A good fit for smaller teams/projects that want cloud elasticity without the complexity and cost-management overhead of a hyperscaler.

---

## Category: Compute Platform

*(How workloads actually run on the chosen hosting model/provider.)*

### Option: Virtual Machines (IaaS)

**Use when:** need full control over the OS/runtime environment, team is comfortable managing servers directly, workload doesn't need container-level orchestration complexity, migrating existing on-prem workloads with minimal changes
**Avoid when:** need fine-grained, fast-scaling deployment of many independent services (containers/orchestration fit better), want to minimize server management entirely (PaaS/serverless fit better)

**Trade-offs:** Full control over the environment, straightforward mental model, easy lift-and-shift from on-prem. More manual ops overhead (patching, scaling, configuration management) than containers or PaaS unless paired with strong automation (Terraform, Ansible).

**Notes:** Often paired with infrastructure-as-code tooling (Terraform, Pulumi) and configuration management (Ansible) to reduce the manual overhead.

---

### Option: Containers + Orchestration (Kubernetes / ECS / etc.)

**Use when:** deployment style is Microservices (see `deployment_style.md`) or otherwise need to run many independently deployable, independently scalable units, team has or is building container/orchestration expertise, need portability across cloud providers or hybrid environments
**Avoid when:** system is a simple monolith with modest scaling needs — the orchestration overhead isn't justified; team has no container/Kubernetes experience and timeline doesn't allow ramp-up

**Trade-offs:** Excellent fit for microservices — independent scaling, rolling deployments, self-healing, strong ecosystem (service mesh, observability tooling). Kubernetes specifically has a real learning curve and operational overhead; managed offerings (EKS, AKS, GKE) reduce but don't eliminate this.

**Common pitfalls:** Adopting Kubernetes for a simple system "because it's standard" without the actual scaling/independent-deployment needs that justify its complexity.

**Notes:** Managed Kubernetes (EKS/AKS/GKE) is almost always preferable to self-managed clusters unless there's a specific reason to self-manage. Simpler container orchestrators (AWS ECS, Google Cloud Run) are worth considering when full Kubernetes complexity isn't needed.

---

### Option: Platform-as-a-Service (PaaS)

**Use when:** want to deploy code without managing servers or orchestration directly, team is small or wants to minimize ops burden, standard web application workloads that fit the platform's supported patterns
**Avoid when:** need fine-grained control over the runtime environment or infrastructure that the PaaS doesn't expose, workload has unusual requirements the platform doesn't support well

**Trade-offs:** Minimal ops burden — push code and the platform handles provisioning, scaling, patching. Less control than VMs/containers, potential cost premium at scale compared to more hands-on approaches, some risk of platform-specific lock-in.

**Examples:** Heroku, AWS Elastic Beanstalk, Google App Engine, Azure App Service, Render, Railway

**Notes:** Excellent fit for early-stage products and small teams prioritizing speed over infrastructure control; often outgrown as systems scale and need more customization.

---

### Option: Serverless Compute Infrastructure

**Use when:** deployment style is Serverless/FaaS (see `deployment_style.md`) — this is the underlying infrastructure that choice runs on, workloads are event-triggered and bursty, want cost to scale to near-zero when idle
**Avoid when:** workload doesn't fit the serverless deployment style decision made earlier — this option should generally follow directly from that decision rather than being chosen independently

**Trade-offs:** No server management at all, automatic scaling, pay-per-execution. Cold starts, execution time/memory limits, and provider-specific constraints apply (see `deployment_style.md` for the full trade-off discussion).

**Notes:** This entry exists mainly to close the loop from `deployment_style.md`'s Serverless option down to the actual infrastructure layer (AWS Lambda, Azure Functions, Google Cloud Functions/Run) — it shouldn't usually be chosen independently of that earlier decision.

---

### Option: Bare-Metal / Dedicated Servers

**Use when:** need the absolute maximum, predictable performance with no virtualization overhead, workload has specialized hardware requirements (e.g. specific GPU configurations, high-performance networking), compliance requires dedicated (non-shared) physical hardware
**Avoid when:** workload doesn't have an unusual performance or compliance requirement that justifies giving up cloud elasticity and management convenience

**Trade-offs:** Maximum performance with no virtualization overhead, full hardware control, predictable performance characteristics. Loses cloud elasticity and much of the managed-service convenience; provisioning new capacity takes much longer than spinning up a cloud instance.

**Notes:** A specialized choice — most cloud providers also offer "bare metal" instance types now, which can provide this benefit without fully leaving the cloud ecosystem.

---

## Decision Summary Guidance (for the orchestrator)

| Signal | Leans toward |
|---|---|
| Strict data residency/compliance, existing data center investment | On-Premises |
| Default for most new systems, need elasticity | Public Cloud |
| Partial compliance constraint, gradual migration | Hybrid Cloud |
| No strong provider preference | AWS |
| Existing Microsoft ecosystem | Azure |
| Data/analytics/ML-heavy workload | GCP |
| Cost-sensitive, simple workload | Smaller/Specialized Provider |
| Deployment style = Microservices | Containers + Orchestration |
| Deployment style = Monolith/Modular Monolith, small team | VMs or PaaS |
| Deployment style = Serverless | Serverless Compute Infrastructure |
| Specialized hardware/compliance/max performance need | Bare-Metal |

Remember: this is a **system-wide** decision. Record it once in the
decision log. This file has a strong dependency on `deployment_style.md`
(especially for compute platform) — make sure that decision is settled
first.
