import time
import subprocess
import platform

try:
    import pyautogui
except ImportError:
    pass

def play_on_spotify(query: str, player=None) -> str:
    if player:
        player.write_log(f"[Spotify] Searching & Playing: {query}")

    system = platform.system()
    
    # Format query for URI
    safe_query = query.replace(" ", "%20")
    uri = f"spotify:search:{safe_query}"
    
    try:
        if system == "Windows":
            subprocess.Popen(["start", uri], shell=True)
        elif system == "Darwin":
            subprocess.run(["open", uri])
        elif system == "Linux":
            subprocess.Popen(["xdg-open", uri])
            
        time.sleep(4.0)  # Wait for Spotify to open and load the search results
        
        # Give focus and attempt to play the top result
        # Usually, after a search via URI, the first result is highlighted or reachable by a couple of Tabs
        pyautogui.press('tab')
        time.sleep(0.2)
        pyautogui.press('tab')
        time.sleep(0.2)
        pyautogui.press('enter')
        time.sleep(0.5)
        pyautogui.press('tab') # Sometimes an extra tab is needed depending on the view
        time.sleep(0.2)
        pyautogui.press('enter')
        
    except Exception as e:
        print(f"[Spotify] ❌ Error playing: {e}")
        return f"Failed to play on Spotify, sir: {e}"
        
    return f"I have opened Spotify and started playing {query}, sir."

def spotify_control(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "play").lower().strip()
    
    if player:
        player.write_log(f"[Spotify] Action: {action}")
        
    print(f"[Spotify] ▶️ Action: {action} Params: {params}")
    
    if action == "play":
        query = params.get("query", "").strip()
        if not query:
            return "Please tell me what you'd like to play on Spotify, sir."
        return play_on_spotify(query, player)
        
    return f"Unknown Spotify action: '{action}'."
