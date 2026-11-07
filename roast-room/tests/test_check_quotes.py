"""Tests for check_quotes.py. Run: python3 -m unittest discover roast-room/tests"""
import contextlib
import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_quotes  # noqa: E402

BRAINS = {
    "paul-graham/principles.md": """
        1. **Users judge.** "Startups are as impersonal as physics. You have to make something people want, and you prosper only to the extent you do." (S092)
        2. **Growth.** "If you're going to optimize a number, the one to choose is your growth rate." (S081)
    """,
    "paul-graham/voice.md": """
        Ends one file with these words: the quick brown fox
    """,
    "paul-graham/zstories.md": """
        jumps over the lazy dog every single morning (S010)
    """,
    "warren-buffett/principles.md": """
        "Cash, though, is to a business as oxygen is to an individual: never thought about when it is present, the only thing in mind when it is absent." (S087)
        "you can cause people to do the wrong thing because they will do what they're incented to do." (Incentives, S045)
    """,
    "alex-hormozi/principles.md": """
        "If you compensate them off of cash collected you will in general get more cash collected." (S074)
    """,
    "leila-hormozi/principles.md": """
        "Your team will rise to the standard you hold, never the one you hope for." (S012)
    """,
}

ROSTER = {"seats": [
    {"key": "graham", "name": "Paul Graham", "short": "Graham", "slug": "paul-graham"},
    {"key": "buffett", "name": "Warren Buffett", "short": "Buffett", "slug": "warren-buffett"},
    {"key": "hormozi", "name": "Alex Hormozi", "short": "Hormozi", "slug": "alex-hormozi"},
    {"key": "leila", "name": "Leila Hormozi", "short": "Leila", "slug": "leila-hormozi"},
]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.brains = root / "brains"
        for rel, body in BRAINS.items():
            p = self.brains / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(body), encoding="utf-8")
        self.roster = root / "roster.json"
        self.roster.write_text(json.dumps(ROSTER), encoding="utf-8")
        self.session = root / "session"
        self.session.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, body):
        (self.session / name).write_text(textwrap.dedent(body), encoding="utf-8")

    def run_check(self, *extra, session=None):
        out, err = io.StringIO(), io.StringIO()
        argv = [str(session or self.session), "--brains", str(self.brains), "--roster", str(self.roster), *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_quotes.main(argv)
        return code, out.getvalue() + err.getvalue()


class TestHappyPath(Base):
    def test_verified_quotes_pass(self):
        self.write("round-1.md", """
            ## Paul Graham (weight 3)
            "If you're going to optimize a number, the one to choose is your growth rate." (S081)
            ## Warren Buffett (weight 2)
            Cash is oxygen. "Cash, though, is to a business as oxygen is to an individual: never thought about when it is present, the only thing in mind when it is absent." (S087)
            "you can cause people to do the wrong thing because they will do what they're incented to do." (Incentives, S045)
        """)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("verified: 3", out)

    def test_curly_quotes_and_year_suffix(self):
        self.write("round-1.md", """
            ## Paul Graham
            “If you’re going to optimize a number, the one to choose is your growth rate.” (S081, 2016)
        """)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_seat_from_citation_overrides_heading(self):
        self.write("verdict.md", "")
        self.write("round-1.md", """
            ## Paul Graham
            Buffett put it best: "Cash, though, is to a business as oxygen is to an individual" (Buffett, S087)
        """)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_alex_short_name_still_resolves_despite_two_hormozis(self):
        self.write("verdict.md", """
            ## Why
            "If you compensate them off of cash collected you will in general get more cash collected." (Hormozi, S074)
        """)
        self.write("round-1.md", "## Alex Hormozi\nnothing quoted\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)


class TestHolesInTheOldScript(Base):
    def test_misattributed_to_wrong_seat(self):
        self.write("round-1.md", """
            ## Warren Buffett
            "If you're going to optimize a number, the one to choose is your growth rate." (S081)
        """)
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("WRONG_SEAT", out)

    def test_wrong_source_id_in_right_brain(self):
        self.write("round-1.md", """
            ## Paul Graham
            "If you're going to optimize a number, the one to choose is your growth rate." (S092)
        """)
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("WRONG_ID", out)
        self.assertIn("S081", out)

    def test_lowercase_and_short_ids_are_still_checked(self):
        for cite in ("(s081)", "(S81)", "(Source: S999)", "[ref S999]"):
            with self.subTest(cite=cite):
                self.write("round-1.md", f'## Paul Graham\n"I guarantee this startup is dead within a single year" {cite}\n')
                code, out = self.run_check()
                self.assertEqual(code, 1, out)
                self.assertIn("UNVERIFIED", out)

    def test_short_fabrication_is_checked(self):
        self.write("round-1.md", '## Paul Graham\n"your moat is a puddle" (S092)\n')
        code, out = self.run_check()
        self.assertEqual(code, 1, out)

    def test_long_fabrication_is_checked(self):
        long_q = " ".join(["fabricated"] * 120)
        self.write("round-1.md", f'## Paul Graham\n"{long_q}" (S092)\n')
        code, out = self.run_check()
        self.assertEqual(code, 1, out)

    def test_quote_wrapped_across_lines(self):
        self.write("round-1.md", '## Paul Graham\n"I guarantee this startup\nis dead within a single year" (S092)\n')
        code, out = self.run_check()
        self.assertEqual(code, 1, out)

    def test_missing_session_folder_is_an_error(self):
        code, out = self.run_check(session=self.session / "typo")
        self.assertEqual(code, 2, out)

    def test_zero_attributed_quotes_is_an_error_unless_allowed(self):
        self.write("round-1.md", "## Paul Graham\nNo quotes here.\n")
        self.assertEqual(self.run_check()[0], 2)
        self.assertEqual(self.run_check("--allow-empty")[0], 0)

    def test_no_round_files_is_an_error(self):
        self.write("verdict.md", '"If you\'re going to optimize a number" (Graham, S081)\n')
        self.assertEqual(self.run_check()[0], 2)

    def test_fabrication_cannot_launder_through_repetition(self):
        self.write("round-1.md", '## Paul Graham\n"the founder clearly never talked to one real customer" (S092)\n')
        self.write("verdict.md", '## Why\n"the founder clearly never talked to one real customer" (Graham, S092)\n')
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("[round-1.md:2]", out)
        self.assertIn("[verdict.md:2]", out)

    def test_rounds_sort_numerically(self):
        # Round 10 quotes a line from round 2's card. Alphabetical order would
        # read round-10 before round-2 and flag it.
        self.write("round-2.md", "## Paul Graham\nThe math kills option A before it starts, plain and simple (S010).\n"
                                 '"If you\'re going to optimize a number, the one to choose is your growth rate." (S081)\n')
        for n in range(3, 10):
            self.write(f"round-{n}.md", "## Paul Graham\nnothing\n")
        self.write("round-10.md", '## Warren Buffett\nGraham said "The math kills option A before it starts" (S010)\n')
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("internal: 1", out)

    def test_no_match_across_file_boundary(self):
        self.write("round-1.md", '## Paul Graham\n"the quick brown fox jumps over the lazy dog" (S010)\n')
        code, out = self.run_check()
        self.assertEqual(code, 1, out)


class TestElisionAndStyle(Base):
    def test_elided_quote_verifies(self):
        self.write("round-1.md", """
            ## Paul Graham
            "Startups are as impersonal as physics. ... you prosper only to the extent you do." (S092)
            "Startups are as impersonal as physics […] you prosper only to the extent you do." (S092)
        """)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_elided_quote_with_invented_piece_fails(self):
        self.write("round-1.md", '## Paul Graham\n"Startups are as impersonal as physics... and your idea is garbage." (S092)\n')
        code, out = self.run_check()
        self.assertEqual(code, 1, out)

    def test_strict_flags_noncanonical_ids(self):
        self.write("round-1.md", '## Paul Graham\n"If you\'re going to optimize a number, the one to choose is your growth rate." (s81)\n')
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("non-canonical", out)
        self.assertEqual(self.run_check("--strict")[0], 1)

    def test_unattributed_quotes_are_counted_not_failed(self):
        self.write("brief.md", "We are thinking about hiring a closer.\n")
        self.write("round-1.md", """
            ## Paul Graham
            The founder said "we are thinking about hiring a closer" and that is fine.
            "If you're going to optimize a number, the one to choose is your growth rate." (S081)
        """)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("unattributed quotes (not checked): 1", out)


if __name__ == "__main__":
    unittest.main()
