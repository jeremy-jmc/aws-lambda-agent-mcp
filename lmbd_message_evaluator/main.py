from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler    import APIGatewayRestResolver
from aws_lambda_powertools.logging          import correlation_paths
from aws_lambda_powertools.metrics          import MetricUnit
from aws_lambda_powertools                  import Metrics
from aws_lambda_powertools                  import Logger
from aws_lambda_powertools                  import Tracer
from urllib.parse                           import parse_qs
import boto3
import json, os, sys, re, glob, time, datetime, random
import requests
import threading
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.web.slack_response import SlackResponse
from pydantic import BaseModel, Field
from typing import Literal
from functools import lru_cache

app = APIGatewayRestResolver()
tracer = Tracer()

# structured log
# See: https://awslabs.github.io/aws-lambda-powertools-python/latest/core/logger/
# logger.info("Hello world API - HTTP 200")
logger = Logger()

# adding custom metrics
# See: https://awslabs.github.io/aws-lambda-powertools-python/latest/core/metrics/
# metrics.add_metric(name="HelloWorldInvocations", unit=MetricUnit.Count, value=1)
metrics = Metrics(namespace="Powertools")

LAMBDA_SERVICE = boto3.client('lambda')

AGENT_NAME = "TARS"     # (The Architect and Research Specialist)
SYSTEM_PROMPT_TEMPLATE = f"""
<core_identity>
You are a message evaluator bot called MessageDictator, whose sole purpose is to analyze if your friend {AGENT_NAME} should answer a message in a Slack thread or not. 

{AGENT_NAME} is multi-agent AI system composed of two sub-agents: ArchitectureAgent and QAAgent.
- QAAgent is an assistant with web-search capabilities, access to AWS documentation, and the ability to fetch content from URLs. It can provide detailed technical recommendations, implementation steps, and links to relevant resources.
- ArchitectureAgent is an assistant with access to AWS resources in your environment. It can run commands via AWS CLI, query AWS services, and provide architecture recommendations based on your AWS deployments

However, {AGENT_NAME} itself doesn't have the capabilities to evaluate if it need to answer a message or not, so you need to evaluate the message and decide if {AGENT_NAME} should answer it or not.
</core_identity>
"""

MESSAGE_EVALUATION_TEMPLATE = f"""
Analyze this thread and evaluate whether {AGENT_NAME} should respond to the last message in the thread. {{specification}}

Your decision should be based on whether the message to pay attention to has already been answered by {AGENT_NAME} or any member of the channel, or if the message requires {AGENT_NAME} to respond.

<message_to_pay_attention>
{{message_to_pay_attention}}
</message_to_pay_attention>

<thread_history>
{{thread_history}}
</thread_history>

Provide a structured response in the following format:

<reasoning>
- Are the AWS documentation and resources relevant to answering the last message? (yes/no)
- Does the last message require performing a web search to provide a useful answer? (yes/no)
- Does the last message require fetching content from URLs/links in the message? (yes/no)
- Is it necessary for {AGENT_NAME} to answer the message? (yes/no)
- Conclusion: A description explaining why you decided whether or not to answer the last message, including any relevant context or considerations.
</reasoning>
<should_answer>Your decision as a boolean: True/False</should_answer>
"""

AGENT_SELECTION_TEMPLATE = f"""
Based on the reasoning provided, choose which sub-agent should handle the message if {AGENT_NAME} is to respond.

<reasoning>
{{reasoning}}
</reasoning>

<thread_history>
{{thread_history}}
</thread_history>

<message_to_pay_attention>
{{message_to_pay_attention}}
</message_to_pay_attention>

Provide a structured response in the following format:

<sub_agent_name>The name of the sub-agent to make the API Call, must be 'ArchitectureAgent' or 'QAAgent'.</sub_agent_name>
<sub_agent_reasoning>Your reasoning for choosing the sub-agent.</sub_agent_reasoning>
"""

class JudgeResponse(BaseModel):
    should_answer: bool = Field(description="Indicates if agent should answer the message.")
    reasoning: str = Field(description="Your reasoning for the decision, including any relevant context or considerations.")

class SubAgentChoice(BaseModel):
    sub_agent_name: Literal["ArchitectureAgent", "QAAgent"] = Field(description="The name of the sub-agent to make the API Call, must be 'ArchitectureAgent' or 'QAAgent'.")
    sub_agent_reasoning: str = Field(description="Your reasoning for choosing the sub-agent.")


