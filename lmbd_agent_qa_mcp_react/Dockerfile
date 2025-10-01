FROM python:3.12-slim

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

# Install uv for faster Python package management
RUN pip install --no-cache-dir uv

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