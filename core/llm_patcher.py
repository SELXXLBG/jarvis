# core/llm_patcher.py
# Transparent patcher to route helper LLM calls through FreeLLMAPI when configured.

import sys
import os
import json
import inspect
import requests
import traceback
from pathlib import Path

# Load original SDK modules if installed
try:
    import google.generativeai as legacy_genai
    ORIGINAL_LEGACY_MODEL = legacy_genai.GenerativeModel
except ImportError:
    legacy_genai = None
    ORIGINAL_LEGACY_MODEL = None

try:
    import google.genai as new_genai
    from google.genai import types as new_genai_types
    ORIGINAL_NEW_CLIENT = new_genai.Client
except ImportError:
    new_genai = None
    new_genai_types = None
    ORIGINAL_NEW_CLIENT = None


BASE_DIR = Path(__file__).resolve().parent.parent
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def get_keys():
    try:
        if API_CONFIG_PATH.exists():
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def get_real_gemini_key():
    return get_keys().get("gemini_api_key", "")

def get_freellmapi_key():
    return get_keys().get("freellmapi_key", "")

def function_to_openai_tool(func):
    sig = inspect.signature(func)
    doc = func.__doc__ or ""
    
    properties = {}
    required = []
    
    for name, param in sig.parameters.items():
        type_str = "string"
        if param.annotation == int:
            type_str = "integer"
        elif param.annotation == float:
            type_str = "number"
        elif param.annotation == bool:
            type_str = "boolean"
            
        properties[name] = {
            "type": type_str
        }
        if param.default == inspect.Parameter.empty:
            required.append(name)
            
    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": doc.strip(),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }
    }

class MockFunctionCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args

class MockPart:
    def __init__(self, text):
        self.text = text

class MockContent:
    def __init__(self, text):
        self.parts = [MockPart(text)]

class MockCandidate:
    def __init__(self, text):
        self.content = MockContent(text)

class MockResponse:
    def __init__(self, text, function_calls=None):
        self.text = text
        self.function_calls = function_calls or []
        self.candidates = [MockCandidate(text)]

def analyze_request_complexity(prompt_or_messages) -> bool:
    """Returns True if the request is considered complex and should use the direct Google API."""
    text = ""
    if isinstance(prompt_or_messages, list):
        for m in prompt_or_messages:
            if isinstance(m, dict):
                text += str(m.get("content", "")) + " "
            elif hasattr(m, 'content'):
                text += str(m.content) + " "
            else:
                text += str(m) + " "
    else:
        text = str(prompt_or_messages)
        
    text_lower = text.lower()
    
    # Very long prompts are usually complex
    if len(text) > 1000:
        return True
        
    complex_keywords = [
        "code", "python", "javascript", "script", "bug", "architecture", 
        "algorithm", "erreur", "fonction", "function", "api", "html", "css",
        "debug", "implement", "database", "sql"
    ]
    
    for kw in complex_keywords:
        if kw in text_lower:
            return True
            
    return False

def call_freellmapi(prompt_or_messages, temperature=0.7, tools=None, response_mime_type=None, override_model="auto"):
    key = get_freellmapi_key()
    if not key:
        raise ValueError("No FreeLLMAPI key configured.")
        
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    
    messages = []
    if isinstance(prompt_or_messages, list):
        for m in prompt_or_messages:
            if isinstance(m, dict):
                messages.append(m)
            elif hasattr(m, 'role') and hasattr(m, 'content'):
                messages.append({"role": m.role, "content": m.content})
            else:
                messages.append({"role": "user", "content": str(m)})
    else:
        messages.append({"role": "user", "content": str(prompt_or_messages)})
        
    payload = {
        "model": override_model,
        "messages": messages,
        "temperature": temperature
    }
    
    if tools:
        openai_tools = []
        for t in tools:
            if callable(t):
                openai_tools.append(function_to_openai_tool(t))
            elif isinstance(t, dict):
                openai_tools.append(t)
            elif hasattr(t, 'to_dict'):
                openai_tools.append(t.to_dict())
            else:
                try:
                    openai_tools.append(function_to_openai_tool(t))
                except Exception:
                    pass
        if openai_tools:
            payload["tools"] = openai_tools
            
    if response_mime_type == "application/json":
        payload["response_format"] = {"type": "json_object"}
        
    r = requests.post("http://127.0.0.1:3001/v1/chat/completions", headers=headers, json=payload, timeout=45)
    r.raise_for_status()
    return r.json()

# --- Legacy google.generativeai Mock ---
class MockGenerativeModel:
    def __init__(self, model_name, *args, **kwargs):
        self.model_name = model_name
        self.original_model = None
        real_key = get_real_gemini_key()
        if real_key and ORIGINAL_LEGACY_MODEL:
            try:
                self.original_model = ORIGINAL_LEGACY_MODEL(model_name, *args, **kwargs)
            except Exception:
                pass

    def generate_content(self, contents, generation_config=None, **kwargs):
        freellmapi_key = get_freellmapi_key()
        
        is_complex = analyze_request_complexity(contents)
        model_choice = "gpt-4o" if is_complex else "gemini-2.5-flash-lite"
        
        if not freellmapi_key:
            if self.original_model:
                return self.original_model.generate_content(contents, generation_config=generation_config, **kwargs)
            raise ValueError("No API key available")
            
        try:
            temp = 0.7
            if generation_config:
                if isinstance(generation_config, dict):
                    temp = generation_config.get("temperature", 0.7)
                elif hasattr(generation_config, "temperature"):
                    temp = getattr(generation_config, "temperature", 0.7)
                    
            res = call_freellmapi(contents, temperature=temp, override_model=model_choice)
            text = res["choices"][0]["message"]["content"]
            return MockResponse(text)
        except Exception as e:
            print(f"[FreeLLMAPI Patcher] Legacy generate_content error (model: {model_choice}): {e}")
            return MockResponse(f"Error calling LLM: {e}")

