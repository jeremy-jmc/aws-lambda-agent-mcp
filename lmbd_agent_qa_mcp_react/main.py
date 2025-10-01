import asyncio
import pdb
import os, re, json, copy, time
import boto3
import requests
from datetime import datetime, timezone
from typing import List
from langchain.chat_models import init_chat_model

from langgraph.prebuilt import create_react_agent
# from langchain_community.tools import DuckDuckGoSearchRun, BraveSearch, BaseTool
from langchain_tavily import TavilySearch
from langgraph.checkpoint.memory import InMemorySaver, MemorySaver
from langgraph.store.memory import InMemoryStore

from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_mcp_adapters.client import MultiServerMCPClient

from langchain_community.chat_message_histories import DynamoDBChatMessageHistory

from settings import *
from models import MessageParticipants, AgentResponse
from memory import DynamoDBManager
from prompts import prompt_template
from utilities import pretty_print_messages, slack_ts_to_datetime
from callbacks import *

LAMBDA_SERVICE = boto3.client('lambda')

class DataLoader:
    def __init__(self):
        self.employees_path = settings.EMPLOYEES_PATH

    def load_employees(self) -> list[dict]:
        with open(self.employees_path, 'r') as file:
            return json.load(file)


class MessageProcessor:
    def __init__(self):
        self.llm_fast = init_chat_model(
            settings.LLM_FAST_MODEL,
            model_provider=settings.MODEL_PROVIDER,
            region_name=settings.REGION_NAME,
            temperature=settings.TEMPERATURE,
        )
        self.data_loader = DataLoader()

    def get_channel_members(self, channel_name: str) -> list[dict]:
        employees = self.data_loader.load_employees()
        return [
            {"name": sub_dict.get("name"), "role": sub_dict.get("role")}
            for sub_dict in employees
            if channel_name in sub_dict.get("channels_list", [])
        ]

    def identify_message_participants(self, channel_name: str, message: str | list[dict], channel_members: list[dict]) -> dict:
        if isinstance(channel_members, list):
            channel_members = json.dumps(channel_members, indent=2)

        res = self.llm_fast.with_structured_output(MessageParticipants).invoke([
            {
                "role": "user",
                "content": (
                    f"Review the following Slack channel \"{channel_name}\" message: {message}. "
                    f"From the provided list of channel members (including their names and roles), "
                    f"determine which individuals should pay attention to this message. "
                    f"CHANNEL MEMBERS:\n{channel_members}. "
                    "Only select names that appear in the channel members list. "
                    "Exclude any names or roles not present in the list, and avoid duplicates. "
                    "Always exclude the sender of the message from the receivers list. "
                    "If the message is clearly addressed to a specific person, include only that individual. "
                    "If it targets a group, include only those group members. "
                    "If there is no explicit addressee, use the context to recommend the most relevant people based on their roles and responsibilities."
                    "Return **only** a JSON object with keys:\n"
                    " - \"cot\": a brief explanation of how you determined the receivers"
                    " - \"sender\": the name and role of the person who sent the message, formatted as a dictionary with keys \"name\" and \"role\". "
                    " - \"receivers\": a list of names of the people who should pay attention to the message, formatted as a list of dictionaries, where each sub-dictionary contains the keys \"name\" and \"role\". "
                ),
            }
        ], temperature=0.0, max_tokens=750, top_p=0.95, performanceConfig={"latency": "optimized"})

        return res.model_dump()

    def create_prompt(self, channel_message: dict, participants: dict) -> str:
        return prompt_template.invoke({
            "channel_name": channel_message['channel'],
            "channel_messages": json.dumps(channel_message['messages'], indent=2),
            "sent_at": json.dumps(participants['receivers'], indent=2),
            "sent_by": json.dumps(participants['sender'], indent=2)
        })


