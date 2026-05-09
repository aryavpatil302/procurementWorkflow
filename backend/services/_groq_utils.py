import os
import time

import groq
from dotenv import load_dotenv

load_dotenv()

MODEL = "llama-3.3-70b-versatile"

_client: groq.Groq | None = None


def get_client() -> groq.Groq:
    global _client
    if _client is None:
        _client = groq.Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


def call_with_retry(client: groq.Groq, max_retries: int = 3, **kwargs):
    """Call Groq chat completions with exponential backoff on RateLimitError."""
    delay = 1
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**kwargs)
        except groq.RateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def assistant_message_dict(choice) -> dict:
    """Convert a Groq ChatCompletionMessage (Pydantic) to a plain dict.

    Groq returns a Pydantic object. Appending it directly to the messages list
    causes JSON serialization failure on the next API call. This converts it to
    the plain dict format the API expects.
    """
    msg: dict = {"role": "assistant", "content": choice.message.content or ""}
    if choice.message.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in choice.message.tool_calls
        ]
    return msg
