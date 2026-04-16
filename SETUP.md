# Персистентная память для AI-CLI сессий

Система, которая собирает историю разговоров с CLI-агентами (Claude Code, Codex, и в принципе любой агент, пишущий turn-based jsonl) в единый корпус, даёт по нему гибридный поиск и отдаёт его нативно обратно в те же клиенты через MCP.

На выходе:

- Единая SQLite-база + векторный индекс Chroma со всей историей независимо от клиента.
- Hybrid-поиск: BM25 (точные токены) + семантика (ONNX multilingual) + RRF-fusion.
- MCP-инструменты (`mem_search`, `mem_probe`, `mem_get_turn`, `mem_get_session`, `mem_stats`), доступные в любом MCP-совместимом клиенте. `mem_probe(term)` — точный FTS-счётчик (coverage-oracle: сколько, где, когда); `mem_search` — ранжирующий поиск с нечёткими совпадениями.
- Инкрементальный sync новых сессий и ежедневные бэкапы через systemd user-таймеры.
- Команда `anamnesis restore` для отката и переезда на другую машину.

Платформа: Linux с `systemd` user-режима. Всё работает offline, embedding — локальный ONNX.

---

## 0. Подход

### Архитектурная позиция

Это **агент-нейтральный слой поверх jsonl-транскриптов**. Любой CLI-агент, который сохраняет диалог в формате «один файл = одна сессия, каждая реплика — отдельная строка», подключается через собственный парсер. Сегодня поддержаны два источника — **Claude Code** (main + subagent jsonl) и **Codex CLI**. Добавление третьего (Aider, Cursor agent, collective, свой собственный CLI) — это написать парсер и указать его директорию в конфиге.

Поверх собранного корпуса живут **три слоя поиска** и **три контура эксплуатации**, одинаковые для всех источников.

### Источники