class AgentFactory:
    def __init__(self):
        self.llm_agent = init_chat_model(
            settings.LLM_AGENT_MODEL,
            model_provider=settings.MODEL_PROVIDER,
            region_name=settings.REGION_NAME,
            temperature=settings.TEMPERATURE,
            # betas=["interleaved-thinking-2025-05-14"],

            # # https://github.com/langchain-ai/langchain/issues/31285
            # # https://github.com/openai/openai-agents-python/issues/810
            # # https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-configure-reasoning.html#kb-test-reasoning-general-considerations
            # # https://github.com/langchain-ai/langchain-aws/blob/main/libs/aws/langchain_aws/chat_models/bedrock_converse.py#L595
            # additional_model_request_fields={
            #     "thinking": {
            #         "type": "enabled",
            #         "budget_tokens": 2048
            #     }
            # }
        )
        
        # Set up environment and cache directories
        os.makedirs(settings.CACHE_DIR, exist_ok=True)
        self.env_config = {
            # "AWS_PROFILE": "default",
            "AWS_REGION": "us-east-1",
            "FASTMCP_LOG_LEVEL": "ERROR",
            "UV_CACHE_DIR": settings.CACHE_DIR,
            "XDG_CACHE_HOME": settings.CACHE_DIR,
            "TMPDIR": settings.TMP_DIR,
            "TEMP": settings.TMP_DIR,
            "TMP": settings.TMP_DIR
        }
        
        # Define MCP server configurations
        # https://www.npmjs.com/package/mcp-remote
        # https://github.com/modelcontextprotocol/inspector
        self.mcp_servers = {
            "awslabs.core-mcp-server": {
                "command": "uvx",
                "args": [
                    "awslabs.core-mcp-server@latest"
                ],
                "transport": "stdio",
                "env": self.env_config,
            },
            "awslabs.aws-documentation-mcp-server": {
                "command": "uvx",
                "args": ["awslabs.aws-documentation-mcp-server@latest"],
                "transport": "stdio",
                "env": self.env_config,
            },
            "awslabs.aws-serverless-mcp-server": {
                "command": "uvx",
                "args": ["awslabs.aws-serverless-mcp-server@latest"],
                "transport": "stdio",
                "env": self.env_config,
            },
            "awslabs.cdk-mcp-server": {
                "command": "uvx",
                "args": ["awslabs.cdk-mcp-server@latest"],
                "transport": "stdio",
                "env": self.env_config,
            },
            "awslabs.aws-pricing-mcp-server": {
                "command": "uvx",
                "args": ["awslabs.aws-pricing-mcp-server@latest"],
                "transport": "stdio",
                "env": self.env_config,
            },
            # https://github.com/modelcontextprotocol/servers/tree/main/src/fetch
            "modelcontextprotocol.fetch": {
                "command": "uvx",
                "args": ["mcp-server-fetch"],
                "transport": "stdio",
                "env": self.env_config,
            }
        }



    async def create_and_run_react_agent(self, prompt):
        """Create and run ReactAgent with ALL tools loaded in the same context"""
        # Load ALL tools using a single MultiServerMCPClient to avoid ClosedResourceError
        # This ensures tools and agent share the same execution context
        
        # Create MultiServerMCPClient with ALL MCP servers at once
        mcp_client = MultiServerMCPClient({
            # "awslabs.core-mcp-server": self.mcp_servers["awslabs.core-mcp-server"],
            "awslabs.aws-documentation-mcp-server": self.mcp_servers["awslabs.aws-documentation-mcp-server"],
            "awslabs.aws-serverless-mcp-server": self.mcp_servers["awslabs.aws-serverless-mcp-server"],
            # "awslabs.cdk-mcp-server": self.mcp_servers["awslabs.cdk-mcp-server"],
            "modelcontextprotocol.fetch": self.mcp_servers["modelcontextprotocol.fetch"],
            # "awslabs.aws-pricing-mcp-server": self.mcp_servers["awslabs.aws-pricing-mcp-server"]
        })
        
        # Get ALL tools from the multi-server client in one call
        tools = await mcp_client.get_tools()
        
        # Add other tools if needed
        other_tools = [
            # {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
        ]
        tools.extend(other_tools)

        get_name = lambda tool: tool['name'] if isinstance(tool, dict) else tool.name
        print("Original tools:", [get_name(copy.deepcopy(tool)) for tool in tools])

        # Filter only this tools
        filtered_tools = ['read_documentation', 'search_documentation', 'recommend', 
                          'get_serverless_templates', 
                          'fetch',
                        #   'prompt_understanding',
                        #   'LambdaLayerDocumentationProvider', 'GetAwsSolutionsConstructPattern'
                          ]
        tools = [tool for tool in tools if get_name(copy.deepcopy(tool)) in filtered_tools]

        # Add other non-MCP tools
        tools += [
            # BraveSearch.from_api_key(api_key=settings.BRAVE_SEARCH_API_KEY, verbose=False, search_kwargs={"count": 5}),
            # DuckDuckGoSearchRun(verbose=True, callbacks=[SearchDelayCallback()])
            TavilySearch(
                max_results=5,
                topic="general",
                # include_answer=False,
                # include_raw_content=False,
                # include_images=False,
                # include_image_descriptions=False,
                # include_favicon=False,
                search_depth="basic",
                # time_range="day",
                # include_domains=None,
                # exclude_domains=None,
                # country=None
            )
        ]

        logfire.info("Loaded tools", tools=tools)
        print("Available tools:", [get_name(copy.deepcopy(tool)) for tool in tools])

        # Create agent immediately after loading tools in the same context
        react_agent = create_react_agent(
            model=self.llm_agent,
            tools=tools,
            debug=False,
            response_format=AgentResponse,
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
        )
        
        # Run the agent immediately in the same context
        response = await react_agent.ainvoke(
            prompt,
            {**settings.config, "recursion_limit": settings.RECURSION_LIMIT}
        )
        
        return response


