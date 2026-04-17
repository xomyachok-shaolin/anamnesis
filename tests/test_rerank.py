import unittest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

from anamnesis.search.hybrid import Hit
from anamnesis.search.rerank import rerank


class RerankTests(unittest.TestCase):
    def _make_hits(self, n):
        return [
            Hit(
                turn_id=i,
                text=f"Document text number {i} with some content",
                meta={"session": "s1", "turn": i, "role": "assistant"},
                rrf_score=1.0 / (60 + i),
            )
            for i in range(1, n + 1)
        ]

    @patch("anamnesis.search.rerank._get_reranker")
    def test_rerank_reorders_hits(self, mock_get):
        mock_reranker = MagicMock()
        # Reverse order: last document scores highest
        mock_reranker.rerank.return_value = [
            MagicMock(index=4, score=0.95),
            MagicMock(index=3, score=0.85),
            MagicMock(index=0, score=0.70),
        ]
        mock_get.return_value = mock_reranker

        hits = self._make_hits(5)
        result = rerank("test query", hits, top_k=3)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].turn_id, 5)  # was index 4
        self.assertEqual(result[1].turn_id, 4)  # was index 3
        self.assertEqual(result[2].turn_id, 1)  # was index 0
        self.assertAlmostEqual(result[0].rerank_score, 0.95)

    @patch("anamnesis.search.rerank._get_reranker")
    def test_rerank_fallback_on_unavailable(self, mock_get):
        mock_get.return_value = None
        hits = self._make_hits(5)
        result = rerank("query", hits, top_k=3)
        self.assertEqual(len(result), 3)
        # Should return original order (no reranking)
        self.assertEqual(result[0].turn_id, 1)

    def test_rerank_empty_hits(self):
        result = rerank("query", [], top_k=3)
        self.assertEqual(len(result), 0)

    @patch("anamnesis.search.rerank._get_reranker")
    def test_rerank_handles_exception(self, mock_get):
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = RuntimeError("model crashed")
        mock_get.return_value = mock_reranker

        hits = self._make_hits(5)
        result = rerank("query", hits, top_k=3)
        # Should fall back to RRF order
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].turn_id, 1)


if __name__ == "__main__":
    unittest.main()
