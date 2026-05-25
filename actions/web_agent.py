"""
Web Agent — Autonomous browser automation (Text-Only Token Minimalist Version)
Uses Playwright to browse locally and extracts internal DOM text.
Sends ZERO images to Gemini API to prevent 429 quota exhaustion.
"""

import os
import sys
import asyncio
import json
import traceback
from pathlib import Path
from playwright.async_api import async_playwright
from google import genai
from google.genai import types

def _get_api_key() -> str:
    try:
        config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f).get("gemini_api_key", "")
    except Exception:
        return ""

# On utilise le modèle Flash standard (très rapide, très bon marché en tokens textuels)
MODEL_ID = "gemini-2.5-flash"
MAX_TURNS = 6

class WebAgentText:
    """Agent web très économe en tokens. Extrait le texte HTML en local."""

    def __init__(self, player=None):
        self.client = genai.Client(api_key=_get_api_key())
        self.browser = None
        self.context = None
        self.page = None
        self.player = player

    def _log(self, msg: str):
        print(msg)
        if self.player and hasattr(self.player, "write_web_log"):
            self.player.write_web_log(msg)

    async def _extract_page_content(self) -> str:
        """Extrait le texte visible de la page via Javascript local."""
        if not self.page:
            return "No page active."
        try:
            # Script JS injecté pour récupérer le texte brut sans les pubs/scripts = 0 tokens image
            js = """
            () => {
                let elements = document.querySelectorAll('h1, h2, h3, h4, p, span, a, button, li');
                let text = [];
                for (let el of elements) {
                    if (el.innerText && el.innerText.trim().length > 0) {
                        text.push(el.tagName + ': ' + el.innerText.trim());
                    }
                }
                return text.slice(0, 150).join('\\n'); // Limite aux 150 premiers éléments clés
            }
            """
            content = await self.page.evaluate(js)
            return content
        except Exception as e:
            return f"Error extracting page: {e}"

    async def run_task(self, prompt: str, show_result_on_screen: bool = False, headless: bool = True) -> str:
        self._log(f"[WebAgent] 🌐 Recherche Économe: {prompt}")
        final_summary = "Impossible de compléter la recherche."

        try:
            async with async_playwright() as p:
                self.browser = await p.chromium.launch(headless=headless)
                self.context = await self.browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                self.page = await self.context.new_page()

                # On définit explicitement les outils que Gemini peut appeler
                def search_google(query: str) -> str:
                    """Search Google for the query and navigate to the results page."""
                    self._log(f"[WebAgent] ➡️ Action: Google Search '{query}'")
                    return "search_queued"

                def go_to_url(url: str) -> str:
                    """Navigate explicitly to a web URL."""
                    self._log(f"[WebAgent] ➡️ Action: Go To '{url}'")
                    return "url_queued"

                def click_on_text(link_text: str) -> str:
                    """Click on a link containing the given text."""
                    self._log(f"[WebAgent] ➡️ Action: Click '{link_text}'")
                    return "click_queued"

                tools = [search_google, go_to_url, click_on_text]

                # Préparation du modèle
                chat = self.client.chats.create(
                    model=MODEL_ID,
                    config=types.GenerateContentConfig(
                        temperature=0.3,
                        tools=tools,
                        system_instruction=(
                            "You are a web research agent. Use the provided tools to navigate the web. "
                            "You will receive the text content of the page after each action. "
                            "When you have found the answer, DO NOT call any tool, simply write your final summary and conclusion."
                        )
                    )
                )

                # Démarrage de la boucle conversationnelle asynchrone
                last_url = "about:blank"
                current_prompt = f"Goal: {prompt}\n\nPlease start by searching or navigating to the desired website."

                for turn in range(MAX_TURNS):
                    self._log(f"[WebAgent] --- Turn {turn+1}/{MAX_TURNS} ---")
                    
                    try:
                        # Exécution asynchrone transparente de chat.send_message
                        response = await asyncio.to_thread(chat.send_message, current_prompt)
                    except Exception as e:
                        self._log(f"[WebAgent] ❌ Api Error: {e}")
                        break

                    # Si Gemini a décidé d'appeler l'une de nos fonctions (tools)
                    if response.function_calls:
                        for fc in response.function_calls:
                            name = fc.name
                            args = fc.args
                            
                            try:
                                if name == "search_google":
                                    query = args.get("query", "")
                                    await self.page.goto(f"https://www.google.com/search?q={query}")
                                elif name == "go_to_url":
                                    url = args.get("url", "")
                                    if not url.startswith("http"): url = "https://" + url
                                    await self.page.goto(url)
                                elif name == "click_on_text":
                                    text = args.get("link_text", "")
                                    # Clique local avec Playwright
                                    await self.page.get_by_text(text).first.click(timeout=3000)
                                    await self.page.wait_for_load_state("domcontentloaded")
                                    await asyncio.sleep(1) # Laisse le temps au js
                            except Exception as e:
                                self._log(f"[WebAgent] ⚠️ Erreur lors de l'action {name}: {e}")

                        # On extrait le texte localement pour le prochain tour
                        page_text = await self._extract_page_content()
                        current_url = self.page.url
                        self._log(f"[WebAgent] 📄 Analyzed page: {current_url}")
                        current_prompt = (
                            f"Action succeeded. Current URL: {current_url}\n"
                            f"Page Text Snippet:\n{page_text}\n\n"
                            f"If you have the answer, output the final summary. Otherwise, take another action."
                        )

                    else:
                        # Gemini a fini et répond en texte direct !
                        self._log(f"[WebAgent] ✅ Objectif atteint.")
                        final_summary = response.text
                        break

                if show_result_on_screen and self.page.url and "about:blank" not in self.page.url:
                    import webbrowser
                    self._log(f"[WebAgent] 🌐 Ouverture du résultat sur le navigateur visible: {self.page.url}")
                    webbrowser.open(self.page.url)

                await self.browser.close()
                self._log("[WebAgent] 🔒 Browser closed")

        except Exception as e:
            traceback.print_exc()
            self._log(f"[WebAgent] ❌ Fatal error: {e}")
            final_summary = f"Web agent failed: {e}"

        return final_summary

def web_agent(parameters: dict, player=None, speak=None) -> str:
    prompt = parameters.get("prompt", "")
    show_result = parameters.get("show_result_on_screen", False)
    
    if not prompt:
        return "No prompt provided."

    headless_mode = True
    if player and getattr(player, "show_web_agent", False):
        headless_mode = False

    if player:
        player.write_log("SYS: 🌐 Web Agent Local text-mode started...")

    try:
        agent = WebAgentText(player=player)
        result = asyncio.run(agent.run_task(prompt, show_result_on_screen=show_result, headless=headless_mode))

        if player:
            player.write_log(f"SYS: 🌐 Web Agent done: {result[:50]}...")
            
        return result
    except Exception as e:
        return f"Error: {e}"
