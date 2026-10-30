"""
Streamlit entry point. This is the one file in this project that CANNOT
be exercised in an automated test the way everything else has been --
there's no headless way to drive a Streamlit UI here. What CAN be (and
is) tested is everything this file delegates to: build_graph(), the
checkpointer/serde setup, and the get_state()/Command(resume=...)
mechanics, all verified in earlier sessions against real LangGraph
behavior (including a simulated app-restart scenario proving a pending
interrupt survives a fresh connection/graph object). Run with:

    streamlit run app.py

Design note on chat history: SessionState deliberately keeps each
category interview's conversation narrow and separate (that's the whole
point of the context-trimming work earlier -- the LLM only sees what's
relevant to the decision currently in front of it). A person watching
this in a UI wants one continuous-feeling conversation, so this file
keeps its OWN running display log in st.session_state, decoupled from
what the LLM actually sees. Trade-off: the pending question always
survives a real app restart (backed by the checkpoint itself, proven
above), but earlier turns' chat bubbles are only kept for the current
Streamlit session -- acceptable for now, flagged rather than hidden.
"""

from __future__ import annotations
import sqlite3
from pathlib import Path

import streamlit as st
import yaml

from state import SessionState, get_checkpoint_serde, Phase
from orchestrator import build_graph
from agent_loop import AgentUnrecoverableError
from merge import slugify
from config import load_config
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

PROJECT_ROOT = Path(__file__).parent.parent
YAML_CONFIG_PATH = PROJECT_ROOT / "knowledge" / "category_dependencies.yaml"


# ---------- Pure(ish) helpers -- the parts of this file that CAN be unit tested ----------

def get_session_paths(project_slug: str) -> tuple[Path, Path]:
    """Returns (db_path, output_dir) for a given project slug. Reads config.yaml
    for the sessions base directory rather than a hardcoded constant."""
    app_config = load_config()
    sessions_dir = PROJECT_ROOT / app_config.session.sessions_dir
    session_dir = sessions_dir / project_slug
    return session_dir / "checkpoint.db", session_dir / "output"


def build_project_graph(project_slug: str, project_goal: str | None = None):
    """
    Builds (or rebuilds, on a fresh process) the compiled graph, sqlite
    connection, and LangGraph config for a given project. Idempotent to
    call again with the same project_slug -- SqliteSaver.setup() is
    safe to call repeatedly, and this doesn't touch existing checkpoint
    data. `project_goal` is only used the very first time a project is
    started (see the caller in main()); it's ignored on resume.
    """
    db_path, output_dir = get_session_paths(project_slug)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    yaml_config = yaml.safe_load(YAML_CONFIG_PATH.read_text())
    app_config = load_config()

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn, serde=get_checkpoint_serde())
    saver.setup()

    graph = build_graph(
        yaml_config, checkpointer=saver,
        ollama_model=app_config.ollama.model, ollama_host=app_config.ollama.host,
        temperature=app_config.ollama.temperature, max_retries=app_config.ollama.max_retries,
        output_dir=str(output_dir),
    )
    graph_config = {"configurable": {"thread_id": project_slug}}
    return graph, graph_config, conn


# ---------- Streamlit UI ----------

def main() -> None:
    st.set_page_config(page_title="Architecture Decision Assistant", page_icon="🏗️")
    st.title("🏗️ Architecture Decision Assistant")

    if "project_slug" not in st.session_state:
        _render_landing()
        return

    _render_session()


def _render_landing() -> None:
    st.write("Give your project a name to start (or resume) a session.")
    with st.form("start_form"):
        project_name = st.text_input("Project name")
        project_goal = st.text_area("Project goal (only needed for a new project)")
        submitted = st.form_submit_button("Start")

    if not submitted or not project_name.strip():
        return

    project_slug = slugify(project_name)
    db_path, _ = get_session_paths(project_slug)
    is_new = not db_path.exists()

    if is_new and not project_goal.strip():
        st.error("This is a new project -- please describe the project goal to begin.")
        return

    graph, graph_config, conn = build_project_graph(project_slug, project_goal)
    st.session_state.project_slug = project_slug
    st.session_state.graph = graph
    st.session_state.graph_config = graph_config
    st.session_state.conn = conn
    st.session_state.display_messages = []
    st.session_state.error = None

    if is_new:
        _invoke_graph(SessionState(project_goal=project_goal))
    else:
        st.info(f"Resuming existing session for '{project_name}'.")
    st.rerun()


