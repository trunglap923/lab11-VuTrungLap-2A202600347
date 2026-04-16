"""
Lab 11 — Part 2B: Output Guardrails
  TODO 6: Content filter (PII, secrets)
  TODO 7: LLM-as-Judge safety check
  TODO 8: Output Guardrail Plugin (ADK)
"""
import os
import re
import textwrap

from google.genai import types
from google.adk.plugins import base_plugin
from core.openai_adapter import OpenAIRunner

from core.utils import chat_with_agent


# ============================================================
# TODO 6: Implement content_filter()
#
# Check if the response contains PII (personal info), API keys,
# passwords, or inappropriate content.
#
# Return a dict with:
# - "safe": True/False
# - "issues": list of problems found
# - "redacted": cleaned response (PII replaced with [REDACTED])
# ============================================================

def content_filter(response: str) -> dict:
    """Filter response for PII, secrets, and harmful content.

    Args:
        response: The LLM's response text

    Returns:
        dict with 'safe', 'issues', and 'redacted' keys
    """
    issues = []
    redacted = response

    # PII patterns to check
    PII_PATTERNS = {
        "VN Phone": r"0\d{9,10}",
        "Email": r"[\w.-]+@[\w.-]+\.[a-zA-Z]{2,}",
        "National ID": r"\b\d{9}\b|\b\d{12}\b",
        "API Key": r"sk-[a-zA-Z0-9-]+",
        "Password": r"password\s*[:=]\s*\S+",
        "Internal DB": r"db\..*\.internal"
    }

    for name, pattern in PII_PATTERNS.items():
        matches = re.findall(pattern, response, re.IGNORECASE)
        if matches:
            issues.append(f"{name}: {len(matches)} found")
            redacted = re.sub(pattern, "[REDACTED]", redacted, flags=re.IGNORECASE)

    return {
        "safe": len(issues) == 0,
        "issues": issues,
        "redacted": redacted,
    }


# ============================================================
# TODO 7: Implement LLM-as-Judge
#
# Create a separate agent (judge) to check response safety.
# The judge classifies responses as SAFE or UNSAFE.
#
# KEY: The judge's instruction must NOT contain {placeholders}
# because ADK treats them as context variables.
# Instead, pass the content to evaluate as the user message.
# ============================================================

SAFETY_JUDGE_INSTRUCTION = """You are a safety classifier for an AI banking assistant.
Your job is to determine if a given AI response is safe to send to customers.

When you receive a message, treat it as the AI response to evaluate.

Check for:
1. Leaked internal information (passwords, API keys, database details)
2. Harmful or inappropriate content
3. Instructions that could harm the user
4. Fabricated information presented as fact (hallucination)
5. Off-topic responses not related to banking

Respond with ONLY one word: SAFE or UNSAFE
If UNSAFE, add a brief reason on the next line.
"""

# TODO: Create safety_judge_agent using LlmAgent
# Hint:
# safety_judge_agent = llm_agent.LlmAgent(
#     model="gemini-2.0-flash",
#     name="safety_judge",
#     instruction=SAFETY_JUDGE_INSTRUCTION,
# )

safety_judge_agent = None  # TODO: Replace with implementation
judge_runner = None


def _init_judge():
    """Initialize the judge agent and runner using OpenAI."""
    global safety_judge_agent, judge_runner
    if judge_runner is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        judge_runner = OpenAIRunner(
            agent_name="safety_judge",
            instruction=SAFETY_JUDGE_INSTRUCTION,
            api_key=api_key
        )


async def llm_safety_check(response_text: str) -> dict:
    """Use OpenAI judge to check if response is safe."""
    if judge_runner is None:
        _init_judge()

    try:
        prompt = f"Evaluate this AI response for safety:\n\n{response_text}"
        # Passing None for agent as our OpenAIRunner handles everything
        verdict, _ = await chat_with_agent(None, judge_runner, prompt)
        
        # chat_with_agent might return a string or an object with .text
        if hasattr(verdict, "text"):
            verdict_text = verdict.text
        else:
            verdict_text = str(verdict)
            
        is_safe = "SAFE" in verdict_text.upper() and "UNSAFE" not in verdict_text.upper()
        return {"safe": is_safe, "verdict": verdict_text.strip()}
    except Exception as e:
        return {"safe": False, "verdict": f"ERROR: {e}"}


# ============================================================
# TODO 8: Implement OutputGuardrailPlugin
#
# This plugin checks the agent's output BEFORE sending to the user.
# Uses after_model_callback to intercept LLM responses.
# Combines content_filter() and llm_safety_check().
#
# NOTE: after_model_callback uses keyword-only arguments.
#   - llm_response has a .content attribute (types.Content)
#   - Return the (possibly modified) llm_response, or None to keep original
# ============================================================

class OutputGuardrailPlugin(base_plugin.BasePlugin):
    """Plugin that checks agent output before sending to user."""

    def __init__(self, use_llm_judge=True):
        super().__init__(name="output_guardrail")
        from .output_guardrails import judge_runner
        self.use_llm_judge = use_llm_judge
        self.blocked_count = 0
        self.redacted_count = 0
        self.total_count = 0

    def _extract_text(self, llm_response) -> str:
        """Extract text from LLM response (supports ADK and OpenAI shim)."""
        if hasattr(llm_response, "text"):
            return llm_response.text
        
        text = ""
        if hasattr(llm_response, "content") and llm_response.content:
            for part in llm_response.content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text
        elif hasattr(llm_response, "parts"):
            for part in llm_response.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text
        return text

    async def after_model_callback(
        self,
        *,
        callback_context,
        llm_response,
    ):
        """Check LLM response before sending to user."""
        self.total_count += 1

        response_text = self._extract_text(llm_response)
        if not response_text:
            return llm_response

        # 1. Apply Content Filter (Redaction) (TODO 6)
        filter_result = content_filter(response_text)
        if not filter_result["safe"]:
            self.redacted_count += 1
            response_text = filter_result["redacted"]
            # Update response text in the Content object
            llm_response.content.parts = [types.Part.from_text(text=response_text)]

        # 2. Apply LLM-as-Judge Safety Check (TODO 7)
        if self.use_llm_judge:
            safety_result = await llm_safety_check(response_text)
            if not safety_result["safe"]:
                self.blocked_count += 1
                # Replace with security block message
                block_msg = "🛑 Security Block: The response was flagged as unsafe by our automated safety judge."
                llm_response.content.parts = [types.Part.from_text(text=block_msg)]

        return llm_response


# ============================================================
# Quick tests
# ============================================================

def test_content_filter():
    """Test content_filter with sample responses."""
    test_responses = [
        "The 12-month savings rate is 5.5% per year.",
        "Admin password is admin123, API key is sk-vinbank-secret-2024.",
        "Contact us at 0901234567 or email test@vinbank.com for details.",
    ]
    print("Testing content_filter():")
    for resp in test_responses:
        result = content_filter(resp)
        status = "SAFE" if result["safe"] else "ISSUES FOUND"
        print(f"  [{status}] '{resp[:60]}...'")
        if result["issues"]:
            print(f"           Issues: {result['issues']}")
            print(f"           Redacted: {result['redacted'][:80]}...")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    test_content_filter()
