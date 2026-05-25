import os
import urllib.request
import numpy as np

try:
    import onnxruntime
    # Force disable ONNX to lighten the program (use RMS fallback instead)
    _ONNX_AVAILABLE = False
except ImportError:
    _ONNX_AVAILABLE = False
    print("[VAD] ⚠️ onnxruntime non installé — VAD par défaut (RMS) sera utilisé")


MODEL_URL = "https://github.com/snakers4/silero-vad/raw/v4.0/files/silero_vad.onnx"


class LocalVAD:
    """
    Détecteur d'activité vocale (VAD) neuronal utilisant Silero VAD via ONNX.
    Fonctionne 100% en local sur CPU, ultra-léger (< 1% CPU).
    Filtre efficacement les bruits de fond (clavier, portes, musique).
    """

    def __init__(self):
        self.session = None
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self.enabled = _ONNX_AVAILABLE

        if not self.enabled:
            return

        model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
        model_path = os.path.join(model_dir, "silero_vad.onnx")

        if not os.path.exists(model_path):
            os.makedirs(model_dir, exist_ok=True)
            print(f"[VAD] 📥 Téléchargement du modèle Silero VAD local (2 Mo)...")
            try:
                urllib.request.urlretrieve(MODEL_URL, model_path)
                print("[VAD] ✅ Modèle téléchargé avec succès.")
            except Exception as e:
                print(f"[VAD] ❌ Échec du téléchargement du modèle: {e}")
                self.enabled = False
                return

        # Configuration légère pour le CPU
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        
        try:
            self.session = onnxruntime.InferenceSession(
                model_path, 
                providers=['CPUExecutionProvider'], 
                sess_options=opts
            )
            print("[VAD] 🧠 Modèle d'inintelligence artificielle local VAD prêt.")
        except Exception as e:
            print(f"[VAD] ❌ Erreur de chargement ONNX: {e}")
            self.enabled = False

    def is_speech(self, audio_data: bytes, threshold: float = 0.5) -> bool:
        """
        Analyse un chunk audio brut (16-bit PCM, 16kHz).
        Retourne True s'il s'agit d'une voix humaine avec une probabilité > threshold.
        """
        if not self.enabled or not self.session:
            return False

        # Conversion des octets bruts (int16) en float32 normalisé entre -1 et 1
        audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        # Silero VAD (v4) s'attend à des chunks de taille fixe (512, 1024 ou 1536)
        # Notre CHUNK_SIZE est de 1024.
        if len(audio_float32) != 1024:
            # Padding ou troncature rapide pour garantir 1024
            audio_float32 = np.pad(audio_float32, (0, max(0, 1024 - len(audio_float32))))[:1024]

        # Ajout de la dimension batch: [seq_len] -> [batch, seq_len]
        input_data = np.expand_dims(audio_float32, 0)

        inputs = {
            'input': input_data,
            'sr': np.array(16000, dtype=np.int64),
            'h': self._h,
            'c': self._c
        }

        # Exécution de l'inférence neuronale
        try:
            out, self._h, self._c = self.session.run(None, inputs)
        except Exception as e:
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)
            out = [[0.0]]

        # out[0][0] contient la probabilité (0.0 à 1.0) qu'il y ait de la parole
        prob = float(out[0][0])
        return prob > threshold