def _render_session() -> None:
    graph = st.session_state.graph
    graph_config = st.session_state.graph_config

    snapshot = graph.get_state(graph_config)

    with st.sidebar:
        st.caption(f"Project: {st.session_state.project_slug}")
        _render_components_sidebar(snapshot)
        if st.button("Start a different project"):
            for key in ("project_slug", "graph", "graph_config", "conn", "display_messages", "error"):
                st.session_state.pop(key, None)
            st.rerun()

    if st.session_state.get("error"):
        st.error(st.session_state.error)
        if st.button("Retry"):
            st.session_state.error = None
            st.rerun()
        return

    for msg in st.session_state.display_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if not snapshot.next:
        _render_done()
        return

    pending_question = _get_pending_interrupt_value(snapshot)
    if pending_question is None:
        st.warning("Session is in progress but has no pending question -- this shouldn't normally happen.")
        return

    if not st.session_state.display_messages or st.session_state.display_messages[-1]["content"] != pending_question:
        st.session_state.display_messages.append({"role": "assistant", "content": pending_question})
        with st.chat_message("assistant"):
            st.write(pending_question)

    answer = st.chat_input("Your answer")
    if answer:
        st.session_state.display_messages.append({"role": "user", "content": answer})
        _invoke_graph(Command(resume=answer))
        st.rerun()


def _render_components_sidebar(snapshot) -> None:
    """
    Shows the actual current component list straight from checkpointed
    state, independent of whatever prose the Component Agent's
    message_to_user happens to contain. The chat bubble can lag or
    under-describe a proposal/edit; this is the ground truth and is
    always kept in sync since it's read fresh from the snapshot on
    every rerun.
    """
    components = (snapshot.values or {}).get("components", {})
    active = {cid: c for cid, c in components.items() if c.get("status") == "active"}
    st.divider()
    st.caption("Current components")
    if not active:
        st.write("_None proposed yet._")
        return
    for c in active.values():
        st.markdown(f"**{c['name']}**")
        if c.get("description"):
            st.caption(c["description"])


def _get_pending_interrupt_value(snapshot) -> str | None:
    for task in snapshot.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def _invoke_graph(input_value) -> None:
    graph = st.session_state.graph
    graph_config = st.session_state.graph_config
    try:
        graph.invoke(input_value, config=graph_config)
    except AgentUnrecoverableError as e:
        st.session_state.error = (
            f"Something went wrong talking to the model for the '{e.category}' decision"
            + (f" on component '{e.component_id}'" if e.component_id else "")
            + f": {e}\n\nThis usually means a problem with the model or Ollama connection, "
            "not with your answers. Check that Ollama is running and the configured model "
            "in config.yaml is available, then retry."
        )


def _render_done() -> None:
    st.success("All decisions are finalized.")
    _, output_dir = get_session_paths(st.session_state.project_slug)
    md_path = output_dir / "Architecture_Decisions.md"
    json_path = output_dir / "decisions.json"

    if md_path.exists():
        st.download_button(
            "Download Architecture_Decisions.md", data=md_path.read_bytes(),
            file_name="Architecture_Decisions.md", mime="text/markdown",
        )
        with st.expander("Preview"):
            st.markdown(md_path.read_text(encoding="utf-8"))
    if json_path.exists():
        st.download_button(
            "Download decisions.json", data=json_path.read_bytes(),
            file_name="decisions.json", mime="application/json",
        )


if __name__ == "__main__":
    main()
