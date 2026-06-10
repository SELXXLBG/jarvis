"""
vector_memory.py — JARVIS Semantic Vector Memory (ChromaDB)
===========================================================
Système de mémoire vectorielle sémantique pour JARVIS.
Utilise ChromaDB pour le stockage persistant et sentence-transformers
pour les embeddings (all-MiniLM-L6-v2, ~80MB).

Collections:
  - conversations : résumés de conversations (timestamp, speaker)
  - facts         : faits appris sur l'utilisateur (sync depuis long_term.json)
  - documents     : contenu de documents / pages web analysés

Dégradation gracieuse : si chromadb ou sentence-transformers ne sont pas
installés, la classe s'instancie quand même mais toutes les méthodes
retournent des résultats vides et affichent des warnings.

Thread-safe avec threading.Lock, initialisation paresseuse du modèle.
"""

import json
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional


# ─── Résolution du répertoire de base du projet ─────────────────────
def _get_base_dir() -> Path:
    """Retourne le répertoire racine du projet JARVIS."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _get_base_dir()

# ─── Détection optionnelle des dépendances ──────────────────────────
_HAS_CHROMADB = False
_HAS_SENTENCE_TRANSFORMERS = False

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    _HAS_CHROMADB = True
except ImportError:
    chromadb = None  # type: ignore
    ChromaSettings = None  # type: ignore

try:
    from sentence_transformers import SentenceTransformer
    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    SentenceTransformer = None  # type: ignore


# ─── Constantes ─────────────────────────────────────────────────────
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
_COLLECTION_NAMES = ("conversations", "facts", "documents")
_MAX_FORMAT_CHARS = 1500
_MAX_BATCH_SIZE = 5000  # ChromaDB batch insert limit


class VectorMemory:
    """
    Mémoire vectorielle sémantique pour JARVIS.

    Utilise ChromaDB + sentence-transformers pour le stockage et la
    recherche sémantique. S'instancie même sans les dépendances :
    toutes les méthodes retournent des résultats vides dans ce cas.

    Attributes:
        available (bool): True si ChromaDB ET sentence-transformers sont installés.
    """

    def __init__(self, persist_dir: str | Path | None = None):
        """
        Initialise VectorMemory.

        Args:
            persist_dir: Répertoire de stockage ChromaDB.
                         Par défaut: <project_root>/memory/chromadb/
        """
        self._lock = Lock()
        self._model: Optional[object] = None  # Lazy-loaded SentenceTransformer
        self._model_loaded = False
        self._client: Optional[object] = None
        self._collections: dict = {}
        self._warned = False  # Évite le spam de warnings

        # Répertoire de persistance
        if persist_dir is None:
            self._persist_dir = BASE_DIR / "memory" / "chromadb"
        else:
            self._persist_dir = Path(persist_dir)

        # Vérifier la disponibilité des dépendances
        self.available = _HAS_CHROMADB and _HAS_SENTENCE_TRANSFORMERS

        if not self.available:
            missing = []
            if not _HAS_CHROMADB:
                missing.append("chromadb")
            if not _HAS_SENTENCE_TRANSFORMERS:
                missing.append("sentence-transformers")
            print(f"[VectorMemory] ⚠️ Dépendances manquantes: {', '.join(missing)}")
            print("[VectorMemory] ⚠️ pip install chromadb sentence-transformers")
            print("[VectorMemory] 💤 Mode dégradé — recherche sémantique désactivée")
            return

        # Initialiser ChromaDB (sans charger le modèle d'embeddings)
        self._init_chromadb()

    # ─── Initialisation ChromaDB ────────────────────────────────────

    def _init_chromadb(self) -> None:
        """Initialise le client ChromaDB et les collections."""
        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                ),
            )

            # Créer ou récupérer les collections
            for name in _COLLECTION_NAMES:
                self._collections[name] = self._client.get_or_create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"},
                )

            print(f"[VectorMemory] ✅ ChromaDB initialisé → {self._persist_dir}")
            stats = self.get_stats()
            total = sum(stats.values())
            if total > 0:
                print(f"[VectorMemory] 📊 {total} éléments en mémoire: {stats}")
            else:
                print("[VectorMemory] 📊 Base vide, synchronisation initiale...")
                self.sync_from_json()

        except Exception as e:
            print(f"[VectorMemory] ❌ Erreur init ChromaDB: {e}")
            print("[VectorMemory] 💤 Mode dégradé activé")
            self.available = False
            self._client = None
            self._collections = {}

    # ─── Chargement paresseux du modèle d'embeddings ────────────────

    def _ensure_model(self) -> bool:
        """
        Charge le modèle d'embeddings à la première utilisation (lazy init).
        Returns True si le modèle est prêt, False sinon.
        """
        if self._model_loaded:
            return self._model is not None

        # On ne charge qu'une seule fois
        self._model_loaded = True

        if not _HAS_SENTENCE_TRANSFORMERS:
            return False

        try:
            print(f"[VectorMemory] 🔄 Chargement du modèle {_EMBEDDING_MODEL_NAME}...")
            self._model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
            print(f"[VectorMemory] ✅ Modèle {_EMBEDDING_MODEL_NAME} chargé")
            return True
        except Exception as e:
            print(f"[VectorMemory] ❌ Erreur chargement modèle: {e}")
            self._model = None
            return False

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """
        Génère les embeddings pour une liste de textes.
        Retourne une liste de vecteurs (listes de floats).
        """
        if not self._ensure_model() or self._model is None:
            return []

        try:
            embeddings = self._model.encode(texts, show_progress_bar=False)
            return embeddings.tolist()
        except Exception as e:
            print(f"[VectorMemory] ❌ Erreur embedding: {e}")
            return []

    # ─── Helpers ────────────────────────────────────────────────────

    def _warn_unavailable(self, method_name: str) -> None:
        """Affiche un warning si le système n'est pas disponible (une seule fois)."""
        if not self._warned:
            print(f"[VectorMemory] ⚠️ {method_name}() ignoré — système non disponible")
            self._warned = True

    @staticmethod
    def _make_id(text: str, prefix: str = "") -> str:
        """Génère un ID déterministe basé sur le contenu (déduplication)."""
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if prefix:
            return f"{prefix}_{content_hash}"
        return content_hash

    @staticmethod
    def _now_iso() -> str:
        """Retourne le timestamp courant en ISO 8601."""
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def _get_collection(self, name: str):
        """Retourne une collection par nom, ou None."""
        return self._collections.get(name)

    # ─── Méthodes publiques — Stockage ──────────────────────────────

    def store_conversation(
        self,
        text: str,
        role: str = "user",
        metadata: dict | None = None,
    ) -> bool:
        """
        Indexe un tour de conversation.

        Args:
            text: Texte de la conversation.
            role: 'user' ou 'assistant'.
            metadata: Métadonnées additionnelles (optionnel).

        Returns:
            True si l'indexation a réussi, False sinon.
        """
        if not self.available:
            self._warn_unavailable("store_conversation")
            return False

        if not text or not text.strip():
            return False

        with self._lock:
            try:
                collection = self._get_collection("conversations")
                if collection is None:
                    return False

                # Construire les métadonnées
                meta = {
                    "role": role,
                    "timestamp": self._now_iso(),
                    "length": str(len(text)),
                }
                if metadata:
                    # ChromaDB ne supporte que str, int, float, bool comme valeurs
                    for k, v in metadata.items():
                        if isinstance(v, (str, int, float, bool)):
                            meta[k] = v
                        else:
                            meta[k] = str(v)

                # ID unique basé sur le contenu + timestamp (évite les doublons)
                doc_id = self._make_id(text, prefix="conv")

                # Générer l'embedding
                embeddings = self._embed([text])
                if not embeddings:
                    return False

                collection.upsert(
                    ids=[doc_id],
                    documents=[text],
                    embeddings=embeddings,
                    metadatas=[meta],
                )
                return True

            except Exception as e:
                print(f"[VectorMemory] ❌ store_conversation error: {e}")
                return False

    def store_fact(self, category: str, key: str, value: str) -> bool:
        """
        Indexe un fait depuis la mémoire.

        Args:
            category: Catégorie du fait (identity, preferences, etc.).
            key: Clé du fait.
            value: Valeur du fait.

        Returns:
            True si l'indexation a réussi, False sinon.
        """
        if not self.available:
            self._warn_unavailable("store_fact")
            return False

        if not value or not value.strip():
            return False

        with self._lock:
            try:
                collection = self._get_collection("facts")
                if collection is None:
                    return False

                # Texte combiné pour l'embedding (plus riche sémantiquement)
                combined_text = f"{category}: {key} = {value}"

                meta = {
                    "category": category,
                    "key": key,
                    "timestamp": self._now_iso(),
                }

                # ID déterministe basé sur category + key (déduplication naturelle)
                doc_id = self._make_id(f"{category}:{key}", prefix="fact")

                embeddings = self._embed([combined_text])
                if not embeddings:
                    return False

                collection.upsert(
                    ids=[doc_id],
                    documents=[combined_text],
                    embeddings=embeddings,
                    metadatas=[meta],
                )
                return True

            except Exception as e:
                print(f"[VectorMemory] ❌ store_fact error: {e}")
                return False

    def delete_fact(self, category: str, key: str) -> bool:
        """
        Supprime un fait de la mémoire sémantique vectorielle.

        Args:
            category: Catégorie du fait.
            key: Clé du fait.

        Returns:
            True si la suppression a réussi, False sinon.
        """
        if not self.available:
            self._warn_unavailable("delete_fact")
            return False

        with self._lock:
            try:
                collection = self._get_collection("facts")
                if collection is None:
                    return False

                doc_id = self._make_id(f"{category}:{key}", prefix="fact")
                collection.delete(ids=[doc_id])
                print(f"[VectorMemory] 🗑️ Fact supprimé de la mémoire vectorielle: {category}/{key}")
                return True
            except Exception as e:
                print(f"[VectorMemory] ❌ delete_fact error: {e}")
                return False

    def store_document(
        self,
        title: str,
        content: str,
        source: str = "",
    ) -> bool:
        """
        Indexe un document ou une page web.
        Les documents longs sont découpés en chunks pour une meilleure
        granularité de recherche.

        Args:
            title: Titre du document.
            content: Contenu textuel du document.
            source: URL ou chemin source (optionnel).

        Returns:
            True si l'indexation a réussi, False sinon.
        """
        if not self.available:
            self._warn_unavailable("store_document")
            return False

        if not content or not content.strip():
            return False

        with self._lock:
            try:
                collection = self._get_collection("documents")
                if collection is None:
                    return False

                # Découpage en chunks (~500 chars avec chevauchement)
                chunks = self._chunk_text(content, chunk_size=500, overlap=50)
                if not chunks:
                    return False

                # Générer les embeddings pour tous les chunks d'un coup
                # Préfixer le titre pour enrichir le contexte sémantique
                texts_to_embed = [f"{title}: {chunk}" for chunk in chunks]
                embeddings = self._embed(texts_to_embed)
                if not embeddings or len(embeddings) != len(chunks):
                    return False

                # Préparer les données pour l'insertion batch
                ids = []
                documents = []
                metadatas = []
                timestamp = self._now_iso()

                for i, chunk in enumerate(chunks):
                    doc_id = self._make_id(f"{title}:{i}:{chunk[:100]}", prefix="doc")
                    ids.append(doc_id)
                    documents.append(f"{title}: {chunk}")
                    metadatas.append({
                        "title": title,
                        "source": source or "",
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                        "timestamp": timestamp,
                    })

                # Insertion par batch (ChromaDB limite ~5000 par appel)
                for start in range(0, len(ids), _MAX_BATCH_SIZE):
                    end = start + _MAX_BATCH_SIZE
                    collection.upsert(
                        ids=ids[start:end],
                        documents=documents[start:end],
                        embeddings=embeddings[start:end],
                        metadatas=metadatas[start:end],
                    )

                print(f"[VectorMemory] 📄 Document indexé: '{title}' ({len(chunks)} chunks)")
                return True

            except Exception as e:
                print(f"[VectorMemory] ❌ store_document error: {e}")
                return False

    @staticmethod
    def _chunk_text(
        text: str,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> list[str]:
        """
        Découpe un texte en morceaux avec chevauchement.
        Essaie de couper aux limites de phrases pour préserver le sens.
        """
        if not text:
            return []

        text = text.strip()
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + chunk_size, text_len)

            # Essayer de couper à la fin d'une phrase
            if end < text_len:
                # Chercher le dernier point, !, ? ou retour à la ligne
                best_break = -1
                for sep in (".\n", "\n\n", ". ", "! ", "? ", "\n"):
                    pos = text.rfind(sep, start + chunk_size // 2, end)
                    if pos > best_break:
                        best_break = pos + len(sep)

                if best_break > start:
                    end = best_break

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            # Avancer avec chevauchement
            start = max(start + 1, end - overlap)

        return chunks

    # ─── Méthodes publiques — Recherche ─────────────────────────────

    def search(
        self,
        query: str,
        n_results: int = 5,
        collection: str | None = None,
    ) -> list[dict]:
        """
        Recherche sémantique dans les collections.

        Args:
            query: Requête de recherche en langage naturel.
            n_results: Nombre max de résultats par collection.
            collection: Nom de collection spécifique, ou None pour toutes.

        Returns:
            Liste de dicts {text, metadata, distance, collection}.
            Liste vide si le système n'est pas disponible.
        """
        if not self.available:
            self._warn_unavailable("search")
            return []

        if not query or not query.strip():
            return []

        with self._lock:
            try:
                # Générer l'embedding de la requête
                query_embedding = self._embed([query])
                if not query_embedding:
                    return []

                # Déterminer les collections à chercher
                if collection and collection in self._collections:
                    target_collections = {collection: self._collections[collection]}
                elif collection:
                    print(f"[VectorMemory] ⚠️ Collection inconnue: '{collection}'")
                    return []
                else:
                    target_collections = self._collections

                results = []

                for col_name, col_obj in target_collections.items():
                    try:
                        # Vérifier que la collection n'est pas vide
                        count = col_obj.count()
                        if count == 0:
                            continue

                        # Limiter n_results au nombre d'éléments disponibles
                        effective_n = min(n_results, count)

                        query_result = col_obj.query(
                            query_embeddings=query_embedding,
                            n_results=effective_n,
                            include=["documents", "metadatas", "distances"],
                        )

                        # Parser les résultats ChromaDB
                        if query_result and query_result.get("documents"):
                            docs = query_result["documents"][0]
                            metas = query_result["metadatas"][0] if query_result.get("metadatas") else [{}] * len(docs)
                            dists = query_result["distances"][0] if query_result.get("distances") else [0.0] * len(docs)

                            for doc, meta, dist in zip(docs, metas, dists):
                                results.append({
                                    "text": doc,
                                    "metadata": meta or {},
                                    "distance": round(float(dist), 4),
                                    "collection": col_name,
                                })

                    except Exception as e:
                        print(f"[VectorMemory] ⚠️ Erreur recherche '{col_name}': {e}")
                        continue

                # Trier par distance (plus proche = plus pertinent)
                results.sort(key=lambda r: r["distance"])

                # Limiter le nombre total de résultats
                return results[:n_results]

            except Exception as e:
                print(f"[VectorMemory] ❌ search error: {e}")
                return []

    # ─── Synchronisation depuis long_term.json ──────────────────────

    def sync_from_json(self, json_path: str | Path | None = None) -> int:
        """
        Lit long_term.json et indexe tous les faits dans la collection 'facts'.
        Utilise des IDs déterministes pour la déduplication.

        Args:
            json_path: Chemin vers le fichier JSON.
                       Par défaut: <project_root>/memory/long_term.json

        Returns:
            Nombre de faits indexés. 0 si erreur ou non disponible.
        """
        if not self.available:
            self._warn_unavailable("sync_from_json")
            return 0

        if json_path is None:
            json_path = BASE_DIR / "memory" / "long_term.json"
        else:
            json_path = Path(json_path)

        if not json_path.exists():
            print(f"[VectorMemory] ⚠️ Fichier introuvable: {json_path}")
            return 0

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                print("[VectorMemory] ⚠️ Format JSON invalide")
                return 0
        except (json.JSONDecodeError, OSError) as e:
            print(f"[VectorMemory] ❌ Erreur lecture JSON: {e}")
            return 0

        count = 0
        valid_categories = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}

        for category, entries in data.items():
            if category not in valid_categories:
                continue
            if not isinstance(entries, dict):
                continue

            for key, entry in entries.items():
                # Extraire la valeur (format: {"value": "...", "updated": "..."} ou string)
                if isinstance(entry, dict):
                    value = entry.get("value", "")
                elif isinstance(entry, str):
                    value = entry
                else:
                    continue

                if not value or not str(value).strip():
                    continue

                if self.store_fact(category, key, str(value)):
                    count += 1

        if count > 0:
            print(f"[VectorMemory] 🔄 Sync JSON → {count} faits indexés depuis {json_path.name}")

        return count

    # ─── Statistiques & Maintenance ─────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        """
        Retourne le nombre d'éléments par collection.

        Returns:
            Dict {collection_name: count}. Dict vide si non disponible.
        """
        if not self.available:
            return {}

        stats = {}
        with self._lock:
            for name, col in self._collections.items():
                try:
                    stats[name] = col.count()
                except Exception as e:
                    print(f"[VectorMemory] ⚠️ Erreur stats '{name}': {e}")
                    stats[name] = -1

        return stats

    def clear_collection(self, name: str) -> bool:
        """
        Vide une collection spécifique.

        Args:
            name: Nom de la collection à vider.

        Returns:
            True si la collection a été vidée, False sinon.
        """
        if not self.available:
            self._warn_unavailable("clear_collection")
            return False

        if name not in _COLLECTION_NAMES:
            print(f"[VectorMemory] ⚠️ Collection inconnue: '{name}'")
            return False

        with self._lock:
            try:
                if self._client is None:
                    return False

                # Supprimer et recréer la collection
                self._client.delete_collection(name=name)
                self._collections[name] = self._client.get_or_create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"},
                )
                print(f"[VectorMemory] 🗑️ Collection '{name}' vidée")
                return True

            except Exception as e:
                print(f"[VectorMemory] ❌ clear_collection error: {e}")
                return False

    def reset_all(self) -> bool:
        """
        Réinitialise toutes les collections (⚠️ supprime toutes les données).

        Returns:
            True si la réinitialisation a réussi, False sinon.
        """
        if not self.available:
            return False

        success = True
        for name in _COLLECTION_NAMES:
            if not self.clear_collection(name):
                success = False

        if success:
            print("[VectorMemory] 🔄 Toutes les collections réinitialisées")

        return success


# ═════════════════════════════════════════════════════════════════════
# Helper de formatage — injection dans le system prompt
# ═════════════════════════════════════════════════════════════════════

def format_vector_results(results: list[dict]) -> str:
    """
    Formate les résultats de recherche vectorielle en texte lisible
    pour injection dans le system prompt.

    Args:
        results: Liste de dicts {text, metadata, distance, collection}
                 retournés par VectorMemory.search().

    Returns:
        Texte formaté, max ~1500 caractères.
        Chaîne vide si aucun résultat.
    """
    if not results:
        return ""

    lines = ["Relevant memories:"]
    current_length = len(lines[0])

    for r in results:
        text = r.get("text", "").strip()
        meta = r.get("metadata", {})
        collection = r.get("collection", "unknown")
        distance = r.get("distance", 0.0)

        # Ignorer les résultats trop éloignés (seuil cosine distance)
        if distance > 1.5:
            continue

        # Construire la ligne selon le type de collection
        if collection == "facts":
            category = meta.get("category", "")
            key = meta.get("key", "")
            # Extraire la valeur du texte (format "category: key = value")
            value = text
            if "=" in text:
                value = text.split("=", 1)[1].strip()
            date_str = meta.get("timestamp", "")[:10]
            if date_str:
                line = f"  - [{category}] {key}: {value} ({date_str})"
            else:
                line = f"  - [{category}] {key}: {value}"

        elif collection == "conversations":
            role = meta.get("role", "?")
            date_str = meta.get("timestamp", "")[:10]
            # Tronquer le texte de conversation
            snippet = text[:120].replace("\n", " ")
            if len(text) > 120:
                snippet += "…"
            line = f"  - [{role}, {date_str}] {snippet}"

        elif collection == "documents":
            title = meta.get("title", "doc")
            source = meta.get("source", "")
            snippet = text[:120].replace("\n", " ")
            if len(text) > 120:
                snippet += "…"
            source_hint = f" ({source})" if source else ""
            line = f"  - [doc: {title}{source_hint}] {snippet}"

        else:
            snippet = text[:120].replace("\n", " ")
            line = f"  - [{collection}] {snippet}"

        # Vérifier la limite de caractères
        if current_length + len(line) + 1 > _MAX_FORMAT_CHARS:
            lines.append("  - ...")
            break

        lines.append(line)
        current_length += len(line) + 1

    # Ne retourner rien si on n'a que le header
    if len(lines) <= 1:
        return ""

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════
# Point d'entrée pour tests rapides
# ═════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  JARVIS VectorMemory — Test rapide")
    print("=" * 60)

    vm = VectorMemory()

    if not vm.available:
        print("\n❌ Dépendances manquantes. Installez-les avec:")
        print("   pip install chromadb sentence-transformers")
        print("\nTest du mode dégradé:")
        print(f"  store_conversation: {vm.store_conversation('test')}")
        print(f"  search: {vm.search('test')}")
        print(f"  get_stats: {vm.get_stats()}")
        print("✅ Mode dégradé fonctionne correctement (pas de crash)")
    else:
        print("\n📝 Test d'indexation...")
        vm.store_fact("identity", "name", "Tony")
        vm.store_fact("preferences", "language", "Python")
        vm.store_fact("projects", "jarvis", "Building an AI assistant")
        vm.store_conversation("Hey JARVIS, can you help me with my project?", role="user")
        vm.store_conversation("Of course, sir. What do you need?", role="assistant")

        print("\n🔍 Test de recherche...")
        results = vm.search("What is the user's name?", n_results=3)
        for r in results:
            print(f"  [{r['collection']}] dist={r['distance']:.4f} → {r['text'][:80]}")

        print(f"\n📊 Stats: {vm.get_stats()}")

        # Test du formatage
        print("\n📋 Formatage:")
        formatted = format_vector_results(results)
        print(formatted if formatted else "  (aucun résultat)")

        # Test sync JSON
        json_path = BASE_DIR / "memory" / "long_term.json"
        if json_path.exists():
            print(f"\n🔄 Sync depuis {json_path.name}...")
            count = vm.sync_from_json()
            print(f"  → {count} faits synchronisés")

    print("\n✅ Tests terminés")
