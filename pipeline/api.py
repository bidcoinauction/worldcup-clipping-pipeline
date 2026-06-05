import os
import sys


def require_api_key(env_var: str) -> str:
    key = os.getenv(env_var)
    if not key:
        raise SystemExit(
            f"{env_var} is not set. Add it to .env or export it in your shell."
        )
    return key


def make_openai_client():
    from openai import OpenAI
    api_key = require_api_key("OPENAI_API_KEY")
    return OpenAI(api_key=api_key, max_retries=4)
