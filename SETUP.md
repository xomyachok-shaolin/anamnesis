# Персистентная семантическая память для Claude Code + Codex

Пошаговая инструкция. На выходе:

- SQLite + Chroma с полным архивом прошлых сессий Claude Code (main + subagents) и Codex CLI.
- Hybrid поиск (BM25 FTS5 + semantic RRF) по всему архиву.
- MCP-инструменты (`mem_search`, `mem_get_turn`, `mem_get_session`, `mem_stats`), доступные нативно из Claude Code и Codex.
- Авто-синк новых сессий и авто-бэкап через systemd user-таймеры.
- Команда `mem-ext restore` для отката и переезда на другую машину.

Платформа: Linux с `systemd` в user-режиме. Всё работает offline (embedding — локальный ONNX).

---

## 0. Подход: как собрать всё прошлое и что с ним делать

### Что считается «прошлым»

Три класса источников, которые лежат в пользовательской `$HOME` в виде jsonl'ов:

- **Claude Code main-сессии** — `~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl`. По одному файлу на верхнеуровневую сессию.
- **Claude Code sub-agent транскрипты** — `~/.claude/projects/<cwd-slug>/<session-uuid>/subagents/*.jsonl`. Отдельные файлы на каждый запуск Explore / Plan / general subagent'а — их **часто больше, чем основных**, и они содержат наиболее содержательную аналитическую работу.
- **Codex CLI сессии** — `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Другой формат: `type: session_meta | response_item`, content — Python-repr строка.

Все три источника должны быть собраны в **один унифицированный корпус**, иначе поиск будет ходить только по части истории.

### Стратегия сбора

1. **Инвентаризация по glob'у** — пройти по всем трём директориям, собрать список jsonl-файлов с их `mtime_ns`.
2. **Парсинг под формат источника** — два парсера: один для Claude Code (одинаковый для main и subagent; отличие — `isSidechain`/`agentId`), один для Codex (другой формат, `ast.literal_eval` для content).
3. **UPSERT в SQLite** — одна схема для всех источников, метка `platform_source ∈ {claude, claude-subagent, codex}`. Идемпотентность через UNIQUE-индексы и `ON CONFLICT DO NOTHING`.
4. **Tracking в `ext_ingest_state`** — по `(source, path) → mtime_ns`. Повторный прогон не перечитывает неизменённые файлы.
5. **Recovery-скрипт для известной граблы** — если `claude-mem` ставили **до** нашего слоя, `sdk_sessions` могут содержать строки без соответствующих turns в `historical_turns`. Отдельный скрипт (`recover_main.py`) перечитывает jsonl для таких сессий и дозаливает.

### Что дальше — зачем их собирать

Сырая коллекция — не продукт. Продукт — это три слоя поверх неё:

1. **BM25 (FTS5)** поверх `historical_turns.text` — находит точные токены, которые семантика упускает: IP-адреса, CVE, имена файлов, идентификаторы, код ошибок. Индекс строится через SQLite unicode61 tokenizer; триггеры держат его в синке с базовой таблицей.

2. **Семантика (Chroma + ONNX multilingual embedding)** — находит смысл через расстояние в 384-мерном пространстве. Модель `paraphrase-multilingual-MiniLM-L12-v2` выбрана как компромисс между качеством на русском техническом и скоростью/весом. Инкрементальное эмбеддинг: `ext_embed_state` отмечает, какие turns уже в Chroma, чтобы пересчитывать только новые.

3. **Гибрид через Reciprocal Rank Fusion** — `score(d) = Σ 1 / (60 + rank_r(d))` по рангам из BM25 и семантики. Это не среднее скоров (разные шкалы), а среднее рангов. На практике даёт нужное: точные имена находит BM25, общие темы — семантика, оба канала поднимают релевантное вверх.

### Как этим пользуешься

Не через CLI — через MCP-инструменты внутри того же клиента, где работаешь:

- Claude Code получает `mem_search` как стандартный tool,
- Codex — тоже (регистрируется через `~/.codex/config.toml`),
- Один процесс = один загруженный embedding model = все последующие запросы <100 мс.

Результаты поиска — не просто reply, а **источники**: session_id + turn_number + timestamp + source. Можно поднять полную окрестность через `mem_get_turn(turn_id, context=N)`. Это превращает историю в адресуемый граф, а не в текстовую свалку.

### Что удерживает систему в рабочем состоянии

- `mem-ext sync` — инкрементально подтягивает новые jsonl-ы и дозаливает их в Chroma. Запускается systemd-таймером.
- `mem-ext verify` — PRAGMA integrity_check, FTS rebuild, drift между SQLite и Chroma, orphaned rows.
- `mem-ext backup` — WAL-safe snapshot в tarball, ротация последних N.
- `mem-ext restore` — обратная операция с сохранением предыдущего состояния в `*.pre-restore-<stamp>`.
- `ext_audit` таблица — пишет каждую операцию с длительностью и payload'ом для forensics через полгода.
- Golden eval — **твой** (не мой) набор известных запросов с известными ответами. Без него ты не знаешь, ухудшил ли что-то в системе очередной «улучшайзинг».

### Короткий маршрут

1. Поставить зависимости (bun, uv, claude-mem, python venv).
2. Клонировать репо, прогнать миграции.
3. Если нужно — перенести свои jsonl'ы на текущую машину.
4. Один раз `mem-ext sync` — собирает всё прошлое.
5. Если видишь пропуски — `mem-ext recover_main`.
6. Подключить MCP к обоим клиентам.
7. Включить systemd-таймеры — дальше оно живёт само.

Ниже — те же шаги подробнее.

---

## 1. Предварительные требования

- `python3 ≥ 3.10`, `git`, `curl`, `sqlite3`.
- Claude Code CLI (`claude`) и/или Codex CLI (`codex`) установлены и авторизованы.
- Свободное место: индекс занимает порядок 1% от суммарного размера твоих jsonl-ов плюс ~220 МБ модель и по ~200 МБ на каждый бэкап.

Проверка:

```bash
python3 --version
sqlite3 --version
claude --version 2>/dev/null
codex --version 2>/dev/null
```

---

## 2. Установить Bun и uv

Bun нужен для worker'а `claude-mem`. uv — быстрый менеджер Python-зависимостей.

```bash
curl -fsSL https://bun.sh/install | bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
bun --version
uv --version
```

---

## 3. Установить claude-mem

```bash
npx -y claude-mem@latest install
```

Это:
- кладёт плагин в `~/.claude/plugins/marketplaces/thedotmack/`,
- создаёт `~/.claude-mem/` (SQLite + конфиг),
- добавляет хуки автозахвата **будущих** сессий Claude Code в `~/.claude/settings.json`.

Запустить worker (держит web-viewer на `:37777`):

```bash
export PATH="$HOME/.bun/bin:$PATH"
nohup npx claude-mem start > /tmp/claude-mem-worker.log 2>&1 &
disown
curl -sS http://localhost:37777/ | head -1   # должен вернуть <!DOCTYPE html>
```

Если падает — смотри `~/.claude-mem/logs/`.

---

## 4. Python venv для расширений

```bash
uv venv ~/.claude-mem/semantic-env --python 3.11
uv pip install --python ~/.claude-mem/semantic-env/bin/python \
    chromadb fastembed mcp pyyaml
