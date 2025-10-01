from langchain.callbacks.base import BaseCallbackHandler
import time

class SearchDelayCallback(BaseCallbackHandler):
    def on_tool_end(self, serialized, **kwargs):
        print("Waiting 2 seconds before search...")
        time.sleep(5)

