# claude-mem-ext

Extensions over [claude-mem](https://github.com/thedotmack/claude-mem):
- Historical backfill for Claude Code + subagents + Codex sessions.
- Hybrid BM25 + semantic search with eval harness.
- Path toward event-based memory and proactive injection.

## Baseline (M0 — 2026-04-14)

- Corpus: 22619 turns from 931 sessions (Claude Code main + subagents + Codex).
- Semantic: fastembed, `paraphrase-multilingual-MiniLM-L12-v2`, Chroma at `~/.claude-mem/semantic-chroma/`.
- **Golden eval**: 17/21 queries passed (81%), avg precision@10 = 0.586.
- Known failures: all 4 involve exact-name tokens (webshell, IP, token, product name) — semantic alone misses them. Hybrid BM25+semantic (M2) expected to fix.

## CLI

    mem-ext sync      # incremental ingest + embed (idempotent, ~2s)
    mem-ext status    # health + drift report
    mem-ext search "query"
    mem-ext backup    # tarball of SQLite + Chroma
    mem-ext eval      # golden eval, hybrid mode

Shortcut:

    $HOME/.claude-mem/semantic-env/bin/python -m mem_ext.cli <cmd>

## Automation

systemd user timers installed in `~/.config/systemd/user/`:
- `mem-ext-sync.timer` — every 30 min
- `mem-ext-backup.timer` — daily

## Current (M5)

- Corpus: 46170 turns / 931 sessions / 9802 user_prompts
- Chroma: 43825 embeddings (drift = short turns filtered)
- Eval: 21/21 (100%), p@10 = 0.776
- Backups: `~/claude-mem-backups/` (keeps last 10)

## Layout

    mem_ext/
      ingest/          # jsonl parsers (claude, subagent, codex) + incremental
      indexers/        # chroma writer, incremental embed
      search/          # bm25, semantic, hybrid RRF
      eval/            # golden.yaml + run.py
      daemon/          # MCP stdio server (registered as 'mem-ext')
      cli.py           # unified CLI
      backup.py        # safe SQLite .backup + tar
      db.py            # migration runner
