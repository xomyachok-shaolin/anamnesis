import unittest

from anamnestic import cli


class SyncParserTests(unittest.TestCase):
    def test_sync_uses_bounded_embed_limit_by_default(self):
        args = cli.build_parser().parse_args(["sync"])

        self.assertEqual(args.embed_limit, cli.DEFAULT_SYNC_EMBED_LIMIT)

    def test_sync_accepts_unlimited_embed_limit_opt_in(self):
        args = cli.build_parser().parse_args(["sync", "--embed-limit", "0"])

        self.assertEqual(args.embed_limit, 0)


if __name__ == "__main__":
    unittest.main()
