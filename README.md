# Multi-Agent Architecture

## AWS Architecture Diagram (Current)

<!-- TODO: Create a Mermaid Diagram of the Current Architecture (lmbd_agent_architecture_aws_mcp, lmbd_agent_qa_mcp_react, lmbd_event_listener, lmbd_message_evaluator, lmbd_message_sender, lmbd_sqs_processor) -->

```mermaid
graph TB
    %% External Systems
    Slack[Slack API]
    
    %% API Gateway
    APIGW[API Gateway]
    
    %% Event Processing
    EventListener[lmbd_event_listener<br/>Node.js Lambda<br/>Slack Event Handler]
    
    %% Message Queue
    SQSQueue[Slack Messages Queue<br/>SQS FIFO]
    SQSDLQ[Dead Letter Queue<br/>SQS FIFO]
    SQSProcessor[lmbd_sqs_processor<br/>Python Lambda<br/>Delayed Message Processor]
    
    %% Message Processing
    MessageEvaluator[lmbd_message_evaluator<br/>Python Lambda<br/>Message Router]
    
    %% Agent System
    QAAgent[lmbd_agent_qa_mcp_react<br/>Python Lambda<br/>ReAct Agent with MCP Tools]
    ArchAgent[lmbd_agent_architecture_aws_mcp<br/>Python Lambda<br/>LangGraph AWS Agent]
    
    %% Message Dispatch
    MessageSender[lmbd_message_sender<br/>Python Lambda<br/>Slack Response Handler]
    
    %% Storage
    SessionTable[(SessionTable<br/>DynamoDB<br/>Chat History)]
    LogTable[(LogTable<br/>DynamoDB<br/>Message Logging)]
    CheckpointTable[(CheckpointTable<br/>DynamoDB<br/>LangGraph State)]
    
    %% Flow
    Slack --> APIGW
    APIGW --> EventListener
    EventListener --> Slack
    
    EventListener --> SQSQueue
    EventListener --> MessageEvaluator
    
    SQSQueue --> SQSProcessor
    SQSProcessor --> MessageEvaluator
    SQSQueue -.-> SQSDLQ
    
    MessageEvaluator --> QAAgent
    MessageEvaluator --> ArchAgent
    
    QAAgent --> MessageSender
    ArchAgent --> MessageSender
    MessageSender --> Slack
    
    %% Storage Connections
    QAAgent --> SessionTable
    QAAgent --> LogTable
    ArchAgent --> CheckpointTable
    MessageSender --> SessionTable
    
    %% Styling
    classDef lambda fill:#ff9900,stroke:#232f3e,stroke-width:2px,color:#fff
    classDef storage fill:#3f48cc,stroke:#232f3e,stroke-width:2px,color:#fff
    classDef queue fill:#ff4b4b,stroke:#232f3e,stroke-width:2px,color:#fff
    classDef external fill:#67b3d9,stroke:#232f3e,stroke-width:2px,color:#fff
    classDef service fill:#8c4fff,stroke:#232f3e,stroke-width:2px,color:#fff
    
    class EventListener,SQSProcessor,MessageEvaluator,QAAgent,ArchAgent,MessageSender lambda
    class SessionTable,LogTable,CheckpointTable storage
    class SQSQueue,SQSDLQ queue
    class Slack external
    class APIGW service
```

## Test Cases
- User writes a message in a thread and mentions the bot.
- User writes a message in a thread without mentioning the bot, but the bot has already responded in the thread ...
    - ... and the message is relevant to the conversation.
    - ... and the message is not relevant to the conversation.
- User writes a message in a thread without mentioning the bot, and the bot has not responded in the thread
    - ... and the message is relevant to the conversation. 
    - ... and the message is not relevant to the conversation.

## UML sequence diagram

### Main Flow Diagram

