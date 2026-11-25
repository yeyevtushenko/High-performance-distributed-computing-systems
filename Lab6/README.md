# Лабораторна робота 6. Розподілене навчання нейромережі за даними на CPU

Звіт: [REPORT.md](REPORT.md).

## Запуск

```bash
python3 -m venv .venv && .venv/bin/pip install torch numpy scikit-learn matplotlib
mkdir -p data
curl -L -o data/bloodmnist.npz "https://zenodo.org/records/10519652/files/bloodmnist.npz?download=1"
scripts/run_all.sh
```
