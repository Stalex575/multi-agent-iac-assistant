"""Gradio demo UI: chat on the left, live pipeline internals on the right.

Run:  python -m infra_assistant.ui   (http://127.0.0.1:7860)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import gradio as gr

from infra_assistant.graph import build_app
from infra_assistant.state import turn_input

APP: Any = None

EXAMPLES = [
    "I need to host a Django web shop with PostgreSQL. It must survive an AZ outage, ~200 req/s at peak, public website, budget $400/month.",
    "Set up infrastructure for an internal analytics API used only from our VPN",
    "What is the difference between an ALB and an NLB?",
    "Write me a poem about Kubernetes",
]


def _app() -> Any:
    global APP
    if APP is None:
        APP = build_app()
    return APP


def _sources_md(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "_no retrieval this turn_"
    return "\n".join(f"{n}. **{h['title']}** (`{h['doc_id']}`, score {h['score']})  \n   {h['source']}"
                     for n, h in enumerate(hits, 1))


async def respond(message: str, chat: list[dict[str, str]], thread_id: str):
    if not message.strip():
        yield chat, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), ""
        return
    chat = chat + [{"role": "user", "content": message}, {"role": "assistant", "content": "⏳ thinking…"}]
    yield chat, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), ""
    try:
        result = await _app().ainvoke(turn_input(message), config={"configurable": {"thread_id": thread_id}})
    except Exception as exc:  # surface errors in the demo instead of crashing the UI
        chat[-1]["content"] = f"❌ Error: `{type(exc).__name__}: {str(exc)[:400]}`"
        yield chat, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), ""
        return
    chat[-1]["content"] = result.get("final_response", "")
    trace = result.get("trace", [])
    path = " → ".join(s["node"] for s in trace)
    yield (
        chat,
        f"**Path:** {path}\n\n**Intent:** {result.get('intent')} - {result.get('intent_reason', '')}",
        trace,
        {"requirements": result.get("requirements", {}), "assumed": result.get("assumed_slots", []),
         "awaiting_answers": result.get("awaiting_requirements", False)},
        _sources_md(result.get("retrieved", [])),
        {k: v for k, v in (result.get("validation") or {}).items() if k != "formatted_code"},
        "",
    )


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="InfraPilot") as demo:
        gr.Markdown("# InfraPilot - requirements → validated Terraform\n"
                    "LangGraph · Gemini · hybrid RAG over AWS Well-Architected + Terraform docs · `terraform validate` loop")
        thread = gr.State(lambda: uuid.uuid4().hex)
        with gr.Row():
            with gr.Column(scale=3):
                chat = gr.Chatbot(height=620, label="Conversation")
                box = gr.Textbox(placeholder="Describe the system you want to deploy, or ask an AWS question…",
                                 show_label=False)
                gr.Examples(EXAMPLES, inputs=box)
                new = gr.Button("New conversation")
            with gr.Column(scale=2):
                path = gr.Markdown("**Path:** –")
                with gr.Tab("Trace"):
                    trace = gr.JSON(label="Per-node trace (ms, decisions)")
                with gr.Tab("Requirements"):
                    reqs = gr.JSON(label="Collected slots")
                with gr.Tab("Retrieved docs"):
                    sources = gr.Markdown()
                with gr.Tab("Validator"):
                    validation = gr.JSON(label="terraform validate + policy + requirement checks")
        outputs = [chat, path, trace, reqs, sources, validation, box]
        box.submit(respond, [box, chat, thread], outputs)
        new.click(lambda: ([], uuid.uuid4().hex, "**Path:** –", None, None, "", None), None,
                  [chat, thread, path, trace, reqs, sources, validation])
    return demo


def main() -> None:
    build_ui().launch(server_name="127.0.0.1", server_port=7860)


if __name__ == "__main__":
    main()
