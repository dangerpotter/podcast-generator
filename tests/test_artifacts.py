"""Unit tests for canonical generated artifact filenames."""

from __future__ import annotations

import unittest

from capella_podcast.artifacts import artifact_filename


class ArtifactFilenameTests(unittest.TestCase):
    def test_required_nomenclature(self) -> None:
        course = "MBA-FPX5006"
        self.assertEqual(
            artifact_filename(course, "summary", 1),
            "cc_mba-fpx5006_assessment_summary-01.docx",
        )
        self.assertEqual(
            artifact_filename(course, "script", 1),
            "cc_mba-fpx5006_podcast_script-01.docx",
        )
        self.assertEqual(
            artifact_filename(course, "podcast", 1),
            "cc_mba-fpx5006_podcast_overview-01.mp3",
        )

    def test_module_number_is_zero_padded(self) -> None:
        self.assertEqual(
            artifact_filename("SWK5017", "summary", 9),
            "cc_swk5017_assessment_summary-09.docx",
        )

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            artifact_filename("MBA-FPX5006", "transcript", 1)


if __name__ == "__main__":
    unittest.main()
