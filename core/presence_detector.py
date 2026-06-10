# core/presence_detector.py — Détecteur de présence pour JARVIS
# Webcam-based presence detection using OpenCV face detection
# Désactivé par défaut — doit être explicitement activé / Disabled by default
# Sentry Mode : tout visage détecté → photo Telegram + verrouillage Windows

import ctypes
import io
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


class PresenceDetector:
    """
    Détecteur de présence basé sur la webcam.
    Utilise OpenCV et le classificateur Haar pour détecter les visages.
    Capture UNE image toutes les 10 secondes (pas de vidéo continue).

    Webcam-based presence detector.
    Uses OpenCV Haar cascade classifier for face detection.
    Captures ONE frame every 10 seconds (not continuous video).

    DISABLED by default — must be explicitly enabled via set_enabled(True).
    """

    # --- Configuration ---
    CAPTURE_INTERVAL: float = 10.0        # Seconds between frame captures
    GRACE_PERIOD: float = 30.0            # Seconds before "user left" triggers
    CASCADE_FILE: str = "haarcascade_frontalface_default.xml"

    # ── Sentry Mode config ────────────────────────────────────────────────────
    SENTRY_ALERT_COOLDOWN: float = 60.0    # Secondes entre deux alertes (anti-spam)
    SENTRY_SAVE_DIR: str = "sentry_alerts" # Dossier local pour les photos d'intrus

    def __init__(self):
        # Thread safety / Sécurité des threads
        self._lock: threading.Lock = threading.Lock()

        # State / État
        self._enabled: bool = False          # DISABLED by default / Désactivé par défaut
        self._running: bool = False
        self._user_present: bool = False
        self._last_seen: Optional[datetime] = None
        self._last_face_detected: Optional[datetime] = None

        # ── Sentry Mode ───────────────────────────────────────────────────────
        self._sentry_mode: bool = False           # True = mode surveillance actif
        self._sentry_last_alert: Optional[float] = None  # timestamp dernière alerte
        self._on_intruder_callbacks: list[Callable[[bytes, str], None]] = []
        # Dossier de sauvegarde des photos d'intrus
        self._sentry_save_path = Path(self.SENTRY_SAVE_DIR)

        # Camera / Caméra
        self._camera = None  # cv2.VideoCapture — initialized on start
        self._cascade = None  # cv2.CascadeClassifier
        self._eye_cascade = None
        self._smile_cascade = None
        self._last_frame = None  # Dernière frame capturée (numpy array)

        # Callbacks
        self._on_arrived_callbacks: list[Callable[[], None]] = []
        self._on_left_callbacks: list[Callable[[], None]] = []
        self._on_fatigue_callbacks: list[Callable[[bool], None]] = []
        self._on_mood_callbacks: list[Callable[[str], None]] = []

        # Fatigue & Mood state
        self._fatigued: bool = False
        self._mood: str = "unknown"
        self._consecutive_eyes_closed: int = 0

        # Face ID state / État Face ID
        self._owner_face_template = None  # Model face cropped to 100x100
        self._owner_descriptors = None    # ORB descriptors
        self._orb = None
        self._face_id_enabled: bool = False

        # Thread
        self._thread: Optional[threading.Thread] = None

        # OpenCV availability / Disponibilité d'OpenCV
        self._cv2_available: bool = False
        self._check_opencv()

        print("[Presence] ✅ Initialisé / Initialized (DISABLED by default)")

    def _check_opencv(self) -> None:
        """Vérifie si OpenCV est disponible. / Check if OpenCV is available."""
        try:
            import cv2  # noqa: F401
            self._cv2_available = True
            print("[Presence]   → OpenCV disponible / OpenCV available")
        except ImportError:
            self._cv2_available = False
            print("[Presence]   ⚠️ OpenCV non trouvé — pip install opencv-python")
            print("[Presence]   ⚠️ Presence detection will be unavailable")

    # ──────────────────────────────────────────────
    #  Callback registration / Enregistrement callbacks
    # ──────────────────────────────────────────────

    def on_user_arrived(self, callback: Callable[[], None]) -> None:
        """
        Enregistre un callback déclenché quand l'utilisateur arrive.
        Register a callback fired when the user arrives (face detected after absence).

        Args:
            callback: No-argument callable.
        """
        with self._lock:
            self._on_arrived_callbacks.append(callback)
        print(f"[Presence] 📌 on_user_arrived callback registered ({len(self._on_arrived_callbacks)} total)")

    def on_user_left(self, callback: Callable[[], None]) -> None:
        """
        Enregistre un callback déclenché quand l'utilisateur part.
        Register a callback fired when the user leaves (no face for grace period).

        Args:
            callback: No-argument callable.
        """
        with self._lock:
            self._on_left_callbacks.append(callback)
        print(f"[Presence] 📌 on_user_left callback registered ({len(self._on_left_callbacks)} total)")

    def on_fatigue_detected(self, callback: Callable[[bool], None]) -> None:
        """Enregistre un callback pour la détection de fatigue."""
        with self._lock:
            self._on_fatigue_callbacks.append(callback)
        print(f"[Presence] 📌 on_fatigue_detected callback registered ({len(self._on_fatigue_callbacks)} total)")

    def on_mood_changed(self, callback: Callable[[str], None]) -> None:
        """Enregistre un callback pour le changement d'humeur."""
        with self._lock:
            self._on_mood_callbacks.append(callback)
        print(f"[Presence] 📌 on_mood_changed callback registered ({len(self._on_mood_callbacks)} total)")

    def on_intruder_detected(self, callback: Callable[[bytes, str], None]) -> None:
        """
        Enregistre un callback déclenché en mode Sentry quand un intrus est détecté.
        Register a callback fired when Sentry detects an intruder.

        Args:
            callback: callable(jpeg_bytes: bytes, timestamp: str)
                      jpeg_bytes = photo JPEG de l'intrus
                      timestamp  = horodatage lisible
        """
        with self._lock:
            self._on_intruder_callbacks.append(callback)
        print(f"[Presence] 📌 on_intruder_detected callback registered ({len(self._on_intruder_callbacks)} total)")

    # ──────────────────────────────────────────────
    #  Public API / API publique
    # ──────────────────────────────────────────────

    def start(self) -> bool:
        """
        Démarre le détecteur de présence dans un thread daemon.
        Start the presence detector in a daemon thread.

        Returns:
            True if started successfully, False otherwise.
        """
        with self._lock:
            if self._running:
                print("[Presence] ⚠️ Déjà en cours / Already running")
                return True

            if not self._cv2_available:
                print("[Presence] ❌ Cannot start: OpenCV not available")
                return False

            if not self._enabled:
                print("[Presence] ⚠️ Cannot start: detector is disabled. Call set_enabled(True) first.")
                return False

        # Initialize camera and cascade / Initialiser caméra et cascade
        if not self._init_camera():
            return False

        with self._lock:
            self._running = True

        self._thread = threading.Thread(
            target=self._detection_loop,
            name="PresenceDetector",
            daemon=True,
        )
        self._thread.start()
        print("[Presence] 🚀 Détecteur démarré / Detector started")
        return True

    def stop(self) -> None:
        """Arrête le détecteur proprement. / Stop the detector gracefully."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        # Wait for thread to finish / Attendre la fin du thread
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15.0)

        # Release camera / Libérer la caméra
        self._release_camera()

        print("[Presence] 🛑 Détecteur arrêté / Detector stopped")

    def is_present(self) -> bool:
        """
        Vérifie si l'utilisateur est présent.
        Check if the user is currently present.

        Returns:
            True if user is present (face detected recently).
        """
        with self._lock:
            return self._user_present

    def set_enabled(self, enabled: bool) -> None:
        """
        Active ou désactive le détecteur.
        Enable or disable the detector.

        When disabled, the detection loop stops and camera is released.
        When enabled, call start() to begin detection.

        Args:
            enabled: True to enable, False to disable.
        """
        with self._lock:
            was_enabled = self._enabled
            self._enabled = enabled

        if not enabled and was_enabled:
            # Disable → stop if running / Désactiver → arrêter si en cours
            self.stop()
            print("[Presence] 🔒 Detector DISABLED")
        elif enabled and not was_enabled:
            print("[Presence] 🔓 Detector ENABLED — call start() to begin detection")

    # ── Sentry Mode public API ────────────────────────────────────────────────

    def activate_sentry(self) -> str:
        """
        Active le mode Sentry.
        Tout visage détecté déclenchera : photo Telegram + verrouillage Windows.
        Active aussi le détecteur de présence si nécessaire.
        """
        with self._lock:
            self._sentry_mode = True
            self._sentry_last_alert = None

        print("[Presence] 🛡️ SENTRY MODE ACTIVATED — any face will trigger alert")

        # S'assurer que le détecteur est actif
        if not self._enabled:
            self.set_enabled(True)
        if not self._running and self._cv2_available:
            self.start()

        return "Sentry mode activated. Any face detected will trigger photo alert and workstation lock."

    def deactivate_sentry(self) -> str:
        """Désactive le mode Sentry."""
        with self._lock:
            self._sentry_mode = False
        print("[Presence] 🔓 SENTRY MODE DEACTIVATED")
        return "Sentry mode deactivated."

    @property
    def sentry_active(self) -> bool:
        with self._lock:
            return self._sentry_mode

    @property
    def last_seen(self) -> Optional[datetime]:
        """Dernière détection de l'utilisateur. / Last time user was detected."""
        with self._lock:
            return self._last_seen

    @property
    def enabled(self) -> bool:
        """Vérifie si le détecteur est activé. / Check if detector is enabled."""
        with self._lock:
            return self._enabled

    # ──────────────────────────────────────────────
    #  Camera management / Gestion de la caméra
    # ──────────────────────────────────────────────

    def _get_camera_source(self):
        """
        Récupère la source de la caméra depuis api_keys.json.
        Si non spécifié ou invalide, effectue une auto-détection et la sauvegarde.
        """
        from pathlib import Path
        import json
        import sys

        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).resolve().parent.parent

        config_path = base_dir / "config" / "api_keys.json"

        source = None
        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if "camera_index" in cfg:
                    source = cfg["camera_index"]
                    # Tenter de convertir en int (ex: 4), sinon laisser en string (ex: URL IP webcam)
                    try:
                        source = int(source)
                    except ValueError:
                        pass
        except Exception as e:
            print(f"[Presence] ⚠️ Erreur lors de la lecture de la config caméra : {e}")

        if source is not None:
            return source

        print("[Presence] 🔍 Aucune caméra configurée. Auto-détection de l'index de caméra...")
        import cv2
        best_index = 0
        for idx in range(6):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                continue
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                best_index = idx
                print(f"[Presence] ✅ Caméra détectée à l'index {idx}")
                break
        else:
            print("[Presence] ⚠️ Aucune caméra active trouvée par auto-détection. Utilisation de l'index 0 par défaut.")

        # Sauvegarder dans la config
        try:
            cfg = {}
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["camera_index"] = best_index
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
            print(f"[Presence] 💾 Index {best_index} sauvegardé dans la config.")
        except Exception as e:
            print(f"[Presence] ⚠️ Impossible de sauvegarder l'index caméra : {e}")

        return best_index

    def _load_owner_face(self) -> None:
        """Charge le visage du propriétaire depuis face.png pour Face ID."""
        try:
            import cv2
            from pathlib import Path
            import sys

            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path(__file__).resolve().parent.parent

            face_path = base_dir / "face.png"
            if not face_path.exists():
                print("[Presence] 📸 Face ID: face.png non trouvé. Face ID désactivé.")
                self._face_id_enabled = False
                return

            img = cv2.imread(str(face_path))
            if img is None:
                print(f"[Presence] 📸 Face ID: Impossible de lire {face_path}. Face ID désactivé.")
                self._face_id_enabled = False
                return

            # Détecter le visage dans face.png
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
            faces = self._cascade.detectMultiScale(gray, 1.1, 5)

            if len(faces) == 0:
                print(f"[Presence] 📸 Face ID: Aucun visage détecté dans face.png. Face ID désactivé.")
                self._face_id_enabled = False
                return

            x, y, w, h = faces[0]
            face_roi = gray[y:y+h, x:x+w]
            self._owner_face_template = cv2.resize(face_roi, (100, 100))
            self._orb = cv2.ORB_create()
            _, self._owner_descriptors = self._orb.detectAndCompute(self._owner_face_template, None)

            self._face_id_enabled = True
            print(f"[Presence] 📸 Face ID activé pour le protocole Sentry (face.png chargé avec succès)")

        except Exception as e:
            print(f"[Presence] ⚠️ Erreur lors du chargement de Face ID : {e}")
            self._face_id_enabled = False

    def _verify_face_id(self, frame, face_coords) -> bool:
        """
        Compare un visage détecté avec le visage du propriétaire.
        Retourne True si c'est le propriétaire (Owner match).
        """
        if not self._face_id_enabled or self._owner_face_template is None:
            return False

        try:
            import cv2
            x, y, w, h = face_coords
            # S'assurer que les coordonnées sont dans l'image
            h_img, w_img = frame.shape[:2]
            x_start = max(0, x)
            y_start = max(0, y)
            x_end = min(w_img, x + w)
            y_end = min(h_img, y + h)

            if (x_end - x_start) < 10 or (y_end - y_start) < 10:
                return False

            # Extraire et prétraiter le visage détecté
            face_roi = frame[y_start:y_end, x_start:x_end]
            gray_face = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
            gray_face = cv2.equalizeHist(gray_face)
            detected_face = cv2.resize(gray_face, (100, 100))

            # 1. Score par Template Matching (Normalized Cross-Correlation)
            res = cv2.matchTemplate(detected_face, self._owner_face_template, cv2.TM_CCOEFF_NORMED)
            _, score, _, _ = cv2.minMaxLoc(res)

            # 2. Score par ORB Features
            _, descriptors = self._orb.detectAndCompute(detected_face, None)
            good_matches_count = 0
            if descriptors is not None and self._owner_descriptors is not None:
                bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
                matches = bf.match(self._owner_descriptors, descriptors)
                good_matches = [m for m in matches if m.distance < 40]
                good_matches_count = len(good_matches)

            # Match si le template match est élevé et que les features ORB concordent
            is_match = (score > 0.48) and (good_matches_count >= 12)
            print(f"[Presence] 🛡️ Face ID matching: score={score:.3f}, good_matches={good_matches_count} -> Match: {is_match}")
            return is_match

        except Exception as e:
            print(f"[Presence] ⚠️ Erreur lors de la vérification Face ID : {e}")
            return False

    def _init_camera(self) -> bool:
        """
        Initialise la caméra et le classificateur Haar.
        Initialize camera and Haar cascade classifier.

        Returns:
            True if both initialized successfully.
        """
        try:
            import cv2

            # Load Haar cascade / Charger le cascade Haar
            cascade_path = cv2.data.haarcascades + self.CASCADE_FILE
            self._cascade = cv2.CascadeClassifier(cascade_path)

            # Load eye and smile cascades
            eye_path = cv2.data.haarcascades + "haarcascade_eye.xml"
            self._eye_cascade = cv2.CascadeClassifier(eye_path)
            
            smile_path = cv2.data.haarcascades + "haarcascade_smile.xml"
            self._smile_cascade = cv2.CascadeClassifier(smile_path)

            if self._cascade.empty():
                print(f"[Presence] ❌ Failed to load cascade: {cascade_path}")
                return False

            print(f"[Presence]   → Face, Eye and Smile Cascades loaded")

            # Charger Face ID une fois que le cascade classificateur est disponible
            self._load_owner_face()

            # Obtenir la source configurée ou auto-détectée
            source = self._get_camera_source()
            print(f"[Presence] 🔌 Ouverture de la caméra : {source}")

            if isinstance(source, int):
                self._camera = cv2.VideoCapture(source, cv2.CAP_DSHOW)
                if not self._camera.isOpened():
                    print(f"[Presence]   → DirectShow failed for index {source}, trying default backend...")
                    self._camera = cv2.VideoCapture(source)
            else:
                # C'est une URL string de flux réseau
                self._camera = cv2.VideoCapture(source)

            # Si la source configurée n'a pas pu être ouverte, essayer les autres indices en cascade (repli)
            if not self._camera or not self._camera.isOpened():
                print(f"[Presence] ⚠️ La caméra source {source} n'a pas pu être ouverte. Tentative de repli...")
                fallback_opened = False
                for fallback_idx in range(6):
                    if fallback_idx == source:
                        continue
                    print(f"[Presence]   → Essai de l'index de repli {fallback_idx}...")
                    cap = cv2.VideoCapture(fallback_idx, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        ret, frame = cap.read()
                        if ret and frame is not None:
                            self._camera = cap
                            fallback_opened = True
                            print(f"[Presence] ✅ Caméra de repli fonctionnelle trouvée à l'index {fallback_idx}")
                            break
                    cap.release()
                    
                    cap = cv2.VideoCapture(fallback_idx)
                    if cap.isOpened():
                        ret, frame = cap.read()
                        if ret and frame is not None:
                            self._camera = cap
                            fallback_opened = True
                            print(f"[Presence] ✅ Caméra de repli fonctionnelle trouvée à l'index {fallback_idx} (sans DSHOW)")
                            break
                    cap.release()

                if not fallback_opened:
                    print("[Presence] ❌ Aucune caméra disponible / Pas de webcam disponible")
                    self._camera = None
                    return False

            # Set low resolution for performance / Basse résolution pour les performances
            self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

            # Déterminer le nom ou l'indice final ouvert pour les logs
            final_source = source if self._camera.isOpened() else "fallback"
            print(f"[Presence]   → Camera opened ({final_source}, 320x240)")
            return True

        except Exception as e:
            print(f"[Presence] ❌ Camera init error: {e}")
            self._camera = None
            self._cascade = None
            return False

    def _release_camera(self) -> None:
        """Libère la caméra. / Release the camera."""
        try:
            if self._camera is not None:
                self._camera.release()
                self._camera = None
                print("[Presence]   → Camera released")
        except Exception as e:
            print(f"[Presence] ⚠️ Camera release error: {e}")

    # ──────────────────────────────────────────────
    #  Detection loop / Boucle de détection
    # ──────────────────────────────────────────────

    def _detection_loop(self) -> None:
        """
        Boucle principale de détection. Capture une image toutes les 10 secondes.
        Main detection loop. Captures one frame every 10 seconds.
        """
        print("[Presence] 🔁 Detection loop started")
        consecutive_failures: int = 0
        max_failures: int = 10  # Stop after 10 consecutive camera failures

        while True:
            # Check if still running / Vérifier si toujours en cours
            with self._lock:
                if not self._running or not self._enabled:
                    break

            try:
                res = self._capture_and_detect()

                if res is None:
                    # Camera error / Erreur caméra
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        print(f"[Presence] ❌ {max_failures} consecutive camera failures — stopping")
                        break
                else:
                    consecutive_failures = 0
                    self._update_presence_state(res)

            except Exception as e:
                print(f"[Presence] ❌ Detection loop error: {e}")
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    break

            # Sleep between captures / Dormir entre les captures
            # Use small increments for fast shutdown / Petits incréments pour arrêt rapide
            for _ in range(int(self.CAPTURE_INTERVAL)):
                with self._lock:
                    if not self._running:
                        break
                time.sleep(1.0)

        # Cleanup / Nettoyage
        with self._lock:
            self._running = False
        print("[Presence] 🔁 Detection loop ended")

    def _capture_and_detect(self) -> Optional[dict]:
        """
        Capture une image et détecte les visages, yeux et sourires.
        Stocke aussi la dernière frame pour le mode Sentry.
        Capture one frame and detect faces, eyes and smile.

        Returns:
            Dict {face: bool, eyes: bool, smile: bool, frame: np.ndarray} or None on error.
        """
        try:
            import cv2

            if self._camera is None or not self._camera.isOpened():
                return None

            # Capture frame / Capturer une image
            ret, frame = self._camera.read()
            if not ret or frame is None:
                print("[Presence] ⚠️ Failed to capture frame")
                return None

            # Stocker la dernière frame (utilisé par Sentry pour la photo)
            self._last_frame = frame.copy()

            # Convert to grayscale for face detection / Convertir en niveaux de gris
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)

            # Detect faces / Détecter les visages
            faces = self._cascade.detectMultiScale(
                gray,
                scaleFactor=1.3,
                minNeighbors=5,
                minSize=(30, 30),
                flags=cv2.CASCADE_SCALE_IMAGE,
            )

            face_found = len(faces) > 0
            eyes_found = False
            smile_found = False
            face_rects = list(faces) if face_found else []

            is_owner = False
            if face_found:
                # Dessiner un rectangle rouge autour du visage pour la photo Sentry
                for (x, y, w, h) in face_rects:
                    cv2.rectangle(self._last_frame, (x, y), (x+w, y+h), (0, 0, 255), 2)

                    roi_gray = gray[y:y+h, x:x+w]

                    # Détecter les yeux
                    if self._eye_cascade and not self._eye_cascade.empty():
                        eyes = self._eye_cascade.detectMultiScale(
                            roi_gray, scaleFactor=1.1, minNeighbors=5, minSize=(15, 15)
                        )
                        if len(eyes) >= 1:
                            eyes_found = True

                    # Détecter le sourire
                    if self._smile_cascade and not self._smile_cascade.empty():
                        smiles = self._smile_cascade.detectMultiScale(
                            roi_gray, scaleFactor=1.7, minNeighbors=22, minSize=(25, 25)
                        )
                        if len(smiles) >= 1:
                            smile_found = True

                # Vérifier Face ID si activé
                if self._face_id_enabled:
                    for rect in face_rects:
                        if self._verify_face_id(frame, rect):
                            is_owner = True
                            break

            return {
                "face": face_found,
                "eyes": eyes_found,
                "smile": smile_found,
                "is_owner": is_owner,
            }

        except Exception as e:
            print(f"[Presence] ⚠️ Capture/detect error: {e}")
            return None

    def _capture_jpeg_bytes(self) -> Optional[bytes]:
        """
        Encode la dernière frame capturée en JPEG et retourne les bytes.
        Encode the last captured frame as JPEG bytes.
        """
        try:
            import cv2
            if self._last_frame is None:
                return None
            # Ajouter un watermark horodaté
            frame = self._last_frame.copy()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(frame, f"[JARVIS SENTRY] {ts}", (4, frame.shape[0] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)
            ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ret:
                return bytes(buf)
            return None
        except Exception as e:
            print(f"[Presence] ⚠️ JPEG encode error: {e}")
            return None

    def _update_presence_state(self, detection: dict) -> None:
        """
        Met à jour l'état de présence, de fatigue, d'humeur et du mode Sentry.
        """
        now = datetime.now()
        face_detected = detection.get("face", False)
        eyes_detected = detection.get("eyes", False)
        smile_detected = detection.get("smile", False)

        with self._lock:
            was_present = self._user_present
            was_fatigued = self._fatigued
            old_mood = self._mood
            sentry_active = self._sentry_mode
            last_alert = self._sentry_last_alert

            callbacks_to_fire = []
            sentry_alert_needed = False

            # ── 1. Mode Sentry : tout visage = intrus (sauf si c'est le propriétaire) ──
            if sentry_active and face_detected:
                is_owner = detection.get("is_owner", False)
                if is_owner:
                    print(f"[Presence] 🛡️ SENTRY — Owner recognized at {now.strftime('%H:%M:%S')} — No alert.")
                else:
                    now_ts = time.monotonic()
                    cooldown_ok = (last_alert is None or
                                   (now_ts - last_alert) >= self.SENTRY_ALERT_COOLDOWN)
                    if cooldown_ok:
                        self._sentry_last_alert = now_ts
                        sentry_alert_needed = True
                        print(f"[Presence] 🚨 SENTRY ALERT — Intruder detected at {now.strftime('%H:%M:%S')}")

            # ── 2. Présence normale ───────────────────────────────────────────
            if not sentry_active:
                if face_detected:
                    self._last_face_detected = now
                    self._last_seen = now

                    if not was_present:
                        self._user_present = True
                        for cb in self._on_arrived_callbacks:
                            callbacks_to_fire.append((cb, ()))
                        print(f"[Presence] 👤 User ARRIVED at {now.strftime('%H:%M:%S')}")
                else:
                    if was_present and self._last_face_detected:
                        elapsed = (now - self._last_face_detected).total_seconds()
                        if elapsed >= self.GRACE_PERIOD:
                            self._user_present = False
                            for cb in self._on_left_callbacks:
                                callbacks_to_fire.append((cb, ()))
                            print(f"[Presence] 🚶 User LEFT at {now.strftime('%H:%M:%S')} (no face for {elapsed:.0f}s)")

            # ── 3. Fatigue & Humeur (seulement si présent, hors Sentry) ──────
            if not sentry_active and self._user_present and face_detected:
                if not eyes_detected:
                    self._consecutive_eyes_closed += 1
                    if self._consecutive_eyes_closed >= 3:
                        if not was_fatigued:
                            self._fatigued = True
                            for cb in self._on_fatigue_callbacks:
                                callbacks_to_fire.append((cb, (True,)))
                            print("[Presence] ⚠️ User FATIGUE detected (eyes closed)")
                else:
                    self._consecutive_eyes_closed = 0
                    if was_fatigued:
                        self._fatigued = False
                        for cb in self._on_fatigue_callbacks:
                            callbacks_to_fire.append((cb, (False,)))
                        print("[Presence] 💚 User recovered from fatigue")

                self._mood = "happy" if smile_detected else "focused/neutral"
                if self._mood != old_mood:
                    for cb in self._on_mood_callbacks:
                        callbacks_to_fire.append((cb, (self._mood,)))
                    print(f"[Presence] 😊 User mood: {self._mood}")
            elif not sentry_active:
                self._consecutive_eyes_closed = 0
                self._mood = "unknown"
                self._fatigued = False

            # Snapshot des callbacks Sentry pour exécution hors du lock
            sentry_cbs = list(self._on_intruder_callbacks) if sentry_alert_needed else []

        # ── Exécuter tous les callbacks hors du lock ──────────────────────────
        for cb, args in callbacks_to_fire:
            try:
                cb(*args)
            except Exception as e:
                print(f"[Presence] ⚠️ Callback error: {e}")

        # ── Sentry Alert : photo + lock (dans un thread séparé) ───────────────
        if sentry_alert_needed:
            ts_str = now.strftime("%Y-%m-%d %H:%M:%S")
            threading.Thread(
                target=self._sentry_alert_worker,
                args=(sentry_cbs, ts_str),
                daemon=True,
                name="SentryAlert"
            ).start()

    def _sentry_alert_worker(self, callbacks: list, timestamp: str) -> None:
        """
        Exécuté dans un thread daemon — prend la photo, notifie, verrouille.
        Runs in a daemon thread — captures photo, notifies callbacks, locks PC.
        """
        # 1. Encoder la frame en JPEG
        jpeg_bytes = self._capture_jpeg_bytes()

        # 2. Sauvegarder localement
        if jpeg_bytes:
            try:
                save_dir = self._sentry_save_path
                save_dir.mkdir(parents=True, exist_ok=True)
                fname = save_dir / f"intruder_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                fname.write_bytes(jpeg_bytes)
                print(f"[Presence] 📸 Intruder photo saved → {fname}")
            except Exception as e:
                print(f"[Presence] ⚠️ Failed to save intruder photo: {e}")

        # 3. Déclencher les callbacks (ex: Telegram)
        for cb in callbacks:
            try:
                cb(jpeg_bytes or b"", timestamp)
            except Exception as e:
                print(f"[Presence] ⚠️ Sentry callback error: {e}")

        # 4. Verrouiller le PC (délai de 1.5s pour laisser le temps à Telegram)
        try:
            import time as _t
            _t.sleep(1.5)
            ctypes.windll.user32.LockWorkStation()
            print("[Presence] 🔒 Workstation LOCKED by Sentry")
        except Exception as e:
            print(f"[Presence] ⚠️ Failed to lock workstation: {e}")

    # ──────────────────────────────────────────────
    #  Status / Statut
    # ──────────────────────────────────────────────

    def get_status(self) -> dict:
        """
        Retourne l'état complet du détecteur.
        Return full detector status.

        Returns:
            Dict with enabled, running, user_present, last_seen, opencv_available.
        """
        with self._lock:
            return {
                "enabled": self._enabled,
                "running": self._running,
                "user_present": self._user_present,
                "last_seen": self._last_seen.isoformat() if self._last_seen else None,
                "opencv_available": self._cv2_available,
                "fatigued": self._fatigued,
                "mood": self._mood,
            }


# ──────────────────────────────────────────────
#  Module-level convenience / Singleton pratique
# ──────────────────────────────────────────────

_default_detector: Optional[PresenceDetector] = None


def get_presence_detector() -> PresenceDetector:
    """
    Retourne le détecteur de présence singleton. / Return the singleton detector.
    Creates it on first call.
    """
    global _default_detector
    if _default_detector is None:
        _default_detector = PresenceDetector()
    return _default_detector


if __name__ == "__main__":
    # Quick test / Test rapide
    print("[Presence] 🧪 Test mode — will run for 60 seconds")
    print("[Presence] 🧪 Make sure a webcam is connected!\n")

    def on_arrived():
        print("  >>> 🟢 CALLBACK: User arrived!")

    def on_left():
        print("  >>> 🔴 CALLBACK: User left!")

    detector = PresenceDetector()
    detector.on_user_arrived(on_arrived)
    detector.on_user_left(on_left)

    # Must enable first / Doit d'abord activer
    detector.set_enabled(True)

    if detector.start():
        try:
            for i in range(60):
                time.sleep(1)
                if i % 10 == 0:
                    status = detector.get_status()
                    print(f"  [{i}s] Present: {status['user_present']}, "
                          f"Last seen: {status['last_seen']}")
        except KeyboardInterrupt:
            pass
        finally:
            detector.stop()
            print("[Presence] 🧪 Test complete")
    else:
        print("[Presence] 🧪 Could not start — check webcam and OpenCV installation")