def mock_configure(*args, **kwargs):
    pass

# --- New google.genai Mock ---
class MockModelsService:
    def __init__(self, original_client=None):
        self.original_client = original_client

    def generate_content(self, model, contents, config=None, **kwargs):
        freellmapi_key = get_freellmapi_key()
        
        is_complex = analyze_request_complexity(contents)
        model_choice = "gpt-4o" if is_complex else "gemini-2.5-flash-lite"
        
        if not freellmapi_key:
            if self.original_client:
                return self.original_client.models.generate_content(model=model, contents=contents, config=config, **kwargs)
            raise ValueError("No API key available")
            
        try:
            temp = 0.7
            mime_type = None
            if config:
                if isinstance(config, dict):
                    temp = config.get("temperature", 0.7)
                    mime_type = config.get("response_mime_type", None)
                else:
                    temp = getattr(config, "temperature", 0.7)
                    mime_type = getattr(config, "response_mime_type", None)
                    
            res = call_freellmapi(contents, temperature=temp, response_mime_type=mime_type, override_model=model_choice)
            text = res["choices"][0]["message"]["content"]
            return MockResponse(text)
        except Exception as e:
            print(f"[FreeLLMAPI Patcher] New models.generate_content error (model: {model_choice}): {e}")
            return MockResponse(f"Error calling LLM: {e}")

class MockChatSession:
    def __init__(self, model, config=None):
        self.model = model
        self.config = config
        self.messages = []
        
        sys_inst = None
        if config:
            if isinstance(config, dict):
                sys_inst = config.get("system_instruction", None)
            else:
                sys_inst = getattr(config, "system_instruction", None)
                
        if sys_inst:
            if isinstance(sys_inst, list):
                sys_inst_str = " ".join(str(p) for p in sys_inst)
            elif hasattr(sys_inst, "parts"):
                sys_inst_str = " ".join(str(p.text) for p in sys_inst.parts if hasattr(p, "text"))
            else:
                sys_inst_str = str(sys_inst)
            self.messages.append({"role": "system", "content": sys_inst_str})

    def send_message(self, prompt):
        self.messages.append({"role": "user", "content": str(prompt)})
        
        is_complex = analyze_request_complexity(self.messages)
        model_choice = "gpt-4o" if is_complex else "gemini-2.5-flash-lite"
        
        temp = 0.7
        tools = None
        if self.config:
            if isinstance(self.config, dict):
                temp = self.config.get("temperature", 0.7)
                tools = self.config.get("tools", None)
            else:
                temp = getattr(self.config, "temperature", 0.7)
                tools = getattr(self.config, "tools", None)
                
        try:
            res = call_freellmapi(self.messages, temperature=temp, tools=tools, override_model=model_choice)
            choice = res["choices"][0]["message"]
            content = choice.get("content") or ""
            
            self.messages.append(choice)
            
            fcs = []
            if "tool_calls" in choice:
                for tc in choice["tool_calls"]:
                    name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    fcs.append(MockFunctionCall(name, args))
                    
            return MockResponse(content, function_calls=fcs)
        except Exception as e:
            print(f"[FreeLLMAPI Patcher] Error in chat.send_message (model: {model_choice}): {e}")
            return MockResponse(f"Error calling LLM: {e}")

class MockChatsService:
    def create(self, model, config=None, **kwargs):
        return MockChatSession(model, config)

class MockClient:
    def __init__(self, api_key=None, http_options=None, **kwargs):
        real_key = get_real_gemini_key()
        self.real_client = None
        if real_key and ORIGINAL_NEW_CLIENT:
            try:
                self.real_client = ORIGINAL_NEW_CLIENT(api_key=real_key, http_options=http_options, **kwargs)
            except Exception as e:
                print(f"[FreeLLMAPI Patcher] Warning: Could not initialize real google client: {e}")
                
        self.models = MockModelsService(self.real_client)
        self.chats = MockChatsService()

    @property
    def aio(self):
        if self.real_client:
            return self.real_client.aio
        raise ValueError(
            "La clé Gemini API est requise pour la voix temps réel (Gemini Live).\n"
            "Ajoutez 'gemini_api_key' dans config/api_keys.json.\n"
            "FreeLLMAPI ne supporte pas le streaming audio WebSocket."
        )

def patch_sdk():
    freellmapi_key = get_freellmapi_key()
    if freellmapi_key:
        print(f"[FreeLLMAPI Patcher] FreeLLMAPI key detected ({freellmapi_key[:15]}...). Applying monkeypatches...")
        if legacy_genai:
            legacy_genai.GenerativeModel = MockGenerativeModel
            legacy_genai.configure = mock_configure
        if new_genai:
            new_genai.Client = MockClient
    else:
        print("[FreeLLMAPI Patcher] No FreeLLMAPI key detected. Using standard Google SDK configuration.")

# Perform patching automatically when imported
try:
    patch_sdk()
except Exception as e:
    print(f"[FreeLLMAPI Patcher] Failed to apply patch: {e}")
    traceback.print_exc()
