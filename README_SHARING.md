# 🤖 Guide de Configuration et de Démarrage — J.A.R.V.I.S

Bienvenue dans le dépôt de **JARVIS** ! Ce guide vous explique comment installer, configurer et lancer cet assistant IA doté de fonctionnalités avancées (vision, diagnostics réseau, contrôle Telegram, protocoles d'urgence et synthèse hors-ligne).

---

## 📋 Prérequis et Installation

### 1. Cloner/Télécharger le projet
Assurez-vous que tous les fichiers du dépôt sont extraits dans un dossier local (par exemple `C:\JARVIS`).

### 2. Installer Python 3.12+ et les dépendances
Ouvrez un terminal Windows (PowerShell) dans le dossier du projet et exécutez les commandes suivantes pour créer l'environnement virtuel et installer les packages :

```powershell
# Créer l'environnement virtuel
python -m venv .venv

# Activer l'environnement
.venv\Scripts\Activate.ps1

# Installer les dépendances
pip install -r requirements.txt
```

---

## ⚙️ Configuration Initiale (`config/api_keys.json`)

Le fichier de configuration de base se trouve dans `config/api_keys.json`. Remplissez les champs requis avec vos propres clés :

```json
{
    "gemini_api_key": "VOTRE_CLE_GEMINI_API",
    "freellmapi_key": "",
    "telegram_bot_token": "VOTRE_TOKEN_BOT_TELEGRAM",
    "telegram_allowed_ids": [],
    "camera_index": 0
}
```

### Détails des champs :
* **`gemini_api_key`** : Votre clé d'API Google Gemini (gratuite sur Google AI Studio).
* **`telegram_bot_token`** : Le token obtenu auprès de [@BotFather](https://t.me/BotFather) sur Telegram si vous souhaitez utiliser l'accès distant.
* **`telegram_allowed_ids`** : Laissez la liste vide `[]` au premier démarrage. Dès que vous enverrez un message à votre bot, **JARVIS enregistrera automatiquement votre ID** dans ce fichier pour sécuriser la communication.
* **`camera_index`** : L'index de votre caméra. Laissez à `0` ou `4` (si utilisation de DroidCam). Le programme détecte automatiquement les caméras fonctionnelles si celle par défaut ne répond pas.

---

## 🛡️ Fonctionnalités Spéciales

### 1. Mode Sentry avec Face ID (Reconnaissance Faciale)
Le mode Sentry surveille votre écran/webcam en votre absence. Si un visage inconnu est détecté, JARVIS prend une photo, l'envoie sur Telegram, et verrouille automatiquement la session Windows.
* **Pour activer Face ID (Optionnel)** : Placez une photo de votre visage nommée **`face.png`** à la racine du dossier JARVIS.
* **Fonctionnement** : Au démarrage, JARVIS détecte et enregistre la structure de votre visage. Lorsque vous passez devant la caméra en mode Sentry, JARVIS vous reconnaît (via Template Matching + descripteurs ORB) et **n'émet pas d'alerte**. Si c'est quelqu'un d'autre, le PC se verrouille instantanément.

### 2. Accès à distance via Telegram
Vous pouvez interagir avec JARVIS depuis votre téléphone :
* **/status** : Rapport complet des performances système (CPU, RAM, GPU, processus).
* **/screenshot** : Capture en temps réel de votre écran de PC.
* **/wake** / **/memory** : Réveiller JARVIS ou consulter ses notes mémorisées.
* Envoyez n'importe quel texte pour lui donner des ordres directs à distance !

### 3. Protocoles d'Urgence
Dites simplement à JARVIS de lancer un protocole :
* **Clean Slate** : *"Jarvis, active le protocole Clean Slate"* (Ferme les navigateurs, efface le presse-papier, vide la corbeille, verrouille le PC).
* **House Party** : *"Jarvis, lance le protocole House Party"* (Ouvre VSCode, Chrome, Discord, Spotify et lance la musique).
* **Sentry** : *"Jarvis, active le mode Sentry"* (Démarre la surveillance active).

### 4. Diagnostics Réseau
Demandez : *"Jarvis, teste ma connexion internet"* ou *"Jarvis, analyse la sécurité du réseau"*.
* Effectue un speedtest précis sans dépendance lourde.
* Détecte les connexions suspectes ou les ports ouverts vulnérables.

### 5. Égaliseur FFT et Mode Offline
* **Égaliseur Réel** : Les barres du HUD s'animent en temps réel au rythme de votre voix et de celle de JARVIS (analyse spectrale FFT).
* **TTS Offline** : Si internet coupe ou si l'API Gemini est surchargée, JARVIS bascule automatiquement sur la synthèse vocale locale de Windows (`pyttsx3`) pour vous avertir et rester fonctionnel.

---

## 🚀 Lancement

Double-cliquez simplement sur le script batch **`LANCER_JARVIS.bat`** situé à la racine du projet. JARVIS chargera son interface HUD et entrera en mode veille, à l'écoute de son mot de réveil *"Jarvis"*.
