<div align="center">

Міністерство освіти і науки України<br>
Національний технічний університет України «Київський політехнічний інститут імені Ігоря Сікорського»

Факультет біомедичної інженерії<br>
Кафедра біомедичної кібернетики

# Звіт

**про виконання лабораторної роботи №5<br>
з дисципліни «Високопродуктивні розподілені обчислювальні системи»**

Тема роботи: «Проєктування потокобезпечної структури даних»<br>
Варіант № 8

</div>

**Виконав:** студент гр. ЗК-61мп Євтушенко Єгор<br>
**Прийняв:** Матвійчук О. В.

<div align="center">

Київ<br>
КПІ ім. Ігоря Сікорського<br>
2026

</div>

---

## Мета роботи

Спроєктувати потокобезпечну структуру даних за варіантом і побудувати стенд, у якому з нею одночасно працюють n потоків: потоки з непарними номерами записують, потоки з парними номерами читають. Синхронізувати доступ до структури глобальним замком і виміряти швидкість запису і читання. Замінити глобальний замок на замок читання-запису (RWLock) і повторити виміри. Обчислити метрики прискорення і порівняти, як змінюється доступ до даних для потоків запису і потоків читання за двох способів синхронізації.

## Варіант

| № | Структура | n | Variant_parametr_3 |
|---|---|---|---|
| 8 | С8 | 12 | 100 000 |

| Структура | Операція запису | Операція читання | Інваріанти для перевірки стенду |
|---|---|---|---|
| С8. Двійкове дерево пошуку записів, ключі від 0 до 100 000 − 1 | insert(k) і remove(k) порівну, видалення позначає вузол видаленим | contains(k) і range_count(lo, hi) порівну | ключі лівого піддерева менші, правого більші; ключ присутній не більше одного разу |

n_w = ⌈12 / 2⌉ = 6, n_r = ⌊12 / 2⌋ = 6. Ключі вибираються рівномірно з діапазону 0 … 99 999, перед прогоном дерево заповнюється половиною простору ключів.

## Середовище

Apple M3 Pro (5 продуктивних і 6 енергоефективних ядер, 11 логічних), 18 ГБ памʼяті, macOS 27.0.1, CPython 3.13.0 (`results/info.json`). Прогони виконано на машині без інших навантажень: кластери попередніх робіт видалено. Python виконує байт-код під глобальним замком інтерпретатора (GIL), тому в кожен момент байт-код виконує лише один з 12 потоків.

## Крок 1. Стенд

Структура в `bst.py` (додаток А), стенд у `bench.py` (додаток Б).

Дерево складається з вузлів `Node` з полями `key`, `left`, `right` і `deleted`:

- `insert(k)` спускається від кореня; якщо ключ знайдено і вузол позначено видаленим, знімає позначку, інакше додає новий лист;
- `remove(k)` знаходить вузол і ставить `deleted = True`, вузол лишається в дереві;
- `contains(k)` повертає `True`, якщо вузол є і не позначений видаленим;
- `range_count(lo, hi)` обходить дерево зі стеком, заходить лише в піддерева, що перетинаються з `[lo, hi]`, і рахує невидалені вузли в діапазоні. Ширина діапазону 100 ключів: `lo` рівномірно з 0 … 99 900, `hi = lo + 99`.

Стенд будує дерево вставкою 50 000 різних випадкових ключів (зерно 0) і запускає 12 потоків з номерами 1 … 12. Потоки з непарними номерами виконують лише запис, по черзі `insert` і `remove`; потоки з парними номерами лише читання, по черзі `contains` і `range_count`. Операції йдуть без пауз, аргументи генеруються в потоці до виклику. Для кожної операції фіксуються моменти `t_call` до захоплення замка, `t_acq` після захоплення і `t_ret` після звільнення. Вимірювання триває D = 10 с після прогріву 1 с.

- Операція входить у N ролі, якщо завершилась у вікні вимірювання; її тривалість τ = t_ret − t_call йде в перцентилі p50 і p99 ролі (p_q = τ₍⌈q·N⌉₎).
- Час очікування t_wait = t_acq − t_call враховується в межах вікна для кожної операції, очікування якої перетинає вікно, включно з операціями, що чекали замок з прогріву і отримали його вже після кінця вікна. Так голодування потоку, який за все вимірювання не отримав замка, дає w = 1.

