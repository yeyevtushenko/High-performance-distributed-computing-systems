# Лабораторна робота 2. Метрики сервісу і межа масштабованості

Звіт: [REPORT.md](REPORT.md).

## Запуск

```bash
cp .env.example .env
docker compose up -d --build
```

Сервіс доступний на `http://localhost:8080`, Prometheus на `http://localhost:9090`. Чотири екземпляри:

```bash
docker compose up -d --scale web=4 && docker compose restart balancer
```

## Вимірювання

```bash
python3 -m venv .venv && .venv/bin/pip install matplotlib
scripts/run_series.sh
.venv/bin/python loadgen/analyze.py
scripts/sigterm_check.sh
```
