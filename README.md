# JARVIS x Gradium : le kit de départ du Projet Jarvis

Un assistant vocal qui tourne sur ton PC et pilote **Claude Code** à la voix.

Tu cliques sur l'orbe et tu parles. **Gradium** transcrit ce que tu dis, **Claude Code** s'en occupe sur ta machine (il cherche, crée des fichiers, analyse...), et Gradium te lit sa réponse avec une voix naturelle. Tu vois chaque action de Claude Code en direct dans le panneau de droite, tu peux lui couper la parole, et il se souvient de toute la conversation.

C'est une base volontairement simple. Le but du hackathon, c'est d'en faire **ton** Jarvis : un métier, des outils, une vraie utilité.

## Ce qu'il te faut

1. **Python 3.10 ou plus récent** : https://www.python.org/downloads/
2. **Claude Code** installé et connecté :
   ```bash
   npm install -g @anthropic-ai/claude-code
   claude        # la première fois : connexion à ton compte Claude
   ```
3. **Une clé API Gradium** : crée ton compte sur https://gradium.ai, puis une clé dans Gradium Studio. Participants du hackathon : active tes crédits avec le code reçu dans le kit (guide : https://gradium.link/how-to-redeem).
4. **Chrome, Edge ou Firefox récent**, et idéalement **un casque**.

Fonctionne sur Windows, Mac et Linux.

## Lancer

**Windows** : double-clique sur `start.bat`.
**Mac / Linux** : `./start.sh` dans un terminal.

La première fois, le script installe tout et crée le fichier `.env` : mets-y ta clé (`GRADIUM_API_KEY=...`) puis relance. La page http://127.0.0.1:8000 s'ouvre toute seule : clique sur l'orbe, autorise le micro, parle.

<details>
<summary>Installation à la main</summary>

```bash
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows : copy .env.example .env
python main.py
```
</details>

À essayer :

- « Crée un fichier avec trois idées de posts LinkedIn sur l'IA. »
- « Cherche la météo à Paris aujourd'hui. »
- « Écris un script Python qui compte les mots d'un fichier texte. »
- « Stop » ou « Annule » pour arrêter une tâche en cours.

Les fichiers créés par Claude Code atterrissent dans `workspace/`. Pas de micro sous la main ? Écris dans le champ sous l'orbe.

## Comment ça marche

```
 micro ──> navigateur ──WebSocket──> serveur local (main.py)
                                          │
                Gradium STT : transcription + détection de fin de phrase     voice.py
                                          │
                Claude Code, processus ouvert en fond (claude -p, stream-json)   agents/
                   │ texte de la réponse          │ actions (outils)
                   ▼                              ▼
                Gradium TTS : voix en streaming   panneau « Activité » en direct
                   │
 haut-parleur <── navigateur
```

| Fichier | Rôle |
|---|---|
| `main.py` | Le serveur : relie le micro, Gradium et l'agent |
| `voice.py` | Écoute (STT) et parole (TTS) avec Gradium |
| `agents/` | Les agents pilotables : Claude Code, Codex, Gemini |
| `jarvis.md` | La personnalité de Jarvis (son prompt système) |
| `mcp.json` | Les outils MCP donnés à Claude Code (vide au départ) |
| `workspace/` | Le dossier où l'agent travaille |
| `static/` | La page : orbe, transcription, panneau d'activité |

Ce qui se passe à chaque phrase :

- Gradium transcrit en continu. Son **VAD sémantique** estime toutes les 80 ms si tu as fini de parler. Quand c'est le cas, la phrase part à Claude Code.
- **Claude Code reste ouvert en fond** pendant toute la session : pas de temps de démarrage, et il se souvient de tout. La conversation reprend même après un redémarrage du serveur. Le bouton « Nouvelle conversation » lui fait tout oublier.
- Sa réponse est **lue mot à mot pendant qu'il l'écrit**. Ses actions (recherche web, fichier écrit...) s'affichent en direct dans le panneau.
- Si tu parles pendant que Jarvis parle, **il se tait** (barge-in). Pour arrêter une tâche en cours, dis « stop » ou clique sur Stop.