Після прогону стенд перевіряє інваріанти: обхід дерева у порядку зростання дає строго зростаючу послідовність ключів (тобто ключі лівого піддерева менші, правого більші) і кожен ключ трапляється один раз. Стенд друкує операції кожного потоку, X_w, X_r, p50 і p99 кожної ролі, найбільший t_wait і w_w, w_r. Спосіб синхронізації вибирається параметром `--sync global|rw`. Режим `x1` вимірює X_1: один потік по черзі виконує запис і читання в пропорції n_w : n_r = 6 : 6, тобто 1 : 1.

Кожен прогін повторено 3 рази, у таблицях медіана трьох прогонів (`scripts/run_all.sh`, `summarize.py`, `results/summary.json`).

Таблиця 1. Стенд

| n | n_w | n_r | Мова і версія | Логічних ядер | X_1, оп/с | Інваріанти після прогонів кроків 2 і 3 |
|---|---|---|---|---|---|---|
| 12 | 6 | 6 | Python 3.13.0 (CPython) | 11 | глобальний замок 180 548,8; RWLock 165 969,4 | виконуються у всіх 6 прогонах |

Інваріанти після прогонів (`results/run_*.json`): ключі в порядку обходу строго зростають і не повторюються; вузлів 96 732, 96 761, 95 753 після прогонів з глобальним замком і 60 091, 51 963, 51 871 після прогонів з RWLock.

## Крок 2. Глобальний замок

`GlobalLock` містить один `threading.Lock`, і кожна операція запису і читання захоплює його на весь час роботи з деревом.

Таблиця 2. Глобальний замок

| Роль | Операцій за D | X, оп/с | p50, мкс | p99, мкс | Найбільший t_wait, мс | w | cv | t_cs, мкс |
|---|---|---|---|---|---|---|---|---|
| Запис | 468 124 | 46 812,4 | 1,08 | 2,08 | 680,42 | 0,981 | 0,282 | 1,035 |
| Читання | 1 089 281 | 108 928,1 | 9,54 | 20,12 | 552,49 | 0,830 | 0,116 | 4,889 |
| Разом | 1 548 886 | 154 888,6 | | | | | | 2,963 |

t_cs,w і t_cs,r виміряно в однопотокових прогонах (`bench.py tcs`, медіана трьох) як середній час від отримання до звільнення замка. t̄_cs = (6 · 1,035 + 6 · 4,889) / 12 = 2,963 мкс, межа 1 / t̄_cs = 337 439 оп/с.

## Крок 3. RWLock

`RWLock` реалізовано на `threading.Condition` з лічильником читачів і прапорцем писця:

- `acquire_read` чекає, поки є активний писець, і збільшує лічильник читачів;
- `release_read` зменшує лічильник і будить усіх, коли він стає нулем;
- `acquire_write` чекає, поки немає ні писця, ні читачів, і ставить прапорець;
- `release_write` знімає прапорець і будить усіх.

Політика замка: перевага читачам. Новий читач входить, поки замок тримає хоч один читач, навіть якщо на замок чекає писець; писець отримує замок лише тоді, коли читачів стає нуль.

Перевірка, що читання не змінює полів структури (`bench.py readonly`, `results/readonly.json`): 6 потоків читання 3 секунди працюють з деревом під RWLock, до і після прогону обчислюється хеш кортежу `(key, deleted, id(left), id(right))` усіх вузлів.

```json
{"reader_threads": 6, "read_ops": 302866, "snapshot_before": -8687921309534208270, "snapshot_after": -8687921309534208270, "unchanged": true}
```

Таблиця 3. RWLock

| Роль | Операцій за D | X, оп/с | p50, мкс | p99, мкс | Найбільший t_wait, мс | w | cv |
|---|---|---|---|---|---|---|---|
| Запис | 0 | 0 | — | — | 11 054,57 | 1,000 | — |
| Читання | 888 734 | 88 873,4 | 11,37 | 337,92 | 94,98 | 0,134 | 0,030 |
| Разом | 888 734 | 88 873,4 | | | | | |

