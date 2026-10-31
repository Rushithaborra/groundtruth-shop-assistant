"""The Speak button: hidden means hidden, and the browser-speech fallback is wired in (the page text, not a browser)."""

import unittest

from groundtruth.paths import ROOT

PAGE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


class VoiceButtonPageTest(unittest.TestCase):
    def test_a_hidden_mic_button_is_not_shown_by_its_display_rule(self) -> None:
        self.assertRegex(PAGE, r"button\.mic\[hidden\]\s*\{\s*display:\s*none")

    def test_the_offline_model_is_still_preferred(self) -> None:
        self.assertRegex(PAGE, r"if \(info\.voice && canRecord\)[^\n]*\n\s*else if \(BrowserSpeech\)")

    def test_the_fallback_says_it_needs_the_internet(self) -> None:
        self.assertIn("needs internet", PAGE)
        self.assertRegex(PAGE, r"window\.SpeechRecognition \|\| window\.webkitSpeechRecognition")

    def test_every_error_path_gives_the_shopper_a_message(self) -> None:
        for error in ("not-allowed", "network", "no-speech"):
            self.assertIn(error, PAGE)


if __name__ == "__main__":
    unittest.main()
