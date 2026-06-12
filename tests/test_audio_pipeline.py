"""
Tests pour le pipeline audio.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
import numpy as np
import sounddevice


class TestAudioInitialization:
    """Tests pour l'initialisation du pipeline audio."""
    
    @patch('sounddevice.InputStream')
    def test_audio_stream_creation(self, mock_stream):
        """Doit créer un stream audio avec les bons paramètres."""
        from core.audio_pipeline import init_audio_stream
        
        mock_stream_instance = Mock()
        mock_stream.return_value = mock_stream_instance
        
        stream = init_audio_stream()
        
        mock_stream.assert_called_once()
        call_kwargs = mock_stream.call_args.kwargs
        assert call_kwargs['samplerate'] == 16000
        assert call_kwargs['blocksize'] == 1024
        assert call_kwargs['dtype'] == np.int16
        assert 'callback' in call_kwargs
    
    @patch('sounddevice.InputStream')
    def test_audio_callback_error_handling(self, mock_stream):
        """Le callback doit gérer les erreurs de status."""
        from core.audio_pipeline import audio_callback
        
        # Mock pour logger
        with patch('core.audio_pipeline.logger') as mock_logger:
            # Test avec status d'erreur
            indata = np.zeros((1024, 1), dtype=np.int16)
            audio_callback(indata, 1024, Mock(), sounddevice.CallbackFlags())
            
            # Test avec status d'erreur
            error_status = sounddevice.CallbackFlags()
            error_status.input_overflow = True
            audio_callback(indata, 1024, Mock(), error_status)
            
            # Vérifier que le logger a été appelé
            mock_logger.warning.assert_called()
    
    def test_audio_compression(self):
        """La compression audio doit réduire la taille des données."""
        from core.audio_pipeline import audio_callback
        
        # Créer des données audio simulées
        original_data = np.random.randint(-32768, 32767, (1024, 1), dtype=np.int16)
        
        # Mock process_audio pour capturer les données compressées
        captured_data = []
        def mock_process(data):
            captured_data.append(data.copy())
        
        with patch('core.audio_pipeline.process_audio', mock_process):
            audio_callback(original_data, 1024, Mock(), sounddevice.CallbackFlags())
            
            assert len(captured_data) == 1
            compressed = captured_data[0]
            
            # Vérifier que les données sont compressées (divisées par 2)
            assert np.all(np.abs(compressed) <= np.abs(original_data.flatten()) // 2)


class TestVAD:
    """Tests pour la détection d'activité vocale."""
    
    @patch('core.vad_local.VAD')
    def test_vad_initialization(self, mock_vad_class):
        """Le VAD doit être initialisé."""
        from core.vad_local import VAD
        
        vad = VAD()
        
        mock_vad_class.assert_called_once()
        assert hasattr(vad, 'session') or hasattr(vad, 'enabled')
    
    @patch('onnxruntime.InferenceSession')
    def test_vad_speech_detection(self, mock_session):
        """Le VAD doit détecter correctement la parole."""
        from core.vad_local import VAD
        
        # Mock de la session ONNX
        mock_instance = Mock()
        mock_instance.run.return_value = [np.array([[0.9]]), np.zeros((2,1,64)), np.zeros((2,1,64))]  # Probabilité + h + c
        mock_session.return_value = mock_instance
        
        vad = VAD()
        vad.session = mock_instance
        vad.enabled = True
        
        # Créer des données audio simulées en bytes (1024 échantillons = 2048 bytes)
        audio_data = np.random.randint(-1000, 1000, 1024, dtype=np.int16).tobytes()
        
        # Tester la détection
        is_speech = vad.is_speech(audio_data)
        
        assert isinstance(is_speech, bool)
        assert is_speech == True
        mock_instance.run.assert_called_once()
    
    def test_vad_silence_threshold(self):
        """Le VAD doit détecter le silence correctement."""
        from core.vad_local import VAD
        
        vad = VAD()
        vad.enabled = True
        
        # Créer du silence en bytes
        silence_data = np.zeros(1024, dtype=np.int16).tobytes()
        
        # Mock le modèle pour retourner une faible probabilité
        with patch.object(vad, 'session') as mock_session:
            mock_session.run.return_value = [np.array([[0.1]]), np.zeros((2,1,64)), np.zeros((2,1,64))]
            
            is_speech = vad.is_speech(silence_data)
            
            assert is_speech == False


class TestAudioBuffer:
    """Tests pour la gestion des buffers audio."""
    
    def test_buffer_overflow_protection(self):
        """Le buffer doit protéger contre les overflows."""
        from core.audio_pipeline import AudioBuffer
        
        buffer = AudioBuffer(max_size=1000)
        
        # Ajouter plus de données que la capacité
        data = np.ones(2000, dtype=np.int16)
        buffer.add(data)
        
        # Le buffer ne doit pas dépasser max_size
        assert len(buffer.get_data()) <= 1000
    
    def test_buffer_clear(self):
        """Le buffer doit pouvoir être vidé."""
        from core.audio_pipeline import AudioBuffer
        
        buffer = AudioBuffer(max_size=1000)
        buffer.add(np.ones(500, dtype=np.int16))
        
        assert len(buffer.get_data()) == 500
        
        buffer.clear()
        assert len(buffer.get_data()) == 0
    
    def test_buffer_concat(self):
        """Le buffer doit concaténer correctement les données."""
        from core.audio_pipeline import AudioBuffer
        
        buffer = AudioBuffer(max_size=5000)
        
        # Ajouter plusieurs segments
        for i in range(5):
            buffer.add(np.ones(100, dtype=np.int16) * i)
        
        data = buffer.get_data()
        assert len(data) == 500
        
        # Vérifier l'ordre des données
        for i in range(5):
            segment = data[i*100:(i+1)*100]
            assert np.all(segment == i)


class TestLatencyOptimization:
    """Tests pour les optimisations de latence."""
    
    def test_buffer_size_optimization(self):
        """La taille du buffer doit être optimisée pour la latence."""
        from core.audio_pipeline import DEFAULT_SAMPLE_RATE, BUFFER_SIZE
        
        # Calculer la latence théorique
        latency_ms = (BUFFER_SIZE / DEFAULT_SAMPLE_RATE) * 1000
        
        # La latence doit être inférieure à 100ms pour le temps réel
        assert latency_ms < 100, f"Latence trop élevée: {latency_ms:.1f}ms"
        
        # La latence doit être supérieure à 10ms pour éviter les overflows
        assert latency_ms > 10, f"Latence trop faible: {latency_ms:.1f}ms"
    
    @patch('sounddevice.InputStream')
    def test_async_audio_processing(self, mock_stream):
        """Le traitement audio doit être asynchrone."""
        import asyncio
        from core.audio_pipeline import AsyncAudioProcessor
        
        processor = AsyncAudioProcessor()
        
        # Simuler des données audio
        audio_data = np.random.randn(1024).astype(np.int16)
        
        # Tester le traitement asynchrone
        async def test_processing():
            result = await processor.process_async(audio_data)
            return result
        
        # Exécuter la coroutine
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(test_processing())
            assert result is not None
        finally:
            loop.close()
    
    def test_audio_chunking(self):
        """Les données audio doivent être chunkées efficacement."""
        from core.audio_pipeline import chunk_audio_data
        
        # Créer 5 secondes d'audio à 16kHz
        audio_data = np.random.randn(16000 * 5).astype(np.int16)
        
        # Chunker en segments de 1024 échantillons
        chunks = list(chunk_audio_data(audio_data, chunk_size=1024))
        
        # Vérifier le nombre de chunks
        expected_chunks = (16000 * 5) // 1024
        assert len(chunks) == expected_chunks
        
        # Vérifier la taille de chaque chunk
        for chunk in chunks:
            assert len(chunk) == 1024
        
        # Vérifier que la concaténation redonne les données originales
        reconstructed = np.concatenate(chunks)
        assert np.array_equal(reconstructed, audio_data[:len(reconstructed)])