Жодна операція запису не завершилась у вікні вимірювання в жодному з трьох прогонів. Шість потоків читання виконують операції без пауз, тому лічильник читачів майже ніколи не падає до нуля, і писці чекають на замок від прогріву до кінця вимірювання: найбільший t_wait 11,05 с більший за D, бо включає секунду прогріву. Для запису p50, p99 і cv не визначені.

Таблиця 4. Операції потоків за D секунд (медіана трьох прогонів)

| Потік | Роль | Глобальний замок | RWLock |
|---|---|---|---|
| 1 | запис | 80 523 | 0 |
| 2 | читання | 195 818 | 147 282 |
| 3 | запис | 69 931 | 0 |
| 4 | читання | 191 220 | 147 317 |
| 5 | запис | 82 360 | 0 |
| 6 | читання | 164 345 | 151 652 |
| 7 | запис | 73 855 | 0 |
| 8 | читання | 183 804 | 151 404 |
| 9 | запис | 67 244 | 0 |
| 10 | читання | 173 782 | 149 996 |
| 11 | запис | 72 564 | 0 |
| 12 | читання | 190 170 | 141 182 |

## Крок 4. Метрики прискорення

Таблиця 5. Метрики прискорення

| Метрика | Формула | Значення |
|---|---|---|
| S(n), глобальний замок | X(G) / X_1 | 154 888,6 / 180 548,8 = 0,858 |
| E(n), глобальний замок | S(n) / n | 0,071 |
| S(n), RWLock | X(RW) / X_1 | 88 873,4 / 165 969,4 = 0,535 |
| E(n), RWLock | S(n) / n | 0,045 |
| S_w | X_w(RW) / X_w(G) | 0 / 46 812,4 = 0 |
| S_r | X_r(RW) / X_r(G) | 88 873,4 / 108 928,1 = 0,816 |
| S_Σ | X(RW) / X(G) | 0,574 |
| ρ, завантаженість глобального замка | X(G) · t̄_cs | 154 888,6 · 2,963 · 10⁻⁶ = 0,459 |

X(G) = 154 889 оп/с менший за межу 1 / t̄_cs = 337 439 оп/с, і глобальний замок утримується лише 46 % часу. Решту часу займають генерація аргументів, облік часу і перемикання потоків: через GIL потік, що отримав замок, може чекати ще й на GIL. Тому 12 потоків виконують менше операцій, ніж один потік (S < 1 з обома замками).

## Крок 5. Порівняння доступу до даних

Таблиця 6. Порівняння доступу до даних. Відношення RW / G обчислено діленням значення з RWLock на значення з глобальним замком; для p50, p99, h і cv запису з RWLock значення не визначене, тому відношення не записано.

| Показник | Глобальний замок: запис | Глобальний замок: читання | RWLock: запис | RWLock: читання | RW / G: запис | RW / G: читання |
|---|---|---|---|---|---|---|
| X, оп/с | 46 812,4 | 108 928,1 | 0 | 88 873,4 | 0 | 0,816 |
| x, на один потік ролі, оп/с | 7 802,1 | 18 154,7 | 0 | 14 812,2 | 0 | 0,816 |
| p50, мкс | 1,08 | 9,54 | — | 11,37 | — | 1,192 |
| p99, мкс | 2,08 | 20,12 | — | 337,92 | — | 16,79 |
| h = p99 / p50 | 1,92 | 2,11 | — | 29,88 | — | 14,16 |
| Найбільший t_wait, мс | 680,42 | 552,49 | 11 054,57 | 94,98 | 16,25 | 0,172 |
| w | 0,981 | 0,830 | 1,000 | 0,134 | 1,020 | 0,162 |
| cv | 0,282 | 0,116 | — | 0,030 | — | 0,261 |
| φ, частка ролі в усіх операціях | 0,302 | 0,698 | 0 | 1,000 | 0 | 1,433 |

Заміна глобального замка на RWLock з перевагою читачам повністю зупинила запис: X_w упала з 46 812 до 0 оп/с, частка очікування писців зросла до 1, а найбільший t_wait запису став більшим за D. Це голодування писця, повʼязане з полем «частка очікування» і «найбільший t_wait».

