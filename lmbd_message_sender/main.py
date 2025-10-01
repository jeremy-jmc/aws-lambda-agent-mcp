import os, json
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from slackstyler import SlackStyler


client = WebClient(token=os.environ['SLACK_BOT_TOKEN'])
styler = SlackStyler()


def format_message_slack(message: str) -> str:
    markdown_text = f"\n{message}"     # Enviando desde Python:
    slack_message = styler.convert(markdown_text)
    return slack_message


def lambda_handler(event, context):
    print(type(event))
    print(f"Received event: {json.dumps(event, indent=2)}")
    event_body = {}
    if 'body' in event:
        event_body = json.loads(event.get('body')) if isinstance(event.get('body'), str) else event.get('body')
    else:
        event_body = json.loads(event) if isinstance(event, str) else event
        
    print(f"Parsed body: {json.dumps(event_body, indent=2)}")
    
    if event_body['source'] == 'QAAgent':
        print("Event from QAAgent")
    elif event_body['source'] == 'ArchitectureAgent':
        print("Event from ArchitectureAgent")
    else:
        raise ValueError(f"Unknown event source: {event_body['source']}")
    
    try:        
        # TODO: check if the model already replied in the thread
        event_body['args']['text'] = format_message_slack(event_body['args']['text'])
        response = client.chat_postMessage(**event_body['args'])
        print(f"{response=}")

    except SlackApiError as e:
        print(f"Error posting message to Slack: {e.response['error']}")

