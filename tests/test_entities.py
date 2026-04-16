import unittest

from anamnesis.entities import extract


class ExtractTests(unittest.TestCase):
    def test_extracts_absolute_paths(self):
        text = "Read /home/user/project/src/main.py for details"
        entities = list(extract(text))
        self.assertIn(("path", "/home/user/project/src/main.py"), entities)

    def test_extracts_home_relative_paths(self):
        text = "Config at ~/projects/anamnesis/config.yaml ok"
        entities = list(extract(text))
        self.assertIn(("path", "~/projects/anamnesis/config.yaml"), entities)

    def test_extracts_dot_relative_paths(self):
        text = "Run ./scripts/deploy.sh or ../lib/utils.py"
        entities = list(extract(text))
        paths = [v for t, v in entities if t == "path"]
        self.assertIn("./scripts/deploy.sh", paths)
        self.assertIn("../lib/utils.py", paths)

    def test_extracts_urls(self):
        text = "See https://github.com/user/repo and http://localhost:8080/api"
        entities = list(extract(text))
        urls = [v for t, v in entities if t == "url"]
        self.assertIn("https://github.com/user/repo", urls)
        self.assertIn("http://localhost:8080/api", urls)

    def test_deduplicates_within_same_text(self):
        text = "/home/a/b.py and /home/a/b.py again"
        entities = list(extract(text))
        paths = [v for t, v in entities if t == "path"]
        self.assertEqual(paths.count("/home/a/b.py"), 1)

    def test_no_false_positive_on_plain_text(self):
        text = "Just a normal sentence with no paths or links."
        entities = list(extract(text))
        self.assertEqual(len(entities), 0)

    def test_trailing_punctuation_stripped(self):
        text = "Check /home/user/file.py."
        entities = list(extract(text))
        paths = [v for t, v in entities if t == "path"]
        self.assertTrue(any(v.endswith(".py") for v in paths))


if __name__ == "__main__":
    unittest.main()
