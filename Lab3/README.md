# Лабораторна робота 3. Сервіс у Kubernetes з автомасштабуванням і без нього

Звіт: [REPORT.md](REPORT.md).

## Запуск

Потрібні Docker, k3d, kubectl і Helm з репозиторієм `prometheus-community`.

```bash
cp .env.example .env
scripts/deploy.sh
```

Сервіс доступний на `http://localhost:8080`, Prometheus на `http://localhost:9090`. Видалення кластера: `scripts/destroy.sh`.

## Вимірювання

```bash
python3 -m venv .venv && .venv/bin/pip install matplotlib
scripts/run_all.sh
.venv/bin/python loadgen/analyze.py
```
