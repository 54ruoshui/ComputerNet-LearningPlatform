"""run_eval_comparison.parse_letter 选项字母解析测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from run_eval_comparison import parse_letter


class TestParseLetter:

    def test_plain_letter(self):
        assert parse_letter("B") == "B"

    def test_correct_answer_phrase(self):
        assert parse_letter("正确答案：B") == "B"
        assert parse_letter("正确答案是 C") == "C"

    def test_correct_option_phrase(self):
        assert parse_letter("正确选项是：B") == "B"

    def test_answer_prefix(self):
        assert parse_letter("答案选A") == "A"

    def test_echoed_options_conclusion_wins_over_first_letter(self):
        """系统臂常回显题面选项再给结论，不能抓到回显选项行里的 A."""
        text = (
            "UDP和TCP之间的差别是（ ）。\n"
            "A. UDP是面向连接的，TCP是无连接的\n"
            "B. UDP是无连接的，TCP是面向连接的\n"
            "C. 两者都是面向连接的\n"
            "D. 两者都是无连接的\n\n"
            "正确选项是：B"
        )
        assert parse_letter(text) == "B"

    def test_last_letter_fallback(self):
        assert parse_letter("综合考虑应该选择 D") == "D"

    def test_bold_letter(self):
        assert parse_letter("**B**") == "B"

    def test_answer_with_trailing_period(self):
        assert parse_letter("答案是 B。") == "B"

    def test_empty(self):
        assert parse_letter("") == "?"
        assert parse_letter(None) == "?"
