import asyncio, nest_asyncio
from datetime import datetime
import os, json, uuid
from typing import Annotated, List, Sequence, Any, Dict, Optional
import traceback

# from IPython.display import Image, display
from langchain_core.tools import tool
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_to_dict, messages_to_dict, messages_from_dict
)
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.types import interrupt, Command, Send, StateSnapshot, Interrupt
from langgraph_checkpoint_dynamodb import DynamoDBSaver, DynamoDBConfig, DynamoDBTableConfig
from langgraph_checkpoint_dynamodb.config import BillingMode

from models import ResponseModel, AgentState
from mcp_servers import multi_client
from utils import _get_tools_sync, tools_to_text


ALL_TOOLS = _get_tools_sync(multi_client)
SELECTED_TOOLS = [t for t in ALL_TOOLS if t.name in ['call_aws']]
NAME_TO_TOOL = {tool.name: tool for tool in SELECTED_TOOLS}

llm = init_chat_model(
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    model_provider="bedrock_converse",
    temperature=0.0,
)


system_prompt = (
    f"You are an expert cloud infrastructure assistant. Today is {datetime.now().strftime('%Y-%m-%d')}.\n"
    "You can help create, manage, and delete cloud resources using the available tools. "
    "You must decide when to call a tool and when to ask the user for more information or approval. "
)

available_tools = (
    "\n\nTOOLS CATALOG:\n"
    + tools_to_text(SELECTED_TOOLS) +
    "\n\n"
)

considerations = (
    "\n\nCONSIDERATIONS:\n"
    "If you are unsure about any details, ask the user for clarification. "
    "You MUST ALWAYS obtain explicit human approval before executing any create/update/delete operation. "
    "HOWEVER, once the user has explicitly approved a specific tool call with its exact arguments, do not ask for approval again unless the tool name or its arguments change drastically. "
    "Always request the user to review arguments before executing any critical (create/update/delete) operation. "
    "Be cautious and prioritize safety and security in all your actions.\n\n"
)

ensure_struct_output = (
    "\n\nSTRICT OUTPUT:\n"
    "Respond ONLY with a valid JSON object that EXACTLY matches the schema below.\n"
    "Do not include any additional text, explanations, visible reasoning, markdown, code blocks, or backticks. "
    "Nothing outside the JSON.\n"
    "Use standard JSON: double quotes, no comments, no trailing commas, and no extra fields.\n"
    "If information is missing, state it ONLY in the 'content' field and set 'need_info' to true. "
    "Do not include any tool call until the required details are clarified.\n"
    "Do not wrap the response or add prefixes or suffixes.\n\n"
    f"{json.dumps(ResponseModel.model_json_schema()['properties'], indent=2)}\n"
)


def parse_response(response: BaseMessage):
    # Ensure string consistency
    m = None
    if isinstance(response.content, str):
        m = response.content
        m = "{" + m[:m.rindex('}')+1]
        response.content = [{"type": "text", "text": m}]
    elif isinstance(response.content, list) and response.content[0]['type'] == 'text':
        m = response.content[0]['text']
        m = "{" + m[:m.rindex('}')+1]
        response.content[0]['text'] = m

    # Add tool_calls if present in structured output or LangChain-native
    struct = ResponseModel(**json.loads(m))

    # Prefer LangChain-provided tool_calls if present; otherwise, use our structured output
    existing_calls = list(getattr(response, "tool_calls", []) or [])
    final_calls = existing_calls

    if not final_calls and struct.tool_to_call:
        id_ = f"tooluse_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        final_calls = [{
            "id": id_,
            "name": struct.tool_to_call,
            "args": struct.tool_args or {}
        }]

    response.tool_calls = final_calls

    # Keep JSON content consistent with the selected tool call (if any LangChain/Structured Output)
    try:
        patched = struct.model_dump()
        if final_calls:
            selected = final_calls[-1]
            patched["tool_to_call"] = selected.get("name")
            patched["tool_args"] = selected.get("args", {})
        response.content[0]["text"] = json.dumps(patched)
    except Exception:
        pass

    return response


def get_memories(state: AgentState):
    # - List all Cloud Formation Stacks
    # - Describe specific Cloud Formation Stack
    return {
        "messages": []
    }


def llm_call(state: AgentState):
    print(f"\n\n>>> llm_call\n", flush=True)
    response = llm.bind_tools(SELECTED_TOOLS).invoke([
        SystemMessage(content=system_prompt),
        *state["messages"], # [-5:]
        SystemMessage(content=available_tools + considerations + ensure_struct_output),
        AIMessage(content="{")  # https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prefill-claudes-response
    ], max_tokens=1024, config={'tags': ['arch-agent', 'llm_call']})

    response = parse_response(response)
    print(f"\n\n>>> response\n", response, flush=True)

    return {
        "messages": [
            response
        ]
    }


