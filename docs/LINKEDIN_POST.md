# LinkedIn post — Agentic Digital Twin

## Post-ready copy

I’ve renamed **My Digital Twin** to **Agentic Digital Twin** — because it now does more than answer questions.

Give it a hiring goal and it:

→ builds a bounded plan
→ chooses only the relevant public tools
→ retrieves evidence from my CV and public code
→ verifies every claim before answering
→ shows the plan, tools, evidence, and verification outcome

“Agentic” is a capability claim here, not just a new label. The system can decide how to complete a goal within a deliberately limited toolset, while authority gates keep personal context, outreach, and contractual decisions under human control.

The stack includes Python, FastAPI, LangChain, LangGraph, MCP, hybrid retrieval, structured tool calling, SSE traces, and a second-pass claim verifier.

Try the Agentic Digital Twin:
https://prathamesh-git9.github.io/agentic-digital-twin/

Source code:
https://github.com/prathamesh-git9/agentic-digital-twin

I’d value feedback on the agent run view: does seeing the plan, tools, evidence, and verification make the answers easier to trust?

#AgenticAI #AIAgents #DigitalTwin #LangGraph #MCP

## Why this wording is defensible

The project uses the term *agentic* for observable capabilities rather than for a chatbot-style interface:

- A model-directed loop creates a bounded plan and selects tools based on the visitor’s goal.
- Tool calls retrieve CV, repository, role, and approved public-context evidence.
- The interface exposes plan, action, evidence, and verification state.
- Unsupported claims are removed, and sensitive or consequential effects stay behind explicit authority gates.

This matches the recurring capability boundary in current primary sources:

- IBM Research’s AAAI 2025 agentic-digital-twin work emphasizes LLMs that autonomously select tools and data streams for a user-specific query: https://research.ibm.com/publications/agentic-ai-for-digital-twin--1
- NIST describes agentic systems as planning multi-step tasks, taking actions through tools and databases, and needing visibility into tool use and gathered evidence: https://www.nist.gov/programs-projects/building-evaluation-probes-agentic-ai
- Anthropic distinguishes agents from fixed workflows by whether the model dynamically directs its process and tool use: https://www.anthropic.com/engineering/building-effective-agents
- The Digital Twin Consortium recommends evaluating agent labels through demonstrated capabilities because the market applies “agentic” to systems with very different levels of agency: https://www.digitaltwinconsortium.org/press-room/06-05-25/

## Publishing note

LinkedIn allows the text of an existing post to be edited, but not its attached photo, document, or video. The existing `artifacts/agentic-digital-twin-linkedin.png` already uses the new wording, so it can remain attached while the text is replaced. LinkedIn’s current post limit is 3,000 characters; the copy above stays below it.
