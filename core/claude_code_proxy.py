# core/claude_code_proxy.py
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import requests

# Add project root to path to load profile_loader
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import core.profile_loader as pl

PORT = 8082

class ClaudeCodeProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default request logs to keep terminal clean
        pass

    def do_POST(self):
        if self.path != "/v1/messages":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            anthropic_req = json.loads(body.decode('utf-8'))
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Invalid JSON: {e}".encode())
            return

        # Load Selyan's active profile configuration
        profile_config = pl.load_api_keys()
        freellm_key = profile_config.get("freellmapi_key")
        freellm_base = profile_config.get("freellmapi_url", "http://127.0.0.1:3001")

        if not freellm_key:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Error: freellmapi_key is not configured in your active profile.")
            return

        # Translate Anthropic messages to OpenAI Chat Completions messages
        openai_messages = []
        
        # Check for system prompt in request, or prepend active profile prompt
        system_prompt = anthropic_req.get("system") or pl.get_system_prompt()
        if system_prompt:
            openai_messages.append({"role": "system", "content": system_prompt})

        for msg in anthropic_req.get("messages", []):
            role = msg.get("role")
            content = msg.get("content")
            
            # Anthropic content can be a list or a string
            if isinstance(content, list):
                text_content = ""
                for part in content:
                    if part.get("type") == "text":
                        text_content += part.get("text", "")
                content = text_content
                
            openai_messages.append({"role": role, "content": content})

        # Map to the FreeLLM target model (usually gpt-4o for Claude Code tasks)
        openai_model = "gpt-4o"

        payload = {
            "model": openai_model,
            "messages": openai_messages,
            "temperature": anthropic_req.get("temperature", 0.7),
            "stream": anthropic_req.get("stream", False)
        }

        headers = {
            "Authorization": f"Bearer {freellm_key}",
            "Content-Type": "application/json"
        }

        try:
            print(f"[Proxy] Forwarding to FreeLLM API (Streaming={payload['stream']})...")
            upstream_res = requests.post(
                f"{freellm_base}/v1/chat/completions",
                json=payload,
                headers=headers,
                stream=payload["stream"],
                timeout=45
            )
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"Upstream Connection Failed: {e}".encode())
            return

        # Handshake response headers
        self.send_response(upstream_res.status_code)
        for h, v in upstream_res.headers.items():
            # Filter headers that conflict with proxying
            if h.lower() not in ["content-length", "transfer-encoding", "content-encoding", "connection"]:
                self.send_header(h, v)

        if payload["stream"]:
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            # Emit initial Anthropic SSE events
            self.emit_sse("message_start", {
                "type": "message_start",
                "message": {
                    "id": "msg_proxy_start",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": anthropic_req.get("model", "claude-3-5-sonnet"),
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}
                }
            })
            self.emit_sse("content_block_start", {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""}
            })

            # Read OpenAI stream and translate to Anthropic format on the fly
            buffer = ""
            for chunk in upstream_res.iter_lines():
                if not chunk:
                    continue
                line = chunk.decode('utf-8').strip()
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        openai_data = json.loads(data_str)
                        delta = openai_data["choices"][0]["delta"]
                        text_delta = delta.get("content", "")
                        if text_delta:
                            # Emit chunk delta
                            self.emit_sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": text_delta}
                            })
                    except Exception:
                        pass

            # Emit final termination events
            self.emit_sse("content_block_stop", {"type": "content_block_stop", "index": 0})
            self.emit_sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 0}
            })
            self.emit_sse("message_stop", {"type": "message_stop"})
        else:
            # Non-streaming response translation
            self.end_headers()
            try:
                openai_json = upstream_res.json()
                text_content = openai_json["choices"][0]["message"]["content"]
                
                anthropic_res = {
                    "id": "msg_proxy_sync",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": text_content}],
                    "model": anthropic_req.get("model", "claude-3-5-sonnet"),
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}
                }
                self.wfile.write(json.dumps(anthropic_res).encode('utf-8'))
            except Exception as e:
                self.wfile.write(f"Parsing error: {e}".encode())

    def emit_sse(self, event_name, data):
        payload = f"event: {event_name}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(payload.encode('utf-8'))
        self.wfile.flush()

def run_proxy():
    print(f"🚀 Claude Code Translation Proxy running on http://127.0.0.1:{PORT}")
    print(f"Targeting active profile: '{pl.get_active_profile_name()}'")
    server = HTTPServer(('127.0.0.1', PORT), ClaudeCodeProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping proxy...")
        server.server_close()

if __name__ == "__main__":
    run_proxy()