def route_after_llm(state: AgentState):
    print(f"\n\n>>> router\n", flush=True)
    last = state["messages"][-1]

    # Si es un HumanMessage (ej: feedback por rechazo), reintenta con el LLM
    if isinstance(last, HumanMessage):
        return "llm_call"
    
    struct = ResponseModel(**json.loads(last.content[0]["text"]))

    if struct.need_info:
        return "need_info"
    elif last.tool_calls and struct.hitl_tool_approval:     # past: elif struct.hitl_tool_approval
        return "approval"
    elif last.tool_calls and not struct.hitl_tool_approval:
        return "tool_handler"

    return END


def approval_node(state: AgentState):
    print(f"\n\n>>> approval_node\n", flush=True)
    state["approved"] = None
    last = state["messages"][-1]
    struct = ResponseModel(**json.loads(last.content[0]["text"]))

    if not last.tool_calls:
        return {"messages": [HumanMessage(content="No tool call found to approve.")],
                "approved": False}

    call = last.tool_calls[-1]
    tool_name = call["name"]
    tool_args = call["args"]

    user_input = interrupt({
        "type": "approval_request",
        "message": f"Do you authorize execution of {tool_name}?",
        "tool_name": tool_name,
        "tool_args": tool_args,
        "risk_note": "This will create/update/delete cloud resources."
    })

    if not user_input.get("approved", False):
        state["messages"][-1].tool_calls = []   # Remove tool call on rejection, bc Validation will fail otherwise
        reason = user_input.get("reason", "User did not authorize this change.")
        feedback = (f"Approval denied for {tool_name} with args {tool_args}. "
                    f"Reason: {reason}. Please propose an alternative or ask for clarification.")
        return {"messages": [HumanMessage(content=feedback)], "approved": False}
    # TODO: save the authorization message in the Messages history for audit and approval saving
    # TODO: test edited args and ensure they are applied correctly
    # Edited args
    edited = user_input.get("edited_args")
    if edited:
        call["args"] = edited
        patched_json = struct.model_dump()
        return {"messages": [
            AIMessage(
                content=[{"type": "text", "text": json.dumps(patched_json)}],
                tool_calls=[call]
            )
        ], "approved": True}

    # Approved directly
    return {"messages": [], "approved": True}


def route_after_approval(state: AgentState):
    print(f"\n\n>>> route_after_approval\n", flush=True)
    return "tool_handler" if state.get("approved") else "llm_call"


def needinfo_node(state: AgentState):
    print(f"\n\n>>> need_info\n", flush=True)
    last = state["messages"][-1]
    struct = ResponseModel(**json.loads(last.content[0]["text"]))

    user_reply = interrupt({
        "type": "need_info",
        "message": struct.content
    })

    # On resume: user returns free text or a dict with expected keys
    # Standardize to text for the LLM
    return {"messages": [HumanMessage(content=user_reply if isinstance(user_reply, str) else json.dumps(user_reply))]}


async def _run_tool_capture(_tool_name: str, _args: Dict[str, Any]):
    try:
        return True, await NAME_TO_TOOL[_tool_name].ainvoke(_args)
    except Exception as e:
        err_text = f"Tool '{_tool_name}' failed: {e.__class__.__name__}: {str(e)}\n" + traceback.format_exc()
        return False, err_text


def tool_handler(state: AgentState):
    print(f"\n\n>>> tool_handler\n", flush=True)
    
    last_message = state["messages"][-1]
    print(f"\t{last_message.tool_calls=}", flush=True)
    if last_message.tool_calls:
        call = last_message.tool_calls[-1]
        tool_name = call["name"]
        args = dict(call.get("args") or {})

        # # ! Enforce error for testing purposes
        # if tool_name == "call_aws":
        #     m = args['cli_command']
        #     del args['cli_command']
        #     args['command'] = m

        # ! WARNING: this entire try-except block was vibecoded to allow synchronous MCP tool execution
        # Execute the tool coroutine exactly once and capture any tool errors as text
        try:
            try:
                loop = asyncio.get_running_loop()
                print(f"\tUsing existing loop", flush=True)
                nest_asyncio.apply(loop)
                ok, payload = loop.run_until_complete(_run_tool_capture(tool_name, args))
            except RuntimeError:
                print(f"\tCreating new loop", flush=True)
                loop = asyncio.new_event_loop()
                try:
                    print(f"\tSetting and using new loop", flush=True)
                    asyncio.set_event_loop(loop)
                    ok, payload = loop.run_until_complete(_run_tool_capture(tool_name, args))
                finally:
                    print(f"\tClosing loop", flush=True)
                    asyncio.set_event_loop(None)
                    loop.close()
        except Exception as e:
            # Any unexpected driver/runtime error is captured here
            ok = False
            payload = f"Tool '{tool_name}' failed (driver): {e.__class__.__name__}: {str(e)}\n" + traceback.format_exc()

        if ok:
            tool_message = ToolMessage(
                content=payload,
                tool_call_id=call["id"],
            )
            print(f"\n\t>>> tool_result\n", payload, flush=True)
        else:
            tool_message = ToolMessage(
                content=payload,
                tool_call_id=call["id"],
            )
            print(f"\n\t>>> tool_error\n", payload, flush=True)
        return {"messages": [tool_message]}

    return {"messages": []}


