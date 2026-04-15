import tempfile
import textwrap
import unittest
from pathlib import Path

from anamnesis.ingest.parsers import parse_claude_jsonl, parse_codex_jsonl, ts_to_epoch


class ParserTests(unittest.TestCase):
    def _write(self, body: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl")
        tmp.write(textwrap.dedent(body).strip() + "\n")
        tmp.close()
        return Path(tmp.name)

    def test_parse_claude_main_session(self):
        path = self._write(
            """
            {"type":"user","cwd":"/workspace/projects/demo","sessionId":"sess-1","timestamp":"2026-04-15T10:00:00Z","message":{"role":"user","content":"Найди проблему"}}
            {"type":"assistant","cwd":"/workspace/projects/demo","sessionId":"sess-1","timestamp":"2026-04-15T10:00:05Z","message":{"role":"assistant","content":[{"type":"thinking","thinking":"skip me"},{"type":"text","text":"Сначала проверю логи"}]}}
            """
        )
        meta = parse_claude_jsonl(path)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["csid"], "sess-1")
        self.assertEqual(meta["cwd"], "/workspace/projects/demo")
        self.assertEqual(meta["turns"][0][1], "Найди проблему")
        self.assertEqual(meta["turns"][1][1], "Сначала проверю логи")

    def test_parse_claude_subagent_uses_unique_session_id(self):
        path = self._write(
            """
            {"type":"assistant","cwd":"/workspace/projects/demo","sessionId":"main-sess","agentId":"agent-42","timestamp":"2026-04-15T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"Готово"}]}}
            """
        )
        meta = parse_claude_jsonl(path, is_subagent=True)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["csid"], "main-sess:agent-42")
        self.assertEqual(meta["platform"], "claude-subagent")

    def test_parse_codex_session_skips_environment_context(self):
        path = self._write(
            """
            {"timestamp":"2026-04-15T10:00:00Z","type":"session_meta","payload":{"id":"codex-1","timestamp":"2026-04-15T10:00:00Z","cwd":"/workspace/projects/demo"}}
            {"timestamp":"2026-04-15T10:00:01Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<environment_context>ignored</environment_context>"}]}}
            {"timestamp":"2026-04-15T10:00:02Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Проверь сборку"}]}}
            {"timestamp":"2026-04-15T10:00:03Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Проверяю"}]}}
            """
        )
        meta = parse_codex_jsonl(path)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["csid"], "codex-1")
        self.assertEqual(len(meta["turns"]), 2)
        self.assertEqual(meta["turns"][0][1], "Проверь сборку")
        self.assertEqual(meta["turns"][1][1], "Проверяю")

    def test_ts_to_epoch_handles_z_suffix(self):
        self.assertEqual(ts_to_epoch("1970-01-01T00:00:01Z"), 1)


if __name__ == "__main__":
    unittest.main()
