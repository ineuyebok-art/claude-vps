import io
import unittest
from unittest import mock

from tests import harness  # noqa: F401
from ccbot import files


class Names(unittest.TestCase):
    def test_paths_are_removed(self):
        self.assertEqual(files.sanitize_name("../../etc/passwd"), "passwd")
        self.assertEqual(files.sanitize_name("..\\..\\win.ini"), "win.ini")

    def test_hidden_and_office_temp(self):
        self.assertEqual(files.sanitize_name(".bashrc"), "bashrc")
        self.assertEqual(files.sanitize_name("~$отчёт.docx"), "отчёт.docx")

    def test_cyrillic_kept_spaces_replaced(self):
        self.assertEqual(files.sanitize_name("Отчёт за май 2026.pdf"), "Отчёт_за_май_2026.pdf")

    def test_windows_reserved(self):
        self.assertEqual(files.sanitize_name("CON.txt"), "_CON.txt")
        self.assertEqual(files.sanitize_name('a<b>:c|d?.txt'), "a_b_c_d_.txt")

    def test_control_chars_and_empty(self):
        self.assertEqual(files.sanitize_name("a\x00b\nc"), "abc")
        self.assertEqual(files.sanitize_name("..."), "file")

    def test_length(self):
        self.assertLessEqual(len(files.sanitize_name("а" * 500 + ".txt")), 130)


class Links(unittest.TestCase):
    def test_allowed(self):
        for url in ("https://drive.google.com/file/d/abc/view", "https://disk.yandex.ru/d/x",
                    "https://yadi.sk/i/x", "https://www.dropbox.com/s/x/a.pdf?dl=0",
                    "https://docs.google.com/document/d/abc/edit"):
            self.assertIsNotNone(files.link_kind(url), url)

    def test_foreign_and_lookalikes(self):
        for url in ("https://evil.com/a", "https://drive.google.com.evil.com/file/d/a",
                    "https://evil-dropbox.com/s/a", "http://drive.google.com/file/d/a",
                    "https://drive.google.com@evil.com/file/d/a", "https://dropbox.com:8443/x",
                    "https://user:pw@drive.google.com/file/d/a"):
            self.assertIsNone(files.link_kind(url), url)

    def test_find_links(self):
        allowed, foreign = files.find_links("смотри https://disk.yandex.ru/d/x, и https://evil.com/a.")
        self.assertEqual(allowed, ["https://disk.yandex.ru/d/x"])
        self.assertEqual(foreign, ["https://evil.com/a"])

    def test_direct_urls(self):
        self.assertIn("id=abc", files.direct_url("https://drive.google.com/file/d/abc/view?usp=sharing"))
        self.assertTrue(files.direct_url("https://docs.google.com/spreadsheets/d/abc/edit").endswith("format=xlsx"))
        self.assertIn("dl=1", files.direct_url("https://www.dropbox.com/s/x/a.pdf?dl=0"))
        self.assertIn("public_key=https%3A%2F%2Fdisk.yandex.ru", files.direct_url("https://disk.yandex.ru/d/x"))

    def test_drive_folder_is_refused(self):
        with self.assertRaises(files.LinkError):
            files.direct_url("https://drive.google.com/drive/folders/abc")

    def test_redirects(self):
        self.assertTrue(files.redirect_allowed("https://doc-0.googleusercontent.com/x"))
        self.assertTrue(files.redirect_allowed("https://uc123.dl.dropboxusercontent.com/x"))
        self.assertTrue(files.redirect_allowed("https://downloader.disk.yandex.net/x"))
        self.assertFalse(files.redirect_allowed("https://169.254.169.254/latest"))
        self.assertFalse(files.redirect_allowed("https://evil.com/googleusercontent.com"))
        self.assertFalse(files.redirect_allowed("http://doc-0.googleusercontent.com/x"))

    def test_redirect_to_foreign_host_is_blocked(self):
        handler = files._Redirects()
        with self.assertRaises(files.LinkError):
            handler.redirect_request(mock.Mock(), None, 302, "", {}, "https://evil.com/x")

    def test_size_limit_while_streaming(self):
        limited = files._Limited(io.BytesIO(b"x" * 100), 50)
        with self.assertRaises(files.LinkError):
            while limited.read(30):
                pass

    def test_foreign_link_is_never_fetched(self):
        with mock.patch.object(files, "_open") as opened:
            with self.assertRaises(files.LinkError):
                files.download_link("https://evil.com/file.exe")
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
