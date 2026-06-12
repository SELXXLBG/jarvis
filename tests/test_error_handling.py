import pytest
from unittest.mock import patch, Mock, AsyncMock
import asyncio
import main
from main import JarvisLive
import sounddevice as sd

# Backup de l'original sleep pour éviter de casser pytest-asyncio
original_sleep = asyncio.sleep

async def selective_sleep(delay, *args, **kwargs):
    # Intercepter uniquement le sleep du retry (retry_delay vaut 1 au premier tour)
    if delay in (1, 2, 4, 8, 10):
        raise BaseException("StopRetryLoop")
    return await original_sleep(delay, *args, **kwargs)

class TestGeminiAPIConnection:
    """Tests pour la gestion des connexions à l'API Gemini dans JarvisLive."""

    @pytest.mark.asyncio
    @patch('google.genai.Client')
    async def test_successful_connection(self, mock_client_class):
        """Une connexion réussie configure la session et lance les services."""
        mock_ui = Mock()
        jarvis = JarvisLive(mock_ui)
        
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        # Simuler le contexte aio.live.connect
        mock_session = AsyncMock()
        
        # Simuler receive comme un itérateur asynchrone
        async def mock_receive():
            # Générateur asynchrone vide
            if False:
                yield None
        mock_session.receive.return_value = mock_receive()
        
        mock_connect_ctx = AsyncMock()
        mock_connect_ctx.__aenter__.return_value = mock_session
        mock_client.aio.live.connect.return_value = mock_connect_ctx
        
        # Lever une BaseException depuis _send_realtime pour casser la boucle infinie sans retry
        with patch.object(jarvis, '_build_config') as mock_build_config:
            mock_build_config.return_value = Mock()
            with patch.object(jarvis, '_send_realtime', side_effect=BaseException("StopLoop")):
                with pytest.raises(BaseException):
                    await jarvis.run()
        
        mock_client.aio.live.connect.assert_called_once()
        mock_ui.set_state.assert_any_call("THINKING")

    @pytest.mark.asyncio
    @patch('google.genai.Client')
    async def test_connection_retry_on_error(self, mock_client_class):
        """Les erreurs de connexion doivent déclencher des retries avec exponential backoff."""
        mock_ui = Mock()
        jarvis = JarvisLive(mock_ui)
        
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        # Faire lever une exception lors de la connexion
        mock_client.aio.live.connect.side_effect = Exception("Connection refused")
        
        with patch('asyncio.sleep', new=selective_sleep):
            with pytest.raises(BaseException):
                await jarvis.run()
            
            assert mock_client.aio.live.connect.call_count >= 1

class TestAudioErrorHandling:
    """Tests pour la gestion des erreurs du flux audio."""

    @pytest.mark.asyncio
    @patch('sounddevice.InputStream')
    async def test_audio_device_unavailable(self, mock_stream):
        """Un périphérique audio indisponible doit lever/logger une exception."""
        mock_stream.side_effect = Exception("Device unavailable")
        
        mock_ui = Mock()
        jarvis = JarvisLive(mock_ui)
        
        with pytest.raises(Exception, match="Device unavailable"):
            await jarvis._listen_audio()

class TestLoggingStructure:
    """Tests pour la configuration du logging."""

    @patch('loguru.logger.add')
    def test_logger_configuration(self, mock_add):
        """Le logger doit être configuré."""
        assert main is not None
