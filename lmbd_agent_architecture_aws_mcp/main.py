# https://huggingface.co/learn/agents-course/en/unit2/langgraph/first_graph
# https://langchain-ai.github.io/langgraph/tutorials/workflows/
# https://github.com/langchain-ai/langchain-academy
# https://awslabs.github.io/mcp/servers/aws-api-mcp-server/#available-mcp-tools
# https://awslabs.github.io/mcp/servers/ccapi-mcp-server/

# https://github.com/langchain-ai/langgraph/discussions/1351
# https://github.com/langgenius/dify/issues/7328
# https://github.com/langchain-ai/langgraph/discussions/3200
# https://github.com/langchain-ai/langchain-aws/issues/124
# https://github.com/langchain-ai/langchain/issues/31285
import os, operator, math, getpass, ast, shutil, requests, re
import boto3
import json

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, Send, StateSnapshot, Interrupt
from concurrent.futures import ThreadPoolExecutor

from graph import llm, agent
from models import MessageToApproval

LAMBDA_SERVICE = boto3.client('lambda')


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
    if 'body' in event:
        request = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    else:
        raise ValueError("Invalid event structure: 'body' field missing.")
    
    # Check for all missing args
    needed_args = ['message', 'thread_ts', 'channel']
    missing_args = [arg for arg in needed_args if arg not in request]
    if missing_args:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": f"Missing required arguments: {', '.join(missing_args)}"
            })
        }

    if not isinstance(request['message'], str):
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "'message' must be a string."
            })
        }
    
    thread_id = request['thread_ts']
    thread_config = {"configurable": {"thread_id": thread_id}}

    last_state: StateSnapshot = agent.get_state(thread_config)
    print(f"\n\t{last_state}\n", last_state)

    # Send user message to the agent. TODO: format in case of HITL response
    result_or_pause = None
    if last_state.values and last_state.created_at: # Check if checkpoint exists
        # Get last interruption if any
        if hasattr(last_state, 'interrupts') and last_state.interrupts:
            last_interrupt: Interrupt = last_state.interrupts[-1]
            interrupt_type: str = last_state.interrupts[-1].value['type']

            human_response = request['message']
            # Continue graph execution based on the last interrupt type
            print(f"Resuming from interrupt ({interrupt_type}): {last_interrupt}")
            if interrupt_type == "need_info":
                result_or_pause = agent.invoke(Command(resume=human_response), thread_config)
            elif interrupt_type == "approval_request":
                # Structured output/parsing can be replaced by Prompt Prefilling
                messages = [
                    SystemMessage(content=f"You are a parsed system that extracts user approval decisions regarding a tool call. Respond ONLY with a valid JSON object that EXACTLY matches the schema below.\n{json.dumps(MessageToApproval.model_json_schema()['properties'], indent=2)}\n"),
                    HumanMessage(content=f"Structure this Human Message approval:\n{human_response}\n With respect to this proposed tool call: {last_interrupt.value['message']}\n")
                ]
                response = llm.with_structured_output(MessageToApproval, include_raw=True).invoke(messages, config={'tags': ['arch-agent', 'parse-approval']})

                print(f"LLM approval parsing response keys: {response.keys()}")
                parsed_response = response['parsed'].model_dump()
                print(f"Parsed content: {parsed_response}")
                
                result_or_pause = agent.invoke(Command(resume=response['parsed'].model_dump()), thread_config)
            else:
                raise NotImplementedError(f"Logic not implemented yet for state interrupt type: {interrupt_type}")
        else:
            raise NotImplementedError(f"State not supported yet: {last_state}")
    else:   # Checkpoint does not exist, start fresh
        print("Starting new thread")
        result_or_pause = agent.invoke({"messages": [
            HumanMessage(content=request['message'])
        ]}, config=thread_config)

    print("\n\tresult_or_pause\n", result_or_pause)
    
    new_state = agent.get_state(thread_config)
    print("\n\tlast_state\n", new_state)

    request_args = {
        'source': 'ArchitectureAgent',
        'comm_channel': 'slack',
        
        'channel': request['channel'],
        'thread_ts': request['thread_ts'],
        'human_message': request['message'],

        'args': {
            'channel': request['channel'],
            'thread_ts': request['thread_ts'],
        }
    }

    # Respond to user
    if len(getattr(new_state, 'next', ())) == 0:
        # TODO: If the graph ENDs, summarize the full checkpoint story and store it somewhere (S3, DynamoDB, etc) to then retrieve it later as memories via RAG.
        print("Empty tuple / Final state")
        content = result_or_pause['messages'][-1].content
        if isinstance(content, list) and content[0]['type'] == 'text':
            content = content[0]['text']
        
        response_dict: dict = json.loads(content)
        
        # Delete the checkpoint from the DB if not needed anymore
        print(f"Deleting checkpoint ({thread_id}) from DB")
        dynamo = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        table = dynamo.Table(os.environ["DYNAMO_DB_CHECKPOINT_TABLE"])
        
        response = table.query(KeyConditionExpression=boto3.dynamodb.conditions.Key('PK').eq(thread_id))
        with table.batch_writer() as batch:
            for item in response['Items']:
                batch.delete_item(Key={'PK': item['PK'], 'SK': item['SK']})  # Assuming SK is sort key

        response_content = response_dict['content']
        request_args['ai_message'] = response_content
        request_args['args']['text'] = response_content

        invoke_message_event(request_args)

        return {
            "statusCode": 200,
            "body": response_content
        }
    else:
        print(new_state.next)
        print("Not final state")
    
    # Ask user (HITL -> need_info or approval)
    if '__interrupt__' in result_or_pause:
        hitl_dict = result_or_pause['__interrupt__'][0].value
        response_content = f"""
        Human intervention required (type: {hitl_dict['type']}):
        {hitl_dict['message']}
        """

        request_args['ai_message'] = response_content
        request_args['args']['text'] = response_content
        invoke_message_event(request_args)

        return {
            "statusCode": 200,
            "body": response_content
        }
    
    raise NotImplementedError("Logic not implemented yet for non-interrupt non-final states.")

