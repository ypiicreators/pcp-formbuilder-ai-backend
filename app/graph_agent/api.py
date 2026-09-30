"""HTTP API for the workflow graph agent."""

from fastapi import APIRouter

from app.graph_agent.graph import run_graph_agent
from app.graph_agent.models import GraphAgentMessageRequest, GraphAgentMessageResponse

router = APIRouter(prefix="/workflow-graph", tags=["workflow-graph-agent"])


@router.post("/message", response_model=GraphAgentMessageResponse)
async def workflow_graph_message(req: GraphAgentMessageRequest) -> GraphAgentMessageResponse:
    return await run_graph_agent(req)