graph = StateGraph(AgentState)

# Nodes
graph.add_node("get_memories", get_memories)
graph.add_node("llm_call", llm_call)
graph.add_node("tool_handler", tool_handler)
graph.add_node("need_info", needinfo_node)
graph.add_node("approval", approval_node)

# Edges
graph.add_edge(START, "get_memories")
graph.add_edge("get_memories", "llm_call")
graph.add_edge("need_info", "llm_call")
graph.add_edge("tool_handler", "llm_call")
graph.add_conditional_edges("llm_call", route_after_llm, {
    "need_info": "need_info",
    "approval": "approval",
    "tool_handler": "tool_handler",
    END: END,
})
graph.add_conditional_edges("approval", route_after_approval, {
    "tool_handler": "tool_handler",
    "llm_call": "llm_call",
})


config = DynamoDBConfig(
    table_config=DynamoDBTableConfig(
        # Customize table name as needed
        table_name=os.environ['DYNAMO_DB_CHECKPOINT_TABLE'],
        billing_mode=BillingMode.PAY_PER_REQUEST,  # PAY_PER_REQUEST or PROVISIONED
        enable_encryption=True,  # Enable server-side encryption
        enable_point_in_time_recovery=False,  # Enable point-in-time recovery
        ttl_days=30,  # Enable TTL with 30 days expiration (set to None to disable)
        ttl_attribute="expireAt",  # TTL attribute name
        
        # For PROVISIONED billing mode only:
        read_capacity=None,  # Provisioned read capacity units
        write_capacity=None,  # Provisioned write capacity units
        
        # Optional auto-scaling configuration
        min_read_capacity=None,
        max_read_capacity=None,
        min_write_capacity=None,
        max_write_capacity=None
    ),
    # aws_access_key_id=aws_access_key,
    # aws_secret_access_key=aws_secret_key,
    # aws_session_token=aws_session_token,
)
checkpointer = DynamoDBSaver(config, deploy=True)

agent = graph.compile(checkpointer=checkpointer)


# -----------------------------------------------------------------------------
# Testing the agent
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # display(Image(agent.get_graph(xray=False).draw_mermaid_png()))

    thread_config = {"configurable": {"thread_id": uuid.uuid4().hex}}

    message_history = [
        HumanMessage(content="Create a t2.micro EC2 instance in us-east-1 region.")
    ]

    result_or_pause = agent.invoke({"messages": message_history}, config=thread_config)

    print(type(result_or_pause))
    print(result_or_pause.keys())
    print(result_or_pause['messages'][-1].content)
    print(json.loads(result_or_pause['messages'][-1].content[0]['text'])['content'])
    print(result_or_pause['__interrupt__'])


    new_result = agent.invoke(Command(resume="Región: us-east-1, tipo: t2.micro, etiquetas: {env: 'dev'}, quiero la ultima AMI de Ubuntu, con segurity group por default. De nombre: Maquinita"), thread_config)

    print(new_result['messages'][-1].content)

    print(json.loads(new_result['messages'][-1].content[0]['text'])['content'])
    print(new_result['__interrupt__'])

    new_result = agent.invoke(Command(resume="Sin SSH Key Pair nomas, yo me conectare luego por la UI. Ah, y prefiero AWS Linux a Ubuntu. "), thread_config)

    new_result = agent.invoke(Command(resume={"approved": True}), thread_config)

    new_result = agent.invoke(Command(resume={"approved": False, "reason": "Prefiero alguna imagen de AWS Linux que Ubuntu", "message": ""}), thread_config)

    new_result = agent.invoke(Command(resume="Si"), thread_config)

    new_result = agent.invoke(Command(resume="Sin SSH Key Pair y creala con Security Group por default y ya creala"), thread_config)

    new_result = agent.invoke(Command(resume="Quiero la ultima imagen de Ubuntu como AMI y de ahi crearlo directamente"), thread_config)


    state = agent.get_state(thread_config)
    print(state.next)
    print(state.interrupts)
    print(dir(state))

    state.values['messages'][-1]

    for v in checkpointer.list(thread_config):
        print(v)


    snapshots = list(agent.get_state_history(thread_config))
    last_snapshot = snapshots[0]

    history = list(agent.get_state_history(thread_config))
    for h in history[::-1]:
        print(h.next)

    last_state = agent.get_state(thread_config)

    print(last_state.values['messages'][-1])

    print(last_state.interrupts)

    for snap in snapshots:
        if hasattr(snap, 'interrupts'):
            print(snap.interrupts)
