from typing import Annotated, List, Sequence, Any, Dict, Optional
from pydantic import BaseModel, Field
from typing_extensions import Literal, TypedDict
from langgraph.graph import MessagesState


class MessageToApproval(BaseModel):
    approved: bool = Field(description="Set to True if the user approves the proposed tool call and arguments, or False if the user rejects them.")
    reason: str = Field(description="Always provide a reason. If 'approved' is True, explain why approval was given. If 'approved' is False, explain why it was rejected.")
    edited_args: Optional[Dict[str, Any]] = Field(default=None, description="If the user wants to modify the proposed tool arguments before approval, they can provide the revised arguments here as a dictionary. If no changes are needed, this field can be omitted or set to None.")
    # message: Optional[str] = Field(default=None, description="An optional message from the user providing additional context or instructions regarding their approval decision.")


class ResponseModel(BaseModel):
    content: str = Field(description="Main response or plan/rationale.")
    need_info: bool = Field(description="True when additional details from the user are needed before defining a tool call (e.g., parameters, clarifications). Any message that requests information from the user should set this flag to true.")
    tool_to_call: Optional[str] = Field(description="The name of the tool to call, if any. Null if no tool call is needed at this time.")
    tool_args: Optional[Dict[str, Any]] = Field(description="A dictionary of arguments to pass to the tool. Null if no tool call is needed at this time.")
    operation_type: Optional[Literal["read", "create", "update", "delete"]] = Field(description="The type of operation the tool will perform. This field is defined by both the tool_to_call and the tool_args. Null if no tool call is needed at this time.")
    hitl_tool_approval: bool = Field(description="True if human approval is required before tool execution. Remember that human approval is ALWAYS required before any create/update/delete operations/tool call. If this parameter is true, you need to call a tool, and the tool call details")
    hitl_tool_approval_reason: str = Field(description=(
        "Justification for the value of `hitl_tool_approval`. "
        "Always explain why human approval is or is not required. "
        "- If operation_type is 'create', 'update', or 'delete', approval is ALWAYS required. "
        "- If operation_type is 'read', approval is NOT required. "
        "- If no tool call is needed, set this to an empty string. "
        "When additional user information is needed before execution, explain why approval is required."
    ))

class AgentState(MessagesState):
    # system_prompt: str = system_prompt
    # ensure_struct_output: str = ensure_struct_output
    tool_calls: List[Dict[str, Any]]
    approved: Optional[bool] = None