class MessageEvaluator():
    def __init__(self):
        self.llm = init_chat_model(
            "us.anthropic.claude-3-5-haiku-20241022-v1:0",
            model_provider="bedrock_converse",
            region_name="us-east-1",
        )
        self.evaluation_prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT_TEMPLATE),
                ("user", MESSAGE_EVALUATION_TEMPLATE)
            ]
        )
        self.agent_selection_prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT_TEMPLATE),
                ("user", AGENT_SELECTION_TEMPLATE)
            ]
        )   


    def was_bot_mentioned(self, thread_history, bot_tag: str, msg_idx: int):
        flag_all, flag_idx = False, False
        for idx, msg in enumerate(thread_history):
            if msg['message'] and bot_tag in msg['message']:
                flag_all = True
                if idx == msg_idx:
                    flag_idx = True
        return flag_all, flag_idx


    def should_answer(self, thread_history, mentioned: bool, msg_idx: int) -> tuple[bool, JudgeResponse]:
        spec = ""
        if mentioned:
            spec = f"As you will see, the bot was mentioned in some message of the thread, so you need to evaluate the thread to decide if {AGENT_NAME} should answer the last message or not."
        else:
            spec = f"As you will see, the bot was NOT mentioned in any message of the thread, so you need to evaluate the thread to decide if {AGENT_NAME} should answer the last message or not."
        response = self.llm.with_structured_output(JudgeResponse, include_raw=True).invoke(
            self.evaluation_prompt_template.invoke({
                'thread_history': thread_history, 'specification': spec, 'message_to_pay_attention': thread_history[msg_idx]['message']
            }), temperature=0.0, top_p=0.95, performanceConfig={"latency": "optimized"}
        )
        print(f"{response.keys()=}")
        print(f"{response['parsed']=}")
        print(f"{response['raw']=}")
        return response['parsed'].should_answer, response['parsed']


    def evaluate_thread(self, thread_history, bot_tag: str, msg_idx: int) -> tuple[bool, str|None]:
        flag_all, flag_idx = self.was_bot_mentioned(thread_history, bot_tag, msg_idx)

        print(json.dumps(thread_history, indent=2))

        send_to_agent = False
        parsed_res = None
        if not flag_idx and not flag_all:
            print(f"Bot was not mentioned in either the thread or the main message (idx {msg_idx}), I'll evaluate the entire thread to decide if I should answer or not.")
            send_to_agent, parsed_res = self.should_answer(thread_history, False, msg_idx)
        elif not flag_idx and flag_all:
            print(f"Bot was mentioned in the thread, but not in the main message (idx {msg_idx}), I'll evaluate the thread to decide if I should answer or not.")
            send_to_agent, parsed_res = self.should_answer(thread_history, True, msg_idx)
        elif flag_idx:
            send_to_agent = True
            print("Bot was mentioned in the main message of the thread, I'll answer it inmediately.")

        if parsed_res:
            answer_rationale = parsed_res.reasoning
        else:
            answer_rationale = "The bot was mentioned in the main message of the thread."
        
        print(f"Should I send the message to {AGENT_NAME}? {send_to_agent}")

        # TODO: verify if some agent is in a intermediate step to call inmediately (using Checkpoint Table in DynamoDB)
        agent_to_call = None
        if send_to_agent:
            router_response = self.llm.with_structured_output(SubAgentChoice, include_raw=True).invoke(
                self.agent_selection_prompt_template.invoke({
                    'thread_history': thread_history, 'reasoning': answer_rationale, 'message_to_pay_attention': thread_history[msg_idx]['message']
                }), temperature=0.0, top_p=0.95, performanceConfig={"latency": "optimized"}
            )
            print(f"{router_response['parsed']=}")
            agent_to_call = router_response['parsed'].sub_agent_name.strip()
            print(f"Agent to call: {agent_to_call}")

        return send_to_agent, agent_to_call


class SlackManager:
    def __init__(self):
        self.client = WebClient(token=os.environ['SLACK_BOT_TOKEN'])


    def get_channel_info(self, channel_id: str):
        response: SlackResponse = self.client.conversations_info(channel=channel_id)
        if response['ok']:
            return response.data['channel']
        else:
            raise SlackApiError(f"Failed to fetch channel info: {response['error']}")


    def get_thread_history(self, channel_id: str, thread_ts: str):
        response: SlackResponse = self.client.conversations_replies(
            channel=channel_id,
            ts=thread_ts
        )
        # print(f"{dir(response)=}")
        # print(f"{vars(response)=}")
        # print(f"{vars(response).keys()=}")

        if response['ok']:
            rs = response.data['messages']
            for sub_dict in rs:
                if 'user' in sub_dict:
                    sub_dict['user'] = self.get_username_from_id(sub_dict['user'])
            return rs
        else:
            raise SlackApiError(f"Failed to fetch thread history: {response['error']}")


    @lru_cache(maxsize=20)
    def get_username_from_id(self, user_id: str):
        slack_response: SlackResponse = self.client.users_info(user=user_id)
        
        # print(f"{slack_response['user']['profile']=}")

        return slack_response['user']['profile'].get('display_name') or \
               slack_response['user']['profile'].get('real_name') or \
               slack_response['user'].get('name')


