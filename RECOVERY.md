# Disaster Recovery

## Что у тебя есть

- **Ежедневные бэкапы**: `~/claude-mem-backups/claude-mem-YYYYMMDD-HHMMSS.tar.gz`
  (keeps last 10, WAL-safe SQLite dump + Chroma snapshot).
- **Репо**: `~/projects/claude-mem-ext/` (git).

Tarball contains two members at the top level:

    claude-mem.db
    semantic-chroma/   (directory)

## Переезд на новую машину

```bash
# 1. Установить зависимости
curl -fsSL https://bun.sh/install | bash
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Склонировать репо
git clone <url> ~/projects/claude-mem-ext
cd ~/projects/claude-mem-ext

# 3. Создать venv
uv venv ~/.claude-mem/semantic-env --python 3.11
uv pip install --python ~/.claude-mem/semantic-env/bin/python \
    chromadb fastembed mcp pyyaml

# 4. Восстановить данные из последнего tarball
cd ~/projects/claude-mem-ext
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m mem_ext.cli restore \
    ~/claude-mem-backups/claude-mem-LATEST.tar.gz

# 5. Запустить миграции (no-op если БД восстановлена полностью)
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m mem_ext.db

# 6. Verify
PYTHONPATH=$PWD ~/.claude-mem/semantic-env/bin/python -m mem_ext.cli verify

# 7. Установить systemd юниты
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mem-ext-sync.timer mem-ext-backup.timer

# 8. Зарегистрировать MCP server в Claude Code
claude mcp add mem-ext ~/.claude-mem/semantic-env/bin/python \
    -e PYTHONPATH=$PWD -- -m mem_ext.daemon.mcp_server
```

## Откат после плохого sync / corrupted DB

```bash
# Выбрать бэкап (последний):
ls -lt ~/claude-mem-backups/ | head

# Остановить активные операции
systemctl --user stop mem-ext-sync.timer
fuser -k 37777/tcp  # claude-mem worker
# Если был запущен MCP server — Claude Code перезапустит сам

mem-ext restore ~/claude-mem-backups/claude-mem-<stamp>.tar.gz

# Предыдущие файлы сохранены рядом:
#   claude-mem.db.pre-restore-<stamp>
#   semantic-chroma.pre-restore-<stamp>/
# Их можно удалить после проверки, что восстановление прошло успешно.

mem-ext verify
mem-ext eval    # smoke test
systemctl --user start mem-ext-sync.timer
```

## SQLite повреждён (verify показывает `sqlite_integrity != ok`)

Если интегрити-чек провалился, а бэкап устарел:

1. Попытка `recover`:

       sqlite3 ~/.claude-mem/claude-mem.db ".recover" > /tmp/recovered.sql
       sqlite3 /tmp/recovered.db < /tmp/recovered.sql
       mem-ext verify   # против /tmp/recovered.db вручную

2. Если не помогло — восстановить из бэкапа (см. выше). Потеряются сессии между бэкапом и сейчас. Они снова проиндексируются автоматически при следующем `sync`, т.к. jsonl файлы живут независимо.

## Если FTS5 деградировал

FTS-индекс можно пересобрать без потери данных:

```bash
sqlite3 ~/.claude-mem/claude-mem.db \
  "INSERT INTO historical_turns_fts(historical_turns_fts) VALUES('rebuild');"
mem-ext verify
```

## Если Chroma «сломалась»

Chroma — это только кэш эмбеддингов, можно снести и пересчитать:

```bash
rm -rf ~/.claude-mem/semantic-chroma
# Очистить учёт уже эмбедженных:
sqlite3 ~/.claude-mem/claude-mem.db "DELETE FROM ext_embed_state;"
mem-ext sync   # всё проиндексируется заново (несколько минут)
```

## Как проверить, что восстановление прошло успешно

```bash
mem-ext status         # должны быть счётчики, healthy=true
mem-ext verify         # все чеки ok
mem-ext eval           # 100% pass
mem-ext search "any известный запрос"
```

## Чего нет в бэкапе (ставится вручную)

- `~/.claude-mem/fastembed-models/` — модель MiniLM-L12 (~220 MB).
  Скачается автоматически при первом `sync` или `search`.
- `~/.claude-mem/semantic-env/` — Python venv с зависимостями.
- Bun (`~/.bun/`) — нужен для `claude-mem` worker.
- systemd user timers (копируются из `systemd/` репо).
- MCP регистрация в Claude Code (`claude mcp add mem-ext ...`).
