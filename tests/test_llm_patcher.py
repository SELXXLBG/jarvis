"""
Tests pour le module core/llm_patcher.py
"""
import pytest
from unittest.mock import Mock, patch, PropertyMock
import sys
import os

import core.llm_patcher as patcher


class TestAnalyzeRequestComplexity:
    """Tests pour la fonction analyze_request_complexity."""
    
    @patch('core.llm_patcher.get_freellmapi_key')
    def test_simple_request_returns_false(self, mock_key):
        """Une requête simple doit retourner False (non complexe)."""
        mock_key.return_value = 'test_key'
        result = patcher.analyze_request_complexity('Quelle heure est-il ?')
        assert result is False
    
    @patch('core.llm_patcher.get_freellmapi_key')
    def test_code_request_returns_true(self, mock_key):
        """Une requête de code doit retourner True (complexe)."""
        mock_key.return_value = 'test_key'
        result = patcher.analyze_request_complexity('Write a Python script to parse XML files')
        assert result is True
    
    @patch('core.llm_patcher.get_freellmapi_key')
    def test_debug_request_returns_true(self, mock_key):
        """Une requête de debugging doit retourner True."""
        mock_key.return_value = 'test_key'
        result = patcher.analyze_request_complexity('Debug this function that crashes')
        assert result is True
    
    @patch('core.llm_patcher.get_freellmapi_key')
    def test_long_prompt_returns_true(self, mock_key):
        """Une requête très longue doit retourner True."""
        mock_key.return_value = 'test_key'
        long_text = ' '.join(['longerword'] * 200)
        result = patcher.analyze_request_complexity(long_text)
        assert result is True
    
    @patch('core.llm_patcher.get_freellmapi_key')
    def test_list_input_analysis(self, mock_key):
        """L'analyse doit fonctionner avec une liste de messages."""
        mock_key.return_value = 'test_key'
        messages = [
            {'role': 'user', 'content': 'Help me with my Python code'},
            {'role': 'assistant', 'content': 'What is the issue?'}
        ]
        result = patcher.analyze_request_complexity(messages)
        assert result is True
    
    @patch('core.llm_patcher.get_freellmapi_key')
    def test_french_code_keywords(self, mock_key):
        """Les mots-clés français doivent être reconnus."""
        mock_key.return_value = 'test_key'
        result = patcher.analyze_request_complexity('J\'ai une erreur dans ma fonction')
        assert result is True


