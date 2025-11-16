from typing import Protocol


class LLMClient(Protocol):
    async def chat(self):
        ...

    async def chat_json(self):
        ...
