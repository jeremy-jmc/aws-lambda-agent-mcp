from langchain_core.messages import BaseMessage
import json
from datetime import datetime
import pytz

def pretty_print_messages(messages: list[BaseMessage]) -> list[dict]:
    """Pretty print a list of messages and extract tool calls if any."""
    tool_calls = []
    
    print("\n=== START OF CONVERSATION ===")
    for m in messages:
        if hasattr(m, 'pretty_repr'):
            print(m.pretty_repr())
        else:
            print(f"{m.role}: {m.content}")
        
        if hasattr(m, 'tool_calls') and m.tool_calls:
            for call in m.tool_calls:
                tool_calls.append({
                    "tool": call.get("name", "unknown_tool"),
                    "input": call.get("args", {})
                })
    print("=== END OF CONVERSATION ===\n")
    
    if tool_calls:
        print(f"Found {len(tool_calls)} tool calls in conversation")
        print(json.dumps(tool_calls, indent=2))
    
    return tool_calls


def slack_ts_to_datetime(slack_ts: str, include_seconds: bool = False) -> str:
    """Convert Slack timestamp string to a datetime object in the format YYYY-MM-DD HH:MM on Lima, Peru timezone."""
    timestamp_float = float(slack_ts)
    lima_tz = pytz.timezone('America/Lima')
    dt = datetime.fromtimestamp(timestamp_float, tz=pytz.utc).astimezone(lima_tz)
    if include_seconds:
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    else:
        return dt.strftime('%Y-%m-%d %H:%M')
