# SEO V2 Engine

Moteur d’analyse SEO du produit SEO V2. Il expose une API FastAPI, orchestre les crawls et les traitements asynchrones, puis produit des résultats exploitables par le dashboard.

## Responsabilités

- exploration et extraction de pages web ;
- analyse technique et détection de priorités ;
- exécution des tâches en arrière-plan avec Celery et Redis ;
- génération de rapports et intégration avec le dashboard ;
- classification et réécriture assistées, activées uniquement lorsqu’une clé de service est configurée.

## Prérequis

- Python 3.12
- Poetry
- PostgreSQL et Redis pour l’exécution complète

## Démarrer

```bash
poetry install
Copy-Item .env.example .env
poetry run uvicorn app.main:app --reload
```

Dans un second terminal, démarrez le worker :

```bash
poetry run celery -A app.worker.celery_worker worker -l info
```

Les variables d’environnement sont définies dans `.env.example`. Le fichier `.env` est local et exclu de Git.

## Tests

```bash
poetry run pytest
```

## Structure

- `app/api` — routes FastAPI
- `app/services` — crawl, audit et intégrations
- `app/worker` — tâches Celery
- `tests` — tests unitaires et d’intégration