- **Claude Code main-сессии** — `~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl`. По одному файлу на верхнеуровневую сессию.
- **Claude Code sub-agent транскрипты** — `~/.claude/projects/<cwd-slug>/<session-uuid>/subagents/*.jsonl`. Отдельные файлы на каждый запуск Explore / Plan / general-агента. Часто их в разы больше, чем main-сессий, и именно в них лежит содержательная аналитика.
- **Codex CLI сессии** — `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Другой формат: события `session_meta | turn_context | response_item | event_msg`, content как Python-repr строка.

Если собирать только один из источников — теряешь параллельный трек работы. Задача — привести гетерогенные форматы к одной схеме, сохраняя метку источника (`platform_source`) для фильтрации и аудита.

### Принцип сбора

1. **Файл — единица идемпотентности.** Каждый jsonl описан в `ext_ingest_state` как `(source, path, mtime_ns)`. Повторный sync не перечитывает неизменённые файлы.
2. **Turn — единица хранения.** Таблица `historical_turns` хранит каждую реплику (user + assistant) с UNIQUE-ключом `(content_session_id, turn_number)`. UPSERT через `ON CONFLICT DO NOTHING` гарантирует отсутствие дублей.
3. **Формат — ответственность парсера.** Сейчас два парсера; добавление нового источника = новый парсер в `anamnesis/ingest/` + запись в `incremental.py::_discover()`.
4. **Восстановление пропусков.** Если какая-то сессия попала в `sdk_sessions` без соответствующих `historical_turns` (такое бывает при миграции между версиями инструментов) — скрипт `recover_main` перечитывает jsonl для таких сессий и дозаливает.

### Слои поверх корпуса

1. **BM25 через SQLite FTS5** — для точных токенов: IP-адреса, CVE, имена файлов, коды ошибок, идентификаторы. Триггеры держат индекс в синке с базой.
2. **Семантика через Chroma + ONNX multilingual** — для смысловых запросов на естественном языке. Модель по умолчанию `paraphrase-multilingual-MiniLM-L12-v2` (384-dim, компромисс скорость / качество). Инкрементальный embedding: `ext_embed_state` отмечает, что уже в Chroma.
3. **Hybrid через Reciprocal Rank Fusion** — объединяет ранги (не скоры — у них разные шкалы) по формуле `score(d) = Σ 1 / (60 + rank_r(d))`. BM25 поднимает точные имена, семантика поднимает близкое по смыслу, RRF склеивает.

Результаты — не текст, а **адресуемые объекты**: `turn_id`, `session_id`, `turn_number`, `timestamp`, `platform_source`. Окрестность поднимается через `mem_get_turn(turn_id, context=N)`, обзор сессии — через `mem_get_session(session_id)`.

### Точки входа

Один stdio-MCP сервер обслуживает всех клиентов, которые понимают MCP:

- Claude Code регистрирует его через `claude mcp add`.
- Codex — через запись в `~/.codex/config.toml`.
- Любой другой MCP-совместимый клиент — аналогично.
- Модель (~220 МБ) загружается один раз при старте процесса; последующие запросы — миллисекунды.

Параллельно есть CLI (`anamnesis`) для эксплуатации: sync, verify, backup, restore, audit, eval.

### Контуры эксплуатации

- **Incremental sync** по mtime — systemd-таймер подхватывает новое без полной пересборки.
- **Verify** — `PRAGMA integrity_check`, FTS rebuild, drift SQLite↔Chroma, orphans. Ловит деградацию до того, как мусор появится в результатах.
- **Audit log** (`ext_audit`) — каждая операция с длительностью и JSON-payload'ом. Через полгода можно реконструировать, когда что сломалось.
- **WAL-safe backup** — tarball с ротацией. `restore` откатывает атомарно с сохранением предыдущего состояния в `*.pre-restore-*`.
- **Golden eval** — **твой** набор известных запросов с известными ответами. Без него нельзя сказать, стал ли поиск лучше или хуже после любого изменения.

### Отношение к `claude-mem`

`claude-mem` (плагин от thedotmack) создаёт базовую SQLite-схему и web-viewer; мы строим наш слой поверх его БД. Если пользуешься Claude Code — ставь его, получишь заодно живые хуки автозахвата. Если Claude Code нет, а нужен только Codex или другой клиент — можно пропустить установку плагина и создать базовую схему вручную (см. §3.1). Данные всё равно живут в `~/.claude-mem/` (имя директории — историческое, может быть переопределено через `ANAMNESIS_DATA_DIR`).

### Короткий маршрут

1. Поставить зависимости (Bun, uv, опционально claude-mem, Python venv).
2. Клонировать репо, прогнать миграции.
3. Перенести jsonl-ы на машину, если они уже есть где-то ещё.
4. Один раз `anamnesis sync` — собрать всё прошлое.
5. При пропусках (см. §16.1) — `recover_main`.
6. Зарегистрировать MCP во всех клиентах, которые будешь использовать.
7. Включить systemd-таймеры.

---

## 1. Предварительные требования

- `python3 ≥ 3.10`, `git`, `curl`, `sqlite3`.
- Хотя бы один CLI-агент, чьи сессии хочешь индексировать (Claude Code CLI `claude`, Codex CLI `codex`, или свой).
- Свободное место: порядка 1% от суммарного размера jsonl плюс ~220 МБ модель и ~200 МБ на каждый бэкап.

Проверка:

```bash
python3 --version
sqlite3 --version
claude --version 2>/dev/null
codex --version 2>/dev/null
```

---

## 2. Установить Bun и uv

Bun нужен, только если ставишь плагин `claude-mem` (он на Bun). uv — быстрый менеджер Python-зависимостей без torch.

```bash
curl -fsSL https://bun.sh/install | bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv --version
```

---

## 3. Базовая SQLite-схема

Два пути — через плагин `claude-mem` (рекомендуется, если используешь Claude Code) или вручную.

### 3.a — через плагин (Claude Code есть)

```bash
npx -y claude-mem@latest install
```

Создаст `~/.claude-mem/claude-mem.db` с базовой схемой, положит плагин в `~/.claude/plugins/marketplaces/thedotmack/`, пропишет хуки автозахвата в `~/.claude/settings.json`.

Опционально — запустить worker (web-viewer на `:37777`):

```bash
export PATH="$HOME/.bun/bin:$PATH"
nohup npx claude-mem start > /tmp/claude-mem-worker.log 2>&1 &
disown
```

### 3.b — вручную (Claude Code не используется)

Создать директорию и пустую БД:

```bash
mkdir -p ~/.claude-mem
sqlite3 ~/.claude-mem/claude-mem.db <<'SQL'
CREATE TABLE IF NOT EXISTS sdk_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_session_id TEXT UNIQUE NOT NULL,
    memory_session_id TEXT UNIQUE,
    project TEXT NOT NULL,
    platform_source TEXT NOT NULL DEFAULT 'claude',
    user_prompt TEXT,
    started_at TEXT NOT NULL,
    started_at_epoch INTEGER NOT NULL,
    completed_at TEXT,
    completed_at_epoch INTEGER,
    status TEXT CHECK(status IN ('active','completed','failed')) NOT NULL DEFAULT 'active',
    worker_port INTEGER,
    prompt_counter INTEGER DEFAULT 0,
    custom_title TEXT
);
CREATE TABLE IF NOT EXISTS user_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_session_id TEXT NOT NULL,
    prompt_number INTEGER NOT NULL,
    prompt_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    request TEXT,
    investigated TEXT,
    learned TEXT,
    completed TEXT,
    next_steps TEXT,
    files_read TEXT,
    files_edited TEXT,
    notes TEXT,
    prompt_number INTEGER,
    discovery_tokens INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL
);
SQL
```

Остальные таблицы (`historical_turns`, `ext_*`, FTS) создадут наши миграции в §7.

---

## 4. Python venv для расширений

```bash
uv venv ~/.claude-mem/semantic-env --python 3.11
uv pip install --python ~/.claude-mem/semantic-env/bin/python \
    chromadb fastembed mcp pyyaml
