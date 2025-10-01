FROM python:3.10.12-slim

# Install system dependencies required for MCP servers
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        nodejs \
        npm \
        gcc \
        g++ \
        make \
        cmake \
        libsqlite3-dev \
        awscli \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/*

# LangGraph-DynamoDB Checkpoint is not compatible with MCP server (installed via PyPi). So we need to install the MCP server in a separate virtual environment, managed by uv.
# Install uv for faster Python package management
RUN pip install --no-cache-dir uv
# Preinstall the MCP server in a dedicated virtual environment and make it available
RUN set -eux; \
    # Enable: -e exit on error, -u undefined var is error, -x echo commands
    echo "Installing MCP server in dedicated environment..."; \
    # Create an isolated virtual environment for the MCP server
    uv venv /opt/mcp-server-env; \
    # Install the MCP server package into that venv (pin version for reproducibility)
    uv pip install --python /opt/mcp-server-env/bin/python awslabs-aws-api-mcp-server==0.2.11; \
    # List binaries produced by the install to see what's available
    echo "Checking installed binaries..."; \
    ls -la /opt/mcp-server-env/bin/; \
    # Try to find a runnable binary/script for the server
    BINARY_NAME=$(find /opt/mcp-server-env/bin/ -name "*aws*mcp*" -o -name "*mcp*server*" | head -1); \
    if [ -z "$BINARY_NAME" ]; then \
        # If no binary found, create a shim that runs the Python module directly
        echo "No MCP server binary found, checking for Python module execution..."; \
        echo '#!/bin/bash' > /usr/local/bin/aws-api-mcp-server; \
        echo 'exec /opt/mcp-server-env/bin/python -m awslabs.aws_api_mcp_server "$@"' >> /usr/local/bin/aws-api-mcp-server; \
        chmod +x /usr/local/bin/aws-api-mcp-server; \
    else \
        # If a binary exists, symlink it into PATH with a stable name
        echo "Found MCP server binary: $BINARY_NAME"; \
        ln -sf "$BINARY_NAME" /usr/local/bin/aws-api-mcp-server; \
    fi; \
    # Quick smoke test to ensure the command is runnable
    echo "Testing MCP server installation..."; \
    /usr/local/bin/aws-api-mcp-server --help >/dev/null; \
    echo "MCP server installed and ready"

# Tell the application which command to use for the preinstalled server
ENV AWS_API_MCP_SERVER_CMD=/usr/local/bin/aws-api-mcp-server

# Set up the Lambda function directory
ENV LAMBDA_TASK_ROOT=/var/task

# Create working directories
RUN mkdir -p ${LAMBDA_TASK_ROOT}
# Create and set permissions for cache directories
RUN mkdir -p /tmp/cache && chmod 777 /tmp/cache

# Copy and install Python requirements first (for better Docker layer caching)
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
WORKDIR ${LAMBDA_TASK_ROOT}

# Install Python dependencies using uv for speed
RUN uv pip install --system --no-cache-dir -r requirements.txt

# Install AWS Lambda Runtime Interface Client
RUN pip install --no-cache-dir awslambdaric

# Copy application code
COPY . ${LAMBDA_TASK_ROOT}/

# Set proper permissions
RUN chmod -R 755 ${LAMBDA_TASK_ROOT}

# Set the CMD to the Lambda runtime handler
ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "main.lambda_handler" ]