from django.test import TestCase
from tracker.languages import (
    CANONICAL_LANGUAGES,
    CANONICAL_CODES,
    normalize_language_code,
    get_display_name,
    is_canonical_code,
)


class LanguageModuleTests(TestCase):
    def test_canonical_languages_count(self):
        self.assertGreaterEqual(len(CANONICAL_LANGUAGES), 180)
        self.assertEqual(len(CANONICAL_LANGUAGES), len(CANONICAL_CODES))

    def test_norwegian_mappings(self):
        self.assertEqual(normalize_language_code("no"), "NB")
        self.assertEqual(normalize_language_code("nor"), "NB")
        self.assertEqual(normalize_language_code("nob"), "NB")
        self.assertEqual(normalize_language_code("nb"), "NB")
        self.assertEqual(normalize_language_code("NB"), "NB")
        self.assertEqual(normalize_language_code("nn"), "NN")
        self.assertEqual(normalize_language_code("nno"), "NN")

    def test_display_name_and_lowercase_mappings(self):
        self.assertEqual(normalize_language_code("Deutsch"), "DE")
        self.assertEqual(normalize_language_code("deutsch"), "DE")
        self.assertEqual(normalize_language_code("de"), "DE")
        self.assertEqual(normalize_language_code("DE"), "DE")
        self.assertEqual(normalize_language_code("Français"), "FR")
        self.assertEqual(normalize_language_code("Italiano"), "IT")
        self.assertEqual(normalize_language_code("日本語"), "JA")
        self.assertEqual(normalize_language_code("한국어"), "KO")
        self.assertEqual(normalize_language_code("Русский"), "RU")

    def test_iso_639_2_mappings(self):
        self.assertEqual(normalize_language_code("eng"), "EN")
        self.assertEqual(normalize_language_code("deu"), "DE")
        self.assertEqual(normalize_language_code("ger"), "DE")
        self.assertEqual(normalize_language_code("jpn"), "JA")
        self.assertEqual(normalize_language_code("kor"), "KO")
        self.assertEqual(normalize_language_code("rus"), "RU")
        self.assertEqual(normalize_language_code("ara"), "AR")
        self.assertEqual(normalize_language_code("ell"), "EL")
        self.assertEqual(normalize_language_code("gre"), "EL")

    def test_unsupported_language(self):
        self.assertIsNone(normalize_language_code("Klingon"))
        self.assertIsNone(normalize_language_code("xx"))
        self.assertFalse(is_canonical_code("Klingon"))
        self.assertTrue(is_canonical_code("en"))  # is_canonical_code strips and uppers

    def test_get_display_name(self):
        self.assertEqual(get_display_name("DE"), "Deutsch")
        self.assertEqual(get_display_name("EN"), "English")
        self.assertEqual(get_display_name("NB"), "Norsk Bokmål")
        self.assertEqual(get_display_name("KO"), "한국어")
        self.assertEqual(get_display_name("RU"), "Русский")
        self.assertEqual(get_display_name("unknown"), "UNKNOWN")