```mermaid
sequenceDiagram
    rect rgb(255, 255, 255)
        actor User
        participant Slack
        participant LambdaJS as EventListener Lambda (JS)
        participant SQS as Delayed Processing Queue
        participant LambdaEval as Evaluation Lambda (Python)
        participant DynamoDB as Message Tracking Table
        participant LambdaPython as LangGraph Agent Lambda (Python)

        User->>Slack: Sends message
        Slack->>LambdaJS: Message notification
        
        LambdaJS->>Slack: 200 OK (<3s)
        LambdaJS->>LambdaJS: Intercepts message
        Note over LambdaJS: Check for idempotency using event_id
        LambdaJS->>LambdaJS: Post-ACK processing
        LambdaJS->>LambdaJS: Checks if bot was mentioned/has responded in thread
        
        alt Bot was mentioned
            LambdaJS->>+LambdaEval: Sends message for immediate evaluation (Event/Async)
            LambdaEval->>LambdaEval: Evaluates message relevance
            alt Message requires agent response
                LambdaEval->>DynamoDB: Registers relevant message in conversation
                LambdaEval->>LambdaPython: Invokes agent
                Note over LambdaPython: Executes LangGraph Agent (see detailed diagram)
                LambdaPython->>Slack: Responds directly to thread
                alt Response successfully sent
                    LambdaPython->>DynamoDB: Registers bot response in conversation
                end
            end
            deactivate LambdaEval
        else Bot was NOT mentioned
            LambdaJS->>SQS: Sends message with 5-minute delay (using event_id as dedup key)
            Note over SQS: Message remains in queue for 5 min
            
            SQS->>LambdaEval: Processes message after delay
            LambdaEval->>LambdaEval: Evaluates message relevance
            alt Message requires intervention
                LambdaEval->>DynamoDB: Registers relevant message in conversation
                LambdaEval->>LambdaPython: Invokes agent
                Note over LambdaPython: Executes LangGraph Agent (see detailed diagram)
                LambdaPython->>Slack: Responds directly to thread
                alt Response successfully sent
                    LambdaPython->>DynamoDB: Registers bot response in conversation
                end
            end
        end
        
        Slack->>User: Shows response (if applicable)
    end
```

- Note over SQS: Visibility = 30 s, maxRetry = 3, DLQ enabled

### LangGraph Agent Execution Detail

```mermaid
sequenceDiagram
    rect rgb(50, 50, 50)
        participant LambdaPython as LangGraph Agent Lambda (Python)
        participant MCP as MCP Tools
        participant Tools as External Tools
        
        rect rgb(255, 255, 255)
            Note over LambdaPython,Tools: Task Execution Phase
            LambdaPython->>MCP: Initializes MCP clients
            activate MCP
            loop Tool Orchestration
                MCP->>Tools: Requests operations
                Tools-->>MCP: Returns results
                MCP-->>LambdaPython: Aggregates results
            end
            deactivate MCP
            
            LambdaPython->>LambdaPython: Formats response
        end
    end
```


## ReAct (Reasoning and Acting) Agent Paradigm

```mermaid
sequenceDiagram
    rect rgb(50, 50, 50)
        participant U as User
        participant A as LLM
        participant T as Tools
        U->>A: Initial input
        Note over A: Prompt + LLM
        loop while tool_calls present
            A->>T: Execute tools
            T-->>A: ToolMessage for each tool_calls
        end
        A->>U: Return final state
    end
```

## DynamoDB Persistence (MessagesTable and SessionTable)

MessagesTable (message logging)
- Keys
  - PK (thread_ts, String): Slack thread timestamp normalized to ISO-8601 via slack_ts_to_datetime(thread_ts)
  - SK (message_ts, String): Slack message timestamp normalized to ISO-8601 via slack_ts_to_datetime(message_ts, True)
  - GSI gsi1: channel_name (HASH) + message_ts (RANGE) to retrieve all messages in a channel ordered by time

SessionTable (chat history for reasoning)
- Key
  - PK (SessionId, String)
- SessionId format
  - Generated by memory.DynamoDBManager.session_table_part_key(channel_id, thread_ts) => "CH#{channel_id}#TH#{ISO8601(thread_ts)}"

### DynamoDB Interactions (Detail)

```mermaid
sequenceDiagram
    participant Agent as LangGraph Agent (Python)
    participant SessionTable
    participant MessagesTable

    Agent->>SessionTable: add_user_message()
    Note over SessionTable: PK=SessionId\nOne item per session\nmessages[] list append

    Agent->>Agent: run ReAct planning + tools

    Agent->>SessionTable: add_ai_message()

    Agent->>MessagesTable: put_item({thread_ts, message_ts, channel_name, agent_response, slack_response, ...})
    Note over MessagesTable: PK=thread_ts (ISO-8601)\nSK=message_ts (ISO-8601)\nGSI gsi1: channel_name + message_ts
```

## Similar projects & References

- [LangChain AWS Template](https://github.com/langchain-ai/langchain-aws-template/tree/main/slack_bot)