```

`sentence-transformers` не ставить — он тянет torch + CUDA. ONNX-backend через `fastembed` решает ту же задачу без гигабайт.

---

## 5. Получить репо

```bash
git clone <url> ~/projects/anamnesis
cd ~/projects/anamnesis
```

Проверка:

```
anamnesis/    # config.py, db.py, cli.py, audit.py, verify.py,
            # restore.py, backup.py, ingest/, indexers/, search/, eval/, daemon/
migrations/ # 001_fts_and_unique.sql, 002_incremental_state.sql, 003_audit_log.sql
systemd/    # *.service, *.timer
```

Ниже все команды подразумевают `cd ~/projects/anamnesis && export PYTHONPATH=$PWD`.

---

## 6. Перенести jsonl-историю (если уже есть)

Если ставишь на новой машине, но старая история где-то лежит:

```bash
# на старой:
tar czf history.tar.gz ~/.claude/projects/ ~/.codex/sessions/

# на новой (раскатает в $HOME):
tar xzf history.tar.gz -C /
```

Если истории нет — пропусти.

---

## 7. Миграции

```bash
~/.claude-mem/semantic-env/bin/python -m anamnesis.db
```

Ожидаемый вывод:

```
Applying 001_fts_and_unique.sql...
Applying 002_incremental_state.sql...
Applying 003_audit_log.sql...
Applied: 001_fts_and_unique.sql, 002_incremental_state.sql, 003_audit_log.sql
```

Проверка таблиц:

```bash
sqlite3 ~/.claude-mem/claude-mem.db ".tables" | tr ' ' '\n' | grep -E "ext_|historical_"
# должны быть: ext_audit, ext_embed_state, ext_ingest_state, ext_migrations,
# historical_turns, historical_turns_fts (+ служебные _fts_*)
```

---

## 8. Первичный бэкфилл всей истории

Одноразовая операция: читает jsonl из всех сконфигурированных директорий, наполняет `sdk_sessions`, `user_prompts`, `historical_turns`, `session_summaries`, затем эмбеддит в Chroma.

```bash
~/.claude-mem/semantic-env/bin/python -m anamnesis.cli sync
```

В конце печатает:

```json
{"ingest": {"total": N, "skipped": 0, "new_files": N, "new_turns": K, "errors": 0},
 "embed":  {"embedded": E, "elapsed": ...}}
```

### 8.1. Проверка целостности

```bash
~/.claude-mem/semantic-env/bin/python -m anamnesis.cli verify
```

Должно быть `"healthy": true`, `"issues": []`, `drift_state_vs_chroma = 0`.

Если `missing_embeddings > 0` — запусти `sync` ещё раз, дошлёт.

---

## 9. Проверить поиск

```bash
~/.claude-mem/semantic-env/bin/python -m anamnesis.cli search "любой запрос из твоего контекста" --top-k 10
```

Результат — turns с `session_id`, `timestamp`, `role`, `source` (claude / claude-subagent / codex / …), snippet. Если возвращает пусто — в корпусе нет того, что ищешь (проверь `anamnesis status` — `turns > 0`).

### 9.1. Регрессионный набор (golden)

В репо `anamnesis/eval/golden.yaml` лежит шаблон. На чужой истории он **не работает** — его надо заменить на 15–30 запросов под **свои** темы.

Формат:

```yaml
queries:
  - query: "текст запроса"
    any_keywords: ["слово1", "слово2"]   # хотя бы одно должно встретиться в хите
    min_hits: 1                          # минимум N хитов в top-K
    top_k: 10
