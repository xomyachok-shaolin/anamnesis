-- Migration 006: purge legacy subagent rows that used agent_id as csid.
--
-- In the pre-refactor era subagent parsing stored content_session_id = agent_id
-- (short hex, no ':'). Current parser builds "{parent_session_id}:{agent_id}".
-- This leaves machines that ran both eras with two parallel record sets for
-- the same underlying jsonl, skewing counts and search rankings.
--
-- The fix: drop rows where content_session_id has no ':' AND source='claude-subagent'.
-- These are guaranteed legacy because modern subagent csids always contain a colon.
-- On machines that never had the old data, the DELETEs are no-ops.
--
-- Also drop the ingest_state rows for subagents so the next sync re-parses
-- every file with the current parser.

DELETE FROM historical_turns
WHERE platform_source = 'claude-subagent'
  AND instr(content_session_id, ':') = 0;

DELETE FROM user_prompts
WHERE content_session_id IN (
    SELECT content_session_id FROM sdk_sessions
    WHERE platform_source = 'claude-subagent'
      AND instr(content_session_id, ':') = 0
);

DELETE FROM session_summaries
WHERE memory_session_id IN (
    SELECT memory_session_id FROM sdk_sessions
    WHERE platform_source = 'claude-subagent'
      AND instr(content_session_id, ':') = 0
);

DELETE FROM sdk_sessions
WHERE platform_source = 'claude-subagent'
  AND instr(content_session_id, ':') = 0;

DELETE FROM anamnestic_ingest_state
WHERE source = 'claude-subagent';

-- Note: anamnestic_embed_state rows for dropped historical_turns are cleaned by
-- the FK cascade (ON DELETE CASCADE).