## Sécurité : ce que Claude Code a le droit de faire

Par défaut, c'est verrouillé :

| Sans demander | Avec ton autorisation (carte dans le panneau) |
|---|---|
| Lire et modifier les fichiers de `workspace/` | Commandes système (shell, PowerShell) |
| Recherche web, lire une page web | Lire ou écrire hors de `workspace/` |
| | Outils MCP |

Quand Claude Code veut faire quelque chose de sensible, Jarvis te dit « J'ai besoin de ton autorisation » et une carte **Autoriser / Refuser** apparaît. Sans réponse au bout de 2 minutes, c'est refusé.

Autres protections :

- Le serveur n'écoute que sur ta machine (127.0.0.1), et seule la page de Jarvis peut s'y connecter.
- Jarvis ignore tes réglages Claude Code globaux (hooks, permissions, MCP) : seuls `workspace/.claude/settings.json` et `mcp.json` comptent.

Pour donner plus de droits sans confirmation, ajoute des règles dans `workspace/.claude/settings.json` (doc : https://code.claude.com/docs/en/settings#permission-settings) :

```json
{ "permissions": { "allow": ["Bash(git status)", "Bash(npm run test:*)"] } }
```

**Ne mets pas ce serveur sur internet** : il fait agir Claude Code sur ta machine.

## Par où commencer à le transformer

**Sa personnalité.** Modifie `jarvis.md` : assistant d'agent immobilier, de plombier, coach sportif... Ensuite, clique sur « Nouvelle conversation » : Claude Code fige son prompt au début d'une conversation.

**Ses outils.** Le plus simple, c'est un serveur MCP dans `mcp.json` : agenda, emails, Notion, base de données, domotique... Par exemple :

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/moi/Documents"]
    }
  }
}
```

Tu peux aussi lui écrire des scripts ou des skills dans `workspace/`. Il les trouvera.

**Sa vitesse.** Le temps de réponse dépend surtout du modèle. `JARVIS_MODEL=haiku` répond le plus vite. `sonnet` ou `opus` sont plus capables pour les vraies tâches.

**Sa voix.** Choisis-la dans la liste en haut à gauche, ou fixe-la avec `JARVIS_VOICE`. Tu peux aussi cloner ta voix avec 10 secondes d'audio, ou en créer une à partir d'une description, dans Gradium Studio, puis mettre son identifiant dans `JARVIS_VOICE`.

**L'écoute.** Si Jarvis te coupe la parole, monte `JARVIS_END_OF_TURN` (0.9). S'il met trop de temps à répondre, baisse-le (0.6). Ajoute les noms propres ou le jargon de ton projet dans `JARVIS_KEYWORDS` pour qu'ils soient bien transcrits.

**L'interface.** Tout est dans `static/index.html`, sans étape de build. L'orbe vient de [AI-Orb](https://github.com/weeeha/AI-Orb) : essaie les finitions `ink`, `pearl`, `vessel`, `plasma`.

## Utiliser Codex, Gemini CLI ou un autre agent

Mets `JARVIS_AGENT=codex` ou `JARVIS_AGENT=gemini` dans `.env`. L'outil doit être installé et connecté (`codex login`, ou `gemini` une première fois).

| Agent | Installation | Remarques |
|---|---|---|
| `claude` | `npm i -g @anthropic-ai/claude-code` | Testé. Streaming mot à mot, autorisations dans le panneau, interruption propre |
| `codex` | `npm i -g @openai/codex` | Expérimental. Sandbox limitée au workspace. Réponse lue d'un bloc (pas de streaming) |
| `gemini` | `npm i -g @google/gemini-cli` | Expérimental. Mode `auto_edit` : le shell est désactivé |

Pour brancher un autre CLI, crée une classe dans `agents/` :
- Hérite de `OneShotAgent` (un processus par phrase) et écris deux méthodes : `command()` (la ligne de commande) et `parse()` (traduire sa sortie JSON en événements). `codex.py` fait 50 lignes : copie-le.
- Ou hérite d'`Agent` pour un processus permanent, comme `claude_code.py`.
- Ajoute ta classe dans `agents/__init__.py`.

## Réglages (.env)

| Variable | Défaut | Rôle |
|---|---|---|
| `GRADIUM_API_KEY` | (obligatoire) | Clé Gradium |
| `GRADIUM_BASE_URL` | serveurs Gradium | `https://eu.api.gradium.ai/api/` pour rester en Europe |
| `JARVIS_AGENT` | `claude` | `claude`, `codex` ou `gemini` |
| `JARVIS_MODEL` | celui de l'agent | Ex. `haiku`, `sonnet`, `opus` pour Claude |
| `JARVIS_VOICE` | `Marius` | Nom d'une voix ou identifiant |
| `JARVIS_GREETING` | `Bonjour, je t'écoute.` | Phrase d'accueil (vide = aucune) |
| `JARVIS_WORKDIR` | `./workspace` | Dossier de travail de l'agent |
| `JARVIS_END_OF_TURN` | `0.8` | Seuil de fin de phrase (0.5 à 0.95) |
| `JARVIS_BARGE_IN` | `true` | Couper la parole à Jarvis en parlant |
| `JARVIS_KEYWORDS` | `Jarvis` | Mots à bien reconnaître, séparés par des virgules |
| `JARVIS_PERMISSION_TIMEOUT` | `120` | Secondes avant refus automatique |
| `JARVIS_PORT` | `8000` | Port du serveur local |
| `JARVIS_OPEN_BROWSER` | `true` | Ouvrir la page au démarrage |
| `DEBUG` | `false` | Logs détaillés dans le terminal |

