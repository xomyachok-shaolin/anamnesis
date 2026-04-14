# anamnesis

Persistent hybrid-search memory for AI-CLI sessions.

Ingests historical transcripts from **Claude Code** (main + sub-agents), **Codex CLI**, and — by plugging in a parser — any tool that writes turn-based jsonl. Builds a unified corpus, offers hybrid search (BM25 + semantic via RRF), and exposes it back into the same CLI clients as MCP tools so past context is searchable without leaving the editor.

Built as an extension layer on top of [`claude-mem`](https://github.com/thedotmack/claude-mem): reuses its SQLite file as the base schema, then adds its own tables, indexes and services alongside. Both coexist without stepping on each other.

## Why

- Transcripts of working sessions with AI agents accumulate across projects and clients. Grepping jsonl is slow and semantically blind; the clients themselves forget everything between restarts.
- An MCP server that answers `mem_search("hybrid query")` with ranked turns — from any past session, any client — turns the archive into an addressable knowledge surface.
- Semantic search alone misses exact tokens (IP addresses, CVE IDs, file paths). BM25 alone misses paraphrases. Reciprocal Rank Fusion of both lets either channel lift a relevant hit.

## What you get

- SQLite + Chroma populated from all jsonl sources, tagged by `platform_source`.
- `anamnesis` CLI: `sync`, `status`, `search`, `verify`, `backup`, `restore`, `audit`, `eval`.
- Stdio MCP server with four tools — `mem_search`, `mem_get_turn`, `mem_get_session`, `mem_stats` — usable from Claude Code, Codex, or any MCP-compatible client.
- systemd user timers for incremental sync and daily WAL-safe backups.
- A golden-query eval harness so changes can be measured, not guessed.

## Architecture

```
jsonl sources (Claude Code main / sub-agents / Codex / ...)
       │
       │  mtime-tracked scanner, per-source parsers
       ▼
SQLite ── historical_turns (+ FTS5)                ◄── BM25
       │
       │  incremental embedder
       │  (ONNX multilingual MiniLM-L12 by default)
       ▼
Chroma (persistent, file-based)                     ◄── semantic
       │
       │  Reciprocal Rank Fusion (K=60)
       ▼
stdio MCP server  ──►  Claude Code / Codex / any MCP client
```

Design principles:

- **File is the idempotency unit.** `anamnesis_ingest_state` tracks `(source, path, mtime_ns)`; re-runs skip unchanged files.
- **Turn is the storage unit.** `historical_turns` has a UNIQUE key on `(content_session_id, turn_number)`; UPSERTs never duplicate.
- **Format is the parser's responsibility.** Adding a new CLI agent means writing a parser under `anamnesis/ingest/` and registering a glob in the incremental scanner.
- **Every operation is audited.** `anamnesis_audit` logs sync / verify / backup / restore with duration and a JSON payload for post-hoc forensics.

## Setup

End-to-end instructions — install, backfill, MCP registration, systemd timers, migration to another machine, known gotchas — live in **[SETUP.md](SETUP.md)**.

## Deliberately not included (yet)

- Privacy redaction for tokens and secrets at index time.
- LLM-based event extraction (decisions / todos / facts from transcripts).
- Cross-encoder reranker stage.
- Off-site backup target (rclone / git-crypt / zfs send).

Each is a future iteration with a measurable trigger for when it should land.
