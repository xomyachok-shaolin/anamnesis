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

## Run eval

    ~/.claude-mem/semantic-env/bin/python -m mem_ext.eval.run

## Layout

    mem_ext/
      ingest/     # jsonl parsers (claude, subagent, codex)
      indexers/   # chroma writer, chunking, incremental
      search/     # bm25, semantic, hybrid, rerank
      eval/       # golden.yaml + run.py
      daemon/     # long-running HTTP/MCP server
