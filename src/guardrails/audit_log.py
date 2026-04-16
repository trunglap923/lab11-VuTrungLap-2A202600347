import json
import os
import time
from datetime import datetime
from google.adk.plugins import base_plugin

class AuditLogPlugin(base_plugin.BasePlugin):
    """
    Audit Log Plugin to record interactions and monitor system safety.
    """
    def __init__(self, log_file="audit_log.json"):
        super().__init__(name="audit_log")
        self.log_file = log_file
        self.logs = []
        self._current_input = {}

    async def on_user_message_callback(self, *, invocation_context, user_message):
        """Record input and start time."""
        user_id = invocation_context.user_id if invocation_context else "anonymous"
        text = user_message.parts[0].text if user_message.parts else ""
        
        # Record attempt immediately
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "input": text,
            "start_time": time.time(),
            "output": "[BLOCKED]", 
            "latency_ms": 0,
            "status": "BLOCKED"
        }
        self.logs.append(log_entry)
        self.export_json()
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        """Update the last log entry with model response."""
        if not self.logs:
            return llm_response
            
        log_entry = self.logs[-1]
        end_time = time.time()
        start_time = log_entry.get("start_time", end_time)
        latency = end_time - start_time
        
        output_text = ""
        # Handle ADK response objects or types.Content
        if hasattr(llm_response, 'text'):
            output_text = llm_response.text
        elif hasattr(llm_response, 'parts'):
            output_text = "".join([p.text for p in llm_response.parts if hasattr(p, 'text')])
        elif hasattr(llm_response, 'content') and hasattr(llm_response.content, 'parts'):
            output_text = "".join([p.text for p in llm_response.content.parts if hasattr(p, 'text')])
        else:
            # Fallback for complex objects: try to find any text part
            output_text = str(llm_response)
            if "text=" in output_text:
                # Basic cleanup for raw reprs if necessary
                pass 

        log_entry["output"] = output_text
        log_entry["latency_ms"] = round(latency * 1000, 2)
        log_entry["status"] = "PASS"
        
        self.export_json()
        return llm_response

    def export_json(self):
        """Export logs to a JSON file."""
        try:
            with open(self.log_file, "w", encoding="utf-8") as f:
                json.dump(self.logs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error exporting audit log: {e}")

    def get_stats(self):
        """Return basic monitoring stats."""
        total = len(self.logs)
        if total == 0:
            return {"total": 0}
        
        avg_latency = sum(l["latency_ms"] for l in self.logs) / total
        return {
            "total_requests": total,
            "avg_latency_ms": round(avg_latency, 2),
        }
