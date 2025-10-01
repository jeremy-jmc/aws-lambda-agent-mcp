import os, shutil
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
import requests
import json
import boto3


def get_current_account_id() -> str:
    """Fetch the current AWS account ID using STS."""
    sts_client = boto3.client('sts')
    identity = sts_client.get_caller_identity()
    return identity['Account']


def get_aws_credentials() -> dict:
    """Fetch AWS credentials from a secure endpoint."""
    url = os.environ['CREDENTIALS_API_URL']

    payload = json.dumps({
        "profile": "admin",
        "account_id": get_current_account_id()
    })
    headers = {
        'x-api-key': os.environ['CREDENTIALS_API_X_API_KEY'],
        'Content-Type': 'application/json'
    }

    response = requests.request("POST", url, headers=headers, data=payload)
    print(f"{response=}")
    response_dict = json.loads(response.text)
    print(f"{response_dict=}")

    return {
        "AWS_ACCESS_KEY_ID": response_dict['credentials']['access_key'],
        "AWS_SECRET_ACCESS_KEY": response_dict['credentials']['secret_key'],
        "AWS_SESSION_TOKEN": response_dict['credentials']['session_token']
    }


env_config = {
    "FASTMCP_LOG_LEVEL": "ERROR",
    "AWS_REGION": os.environ.get('AWS_REGION', 'us-east-1'),
}

# Add AWS credentials to MCP environment if they exist
aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
aws_session_token = os.environ.get('AWS_SESSION_TOKEN')

if aws_access_key and aws_secret_key:
    env_config.update({
        "AWS_ACCESS_KEY_ID": aws_access_key,
        "AWS_SECRET_ACCESS_KEY": aws_secret_key,
    })
    if aws_session_token:
        env_config["AWS_SESSION_TOKEN"] = aws_session_token
    print("AWS credentials configured for MCP server")
else:
    print("No explicit AWS credentials found, using default provider chain")


def _resolver_aws_api_mcp_server_cmd():
    """Resolve the MCP server command, preferring preinstalled binary over uvx."""
    # Check if we have a preinstalled binary via environment variable
    explicit = os.environ.get("AWS_API_MCP_SERVER_CMD")
    if explicit and os.path.exists(explicit):
        print(f"Using preinstalled MCP server: {explicit}")
        return explicit, []
    
    # Check if aws-api-mcp-server is available on PATH
    try:
        bin_path = shutil.which("aws-api-mcp-server")
        if bin_path:
            print(f"Using MCP server from PATH: {bin_path}")
            return bin_path, []
    except Exception:
        print("MCP not found in PATH")

    # Fallback to uvx (will cause cold-start downloads)
    print("Falling back to uvx (may cause cold-start downloads)")
    return "uvx", ["awslabs.aws-api-mcp-server@latest"]

cmd, args = _resolver_aws_api_mcp_server_cmd()

multi_client = MultiServerMCPClient({
    "awslabs.aws-api-mcp-server": {
        "command": cmd,
        "args": args,
        "env": {
            # "AWS_REGION": "us-east-1"
            "READ_OPERATIONS_ONLY": "false",
            "REQUIRE_MUTATION_CONSENT": "false",
            **env_config,
            **get_aws_credentials(),
        },
        # "disabled": "false",
        # "autoApprove": [],
        "transport": "stdio",
    },
})
