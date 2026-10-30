# Architecture Decision Assistant

A local-first AI assistant that interviews you about a software project and produces an audit-ready architecture decision log. It runs entirely on your machine using [Ollama](https://ollama.com), so no project details leave your computer.

You describe the project, the assistant proposes a set of system components, and then walks through six architecture decision categories, asking only the questions it needs. When you approve the result, it exports the full decision log as Markdown and JSON.

## How it works

The session moves through five phases:

1. **Gathering components.** The assistant proposes components for your project goal. You add, remove or edit them, then lock the list.
2. **Category interviews.** For each decision category (and each component, where the category is per-component), the assistant asks targeted questions until every decision driver is resolved, then commits a decision with a rationale.
3. **Revalidation.** If you change an upstream decision or edit a component, downstream decisions are flagged stale and re-checked with a short prompt instead of a full re-interview.
4. **Review.** You see the full decision log and can approve it or ask to revisit a specific decision.
5. **Report generation.** The final log is written to disk.

### Decision categories

Processed in this order, which is a valid topological sort of the dependency graph in `knowledge/category_dependencies.yaml`:

| Category | Scope | Covers |
|---|---|---|
| `deployment_style` | system | Monolith, microservices, event-driven, serverless |
| `hardware` | system | Hosting model, cloud provider, compute platform |
| `structure_pattern` | per component | Layered, hexagonal, microkernel, event sourcing + CQRS |
| `interfaces` | per component | Web, mobile, desktop, CLI, voice, headless |
| `database` | per component | Data model category and engine |
| `apis` | per component | REST, GraphQL, gRPC, async messaging, backend stack |

## Design notes

- **Deterministic orchestration.** Which interview runs next is decided by plain Python reading the dependency YAML. No LLM call is spent on routing, so the flow is reproducible.
- **Two nodes per agent.** LangGraph re-runs a node from the top when it resumes after an `interrupt()`. Each agent is therefore split into a "step" node that makes the one LLM call and an "ask user" node that only calls `interrupt()`. On a 3-turn interview this measured 3 LLM calls instead of 9.
- **Structured output with retries.** Every LLM turn is validated against a Pydantic schema. Malformed output is fed back to the model with the specific error, up to `max_retries` times.
- **Resumable sessions.** State is checkpointed to SQLite per project, so a pending question survives an app restart.

## Requirements

- Python 3.10 or newer
- [Ollama](https://ollama.com) installed and running, with a model pulled (for example `ollama pull llama3.2`)

## Setup

```bash
pip install -r requirements.txt
```

Edit `config.yaml` to match your Ollama setup:

```yaml
ollama:
  model: "llama3.1"                # any model you have pulled in Ollama
  host: "http://localhost:11434"   # Ollama server address
  temperature: 0.1                 # low values give more reliable structured output
  max_retries: 3                   # retries for malformed structured output, per call

session:
  sessions_dir: "./sessions"       # each project gets its own subfolder here
```

The model name must match one shown by `ollama list`. Smaller models can struggle with structured output, so if you see repeated "trouble processing" errors, try a larger model.

## Run

From the project root:

```bash
streamlit run src/app.py
```

Enter a project name and goal to start a new session. Entering the name of an existing project resumes it.

## Output

When you approve the review, two files are written to `sessions/<project-slug>/output/`:

- `Architecture_Decisions.md`: the human-readable decision log
- `decisions.json`: the same data in machine-readable form

Both can also be downloaded from the app once the session is done. Each session's checkpoint lives next to its output at `sessions/<project-slug>/checkpoint.db`.

## Project layout

```
config.yaml                 Ollama and session settings
requirements.txt
knowledge/
  category_dependencies.yaml   Dependency graph and interview order
  *.md                         Knowledge base for each decision category
src/
  app.py                    Streamlit UI
  orchestrator.py           Scheduling, review and graph assembly
  graph_nodes.py            LangGraph node functions (two nodes per agent)
  agent_loop.py             Standalone loop versions, used for unit testing
  state.py                  Session state and Pydantic models
  schemas.py                Structured-output schemas for LLM turns
  prompts.py                Prompt construction
  merge.py                  State mutation (decisions, components, staleness)
  queries.py                Read-only state queries
  knowledge_loader.py       Parses the knowledge base files
  ollama_client.py          Ollama wrapper with validation and retries
  text_matching.py          Fuzzy matching for review requests
  reports.py                Markdown and JSON report rendering
  config.py                 config.yaml loader
```

## Troubleshooting

- **"I'm having trouble processing that internally."** The model failed to return valid structured output after all retries. Check the terminal running Streamlit for `Structured output attempt` warnings, which show the exact validation error. Try a larger model or raise `max_retries`.
- **Connection errors.** Make sure Ollama is running and `ollama.host` in `config.yaml` is correct.
- **Model not found.** Run `ollama list` and set `ollama.model` to a name that appears there.
