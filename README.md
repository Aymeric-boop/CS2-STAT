# CS2 Stats Dashboard

Dashboard de statistiques local pour **Counter-Strike 2**, alimenté en temps réel par le **Game State Integration (GSI)** de Valve. Toutes les données sont stockées localement dans une base SQLite — aucune dépendance externe, aucun compte requis.

---

## Fonctionnalités

### Vue d'ensemble
- **K/D Ratio** global avec décompte kills/morts
- **Win Rate** avec répartition victoires / défaites / égalités (donut chart)
- **HS%** (pourcentage headshots) avec total headshots
- **ADR** (Average Damage per Round)
- **KPR** (Kills per Round)
- **Taux de survie** — pourcentage de rounds survécus
- **Temps total en partie** et **temps total passé sur CS2** (toutes sessions)
- **Records personnels** : meilleur K/D sur un match, meilleur ADR, K/D CT, K/D T
- **Forme récente** — les 15 dernières parties sous forme de pastilles V/D/N (avec tooltip map + K/D)
- **Séries** — série actuelle + record de victoires consécutives + record de défaites
- **Graphique K/D** sur les 50 dernières parties (couleur = résultat, ligne de moyenne)
- **Graphique ADR** par partie (couleur = niveau : faible / correct / excellent)
- **Graphique HS%** par partie
- **Multi-kills** : total 1K (entry), doubles, triples, quadruples, ACE

### Historique des parties
- 50 dernières parties avec :
  - Résultat (V/D/N), map, mode de jeu
  - Score final CT-T
  - Kills / Deaths / Assists
  - K/D ratio (coloré : vert ≥1.3 / rouge <0.9)
  - HS%, ADR, MVPs, durée, date et heure

### Maps
- Cartes par map avec win rate (barre colorée), résultats V/D/N
- K/D moyen, ADR moyen, temps total joué
- Répartition rounds gagnés CT vs T par map
- Graphique win rate par map (horizontal)
- Graphique K/D moyen par map (horizontal)

### Armes
- Top 12 armes par kills avec barre double (kills en orange, headshots en or)
- Pourcentage HS affiché par arme
- Donut de répartition des kills par arme (top 8)
- Graphique HS% par arme (coloré selon le niveau)

### CT / T (Comparaison par côté)
- K/D, win rate rounds, ADR, taux de survie — séparément pour CT et T
- Graphique comparatif K/D CT vs T
- Graphique comparatif round win rate CT vs T

### Économie
- Win rate par type d'achat : **Pistolet** / **Éco** / **Force** / **Semi** / **Full buy**
- ADR moyen par type d'achat
- Graphiques comparatifs

### Live (en partie)
- Bandeau vert affiché automatiquement quand CS2 est actif
- Round actuel, équipe, kills, morts, arme active, argent, barre de vie
- Poll toutes les **3 secondes**

### Comptes Steam (onglet ⚙)
- Liste blanche de Steam IDs — seuls ces comptes sont enregistrés
- Filtre automatique anti-spectateur : si tu regardes quelqu'un, ses données sont ignorées
- Plusieurs comptes supportés (pas de limite)
- Interface d'ajout / suppression directement dans le dashboard
- **Liste vide = tout accepter** (mode setup, à éviter en jeu)

---

## Installation

### Prérequis
- Python 3.9+
- CS2 installé via Steam

### 1. Cloner le dépôt
```bash
git clone https://github.com/ton-pseudo/cs2-dashboard.git
cd cs2-dashboard
```

### 2. Installer les dépendances Python
```bash
pip install -r requirements.txt
```

### 3. Copier le fichier de configuration GSI
Copier `gamestate_integration_dashboard.cfg` dans le dossier `cfg` de CS2 :

**Windows :**
```
C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\cfg\
```

**Linux (Proton) :**
```
~/.steam/steam/steamapps/common/Counter-Strike Global Offensive/game/csgo/cfg/
```

### 4. Lancer le serveur
```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Pour qu'il tourne en arrière-plan (Windows) :
```bash
start /B python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