```

**Не ставь `sentence-transformers` напрямую** — он тянет torch + CUDA. ONNX-backend через `fastembed` достаточен.

---

## 5. Получить репо `claude-mem-ext`

Клонировать или перекопировать рабочую копию:

```bash
git clone <url> ~/projects/claude-mem-ext
cd ~/projects/claude-mem-ext
```

Структура, которая должна быть:

```
mem_ext/    # config.py, db.py, cli.py, audit.py, verify.py,
            # restore.py, backup.py, ingest/, indexers/, search/, eval/, daemon/
migrations/ # 001_fts_and_unique.sql, 002_incremental_state.sql, 003_audit_log.sql
systemd/    # *.service, *.timer
```

---

## 6. Перенести jsonl-историю (если у тебя есть)

Если ты устанавливаешь на новой машине, но история Claude Code / Codex уже накоплена на старой — перенеси исходные jsonl:

```bash
# на старой машине:
tar czf claude-history.tar.gz ~/.claude/projects/ ~/.codex/sessions/

# на новой (положит в $HOME):
tar xzf claude-history.tar.gz -C /
```

Если ставишь с нуля — пропусти. Система будет захватывать всё, что появится начиная с этого момента.

---

## 7. Миграции

```bash
cd ~/projects/claude-mem-ext
export PYTHONPATH=$PWD
~/.claude-mem/semantic-env/bin/python -m mem_ext.db
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
# должны быть ext_audit, ext_embed_state, ext_ingest_state, ext_migrations,
# historical_turns, historical_turns_fts (+ _fts_* служебные)
```

---

## 8. Первичный бэкфилл всей истории

Одноразовая операция: читает jsonl из `~/.claude/projects/**` и `~/.codex/sessions/**`, наполняет `sdk_sessions`, `user_prompts`, `historical_turns`, `session_summaries`, затем эмбеддит в Chroma.

```bash
~/.claude-mem/semantic-env/bin/python -m mem_ext.cli sync
```

В конце печатает что-то вроде:

```
{"ingest": {"total": N, "skipped": 0, "new_files": N, "new_turns": K, "errors": 0},
 "embed":  {"embedded": E, "elapsed": ...}}
```

### 8.1. Проверка целостности

```bash
~/.claude-mem/semantic-env/bin/python -m mem_ext.cli verify
```

Ожидаемо: `"healthy": true`, `"issues": []`, `drift_state_vs_chroma = 0`.

Если `missing_embeddings > 0` — запусти `mem-ext sync` ещё раз, он дошлёт.

---

## 9. Проверить поиск

Hybrid (BM25 + семантика через RRF):

```bash
~/.claude-mem/semantic-env/bin/python -m mem_ext.cli search "любой запрос из твоего контекста" --top-k 10
```

Должны появиться turns с session_id, timestamp, role, source (claude / claude-subagent / codex) и snippet.

### 9.1. Регрессионный набор (golden)

В репо лежит шаблонный `mem_ext/eval/golden.yaml`. Он **рабочий только на исходном корпусе автора**. Для твоей истории его надо переписать:

1. Собери 15–30 запросов, для которых ты знаешь, что ответ есть в твоей истории.
2. Для каждого укажи keywords, которые должны встретиться в top-K matches.
3. Формат:

```yaml
queries:
  - query: "текст твоего запроса"
    any_keywords: ["точное_слово1", "точное_слово2"]
    min_hits: 1   # хотя бы 1 из top-K должен содержать keyword
    top_k: 10
```

Прогон:

```bash
~/.claude-mem/semantic-env/bin/python -m mem_ext.cli eval --mode hybrid
```

Это тест на регрессию: после любого изменения (новая модель, другой tokenizer, правка chunk-логики) результат не должен упасть. Без своего golden-набора ты не узнаешь, стало лучше или хуже.

---

## 10. Зарегистрировать MCP в Claude Code

```bash
claude mcp add mem-ext ~/.claude-mem/semantic-env/bin/python \
    -e PYTHONPATH=$HOME/projects/claude-mem-ext \
    -- -m mem_ext.daemon.mcp_server

claude mcp list   # должно быть "mem-ext ... ✓ Connected"
```

При следующем запуске Claude Code (CLI или IDE-extension) будут доступны:

- `mem_search(query, top_k, role, mode)` — hybrid / semantic / bm25
- `mem_get_turn(turn_id, context)` — turn + окрестность
- `mem_get_session(session_id, max_turns)`
- `mem_stats()`

---

## 11. Зарегистрировать MCP в Codex

Открыть `~/.codex/config.toml` и добавить (предварительно сделав бэкап):

```bash
cp ~/.codex/config.toml ~/.codex/config.toml.bak

cat >> ~/.codex/config.toml <<EOF

[mcp_servers.mem-ext]
command = "$HOME/.claude-mem/semantic-env/bin/python"
args = ["-m", "mem_ext.daemon.mcp_server"]
env = { PYTHONPATH = "$HOME/projects/claude-mem-ext" }
EOF
```

(Подставь реальный `$HOME` вручную если shell не раскроет.)

Проверить:

```bash
codex mcp list              # mem-ext, enabled=true
codex mcp get mem-ext       # детали
```

Оба клиента используют **один и тот же** индекс — дублировать не надо.

---

## 12. Systemd user-таймеры

```bash
mkdir -p ~/.config/systemd/user
cp ~/projects/claude-mem-ext/systemd/*.service \
   ~/projects/claude-mem-ext/systemd/*.timer \
   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now mem-ext-sync.timer
systemctl --user enable --now mem-ext-backup.timer

systemctl --user list-timers | grep mem-ext
```

- `mem-ext-sync.timer` — инкрементальный sync (mtime-based) + WAL checkpoint.
- `mem-ext-backup.timer` — ежедневный WAL-safe snapshot DB + Chroma в `~/claude-mem-backups/` (ротация: последние 10).

### 12.1. Работа без активной сессии

Чтобы user-таймеры работали когда ты не залогинен:

```bash
sudo loginctl enable-linger $USER
```

### 12.2. Проверка запуска

```bash
systemctl --user start mem-ext-sync.service
journalctl --user -u mem-ext-sync.service -n 30
```

---

## 13. Ежедневные команды

Удобный alias:

```bash
alias memext='PYTHONPATH=$HOME/projects/claude-mem-ext $HOME/.claude-mem/semantic-env/bin/python -m mem_ext.cli'
```

```bash
memext status        # сессии / turns / embedded / drift / last_ingest
memext verify        # integrity SQLite + FTS + drift + orphans
memext search "query" --top-k 10
memext sync          # вручную (обычно не надо — таймер делает)
memext backup        # вручную (обычно не надо)
memext audit --limit 20   # недавние операции (sync/backup/verify/restore)
memext eval --mode hybrid # регрессионный тест по своему golden.yaml
memext restore ~/claude-mem-backups/claude-mem-<stamp>.tar.gz
```

---

## 14. Где что лежит

```
~/.claude-mem/
├─ claude-mem.db                  # SQLite: все таблицы
├─ semantic-chroma/               # Chroma коллекция 'history_turns'
├─ fastembed-models/               # ONNX модель (cached)
├─ semantic-env/                   # Python venv
├─ health.json                     # snapshot последнего sync
├─ settings.json                   # claude-mem конфиг
├─ supervisor.json, worker.pid     # worker state
└─ logs/                           # worker logs

~/claude-mem-backups/              # tarball'ы (last N)

~/projects/claude-mem-ext/         # код (git)
├─ mem_ext/
│  ├─ config.py                    # пути, модель, коллекция (env-overridable)
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
├─ systemd/
├─ SETUP.md, RECOVERY.md, README.md
```

---

## 15. Переезд на другую машину

```bash
# На старой (или из последнего бэкапа):
#   ~/claude-mem-backups/claude-mem-LATEST.tar.gz
#   ~/projects/claude-mem-ext/   — репо
#   ~/.codex/sessions/, ~/.claude/projects/   — если хочешь иметь raw jsonl

# На новой — §1–5, затем:
cd ~/projects/claude-mem-ext
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m mem_ext.cli restore \
    ~/claude-mem-backups/claude-mem-LATEST.tar.gz

PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m mem_ext.db   # миграции (no-op)
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m mem_ext.cli verify
```

Подробнее — в [RECOVERY.md](RECOVERY.md).

---

## 16. Известные грабли

### 16.1. Claude Code main-сессии могут не попасть в `historical_turns`

Если `claude-mem` был установлен **до** добавления `claude-mem-ext` и уже писал в БД, в `sdk_sessions` могут быть строки с `platform_source='claude'` без соответствующих `historical_turns`. Idempotency `sync`'а пропустит их.

Диагностика:

```bash
sqlite3 ~/.claude-mem/claude-mem.db "
SELECT platform_source, COUNT(*) FROM historical_turns GROUP BY platform_source;"
```

Если `claude` отсутствует или сильно меньше ожидаемого — запусти:

```bash
cd ~/projects/claude-mem-ext
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m mem_ext.ingest.recover_main
```

Дальше — `mem-ext sync`, чтобы Chroma догнала.

### 16.2. Формат Codex jsonl

`~/.codex/sessions/**/*.jsonl` отличается от Claude Code: `type: session_meta | turn_context | response_item | event_msg`, content хранится как Python-repr (`"[{'type': 'input_text', ...}]"`), парсим через `ast.literal_eval`. При смене формата OpenAI — парсер `parse_codex_jsonl` может требовать правки.

### 16.3. `paraphrase-multilingual-MiniLM-L12-v2` — mean pooling warning

fastembed ≥ 0.6 переключился с CLS на mean pooling. Качество близко к прежнему, но для точного воспроизведения предыдущего поведения можно зафиксировать `fastembed==0.5.1`.

### 16.4. Своя Chroma у `claude-mem` на :8000

`claude-mem` объявляет собственный Chroma на `127.0.0.1:8000`, но поднимает его лениво. **Наш** семантический индекс живёт отдельно в `~/.claude-mem/semantic-chroma/` через `chromadb.PersistentClient` и не зависит от их сервера.

### 16.5. Codex + Claude Code одновременно

Оба клиента работают с одной `claude-mem.db` и одним `semantic-chroma/`. SQLite в WAL-режиме выдерживает конкурентные чтения. Параллельные writes в `sync` защищены `Type=oneshot` systemd-юнита. MCP-запросы (`mem_search`) — read-only.

### 16.6. Первый запрос через MCP медленнее

При старте процесса модель (~220 МБ) загружается в RAM. Последующие запросы — быстрые. Кэш модели — в `~/.claude-mem/fastembed-models/`.

---

## 17. Кастомизация через env vars

| Variable | Default | Что делает |
|---|---|---|
| `MEM_EXT_DATA_DIR` | `~/.claude-mem` | корень данных |
| `MEM_EXT_CC_ROOT` | `~/.claude/projects` | источник Claude Code jsonl |
| `MEM_EXT_CODEX_ROOT` | `~/.codex/sessions` | источник Codex jsonl |
| `MEM_EXT_BACKUP_ROOT` | `~/claude-mem-backups` | куда бэкапить |
| `MEM_EXT_BACKUP_KEEP_LAST` | `10` | ротация |
| `MEM_EXT_EMBED_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | ONNX-модель fastembed |
| `MEM_EXT_CHROMA_COLLECTION` | `history_turns` | имя коллекции |