Для читання частка очікування впала з 0,83 до 0,13, найбільший t_wait з 552 до 95 мс, а рівномірність між потоками читання покращилась: cv з 0,116 до 0,030. Проте швидкість читання не зросла, а впала до 0,82 від швидкості під глобальним замком, а хвіст тривалості виріс у 17 разів (p99 338 мкс проти 20 мкс, h з 2,1 до 29,9). Читачі можуть тримати замок одночасно, але через GIL виконують байт-код по черзі, тому спільний режим не дає паралельності, а лише додає накладні витрати `Condition` на кожну операцію, і операція читання чекає вже не на замок, а на GIL, через що зростає її хвіст.

## Висновки

У цій лабораторній роботі ми написали двійкове дерево пошуку з логічним видаленням і стенд, на якому 6 потоків записують і 6 читають. З глобальним замком дерево виконує 154 889 операцій за секунду, з них 30 % записів, а один потік виконує 180 549 операцій за секунду. Тобто 12 потоків працюють навіть повільніше за один (S = 0,86), бо через GIL одночасно виконується лише один потік, а замок зайнятий лише 46 % часу від межі 337 439 операцій за секунду.

RWLock з перевагою читачам повністю заблокував запис: за 10 секунд у трьох прогонах не завершилась жодна операція запису, писці чекали замок більше 11 секунд. Для потоків читання частка очікування замка впала з 0,83 до 0,13, а розподіл операцій між ними став рівнішим, але швидкість читання все одно знизилась до 0,82 від швидкості з глобальним замком, а p99 зріс з 20 до 338 мкс. Сумарна пропускна здатність з RWLock менша в 1,7 раза. Інваріанти дерева виконуються після всіх прогонів, а читання не змінює полів дерева.

## ДОДАТОК А. Лістинг `bst.py`

```python
import threading


class Node:
    __slots__ = ("key", "left", "right", "deleted")

    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None
        self.deleted = False


class GlobalLock:
    policy = "один мʼютекс threading.Lock для всіх операцій"

    def __init__(self):
        self._lock = threading.Lock()

    def acquire_read(self):
        self._lock.acquire()

    def release_read(self):
        self._lock.release()

    def acquire_write(self):
        self._lock.acquire()

    def release_write(self):
        self._lock.release()


class RWLock:
    policy = "перевага читачам: новий читач входить, поки замок тримає хоч один читач; писець чекає, поки читачів не стане нуль"

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False

    def acquire_read(self):
        with self._cond:
            while self._writer:
                self._cond.wait()
            self._readers += 1

    def release_read(self):
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self):
        with self._cond:
            while self._writer or self._readers:
                self._cond.wait()
            self._writer = True

    def release_write(self):
        with self._cond:
            self._writer = False
            self._cond.notify_all()


class BST:
    def __init__(self):
        self.root = None

    def insert(self, key):
        if self.root is None:
            self.root = Node(key)
            return True
        node = self.root
        while True:
            if key == node.key:
                if node.deleted:
                    node.deleted = False
                    return True
                return False
            if key < node.key:
                if node.left is None:
                    node.left = Node(key)
                    return True
                node = node.left
            else:
                if node.right is None:
                    node.right = Node(key)
                    return True
                node = node.right

    def remove(self, key):
        node = self.root
        while node is not None:
            if key == node.key:
                if node.deleted:
                    return False
                node.deleted = True
                return True
            node = node.left if key < node.key else node.right
        return False

    def contains(self, key):
        node = self.root
        while node is not None:
            if key == node.key:
                return not node.deleted
            node = node.left if key < node.key else node.right
        return False

    def range_count(self, lo, hi):
        count = 0
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if lo < node.key:
                stack.append(node.left)
            if node.key < hi:
                stack.append(node.right)
            if lo <= node.key <= hi and not node.deleted:
                count += 1
        return count

    def check_invariants(self):
        keys = []
        stack, node = [], self.root
        while stack or node is not None:
            while node is not None:
                stack.append(node)
                node = node.left
            node = stack.pop()
            keys.append(node.key)
            node = node.right
        ordered = all(a < b for a, b in zip(keys, keys[1:]))
        return {"nodes": len(keys), "in_order_strictly_increasing": ordered, "unique_keys": len(set(keys)) == len(keys)}

    def snapshot(self):
        out = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node is not None:
                out.append((node.key, node.deleted, id(node.left), id(node.right)))
                stack.append(node.left)
                stack.append(node.right)
        return hash(tuple(out))
```