class TestCallFreeLLMAPI:
    """Tests pour la fonction call_freellmapi."""
    
    @patch('core.llm_patcher.get_freellmapi_key')
    @patch('requests.post')
    def test_successful_call(self, mock_post, mock_key):
        """Un appel API réussi doit retourner le JSON de réponse."""
        mock_key.return_value = 'test_key'
        mock_response = Mock()
        mock_response.json.return_value = {
            'choices': [{'message': {'content': 'Test response'}}]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        result = patcher.call_freellmapi('Test prompt')
        
        assert result['choices'][0]['message']['content'] == 'Test response'
        mock_post.assert_called_once()
    
    @patch('core.llm_patcher.get_freellmapi_key')
    def test_missing_key_raises_error(self, mock_key):
        """Une clé manquante doit lever une ValueError."""
        mock_key.return_value = ''
        
        with pytest.raises(ValueError, match='No FreeLLMAPI key'):
            patcher.call_freellmapi('Test prompt')
    
    @patch('core.llm_patcher.get_freellmapi_key')
    @patch('requests.post')
    def test_tools_conversion(self, mock_post, mock_key):
        """Les tools doivent être convertis au format OpenAI."""
        mock_key.return_value = 'test_key'
        mock_response = Mock()
        mock_response.json.return_value = {'choices': [{'message': {'content': ''}}]}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        def sample_func(x: int, y: str):
            """Sample function for testing."""
            pass
        
        patcher.call_freellmapi('Test', tools=[sample_func])
        
        call_args = mock_post.call_args
        payload = call_args.kwargs['json']
        assert 'tools' in payload
        assert len(payload['tools']) == 1
        assert payload['tools'][0]['type'] == 'function'
    
    @patch('core.llm_patcher.get_freellmapi_key')
    @patch('requests.post')
    def test_json_mode(self, mock_post, mock_key):
        """Le mode JSON doit être activé correctement."""
        mock_key.return_value = 'test_key'
        mock_response = Mock()
        mock_response.json.return_value = {'choices': [{'message': {'content': '{}'}}]}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        patcher.call_freellmapi('Test', response_mime_type='application/json')
        
        call_args = mock_post.call_args
        payload = call_args.kwargs['json']
        assert payload['response_format']['type'] == 'json_object'


class TestMockModelsService:
    """Tests pour le MockModelsService."""
    
    @patch('core.llm_patcher.get_freellmapi_key')
    @patch('core.llm_patcher.call_freellmapi')
    def test_simple_request_fallback(self, mock_call, mock_key):
        """Doit utiliser le client original si pas de clé FreeLLMAPI."""
        mock_key.return_value = ''
        mock_original = Mock()
        mock_original.models.generate_content.return_value = Mock(text='Original response')
        
        service = patcher.MockModelsService(original_client=mock_original)
        result = service.generate_content('model', ['prompt'])
        
        mock_original.models.generate_content.assert_called_once()
    
    @patch('core.llm_patcher.get_freellmapi_key')
    @patch('core.llm_patcher.analyze_request_complexity')
    @patch('core.llm_patcher.call_freellmapi')
    def test_complex_routing(self, mock_call, mock_analyze, mock_key):
        """Les requêtes complexes doivent être routées vers gpt-4o."""
        mock_key.return_value = 'test_key'
        mock_analyze.return_value = True
        mock_call.return_value = {'choices': [{'message': {'content': 'GPT-4 response'}}]}
        
        service = patcher.MockModelsService()
        result = service.generate_content('model', 'Code this for me')
        
        assert 'gpt-4o' in str(mock_call.call_args)


class TestMockChatSession:
    """Tests pour le MockChatSession."""
    
    @patch('core.llm_patcher.get_freellmapi_key')
    @patch('core.llm_patcher.call_freellmapi')
    def test_send_message(self, mock_call, mock_key):
        """Doit gérer une conversation et retourner la réponse."""
        mock_key.return_value = 'test_key'
        mock_call.return_value = {
            'choices': [{
                'message': {
                    'content': 'Assistant response',
                    'tool_calls': []
                }
            }]
        }
        
        session = patcher.MockChatSession('model')
        result = session.send_message('Hello')
        
        assert result.text == 'Assistant response'
        assert len(session.messages) == 2  # user + assistant
    
    @patch('core.llm_patcher.get_freellmapi_key')
    @patch('core.llm_patcher.call_freellmapi')
    def test_tool_calls_extraction(self, mock_call, mock_key):
        """Doit extraire les appels de tools correctement."""
        mock_key.return_value = 'test_key'
        mock_call.return_value = {
            'choices': [{
                'message': {
                    'content': '',
                    'tool_calls': [{
                        'function': {
                            'name': 'test_func',
                            'arguments': '{"param": "value"}'
                        }
                    }]
                }
            }]
        }
        
        session = patcher.MockChatSession('model')
        result = session.send_message('Call tool')
        
        assert len(result.function_calls) == 1
        assert result.function_calls[0].name == 'test_func'
        assert result.function_calls[0].args == {'param': 'value'}


class TestFunctionToOpenAITool:
    """Tests pour la conversion de fonction vers schéma OpenAI."""
    
    def test_basic_function(self):
        """Doit convertir une fonction simple correctement."""
        def test_func(name: str, count: int):
            """Test function."""
            pass
        
        schema = patcher.function_to_openai_tool(test_func)
        
        assert schema['type'] == 'function'
        assert schema['function']['name'] == 'test_func'
        assert 'description' in schema['function']
        assert schema['function']['parameters']['properties']['name']['type'] == 'string'
        assert schema['function']['parameters']['properties']['count']['type'] == 'integer'
        assert 'name' in schema['function']['parameters']['required']
        assert 'count' in schema['function']['parameters']['required']
    
    def test_optional_parameters(self):
        """Les paramètres optionnels ne doivent pas être required."""
        def test_func(required: str, optional: str = 'default'):
            """Test with optional."""
            pass
        
        schema = patcher.function_to_openai_tool(test_func)
        
        assert 'required' in schema['function']['parameters']['required']
        assert 'optional' not in schema['function']['parameters']['required']
    
    def test_type_mapping(self):
        """Tous les types Python doivent être mappés correctement."""
        def test_func(int_param: int, float_param: float, bool_param: bool, str_param: str):
            """Test types."""
            pass
        
        schema = patcher.function_to_openai_tool(test_func)
        props = schema['function']['parameters']['properties']
        
        assert props['int_param']['type'] == 'integer'
        assert props['float_param']['type'] == 'number'
        assert props['bool_param']['type'] == 'boolean'
        assert props['str_param']['type'] == 'string'