class QAAWSReactAgent:
    def __init__(self):
        self.message_processor = MessageProcessor()
        self.agent_factory = AgentFactory()
        self.data_loader = DataLoader()
        self.dynamo_manager = DynamoDBManager()

    async def process_message(self, channel_message: dict | list[dict]) -> str:
        """Process a single channel message"""
        if isinstance(channel_message, list):
            raise ValueError("Expected a single channel message, not a list")
        
        print(f"Processing channel: {channel_message['channel']}")

        # Get channel members and identify participants
        c_n = channel_message['channel']
        idx_msg = channel_message['message_idx']
        channel_members = self.message_processor.get_channel_members(c_n)
        participants = self.message_processor.identify_message_participants(c_n, channel_message['messages'][idx_msg], channel_members)

        # Create prompt
        prompt = self.message_processor.create_prompt(channel_message, participants)

        # TODO: pass this logic of saving message history to the Sender function
        thread_history = DynamoDBChatMessageHistory(
            table_name=settings.DYNAMODB_SESSIONS_TABLE_NAME,
            session_id=self.dynamo_manager.session_table_part_key(channel_message['channel'], channel_message['thread_ts']),
            # TODO: use `ttl` parameter to set TTL for messages
        )
        thread_history.add_user_message(str(channel_message['messages'][idx_msg]))

        user_response = await self._run_react_agent(prompt, {
            'channel_name': channel_message['channel'],
            'thread_ts': channel_message['thread_ts'],
            'message_ts': channel_message['ts'],
        })
        thread_history.add_ai_message(user_response)

        return user_response
    

    async def _run_react_agent(self, prompt, table_keys: dict) -> str:
        response = await self.agent_factory.create_and_run_react_agent(prompt)
        
        logfire.info(f"Response from React Agent {datetime.now(timezone.utc)}", response=response)
        tool_call_list = pretty_print_messages(response["messages"])
        # print(f"{dir(response)=}")
        # print(f"{response.keys()=}")
        print(response['structured_response'].model_dump())

        response_model = response["structured_response"]
        summary = response_model.summary
        steps = "\n".join(response_model.processing_steps)
        docs = "\n".join(link.url for link in response_model.documentation)

        response['slack_response'] = (
            f"{summary}\n\n"
            "Para lograr esta respuesta realicé estos pasos:\n\n"
            f"{steps}\n\n"
            f"{docs}"
        )

        self.dynamo_manager.log_message(response, table_keys)

        return response['slack_response']


def invoke_message_event(request_args: dict):
    if os.environ.get('ENV', 'dev') == 'dev':
        sender_function_url = re.sub(r'https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?', r'http://host.docker.internal\2', os.environ['LOCAL_SENDER_FUNCTION_URL'])

        print(f"Calling local agent at {sender_function_url}")
        response = requests.post(
            sender_function_url,
            headers={'Content-Type': 'application/json'},
            json=request_args,
        )
        print("Event sent to local agent:", response.status_code, response.text)
    else:
        sender_lambda_arn = os.environ['SENDER_FUNCTION_ARN']

        print(f"Invoking Lambda Function {sender_lambda_arn}")
        response = LAMBDA_SERVICE.invoke(
            FunctionName=sender_lambda_arn,
            InvocationType='Event',  # Asynchronous invocation
            Payload=json.dumps(request_args),
        )
        print("Event sent to Lambda:", response)


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event, indent=2)}")

    try:
        start = time.time()
        # Parse the request body
        if 'body' in event:
            request = json.loads(event['body'])
        else:
            request = event
        print(f"Parsed request: {json.dumps(request, indent=2)}")
        
        required_fields = ['channel', 'thread_ts']
        missing_fields = [field for field in required_fields if field not in request]
        content_field = [v for v in ['thread_history', 'message'] if v not in request]
        if len(missing_fields) or len(content_field) == 2:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': f'Missing required fields: {missing_fields} {content_field}'
                })
            }
        human_message = request['message']
        if "message" in request:
            # If the request contains a single message, convert it to a list
            request['thread_history'] = [request['message']]
            del request['message']

        agent = QAAWSReactAgent()
        print(json.dumps(request['thread_history'], indent=2))

        channel_message = {
            'channel': request['channel'],
            'messages': request['thread_history'],
            'message_idx': request.get('message_idx', 0),  # Default to 0 if not provided
            'thread_ts': request['thread_ts'],  # Thread timestamp
            'ts': request['ts'] if 'ts' in request else request['thread_ts']    # Message timestamp
        }

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response_content: str = loop.run_until_complete(agent.process_message(channel_message))
        finally:
            loop.close()

        request_args = {
            'source': 'QAAgent',
            'comm_channel': 'slack',
            
            'channel': request['channel'],
            'thread_ts': request['thread_ts'],
            'human_message': human_message,
            'ai_message': response_content,

            'args': {
                'channel': request['channel'],
                'thread_ts': request['thread_ts'],
                'text': response_content
            }
        }
        
        invoke_message_event(request_args)

        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'success': True,
                'processed_message': channel_message,
                'response': response_content,
                'processing_time': f"{time.time() - start:.2f} seconds"
            })
        }

    except json.JSONDecodeError as e:
        print(f"JSON decode error: {str(e)}")
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'error': 'Invalid JSON in request body'
            })
        }
    except Exception as e:
        print(f"Error processing message: {str(e)}")
        raise e
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'error': 'Internal server error',
                'details': str(e)
            })
        }

"""
https://github.com/slackapi/python-slack-sdk
https://tools.slack.dev/bolt-python/getting-started/
https://www.tavily.com/
"""