---

## 18. Проверочный чек-лист

```bash
# 1. Worker жив
curl -sS http://localhost:37777/ | head -1

# 2. CLI работает
memext status
memext verify

# 3. MCP в обоих клиентах
claude mcp list | grep mem-ext
codex mcp list  | grep mem-ext

# 4. Таймеры активны
systemctl --user list-timers | grep mem-ext

# 5. Поиск возвращает результаты
memext search "тест" --top-k 3

# 6. После первого ежедневного цикла — бэкап появился
ls -lh ~/claude-mem-backups/
```

Все шесть ✅ — система работает автономно.

---

## 19. Удаление

```bash
systemctl --user disable --now mem-ext-sync.timer mem-ext-backup.timer
rm ~/.config/systemd/user/mem-ext-*.{service,timer}
systemctl --user daemon-reload

claude mcp remove mem-ext
codex mcp remove mem-ext

# Данные — в ~/.claude-mem/ и ~/claude-mem-backups/, удаляй вручную при необходимости.
```

claude-mem плагин:

```bash
claude plugin remove claude-mem@thedotmack
# или снять флаги в ~/.claude/settings.json
```

---

## 20. Что НЕ входит (осознанно отложено)

- **Privacy layer** — маскировка токенов/секретов при индексации. Risky: они сейчас попадают в tarball-бэкапы и в Chroma.
- **Event extraction (decisions/todos/facts)** через локальный LLM. Превращает архив в базу знаний, но требует Ollama + дообучения схем.
- **Апгрейд на `multilingual-e5-large`** (1024-dim). Жирнее, но качество выше; окупается только если MiniLM промахивается на твоём домене.
- **Off-site backup** — сейчас только локальный диск. Для production добавить `rclone` / `git-crypt` / zfs send.
- **Reranker (cross-encoder)**. Добавляет 2–3 сек/запрос, но даёт заметный precision на «близких, но не тех» результатах.

Каждый пункт — отдельный кусок работы с измеримым критерием, когда его стоит включить.
