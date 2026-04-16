import os
from openai import AsyncOpenAI
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class OpenAIResponsePart:
    text: str

@dataclass
class OpenAIResponseContent:
    parts: List[OpenAIResponsePart]

@dataclass
class OpenAIResponse:
    text: str
    parts: List[OpenAIResponsePart] = field(default_factory=list)
    content: Optional[object] = None
    
    def __post_init__(self):
        if not self.parts:
            self.parts = [OpenAIResponsePart(text=self.text)]
        # For ADK plugin compatibility
        from google.genai import types
        self.content = types.Content(
            role="model",
            parts=[types.Part.from_text(text=self.text)]
        )

class OpenAIAdapter:
    """
    Shim to mimic google-adk LlmAgent/Runner using OpenAI gpt-4o-mini.
    """
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def generate_content(self, prompt: str, system_instruction: str = ""):
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.7
        )
        
        text = response.choices[0].message.content
        return OpenAIResponse(text=text)

class OpenAIRunner:
    """
    Shim to mimic google-adk InMemoryRunner for OpenAI.
    """
    def __init__(self, agent_name: str, instruction: str, api_key: str, plugins: list = None):
        self.agent_name = agent_name
        self.app_name = agent_name  # Required by chat_with_agent
        self.instruction = instruction
        self.adapter = OpenAIAdapter(api_key=api_key)
        self.plugins = plugins or []
        
        # Mock session service for chat_with_agent
        class MockSession:
            def __init__(self): self.id = "mock-session-id"
            
        class MockSessionService:
            async def get_session(self, **kwargs): return MockSession()
            async def create_session(self, **kwargs): return MockSession()
            
        self.session_service = MockSessionService()

    async def run_async(self, user_id: str, session_id: str, new_message):
        """Mock ADK run_async as an async generator."""
        # 1. on_user_message_callback
        input_text = new_message.parts[0].text if new_message.parts else ""
        
        for plugin in self.plugins:
            if hasattr(plugin, 'on_user_message_callback'):
                blocked = await plugin.on_user_message_callback(
                    invocation_context=None, 
                    user_message=new_message
                )
                if blocked:
                    # ADK expects a stream of events. We'll yield a wrapper.
                    class BlockEvent:
                        def __init__(self, content): self.content = content
                    yield BlockEvent(content=blocked)
                    return

        # 2. Main Generation
        response = await self.adapter.generate_content(input_text, self.instruction)

        # 3. after_model_callback
        processed_response = response
        for plugin in self.plugins:
            # Check if it has the callback AND it's not the base class's empty implementation if it exists
            if hasattr(plugin, 'after_model_callback'):
                try:
                    res = await plugin.after_model_callback(
                        callback_context=None,
                        llm_response=processed_response
                    )
                    if res is not None:
                        processed_response = res
                except Exception as e:
                    print(f"Plugin {plugin.name} after_model_callback error: {e}")
        
        # Yield the final response wrapped in an object chat_with_agent expects
        class ResponseEvent:
            def __init__(self, content): self.content = content
        
        from google.genai import types
        final_content = types.Content(
            role="model",
            parts=[types.Part.from_text(text=processed_response.text)]
        )
        yield ResponseEvent(content=final_content)
