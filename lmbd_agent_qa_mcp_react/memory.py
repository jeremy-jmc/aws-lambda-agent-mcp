import boto3
import os
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
from utilities import slack_ts_to_datetime
from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict, filter_messages


class DynamoDBManager():
    def __init__(self):
        self.dynamo = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    
    def log_message(self, response: dict, table_keys: dict):
        table = self.dynamo.Table(os.environ["DYNAMO_DB_LOG_TABLE"])
        # TODO: all chain log & tool call list should be in the same table
        new_row = {
            **table_keys,     # Include all the PK, SK, and secondary index keys
            "chain": messages_to_dict(response['messages']),
            "tool_calls": messages_to_dict(filter_messages(response['messages'], include_types=["tool"])),
            "agent_response": response['structured_response'].model_dump(),
            "slack_response": response.get('slack_response', ''),
        }
        new_row['thread_ts'] = slack_ts_to_datetime(table_keys['thread_ts'])
        new_row['message_ts'] = slack_ts_to_datetime(table_keys['message_ts'], True)
        table.put_item(Item=new_row)

    @staticmethod
    def dynamo_to_python(dynamo_object: dict) -> dict:
        """Convert DynamoDB item to Python dict."""
        deserializer = TypeDeserializer()
        return {
            k: deserializer.deserialize(v)
            for k, v in dynamo_object.items()
        }

    @staticmethod
    def python_to_dynamo(python_object: dict) -> dict:
        """Convert Python dict to DynamoDB item."""
        serializer = TypeSerializer()
        return {
            k: serializer.serialize(v)
            for k, v in python_object.items()
        }
    
    @staticmethod
    def session_table_part_key(channel_id: str, thread_ts: str) -> str:
        """Generate a session table partition key based on channel ID and thread timestamp."""
        thread_ts_dt = slack_ts_to_datetime(thread_ts)
        return f"CH#{channel_id}#TH#{thread_ts_dt}"
    
    

"""
If your table’s primary key is `pk=thread_id`, `sk=message_ts`, you could add a GSI with `gsi1pk=channel_id`, `gsi1sk=message_ts` to efficiently query all messages in a channel, regardless of thread.
"""

# TODO: https://python.langchain.com/docs/versions/migrating_memory/long_term_memory_agent/
# https://github.com/SongTran712/VPBank_2025/blob/main/multiagent/dynamodb.py
# https://python.langchain.com/docs/integrations/memory/aws_dynamodb/
# https://python.langchain.com/api_reference/community/chat_message_histories/langchain_community.chat_message_histories.dynamodb.DynamoDBChatMessageHistory.html
# https://builder.aws.com/content/2z0jB7JYkoiKeuZvD4mX8rnsjHA/context-management-in-strandssdk-with-dynamodb
# https://medium.com/@dminhk/adding-amazon-dynamodb-memory-to-amazon-bedrock-using-langchain-expression-language-lcel-%EF%B8%8F-1ca55407ecdb