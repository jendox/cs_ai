import logging
import os
import uuid
from typing import Any

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai.types import Content, Part

from src.ai.interfaces import LLMProvider


class GoogleProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._agent: Agent | None = None
        self._runner = None
        self.logger = logging.getLogger("google_llm")

    def _initialize_agent(self, system_prompt: str):
        if self._agent is None:
            os.environ["GOOGLE_API_KEY"] = self._api_key
            self._agent = Agent(
                name="google_llm_provider",
                model=self._model,
                description="SuperSelf CS agent.",
                instruction=system_prompt,
            )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        user_id: str,
        system_prompt: str,
        session_id: str | None = None,
        tools: list | None = None,
    ) -> str:
        self._initialize_agent(system_prompt)
        session_id = session_id or uuid.uuid4().hex
        last_message = messages[-1]["content"] if messages else ""
        new_message = Content(parts=[Part(text=last_message)])

        runner = InMemoryRunner(agent=self._agent)
        full_response = ""
        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=new_message,
            ):
                if (
                    hasattr(event, "message")
                    and event.message
                    and event.message.text
                ):
                    full_response += event.message.text
            return full_response
        except Exception as exc:
            print(f"ERROR: {str(exc)}")
            return ""
