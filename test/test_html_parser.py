import os
import unittest

from gscholar_mail.html_parser import HTMLParser, _clean_text

BASE_DIR = os.path.dirname(__file__)


class TestHTMLPareser(unittest.TestCase):
    def test_parse_forward_gmail(self):

        with open(os.path.join(BASE_DIR, "forward_sample.txt"), "r") as f:
            html = f.read().encode("utf-8").decode("utf-8", errors="replace")
        self.assertTrue(len(html) > 0)
        parser = HTMLParser("subject")
        parser.parse(html)

        self.assertEqual(len(parser.papers), 2)
        print(parser.papers)
        self.assertTrue(parser.check_valid_papers())
        self.assertEqual(parser.papers[0].authors, "Awesome authors…- Jounal A, 2026")
        self.assertEqual(parser.papers[0].title, "Title 1 and The Rest")
        self.assertEqual(parser.papers[0].idx, "TITLE 1 AND THE REST")
        self.assertEqual(parser.papers[0].year, 2026)
        self.assertGreaterEqual(len(parser.papers[0].link), 0)
        self.assertEqual(parser.papers[1].authors, "Authors好…, 2026")
        self.assertEqual(parser.papers[1].title, "Title 2 Long Title")
        self.assertEqual(parser.papers[1].idx, "TITLE 2 LONG TITLE")
        self.assertEqual(parser.papers[1].year, 2026)
        self.assertGreaterEqual(len(parser.papers[1].link), 0)

    def test_clean_text(self):
        self.assertEqual(_clean_text("Awesome authors…\\xa0- Jounal A, 2026"), "Awesome authors…- Jounal A, 2026")


if __name__ == "__main__":  # cov: ignore
    unittest.main()