def invoke_agent_by_environment(agent_to_call: str, request_args: dict):
    if os.environ.get('ENV', 'dev') == 'dev':
        if agent_to_call == 'QAAgent':
            agent_url_key = 'LOCAL_AGENT_QA_URL'
        elif agent_to_call == 'ArchitectureAgent':
            agent_url_key = 'LOCAL_AGENT_ARCHITECTURE_URL'
        else:
            raise ValueError(f"Unknown agent to call: {agent_to_call}")

        local_agent_url = re.sub(r'https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?', r'http://host.docker.internal\2', os.environ[agent_url_key])
        
        print(f"Calling local agent at {local_agent_url}")
        response = requests.post(
            local_agent_url,
            headers={'Content-Type': 'application/json'},
            json=request_args,
        )
        print("Event sent to local agent:", response.status_code, response.text)
    else:
        if agent_to_call == 'QAAgent':
            agent_url_key = 'AGENT_QA_LAMBDA_ARN'
        elif agent_to_call == 'ArchitectureAgent':
            agent_url_key = 'AGENT_ARCHITECTURE_LAMBDA_ARN'
        else:
            raise ValueError(f"Unknown agent to call: {agent_to_call}")
        
        agent_lambda_arn = os.environ[agent_url_key]

        print(f"Invoking Lambda Function {agent_lambda_arn}")
        response = LAMBDA_SERVICE.invoke(
            FunctionName=agent_lambda_arn,
            InvocationType='Event',
            Payload=json.dumps({
                'body': json.dumps(request_args),
                'headers': {'Content-Type': 'application/json'}
            })
        )
        print("Event sent to agent Lambda:", response)


@tracer.capture_method
def lambda_handler(event, context):
    slack_client = SlackManager()
    msg_eval = MessageEvaluator()
    try:
        # Process the event
        print("Received event:", event)

        request_body = json.loads(event['body'].replace("'", "\""))
        if any(key not in request_body for key in ['bot_tag', 'channel', 'ts', 'user']):
            print(json.dumps(request_body, indent=2))
            raise ValueError("Invalid event structure.")
        
        msg_to_pay_attention = request_body['text']
        
        bot_tag = request_body.get('bot_tag', None)
        thread_history = slack_client.get_thread_history(
            channel_id=request_body['channel'],
            thread_ts=request_body['thread_ts'] if 'thread_ts' in request_body else request_body['ts']
        )
        idx_msg_to_pay_attention = next((
            idx for idx, msg in enumerate(thread_history)
            if msg['text'] == msg_to_pay_attention
        ), None)
        print(json.dumps(thread_history, indent=2))
        print(f"{idx_msg_to_pay_attention=}")

        if idx_msg_to_pay_attention + 1 < len(thread_history):
            print(f"Message to pay attention is not the last message in the thread")
            return {
                'statusCode': 200,
                'body': 'Message to pay attention is not the last message in the thread'
            }

        t_story = [{"from": msg['user'], "message": msg['text']} for msg in thread_history if 'text' in msg and 'user' in msg]
        send_to_agent, agent_to_call = msg_eval.evaluate_thread(t_story, bot_tag, idx_msg_to_pay_attention)
        print(f"{send_to_agent=}")

        if send_to_agent:
            request_args = {
                'channel': slack_client.get_channel_info(request_body['channel'])['name_normalized'],
                'thread_history': t_story,
                'thread_ts': request_body['thread_ts'] if 'thread_ts' in request_body else request_body['ts'],
                'ts': request_body['ts'],
                'message_idx': idx_msg_to_pay_attention,
                'message': t_story[idx_msg_to_pay_attention]['message'],
                'media_channel': 'slack'
            }
            invoke_agent_by_environment(agent_to_call, request_args)

            return {
                'statusCode': 200,
                'body': 'Message processed successfully'
            }
        else:
            print("Not sending message to any agent.")
            return {
                'statusCode': 200,
                'body': 'No need to respond to the message'
            }
    except Exception as e:
        print("Error processing event:", e)
        return {
            'statusCode': 500,
            'body': 'Error processing message'
        }
