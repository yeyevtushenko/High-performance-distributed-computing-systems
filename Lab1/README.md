# Лабораторна робота 1. CRUD-сервіс донацій крові

Звіт: [REPORT.md](REPORT.md).

## Запуск

```bash
cp .env.example .env
docker compose up -d --build
```

Сервіс доступний через балансувальник на `http://localhost:8080`. Два вебінстанси:

```bash
docker compose up -d --scale web=2
```

## Навантаження

```bash
python3 loadgen/loadgen.py seed
python3 loadgen/loadgen.py open --rate 100 --out results/open_n1_r100_1.json
```

Повна серія: `scripts/run_series.sh`, закритий контур: `scripts/closed_loop.sh`, зведена таблиця: `python3 loadgen/summarize.py`.