### 5. Ouvrir le dashboard
Ouvre [http://localhost:8000](http://localhost:8000) dans ton navigateur.

### 6. Configurer ton Steam ID (recommandé)
- Va dans l'onglet **⚙ Comptes**
- Ajoute ton Steam ID (17 chiffres, ex : `76561198XXXXXXXXX`)
- Voir ci-dessous comment le trouver

---

## Trouver son Steam ID

**Méthode 1 — Console CS2 :**
1. Lance CS2
2. Ouvre la console (touche `~` ou `ù`)
3. Tape `status`
4. Cherche la ligne avec ton pseudo — le Steam ID est au format `76561198XXXXXXXXX`

**Méthode 2 — Web :**
1. Va sur [steamidfinder.com](https://www.steamidfinder.com)
2. Entre ton pseudo Steam ou l'URL de ton profil

**Méthode 3 — Paramètres Steam :**
1. Steam → Paramètres → Compte
2. Clique sur "Voir les détails du compte" → le Steam ID est affiché

---

## Architecture

```
cs2-dashboard/
├── app.py                                    ← Serveur FastAPI (GSI + API REST)
├── dashboard.html                            ← Interface web (HTML/CSS/JS)
├── gamestate_integration_dashboard.cfg       ← Config GSI CS2
├── config.json                               ← Steam IDs autorisés (généré au 1er lancement)
├── cs2_stats.db                             ← Base SQLite (créée automatiquement)
└── requirements.txt
```

### Base de données (SQLite)

| Table | Contenu |
|---|---|
| `matches` | 1 ligne par match : kills, deaths, assists, ADR, HS, MVPs, résultat, score, durée, côtés CT/T |
| `rounds` | 1 ligne par round : kills, HS, dégâts, équipement, argent, type éco, survie, résultat |
| `weapon_stats` | Kills + headshots par arme par match |
| `sessions` | Temps passé sur CS2 par session |

### API endpoints

| Méthode | Endpoint | Description |
|---|---|---|
| `POST` | `/gsi` | Reçoit les données temps réel de CS2 |
| `GET` | `/api/stats` | Statistiques globales agrégées |
| `GET` | `/api/matches` | Historique des matchs (`?limit=50`) |
| `GET` | `/api/performance` | Données par match pour les graphiques |
| `GET` | `/api/maps` | Stats agrégées par map |
| `GET` | `/api/weapons` | Stats par arme |
| `GET` | `/api/multikills` | Compteurs multi-kills |
| `GET` | `/api/economy` | Stats par type d'achat |
| `GET` | `/api/sides` | Comparaison CT vs T |
| `GET` | `/api/streaks` | Séries de victoires/défaites |
| `GET` | `/api/form` | 15 derniers matchs (pastilles forme) |
| `GET` | `/api/live` | État en temps réel (round, arme, HP…) |
| `GET` | `/api/config` | Voir la config (Steam IDs) |
| `POST` | `/api/config/add_id` | Ajouter un Steam ID |
| `POST` | `/api/config/remove_id` | Supprimer un Steam ID |

---

## Problèmes connus & limites

**ADR :** calculé uniquement à partir des dégâts enregistrés via GSI (`round_totaldmg`). Cette valeur est fournie par CS2 en fin de round — elle peut être absente sur certaines versions ou modes.

**GSI et spectateur :** le filtre anti-spectateur compare le `steamid` du `provider` (machine locale) et du `player` (joueur observé). Si tu joues sur ta propre machine, ils sont identiques et tout fonctionne.

**Multi-comptes :** tous les matchs sont stockés dans la même base. Si tu veux séparer les stats par compte, tu peux créer des dossiers séparés avec leur propre `cs2_stats.db`.

**Modes supportés :** compétitif, casual, deathmatch, wingman. Les stats peuvent être incomplètes sur des modes non standard.

---

## Réinitialiser les données

```bash
# Supprimer uniquement les données (la config reste)
del cs2_stats.db      # Windows
rm cs2_stats.db       # Linux/Mac
```

La base est recréée automatiquement au prochain lancement du serveur.

---

## Stack technique

- **Backend :** Python · FastAPI · uvicorn · SQLite
- **Frontend :** HTML/CSS/JS vanilla · Chart.js 4.4 · Google Fonts (Barlow Condensed, Share Tech Mono, Rajdhani)
- **Protocole :** CS2 GSI (HTTP POST JSON, aucun mod requis)

---

## Licence

MIT — libre d'utilisation, de modification et de redistribution.
