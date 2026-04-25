import unittest
from unittest.mock import patch

from anamnestic.indexers import incremental_chroma


class SemanticOptionalTests(unittest.TestCase):
    def test_embedding_run_skips_before_db_access_when_semantic_disabled(self):
        with (
            patch.object(incremental_chroma, "SEMANTIC_ENABLED", False),
            patch.object(incremental_chroma, "connect", side_effect=AssertionError),
        ):
            result = incremental_chroma.run()

        self.assertEqual(result["embedded"], 0)
        self.assertEqual(result["skipped"], "semantic_disabled")

    def test_embedding_run_auto_skips_before_db_access_when_deps_unavailable(self):
        with (
            patch.object(incremental_chroma, "SEMANTIC_ENABLED", True),
            patch.object(incremental_chroma, "SEMANTIC_REQUIRED", False),
            patch.object(incremental_chroma, "semantic_dependencies_available", return_value=False),
            patch.object(incremental_chroma, "connect", side_effect=AssertionError),
        ):
            result = incremental_chroma.run()

        self.assertEqual(result["embedded"], 0)
        self.assertEqual(result["skipped"], "semantic_unavailable")

    def test_embedding_run_required_reports_missing_model_as_error(self):
        with (
            patch.object(incremental_chroma, "SEMANTIC_ENABLED", True),
            patch.object(incremental_chroma, "SEMANTIC_REQUIRED", True),
            patch.object(incremental_chroma, "semantic_dependencies_available", return_value=False),
            patch.object(incremental_chroma, "connect", side_effect=AssertionError),
        ):
            result = incremental_chroma.run()

        self.assertEqual(result["embedded"], 0)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