## ДОДАТОК Б. Лістинг `bench.py`

```python
import argparse
import json
import math
import os
import platform
import random
import statistics
import sys
import threading
import time
from array import array

from bst import BST, GlobalLock, RWLock

KEYS = 100_000
RANGE_WIDTH = 100
LOCKS = {"global": GlobalLock, "rw": RWLock}


def build(seed=0):
    tree = BST()
    keys = random.Random(seed).sample(range(KEYS), KEYS // 2)
    for k in keys:
        tree.insert(k)
    return tree


def percentile(sorted_values, q):
    if not sorted_values:
        return None
    return sorted_values[max(0, math.ceil(q * len(sorted_values)) - 1)]


def make_ops(tree, lock, role, rnd):
    if role == "w":
        ops = [(tree.insert, lock.acquire_write, lock.release_write), (tree.remove, lock.acquire_write, lock.release_write)]

        def args():
            return (rnd.randrange(KEYS),)
    else:
        ops = [(tree.contains, lock.acquire_read, lock.release_read), (tree.range_count, lock.acquire_read, lock.release_read)]

        def args():
            lo = rnd.randrange(KEYS - RANGE_WIDTH + 1)
            return (lo,) if turn[0] == 0 else (lo, lo + RANGE_WIDTH - 1)
    turn = [0]
    return ops, args, turn


def worker(tid, role, tree, lock, state, out):
    rnd = random.Random(1000 + tid)
    ops, args, turn = make_ops(tree, lock, role, rnd)
    durations = array("d")
    count = 0
    wait_sum = 0.0
    max_wait = 0.0
    perf = time.perf_counter
    state["barrier"].wait()
    m_start, m_end = state["m_start"], state["m_end"]
    while not state["stop"]:
        fn, acquire, release = ops[turn[0]]
        a = args()
        turn[0] ^= 1
        t_call = perf()
        acquire()
        t_acq = perf()
        fn(*a)
        release()
        t_ret = perf()
        if t_call < m_end and t_ret >= m_start:
            wait_sum += max(0.0, min(t_acq, m_end) - max(t_call, m_start))
            max_wait = max(max_wait, t_acq - t_call)
        if m_start <= t_ret < m_end:
            count += 1
            durations.append(t_ret - t_call)
    out[tid] = {"role": role, "count": count, "durations": durations, "wait_sum": wait_sum, "max_wait": max_wait}


def role_stats(rows, D):
    durations = sorted(x for r in rows for x in r["durations"])
    counts = [r["count"] for r in rows]
    n = sum(counts)
    mu = statistics.mean(counts)
    sigma = math.sqrt(sum((c - mu) ** 2 for c in counts) / len(counts))
    p50, p99 = percentile(durations, 0.5), percentile(durations, 0.99)
    return {"ops": n, "X": n / D, "x_per_thread": n / D / len(rows),
            "p50_us": p50 * 1e6 if n else None, "p99_us": p99 * 1e6 if n else None,
            "h": p99 / p50 if n else None, "max_wait_ms": max(r["max_wait"] for r in rows) * 1e3,
            "w": sum(r["wait_sum"] for r in rows) / (len(rows) * D), "cv": sigma / mu if mu else None}


def run(args):
    tree = build()
    lock = LOCKS[args.sync]()
    n = args.threads
    roles = {tid: ("w" if tid % 2 == 1 else "r") for tid in range(1, n + 1)}
    out = {}
    state = {"stop": False, "barrier": threading.Barrier(n + 1)}
    threads = [threading.Thread(target=worker, args=(tid, roles[tid], tree, lock, state, out)) for tid in roles]
    for t in threads:
        t.start()
    now = time.perf_counter()
    state["m_start"] = now + args.warmup
    state["m_end"] = now + args.warmup + args.duration
    state["barrier"].wait()
    time.sleep(args.warmup + args.duration + 0.05)
    state["stop"] = True
    for t in threads:
        t.join()
    writers = [out[t] for t in roles if roles[t] == "w"]
    readers = [out[t] for t in roles if roles[t] == "r"]
    w, r = role_stats(writers, args.duration), role_stats(readers, args.duration)
    total = w["X"] + r["X"]
    result = {
        "sync": args.sync, "threads": n, "n_w": len(writers), "n_r": len(readers), "duration_s": args.duration,
        "per_thread": {tid: {"role": "запис" if roles[tid] == "w" else "читання", "ops": out[tid]["count"]} for tid in roles},
        "write": w, "read": r, "X": total, "phi_w": w["X"] / total, "phi_r": r["X"] / total,
        "invariants": tree.check_invariants(),
    }
    save(args.out, result)


def single(tree, lock, plan, warmup, duration):
    rnd = random.Random(7)
    w_ops, w_args, _ = make_ops(tree, lock, "w", rnd)
    r_ops, r_args, r_turn = make_ops(tree, lock, "r", rnd)
    w_turn = [0]
    perf = time.perf_counter
    start = perf()
    m_start, m_end = start + warmup, start + warmup + duration
    count = 0
    cs = {"w": [], "r": []}
    i = 0
    while True:
        t_call = perf()
        if t_call >= m_end:
            break
        role = plan[i % len(plan)]
        i += 1
        if role == "w":
            fn, acquire, release = w_ops[w_turn[0]]
            a = w_args()
            w_turn[0] ^= 1
        else:
            fn, acquire, release = r_ops[r_turn[0]]
            a = r_args()
            r_turn[0] ^= 1
        acquire()
        t_acq = perf()
        fn(*a)
        t_rel = perf()
        release()
        if t_call >= m_start:
            count += 1
            cs[role].append(t_rel - t_acq)
    return count / duration, cs


def x1(args):
    g = math.gcd(args.n_w, args.n_r)
    plan = ["w"] * (args.n_w // g) + ["r"] * (args.n_r // g)
    x, _ = single(build(), LOCKS[args.sync](), plan, args.warmup, args.duration)
    save(args.out, {"sync": args.sync, "plan": f"{args.n_w}:{args.n_r}", "X_1": x, "duration_s": args.duration})


def tcs(args):
    _, cs_w = single(build(), GlobalLock(), ["w"], args.warmup, args.duration)
    _, cs_r = single(build(), GlobalLock(), ["r"], args.warmup, args.duration)
    t_w = statistics.mean(cs_w["w"])
    t_r = statistics.mean(cs_r["r"])
    n, n_w, n_r = args.n_w + args.n_r, args.n_w, args.n_r
    t_bar = (n_w * t_w + n_r * t_r) / n
    save(args.out, {"t_cs_w_us": t_w * 1e6, "t_cs_r_us": t_r * 1e6, "t_cs_bar_us": t_bar * 1e6, "limit_ops": 1 / t_bar})


def readonly(args):
    tree = build()
    before = tree.snapshot()
    lock = RWLock()
    out = {}
    n = args.threads
    state = {"stop": False, "barrier": threading.Barrier(n + 1)}
    now = time.perf_counter()
    state["m_start"], state["m_end"] = now, now + args.duration
    threads = [threading.Thread(target=worker, args=(tid, "r", tree, lock, state, out)) for tid in range(1, n + 1)]
    for t in threads:
        t.start()
    state["barrier"].wait()
    time.sleep(args.duration)
    state["stop"] = True
    for t in threads:
        t.join()
    after = tree.snapshot()
    save(args.out, {"reader_threads": n, "read_ops": sum(o["count"] for o in out.values()),
                    "snapshot_before": before, "snapshot_after": after, "unchanged": before == after})


def info(args):
    save(args.out, {"python": sys.version.split()[0], "implementation": platform.python_implementation(),
                    "logical_cores": os.cpu_count(), "platform": platform.platform()})


def save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in data.items() if k != "per_thread"}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "x1", "tcs", "readonly", "info"):
        s = sub.add_parser(name)
        s.add_argument("--out", required=True)
        s.add_argument("--sync", choices=LOCKS, default="global")
        s.add_argument("--threads", type=int, default=12)
        s.add_argument("--n-w", type=int, default=6)
        s.add_argument("--n-r", type=int, default=6)
        s.add_argument("--warmup", type=float, default=1)
        s.add_argument("--duration", type=float, default=10)
    args = p.parse_args()
    {"run": run, "x1": x1, "tcs": tcs, "readonly": readonly, "info": info}[args.cmd](args)


if __name__ == "__main__":
    main()
```