## Bon à savoir

- **Crédits Gradium** : la transcription coûte 3 crédits par seconde de micro ouvert, la voix 1 crédit par caractère. Une heure de session coûte environ 11 000 crédits d'écoute, plus la voix. Clique sur l'orbe pour couper le micro quand tu ne parles plus à Jarvis.
- **Offre gratuite de Gradium** : limitée à 1 500 caractères de voix par session. Avec les crédits du hackathon, tu es large.
- **Casque recommandé.** Sans casque, laisse « Anti-écho » coché, sinon Jarvis risque de s'entendre et de se couper lui-même. Si ça arrive quand même, mets `JARVIS_BARGE_IN=false`.
- **Une seule page à la fois** : si tu ouvres un deuxième onglet, le premier est déconnecté. Claude Code, lui, continue son travail.
- **Ne commite jamais ton `.env`.** Il est déjà dans le `.gitignore`.

## Dépannage

| Problème | Solution |
|---|---|
| « Clé Gradium manquante » | Mets `GRADIUM_API_KEY` dans `.env` et relance |
| « Commande claude introuvable » | `npm install -g @anthropic-ai/claude-code`, puis rouvre le terminal |
| Claude Code répond « Not logged in » | Lance `claude` une fois dans un terminal pour te connecter |
| Micro refusé | Autorise le micro pour 127.0.0.1 dans les réglages du navigateur |
| Jarvis se coupe tout seul | Casque, ou « Anti-écho » coché, ou `JARVIS_BARGE_IN=false` |
| Il répond avant que tu aies fini | `JARVIS_END_OF_TURN=0.9` |
| Le détail d'une erreur | `DEBUG=true` dans `.env`, puis regarde le terminal |

## La doc

- Gradium : https://docs.gradium.ai (toute la doc en un fichier, à donner à ton agent : https://docs.gradium.ai/llms.txt)
- Transcription temps réel et fin de tour : https://docs.gradium.ai/guides/recipes/turn-taking
- Voix en streaming : https://docs.gradium.ai/guides/recipes/llm-to-tts
- Voix : https://docs.gradium.ai/guides/voices/flagship-voices, clonage : https://docs.gradium.ai/guides/voices/custom-voices
- Mots-clés (noms propres, jargon) : https://docs.gradium.ai/guides/recipes/keyword-boosting
- Claude Code en mode headless : https://code.claude.com/docs/en/headless
- MCP dans Claude Code : https://code.claude.com/docs/en/mcp

## Licence

MIT. L'orbe vient de [AI-Orb](https://github.com/weeeha/AI-Orb) de Nick Vyhouski (MIT, licence dans `static/vendor/ai-orb/`).
