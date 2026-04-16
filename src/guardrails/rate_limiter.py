from collections import defaultdict, deque
import time
from google.adk.plugins import base_plugin
from google.genai import types

class RateLimitPlugin(base_plugin.BasePlugin):
    """
    Rate Limiter Plugin to prevent abuse and brute-force attacks.
    Uses a sliding window algorithm to track requests per user.
    """
    def __init__(self, max_requests=10, window_seconds=60):
        super().__init__(name="rate_limiter")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.user_windows = defaultdict(deque)

    async def on_user_message_callback(self, *, invocation_context, user_message):
        """
        Check if the user has exceeded the rate limit.
        """
        user_id = invocation_context.user_id if invocation_context else "anonymous"
        now = time.time()
        window = self.user_windows[user_id]

        # Remove expired timestamps
        while window and window[0] <= now - self.window_seconds:
            window.popleft()

        if len(window) >= self.max_requests:
            wait_time = int(self.window_seconds - (now - window[0]))
            print(f"[RATE LIMIT] User {user_id} blocked. Wait {wait_time}s.")
            return types.Content(
                parts=[types.Part(text=f"Rate limit exceeded. Please try again in {wait_time} seconds.")]
            )

        # Allow request and record timestamp
        window.append(now)
        return None
