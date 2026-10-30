# Database Knowledge Base

This file covers **data storage decisions**: first the data model category
(SQL/relational, document, key-value, wide-column, graph, time-series,
NewSQL), then the specific engine within that category (e.g. PostgreSQL
vs MySQL vs Oracle within Relational).

This decision is typically made **per component** — different components
in the same system often legitimately use different databases (polyglot
persistence). Don't assume one database choice for the whole system.

## How to use this file (for the orchestrator/LLM)

**Step 1** — determine the data model category using the decision drivers
below. **Step 2** — once the category is settled, use the matching
sub-section to pick a specific engine. Don't jump straight to a specific
product before the data model category is actually settled.

Decision drivers to gather (infer where possible, ask only what's unclear):

- **Data shape**: is data naturally tabular/relational, document-like/nested, simple key-value, highly interconnected (graph), or time-stamped/sequential?
- **Consistency needs**: strong ACID transactions required, or is eventual consistency acceptable?
- **Query complexity**: need complex joins/aggregations, or mostly simple lookups by key?
- **Read/write ratio and scale**: read-heavy, write-heavy, or balanced? What's the expected throughput?
- **Schema volatility**: is the schema stable and well-understood, or expected to evolve frequently?
- **Consistency vs availability trade-off (CAP)**: if the system must partition-tolerate, does it favor consistency or availability?
- **Latency requirements**: sub-millisecond lookups needed, or is normal DB latency fine?
- **Operational familiarity/budget**: team's existing expertise, licensing costs, managed-service availability
- **Compliance/licensing constraints**: e.g. must use an existing enterprise Oracle license, data residency rules

---

## Category: Relational (SQL)

**Use when:** data is naturally tabular with clear relationships, need strong ACID transactions, need complex queries/joins/aggregations, schema is relatively stable
**Avoid when:** data is highly nested/schema-less and evolves rapidly, need extreme horizontal write scaling beyond what sharding a relational DB can reasonably provide, access pattern is pure key-value lookups at massive scale

**General trade-offs:** Mature tooling, strong consistency guarantees, powerful query language (SQL), but vertical scaling limits and schema migrations require more care than schema-less alternatives.

### Option: PostgreSQL

**Use when:** default choice for most new relational workloads — need advanced features (JSONB, full-text search, extensions like PostGIS), strong standards compliance, open-source with no licensing cost
**Avoid when:** need built-in horizontal write sharding out of the box (native Postgres doesn't provide this; needs extensions/tooling), team requires a fully managed proprietary ecosystem with enterprise support contracts

**Trade-offs:** Extremely feature-rich and extensible, strong community and tooling, free and open-source. Horizontal scaling requires additional tooling (Citus, read replicas) rather than being native.

**Common pitfalls:** Not tuning autovacuum/indexes for high-write workloads; treating JSONB columns as a replacement for proper schema design when a relational model would fit better.

**Scaling notes:** Scales vertically well; read scaling via replicas is straightforward; write scaling requires sharding solutions or a NewSQL alternative if truly needed.

---

### Option: MySQL / MariaDB

**Use when:** need a widely-supported, well-understood RDBMS with a huge hosting/tooling ecosystem, simpler feature needs than Postgres, read-heavy web applications
**Avoid when:** need Postgres's more advanced data types/extensions, need the strictest standards compliance

**Trade-offs:** Very widely supported (hosting, ORMs, tooling), simple replication setup, large talent pool. Historically weaker on complex queries and some advanced SQL features compared to Postgres, though this gap has narrowed.

**Common pitfalls:** Relying on MyISAM instead of InnoDB (loses transactional integrity); default configurations often need tuning for production write loads.

**Scaling notes:** Similar profile to Postgres — vertical scaling plus read replicas; write scaling needs sharding (e.g. Vitess) for very large workloads.

---

### Option: Oracle Database

**Use when:** enterprise environment already standardized on Oracle, need advanced enterprise features (fine-grained auditing, advanced partitioning, RAC for high availability), regulatory/compliance requirements that map to Oracle's certified feature set
**Avoid when:** licensing cost is a concern, team has no existing Oracle expertise, a simpler/free RDBMS would meet the same requirements

**Trade-offs:** Extremely mature, feature-rich, strong enterprise support and high-availability options (RAC). Licensing costs are significant and often the deciding factor against it.

**Common pitfalls:** Choosing Oracle by default in enterprise contexts without checking whether the licensing cost is actually justified by features being used.

**Scaling notes:** Scales well vertically and via RAC for high availability; among the most capable RDBMS options for very large enterprise workloads, at a cost.

---

### Option: Microsoft SQL Server

**Use when:** organization is standardized on the Microsoft stack (.NET, Azure), need tight integration with Microsoft tooling (Power BI, Azure services, Active Directory)
**Avoid when:** team is not on the Microsoft stack, licensing cost matters, need cross-platform flexibility without vendor considerations

**Trade-offs:** Excellent integration with Microsoft ecosystem and tooling, strong enterprise features. Licensing costs and a historical Windows-centric bias (though Linux support now exists).

**Common pitfalls:** Underestimating licensing costs at scale (core-based licensing can get expensive).

**Scaling notes:** Comparable to other major RDBMS options; strong integration with Azure managed offerings for scaling.

---

### Option: SQLite

**Use when:** embedded/local storage, mobile or desktop apps, prototyping, low-write-concurrency single-user or single-process scenarios, testing environments
**Avoid when:** need concurrent multi-writer access, need a client-server database for a networked application, need to scale beyond a single file/process

**Trade-offs:** Zero-configuration, serverless, extremely simple to deploy (just a file). Not designed for high-concurrency multi-user server workloads.

**Common pitfalls:** Using it as a production database for a multi-user networked application where write concurrency becomes a bottleneck.

**Scaling notes:** Not intended to scale beyond a single file/process; appropriate scope is local/embedded use, not a scalable server-side datastore.

---

## Category: Document Store (NoSQL)

**Use when:** data is naturally nested/hierarchical (JSON-like documents), schema varies between records or evolves frequently, need to store and query semi-structured data without rigid upfront schema design
**Avoid when:** data has many relationships that require joins across collections, need strong multi-document ACID transactions as the primary access pattern (though some document stores now support this to a degree)

### Option: MongoDB

**Use when:** default choice for most document-store needs — flexible schema, rich query language, wide adoption and tooling, need horizontal scaling via sharding
**Avoid when:** data is actually highly relational (a relational DB would fit better), need extremely low-latency key-value access (consider a dedicated KV store instead)

**Trade-offs:** Flexible schema, good developer ergonomics, native horizontal scaling via sharding, mature ecosystem. Multi-document transactions exist but add overhead; not a substitute for genuinely relational data modeling.

**Common pitfalls:** Modeling data exactly like a relational schema (defeats the purpose) or, conversely, avoiding all structure until data becomes inconsistent and hard to query.

**Scaling notes:** Scales horizontally well via native sharding; read scaling via replica sets.

---

### Option: Amazon DynamoDB

**Use when:** already on AWS, need a fully managed, serverless, extremely low-latency key-value/document store with predictable performance at any scale
**Avoid when:** need complex ad-hoc queries/joins, want to avoid vendor lock-in, access patterns aren't well understood upfront (DynamoDB requires designing table/index structure around known access patterns in advance)

**Trade-offs:** Fully managed, scales seamlessly, very low and predictable latency. Requires access patterns to be well-understood at design time; ad-hoc querying is limited compared to MongoDB or SQL.

**Common pitfalls:** Designing the table structure without first mapping out actual query access patterns, leading to expensive redesigns later.

**Scaling notes:** Scales near-automatically and is designed for very high, predictable-latency throughput; this is one of its core selling points.

---

## Category: Key-Value Store (NoSQL)

**Use when:** access pattern is simple lookups by key, need extremely low latency, use cases like caching, session storage, real-time leaderboards, rate limiting
**Avoid when:** need complex queries, relationships, or aggregations — key-value stores are intentionally minimal

### Option: Redis

**Use when:** need an extremely fast in-memory store for caching, session management, pub/sub messaging, leaderboards, or rate limiting; can tolerate data loss on crash unless persistence is explicitly configured
**Avoid when:** need this to be the durable system of record for critical data without careful persistence/replication configuration; dataset is far larger than available memory (though Redis has some on-disk options)

**Trade-offs:** Extremely fast (in-memory), versatile data structures (strings, hashes, sets, sorted sets, streams), widely used for caching. Primarily memory-bound; durability requires explicit configuration (AOF/RDB persistence, replication).

**Common pitfalls:** Using Redis as a primary durable datastore without proper persistence and backup strategy in place.

**Scaling notes:** Scales via clustering (Redis Cluster) for both capacity and throughput; read scaling via replicas.

---

### Option: Memcached

**Use when:** need a very simple, high-performance, pure in-memory cache with no persistence needs, simpler use case than what Redis offers
**Avoid when:** need any persistence, richer data structures, or pub/sub — Redis is generally the better default unless the workload is this narrow

**Trade-offs:** Extremely simple and fast for pure caching. Much more limited feature set than Redis (no persistence, no advanced data structures).

**Common pitfalls:** Choosing it out of habit when Redis would offer more flexibility for roughly the same operational cost.

**Scaling notes:** Scales horizontally by adding nodes to the cache pool; purely in-memory, no persistence to worry about.

---

## Category: Wide-Column / Columnar Store (NoSQL)

**Use when:** need to handle very high write throughput at large scale, data has a semi-structured row-key + flexible columns shape, time-series-like or IoT-scale data ingestion
**Avoid when:** dataset is small/moderate — the operational complexity isn't justified; need complex ad-hoc queries or joins

### Option: Apache Cassandra

**Use when:** need massive write throughput across many nodes, need high availability with no single point of failure, multi-datacenter replication is a requirement
**Avoid when:** team lacks operational experience with distributed databases, dataset/throughput doesn't actually require this scale, need strong consistency by default (Cassandra defaults to eventual consistency, tunable per query)

**Trade-offs:** Excellent write throughput and horizontal scalability, no single point of failure, multi-datacenter support. Eventually consistent by default (tunable), and modeling data requires designing around query patterns upfront (denormalization is expected).

**Common pitfalls:** Modeling data relationally instead of around specific query patterns, which is how Cassandra is meant to be used; underestimating the operational expertise required to run it well.

**Scaling notes:** One of the strongest options for near-linear horizontal write scaling across many commodity nodes.

---

### Option: Amazon DynamoDB (Wide-Column mode) / Google Bigtable

**Use when:** need a fully managed wide-column-style store without operating the cluster yourself (Bigtable on GCP, DynamoDB on AWS), massive scale IoT/time-series/analytics ingestion
**Avoid when:** need to avoid cloud vendor lock-in, workload doesn't need this scale

**Trade-offs:** Removes the operational burden of running Cassandra/HBase yourself; scales seamlessly. Tied to a specific cloud provider.

**Scaling notes:** Designed for very large-scale, high-throughput ingestion with minimal operational overhead.

---

## Category: Graph Database

**Use when:** data is fundamentally about relationships/connections between entities (social graphs, recommendation engines, fraud detection, knowledge graphs), queries are naturally traversal-based ("find all X connected to Y within N hops")
**Avoid when:** relationships aren't actually central to the access patterns — using a graph DB for mostly tabular data adds unneeded complexity

### Option: Neo4j

**Use when:** default choice for most graph use cases — mature query language (Cypher), strong tooling and visualization support
**Avoid when:** need extreme horizontal write scaling beyond what Neo4j's clustering comfortably provides, use case doesn't actually need graph traversal as the primary access pattern

**Trade-offs:** Purpose-built for graph traversal queries, intuitive query language, strong ecosystem and visualization tools. Horizontal scaling is more limited than some distributed alternatives.

**Common pitfalls:** Using a graph database when the actual queries are mostly simple lookups that a relational or document store would handle just as well with less operational overhead.

**Scaling notes:** Scales vertically well; horizontal scaling exists via clustering but is less seamless than wide-column or document stores designed for that from the ground up.

---

### Option: Amazon Neptune

**Use when:** already on AWS, need a managed graph database, support for both property graph (Gremlin) and RDF (SPARQL) models
**Avoid when:** want to avoid vendor lock-in, need Neo4j's specific tooling/ecosystem

**Trade-offs:** Fully managed, integrates with AWS ecosystem, supports two graph query paradigms. Tied to AWS.

**Scaling notes:** Managed scaling and read replicas handled by AWS; good for teams that don't want to operate graph infrastructure themselves.

---

## Category: Time-Series Database

**Use when:** data is fundamentally timestamped and sequential (metrics, IoT sensor data, monitoring/observability data), need efficient time-range queries and downsampling/retention policies
**Avoid when:** data isn't genuinely time-series in nature — general-purpose databases handle occasional timestamped data fine without a specialized engine

### Option: InfluxDB

**Use when:** need a purpose-built time-series database for metrics/monitoring/IoT, want built-in retention policies and downsampling
**Avoid when:** need this data to be joined heavily with relational data — consider TimescaleDB instead if staying in the SQL ecosystem matters

**Trade-offs:** Purpose-built for time-series workloads, efficient storage and querying of time-ranged data, built-in retention/downsampling. Smaller general-purpose query ecosystem than SQL-based alternatives.

**Scaling notes:** Designed to handle high-throughput time-series ingestion efficiently; clustering available in commercial offerings for horizontal scale.

---

### Option: TimescaleDB

**Use when:** want time-series capabilities but need to stay within the PostgreSQL/SQL ecosystem (joins with relational data, existing Postgres tooling/expertise)
**Avoid when:** don't need any relational join capability and a purpose-built engine like InfluxDB would be simpler

**Trade-offs:** Combines time-series performance with full SQL and relational capability (it's a Postgres extension). Slightly less specialized raw time-series performance than a dedicated engine like InfluxDB at extreme scale.

**Scaling notes:** Scales similarly to Postgres, with time-series-specific optimizations (automatic partitioning/"hypertables") built in.

---

## Category: NewSQL

**Use when:** need relational semantics (SQL, ACID transactions) *and* horizontal scalability beyond what traditional RDBMS sharding comfortably provides — the "best of both worlds" category
**Avoid when:** workload doesn't actually need this scale — traditional RDBMS with read replicas is simpler and sufficient for most systems; team has no experience with distributed SQL operational characteristics

### Option: CockroachDB

**Use when:** need PostgreSQL-compatible SQL with built-in horizontal scaling and strong consistency, need multi-region resilience with automatic failover
**Avoid when:** workload is well within what a traditional RDBMS handles — added operational complexity isn't justified

**Trade-offs:** Distributed SQL with strong consistency and PostgreSQL wire compatibility, automatic sharding and rebalancing, multi-region support. More operational complexity than a single-node RDBMS; potentially higher latency for single-row operations due to consensus overhead.

**Scaling notes:** Designed from the ground up for horizontal scale-out while retaining SQL and strong consistency.

---

### Option: Google Cloud Spanner

**Use when:** already on GCP, need globally distributed strong consistency with SQL semantics at very large scale
**Avoid when:** want to avoid vendor lock-in, workload doesn't need global-scale distribution

**Trade-offs:** Globally distributed, strongly consistent, SQL interface, fully managed. Tied to GCP; cost can be significant at scale.

**Scaling notes:** One of the few systems offering global strong consistency at scale; purpose-built for that specific, demanding requirement.

---

## Decision Summary Guidance (for the orchestrator)

**Step 1 — Data model category:**

| Signal | Leans toward |
|---|---|
| Tabular data, need joins, ACID transactions | Relational (SQL) |
| Nested/semi-structured, flexible schema | Document Store |
| Simple key lookups, need extreme low latency | Key-Value Store |
| Very high write throughput, huge scale | Wide-Column Store |
| Relationships/connections are the primary query pattern | Graph Database |
| Timestamped, sequential data (metrics/IoT) | Time-Series Database |
| Need SQL + ACID + horizontal scale together | NewSQL |

**Step 2 — Specific engine**, once category is chosen: weigh existing team
familiarity, managed-service availability, licensing/budget constraints,
and cloud provider alignment (if any) alongside the per-option guidance
above.

Remember this decision is typically made **per component** — record which
component each database decision applies to in the decision log, not just
a single system-wide choice.