```

Принцип: выбираешь темы, про которые точно знаешь, что они обсуждались. Формулируешь запросы так, как реально будешь искать (обобщённо, не точно-по-словам). В `any_keywords` — точные токены, которые должны оказаться в результатах.

Прогон:

```bash
~/.claude-mem/semantic-env/bin/python -m anamnesis.cli eval --mode hybrid
```

Смысл не в 100%, а в **baseline**. После любого изменения (смена модели, правка tokenizer, эксперимент с весами RRF) прогоняешь снова и сравниваешь числа.

---

## 10. Зарегистрировать MCP-сервер в клиентах

### 10.a — Claude Code

```bash
claude mcp add anamnesis ~/.claude-mem/semantic-env/bin/python \
    -e PYTHONPATH=$HOME/projects/anamnesis \
    -- -m anamnesis.daemon.mcp_server

claude mcp list   # должно быть "anamnesis ... ✓ Connected"
```

### 10.b — Codex CLI

```bash
cp ~/.codex/config.toml ~/.codex/config.toml.bak

cat >> ~/.codex/config.toml <<EOF

[mcp_servers.anamnesis]
command = "$HOME/.claude-mem/semantic-env/bin/python"
args = ["-m", "anamnesis.daemon.mcp_server"]
env = { PYTHONPATH = "$HOME/projects/anamnesis" }
EOF
```

(Если shell не раскроет `$HOME` в heredoc — подставь путь вручную.)

Проверить:

```bash
codex mcp list              # anamnesis, enabled=true
codex mcp get anamnesis       # детали
```

### 10.c — любой другой MCP-совместимый клиент

Конфигурация аналогична: stdio-транспорт, команда `python -m anamnesis.daemon.mcp_server`, env `PYTHONPATH`. Инструменты, которые экспонируются: `mem_search`, `mem_get_turn`, `mem_get_session`, `mem_stats`.

Все клиенты используют **один** SQLite и **одну** Chroma — дублировать данные не нужно.

---

## 11. Systemd user-таймеры

```bash
mkdir -p ~/.config/systemd/user
cp ~/projects/anamnesis/systemd/*.service \
   ~/projects/anamnesis/systemd/*.timer \
   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now anamnesis-sync.timer
systemctl --user enable --now anamnesis-backup.timer

systemctl --user list-timers | grep anamnesis
```

Юниты:

- `anamnesis-sync.timer` — инкрементальный sync + WAL checkpoint.
- `anamnesis-backup.timer` — ежедневный WAL-safe snapshot DB + Chroma в `~/anamnesis-backups/` (ротация последних 10).

### 11.1. Работа без активной сессии

```bash
sudo loginctl enable-linger $USER
```

### 11.2. Проверка запуска

```bash
systemctl --user start anamnesis-sync.service
journalctl --user -u anamnesis-sync.service -n 30
```

---

## 12. Ежедневные команды

Алиас:

```bash
alias anamnesis='PYTHONPATH=$HOME/projects/anamnesis $HOME/.claude-mem/semantic-env/bin/python -m anamnesis.cli'
```

```bash
anamnesis status            # сессии / turns / embedded / drift / last_ingest
anamnesis verify            # integrity SQLite + FTS + drift + orphans
anamnesis search "query" --top-k 10
anamnesis sync              # вручную (обычно делает timer)
anamnesis backup            # вручную (обычно делает timer)
anamnesis audit --limit 20  # последние операции с timestamps
anamnesis eval --mode hybrid
anamnesis restore ~/anamnesis-backups/<tarball>.tar.gz
```

---

## 13. Где что лежит

```
~/.claude-mem/                     # данные (путь историческиий; переопределяется
                                   # через ANAMNESIS_DATA_DIR)
├─ claude-mem.db                   # SQLite: все таблицы
├─ semantic-chroma/                # Chroma коллекция 'history_turns'
├─ fastembed-models/               # ONNX модель (cached)
├─ semantic-env/                   # Python venv
├─ health.json                     # snapshot последнего sync
├─ settings.json                   # конфиг claude-mem (если плагин стоит)
├─ supervisor.json, worker.pid     # worker state (claude-mem)
└─ logs/                           # worker logs (claude-mem)

~/anamnesis-backups/              # tarball'ы (last N)

~/projects/anamnesis/                # код (git)
├─ anamnesis/
│  ├─ config.py                    # пути / модель / коллекция (env-overridable)
│  ├─ db.py                        # connect() + миграции
│  ├─ cli.py                       # sync/status/search/backup/verify/restore/audit/eval
│  ├─ audit.py                     # audited() + write_health()
│  ├─ backup.py, restore.py, verify.py
│  ├─ ingest/
│  │  ├─ incremental.py            # mtime scanner, UPSERT
│  │  └─ recover_main.py           # скрипт из §16.1
│  ├─ indexers/incremental_chroma.py
│  ├─ search/hybrid.py             # BM25 + semantic → RRF
│  ├─ eval/{golden.yaml, run.py}
│  └─ daemon/mcp_server.py         # stdio MCP
├─ migrations/
└─ systemd/
```

---

## 14. Добавить новый источник jsonl

Сценарий: кроме Claude Code и Codex появился третий клиент (Aider, Cursor agent, свой собственный). Интеграция:

1. Написать парсер в `anamnesis/ingest/parsers_<name>.py`, возвращающий dict:

   ```python
   {"csid": "...", "cwd": "...", "title": ..., "first_ts": "...", "last_ts": "...",
    "turns": [(role, text, ts), ...], "files": [...], "platform": "<name>"}
   ```

2. Зарегистрировать источник в `anamnesis/ingest/incremental.py::_discover()`:

   ```python
   for p in glob(os.path.join(MY_ROOT, "pattern.jsonl")):
       yield "<name>", p, os.stat(p).st_mtime_ns
   ```

3. Добавить ветку в `process()` → вызывающую твой парсер.
4. `anamnesis sync` начнёт подхватывать новые файлы. `platform_source='<name>'` появится в `anamnesis status` / `mem_stats()`.

Голден-набор можно расширить запросами, специфичными для этого клиента.

---

## 15. Переезд на другую машину

На старой (или из последнего бэкапа) нужно:

- `~/anamnesis-backups/<latest>.tar.gz` — данные,
- `~/projects/anamnesis/` — репо,
- (опционально) `~/.codex/sessions/`, `~/.claude/projects/` — если хочешь иметь raw jsonl-ы.

На новой — §§1–5, затем:

```bash
cd ~/projects/anamnesis
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m anamnesis.cli restore \
    ~/anamnesis-backups/<latest>.tar.gz

PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m anamnesis.db     # миграции (no-op)
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m anamnesis.cli verify
```

Далее §§10–11: регистрация MCP в клиентах + systemd-таймеры.

**Как устроен restore.** Tarball содержит `claude-mem.db` и `semantic-chroma/` на верхнем уровне. Команда распаковывает во временную директорию, атомарно подменяет текущие файлы, а старые сохраняет рядом как `claude-mem.db.pre-restore-<stamp>` и `semantic-chroma.pre-restore-<stamp>/`. Если что-то пошло не так — эти файлы остаются, можно откатиться.

---

## 16. Известные грабли

### 16.1. Пропущенные main-сессии

Если базовая SQLite уже существовала (например, из старого `claude-mem`) и в `sdk_sessions` есть строки без соответствующих записей в `historical_turns` — идемпотентность `sync` по content_session_id пропустит их.

Диагностика:

```bash
sqlite3 ~/.claude-mem/claude-mem.db "
SELECT platform_source, COUNT(*) FROM historical_turns GROUP BY platform_source;"
```

Если для какого-то источника 0 или сильно меньше ожидаемого:

```bash
cd ~/projects/anamnesis
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m anamnesis.ingest.recover_main
```

Перечитает jsonl и дозальёт. Далее — `anamnesis sync`, чтобы Chroma догнала.

### 16.2. Смена формата у клиента

Если один из клиентов меняет формат jsonl — парсер в `anamnesis/ingest/` требует обновления. Симптом: после апгрейда клиента `new_files` в sync'е не растёт или стабильно `errors > 0`.

### 16.3. `paraphrase-multilingual-MiniLM-L12-v2` — mean pooling warning

`fastembed ≥ 0.6` переключился с CLS на mean pooling. Качество близко. Для точного воспроизведения старого поведения — `fastembed==0.5.1`.

### 16.4. Своя Chroma у `claude-mem` на `:8000`

`claude-mem` объявляет собственный Chroma, но поднимает его лениво. Наш индекс живёт отдельно в `~/.claude-mem/semantic-chroma/` через `chromadb.PersistentClient`. Их Chroma не используется и не мешает.

### 16.5. Несколько клиентов одновременно

Все клиенты работают с одной БД и одной Chroma. SQLite в WAL-режиме выдерживает конкурентные чтения. Параллельные writes в `sync` защищены systemd-юнитом `Type=oneshot`. MCP-запросы — read-only.

### 16.6. Первый поиск через MCP медленнее

При старте процесса модель (~220 МБ) загружается в RAM. Последующие запросы — быстрые. Кэш модели — в `~/.claude-mem/fastembed-models/`.

### 16.7. SQLite повреждён (`verify` показывает `sqlite_integrity != ok`)

Если бэкап свежий — `anamnesis restore`. Если нет:

```bash
sqlite3 ~/.claude-mem/claude-mem.db ".recover" > /tmp/recovered.sql
sqlite3 /tmp/recovered.db < /tmp/recovered.sql
# перенести /tmp/recovered.db в ~/.claude-mem/claude-mem.db вручную после проверки
```

Потерянные сессии (между последним бэкапом и сбоем) вернутся автоматически при следующем `sync` — jsonl-файлы живут независимо от БД.

### 16.8. FTS5 деградировал

FTS перестраивается без потери данных:

```bash
sqlite3 ~/.claude-mem/claude-mem.db \
    "INSERT INTO historical_turns_fts(historical_turns_fts) VALUES('rebuild');"
anamnesis verify
```

### 16.9. Chroma «сломалась»

Chroma — кэш эмбеддингов, сносится и пересчитывается без потери данных:

```bash
rm -rf ~/.claude-mem/semantic-chroma
sqlite3 ~/.claude-mem/claude-mem.db "DELETE FROM ext_embed_state;"
anamnesis sync
```

---

## 17. Кастомизация через env vars

| Variable | Default | Что делает |
| --- | --- | --- |
| `ANAMNESIS_DATA_DIR` | `~/.claude-mem` | корень данных (БД + Chroma + venv + модель) |
| `ANAMNESIS_CC_ROOT` | `~/.claude/projects` | источник Claude Code jsonl |
| `ANAMNESIS_CODEX_ROOT` | `~/.codex/sessions` | источник Codex jsonl |
| `ANAMNESIS_BACKUP_ROOT` | `~/anamnesis-backups` | куда бэкапить |
| `ANAMNESIS_BACKUP_KEEP_LAST` | `10` | ротация |
| `ANAMNESIS_EMBED_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | ONNX fastembed |
| `ANAMNESIS_CHROMA_COLLECTION` | `history_turns` | имя коллекции |

---

## 18. Проверочный чек-лист после установки

```bash
anamnesis status                                  # корпус заполнен
anamnesis verify                                  # healthy=true
claude mcp list 2>/dev/null | grep anamnesis     # Claude Code видит (если поставлен)
codex  mcp list 2>/dev/null | grep anamnesis     # Codex видит (если поставлен)
systemctl --user list-timers | grep anamnesis    # таймеры активны
anamnesis search "любой_твой_запрос" --top-k 3    # поиск возвращает результаты
ls -lh ~/anamnesis-backups/                   # после первого дня — tarball
```

---

## 19. Удаление

```bash
systemctl --user disable --now anamnesis-sync.timer anamnesis-backup.timer
rm ~/.config/systemd/user/anamnesis-*.{service,timer}
systemctl --user daemon-reload

claude mcp remove anamnesis 2>/dev/null
codex mcp remove anamnesis 2>/dev/null
```

`claude-mem` плагин (если ставил):

```bash
claude plugin remove claude-mem@thedotmack
# или снять флаги в ~/.claude/settings.json
```

Данные остаются в `~/.claude-mem/` и `~/anamnesis-backups/` — удаляй вручную.

---

## 20. Что НЕ входит (осознанно отложено)

- **Privacy layer** — маскировка токенов/секретов при индексации. Риск: секреты попадают в tarball-бэкапы и в Chroma. Включать когда корпус содержит чувствительные данные или бэкап уходит за пределы машины.
- **Event extraction** (decisions / todos / facts через локальный LLM) — превращает архив в базу знаний, не в базу реплик. Отдельный кусок работы с Ollama + структурированными промптами.
- **Апгрейд на `multilingual-e5-large`** (1024-dim) — жирнее, качество выше. Только если MiniLM систематически промахивается на твоём домене (golden eval покажет).
- **Off-site backup** — сейчас только локальный диск. Для серьёзной надёжности — `rclone` / `git-crypt` / `zfs send` на внешнее хранилище.
- **Reranker (cross-encoder)** — добавляет 2–3 сек/запрос, но поднимает precision на «близких, но не тех» результатах. Включать если hybrid даёт релевантное, но вторым-третьим, а не первым.

Каждый пункт — отдельная итерация с измеримым критерием.
