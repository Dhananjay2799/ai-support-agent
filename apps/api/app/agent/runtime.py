"""Process-wide agent wiring: one graph, one pool, built on first use.

Serverless instances may get a fresh event loop; anything bound to a loop (the pool,
the checkpointer) is rebuilt if the loop changed.
"""

import asyncio
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.graph import AgentState, build_graph
from app.agent.llm import OpenAIResponder
from app.agent.tools import ToolEnv
from app.config import get_settings
from app.db import get_pool
from app.llm.client import get_openai
from app.policy.config import load_policy
from app.rag.embeddings import OpenAIEmbedder
from app.rag.guardrails import OpenAISupportJudge, load_thresholds
from app.rag.retriever import HybridRetriever

Graph = CompiledStateGraph[AgentState, None, AgentState, AgentState]

_cache: dict[str, Any] = {}


async def get_graph() -> Graph:
    loop = asyncio.get_running_loop()
    if _cache.get("loop") is loop:
        graph: Graph = _cache["graph"]
        return graph

    settings = get_settings()
    client = get_openai()
    pool = await get_pool()
    embedder = OpenAIEmbedder(client)
    env = ToolEnv(
        pool=pool,
        retriever=HybridRetriever(embedder),
        judge=OpenAISupportJudge(client, settings.judge_model, settings.judge_reasoning_effort),
        thresholds=load_thresholds(),
        policy=load_policy(),
        demo_mode=settings.demo_mode,
    )
    responder = OpenAIResponder(client, settings.agent_model, settings.agent_reasoning_effort)
    graph = build_graph(responder, env, AsyncPostgresSaver(pool))
    _cache.update(loop=loop, graph=graph)
    return graph
