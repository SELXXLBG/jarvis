"""
Configuration et fixtures pour les tests pytest.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# Ajouter le répertoire racine au path Python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture
def mock_audio_device():
    """Mock pour les périphériques audio."""
    with patch('sounddevice.InputStream') as mock_stream:
        mock_stream.return_value = Mock()
        yield mock_stream

@pytest.fixture
def mock_gemini_api():
    """Mock pour l'API Gemini."""
    with patch('google.generativeai.GenerativeModel') as mock_model:
        mock_response = Mock()
        mock_response.text = "Mocked Gemini response"
        mock_model.return_value.generate_content.return_value = mock_response
        yield mock_model

@pytest.fixture
def mock_requests():
    """Mock pour les requêtes HTTP."""
    with patch('requests.post') as mock_post:
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Mocked FreeLLMAPI response"}}]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        yield mock_post

@pytest.fixture
def mock_vad():
    """Mock pour le VAD (Voice Activity Detection)."""
    with patch('core.vad_local.VAD') as mock_vad_class:
        mock_vad_instance = Mock()
        mock_vad_instance.is_speech = Mock(return_value=True)
        mock_vad_class.return_value = mock_vad_instance
        yield mock_vad_class

@pytest.fixture
def mock_ui():
    """Mock pour l'interface utilisateur."""
    with patch('ui.JarvisUI') as mock_ui_class:
        mock_ui_instance = Mock()
        mock_ui_instance.write_log = Mock()
        mock_ui_instance.set_state = Mock()
        mock_ui_class.return_value = mock_ui_instance
        yield mock_ui_class

@pytest.fixture
def sample_api_keys():
    """Clés API de test."""
    return {
        "gemini_api_key": "test_gemini_key_123",
        "freellmapi_key": "test_freellmapi_key_456"
    }

@pytest.fixture
def mock_file_system():
    """Mock pour les opérations de fichiers."""
    with patch('pathlib.Path.exists') as mock_exists:
        with patch('builtins.open', new_callable=Mock) as mock_open:
            mock_exists.return_value = True
            mock_file = Mock()
            mock_file.__enter__ = Mock(return_value=mock_file)
            mock_file.__exit__ = Mock(return_value=None)
            mock_file.read = Mock(return_value='{"gemini_api_key": "test"}')
            mock_file.write = Mock()
            mock_open.return_value = mock_file
            yield mock_open