## ДОДАТОК В. Лістинг `summarize.py`

```python
import json
import statistics

RUNS = (1, 2, 3)
FIELDS = ("ops", "X", "x_per_thread", "p50_us", "p99_us", "h", "max_wait_ms", "w", "cv")


def load(name):
    with open(f"results/{name}.json") as f:
        return json.load(f)


def med(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def summary(sync):
    runs = [load(f"run_{sync}_{i}") for i in RUNS]
    out = {role: {k: med([r[role][k] for r in runs]) for k in FIELDS} for role in ("write", "read")}
    out["X"] = med([r["X"] for r in runs])
    out["ops"] = med([r["write"]["ops"] + r["read"]["ops"] for r in runs])
    out["phi_w"] = med([r["phi_w"] for r in runs])
    out["phi_r"] = med([r["phi_r"] for r in runs])
    out["per_thread"] = {tid: med([r["per_thread"][tid]["ops"] for r in runs]) for tid in runs[0]["per_thread"]}
    out["roles"] = {tid: runs[0]["per_thread"][tid]["role"] for tid in runs[0]["per_thread"]}
    out["invariants"] = [r["invariants"] for r in runs]
    out["X_1"] = med([load(f"x1_{sync}_{i}")["X_1"] for i in RUNS])
    out["runs_X"] = [r["X"] for r in runs]
    return out


def ratio(rw, g):
    if rw is None or g is None:
        return None
    return rw / g if g else rw - g


def main():
    g, rw = summary("global"), summary("rw")
    tcs = [load(f"tcs_{i}") for i in RUNS]
    t_w = med([t["t_cs_w_us"] for t in tcs])
    t_r = med([t["t_cs_r_us"] for t in tcs])
    t_bar = med([t["t_cs_bar_us"] for t in tcs])
    limit = 1e6 / t_bar
    metrics = {
        "S_G": g["X"] / g["X_1"], "E_G": g["X"] / g["X_1"] / 12,
        "S_RW": rw["X"] / rw["X_1"], "E_RW": rw["X"] / rw["X_1"] / 12,
        "S_w": rw["write"]["X"] / g["write"]["X"], "S_r": rw["read"]["X"] / g["read"]["X"],
        "S_sum": rw["X"] / g["X"], "rho": g["X"] * t_bar / 1e6,
    }
    compare = {}
    for k in FIELDS[1:]:
        compare[k] = {"G_w": g["write"][k], "G_r": g["read"][k], "RW_w": rw["write"][k], "RW_r": rw["read"][k],
                      "ratio_w": ratio(rw["write"][k], g["write"][k]), "ratio_r": ratio(rw["read"][k], g["read"][k])}
    compare["phi"] = {"G_w": g["phi_w"], "G_r": g["phi_r"], "RW_w": rw["phi_w"], "RW_r": rw["phi_r"],
                      "ratio_w": ratio(rw["phi_w"], g["phi_w"]), "ratio_r": ratio(rw["phi_r"], g["phi_r"])}
    out = {"info": load("info"), "global": g, "rw": rw,
           "t_cs_w_us": t_w, "t_cs_r_us": t_r, "t_cs_bar_us": t_bar, "limit_ops": limit,
           "metrics": metrics, "compare": compare, "readonly": load("readonly")}
    with open("results/summary.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: out[k] for k in ("t_cs_w_us", "t_cs_r_us", "t_cs_bar_us", "limit_ops", "metrics")}, indent=2))
    print("X_1", g["X_1"], rw["X_1"], "X", g["runs_X"], rw["runs_X"])


if __name__ == "__main__":
    main()
```

## ДОДАТОК Г. Лістинг `scripts/run_all.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
$PY bench.py info --out results/info.json
for sync in global rw; do
  for i in 1 2 3; do
    $PY bench.py run --sync $sync --out results/run_${sync}_$i.json
    $PY bench.py x1 --sync $sync --out results/x1_${sync}_$i.json
  done
done
for i in 1 2 3; do
  $PY bench.py tcs --out results/tcs_$i.json
done
$PY bench.py readonly --threads 6 --duration 3 --out results/readonly.json
$PY summarize.py
```
