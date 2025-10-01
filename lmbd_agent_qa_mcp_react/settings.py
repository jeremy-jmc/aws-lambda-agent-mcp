# Load .env variables and secrets (from AWS Secrets Manager if available)
import os, json
from datetime import datetime, timezone
from dotenv import load_dotenv
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, '../../.env'))

import boto3
secrets_manager_client = boto3.client('secretsmanager', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
# secret_value = secrets_manager_client.get_secret_value(SecretId=os.environ['SECRET_NAME'])
# secret = json.loads(secret_value['SecretString'])
# os.environ["LOGFIRE_TOKEN"] = secret.get('LOGFIRE_TOKEN')

import logfire
logfire.configure()

# logfire.info("Environment Variables Loaded", env_vars=os.environ.items())
print(os.environ.items())

class Settings:
    # LLM Configuration
    LLM_FAST_MODEL = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    LLM_AGENT_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"  # us.anthropic.claude-3-7-sonnet-20250219-v1:0
    MODEL_PROVIDER = "bedrock_converse"
    REGION_NAME = "us-east-1"
    TEMPERATURE = 0.1
    
    # API Keys
    BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY")
    
    # Paths
    EMPLOYEES_PATH = os.path.join(SCRIPT_DIR, "metadata/employees.json")
    MESSAGES_PATH = os.path.join(SCRIPT_DIR, "metadata/slack_messages.json")
    GRAPH_OUTPUT_PATH = "./misc/agent_graph.png"
    
    # Lambda environment-specific paths
    TMP_DIR = "/tmp" if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else os.path.join(os.path.dirname(SCRIPT_DIR), "tmp")
    CACHE_DIR = os.path.join(TMP_DIR, "cache")
    
    # Agent Configuration
    RECURSION_LIMIT = 50
    
    DYNAMODB_SESSIONS_TABLE_NAME = os.environ['DYNAMO_DB_SESSION_TABLE']

    THREAD_ID = 1
    @property
    def config(self):
        return {"configurable": {"thread_id": self.THREAD_ID}}


settings = Settings()