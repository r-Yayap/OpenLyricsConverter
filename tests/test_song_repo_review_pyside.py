import os
import pathlib
import tempfile
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtWidgets
    import song_repo_review_pyside as review_pyside

    HAS_QT = True
except Exception:
    QtWidgets = None
    review_pyside = None
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class PySideReviewResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_window_loads_empty_output_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            window = review_pyside.ReviewResolverWindow(pathlib.Path(temp_dir))
            try:
                self.app.processEvents()
                self.assertEqual(window.issue_table.rowCount(), 0)
                self.assertIn("No review items", window.status_label.text())
            finally:
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
