import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader

import image_batch_to_pdf as app


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "images"
        self.input.mkdir()
        self.output = self.root / "result.pdf"

    def tearDown(self):
        self.temp.cleanup()

    def image(self, name="1.jpg", color="red", size=(60, 30), mode="RGB", **options):
        with Image.new(mode, size, color) as image:
            image.save(self.input / name, **options)
        return self.input / name

    def leftovers(self):
        return list(self.root.glob(".image-pdf-*.tmp"))

    def test_numeric_order_original_regression_and_unchanged_files(self):
        for name, color in [("10.jpg", "blue"), ("2.jpg", "green"), ("1.jpg", "red")]:
            self.image(name, color)
        before = {p.name: p.read_bytes() for p in self.input.iterdir()}
        pages = app.convert(self.input, self.output)
        self.assertEqual([p.path.name for p in pages], ["1.jpg", "2.jpg", "10.jpg"])
        with self.output.open("rb") as stream:
            reader = PdfReader(stream)
            self.assertEqual(len(reader.pages), 3)
            for page, channel in zip(reader.pages, [0, 1, 2]):
                with page.images[0].image as image:
                    pixel = image.getpixel((10, 10))
                    self.assertGreater(pixel[channel], max(pixel[i] for i in range(3) if i != channel) + 100)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.input.iterdir()})
        self.assertEqual(self.leftovers(), [])

    def test_natural_names_case_and_subdirectories(self):
        for name in ["page10.PNG", "page2.jpeg", "page1.jpg"]:
            self.image(name)
        (self.input / "nested").mkdir()
        (self.input / "readme.txt").write_text("not an image")
        self.assertEqual([p.path.name for p in app.plan(self.input)], ["page1.jpg", "page2.jpeg", "page10.PNG"])

    def test_empty_and_missing_directory(self):
        for directory in [self.input, self.root / "missing"]:
            with self.assertRaises(app.ConversionError):
                app.convert(directory, self.output)
        self.assertFalse(self.output.exists())

    def test_corrupt_later_image_does_not_create_output(self):
        self.image()
        (self.input / "2.png").write_bytes(b"not a png")
        with self.assertRaises(app.ConversionError):
            app.convert(self.input, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.leftovers(), [])

    def test_refuses_existing_output_and_can_explicitly_replace(self):
        self.image()
        self.output.write_bytes(b"keep original")
        with self.assertRaises(app.ConversionError):
            app.convert(self.input, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep original")
        app.convert(self.input, self.output, overwrite=True)
        self.assertTrue(self.output.read_bytes().startswith(b"%PDF-"))

    def test_failed_save_preserves_old_output_and_removes_temporary(self):
        self.image()
        self.output.write_bytes(b"keep original")
        with patch.object(Image.Image, "save", side_effect=OSError("disk full")):
            with self.assertRaises(app.ConversionError):
                app.convert(self.input, self.output, overwrite=True)
        self.assertEqual(self.output.read_bytes(), b"keep original")
        self.assertEqual(self.leftovers(), [])

    def test_no_overwrite_does_not_replace_concurrent_writer(self):
        self.image()
        original = app.os.link
        def race(source, destination):
            Path(destination).write_bytes(b"other writer")
            return original(source, destination)
        with patch.object(app.os, "link", side_effect=race):
            with self.assertRaises(app.ConversionError):
                app.convert(self.input, self.output)
        self.assertEqual(self.output.read_bytes(), b"other writer")
        self.assertEqual(self.leftovers(), [])

    def test_cleanup_failure_does_not_report_published_pdf_as_failed(self):
        self.image()
        output = io.StringIO()
        original = Path.unlink
        def denied(path, *args, **kwargs):
            if path.name.startswith(".image-pdf-"):
                raise PermissionError("shared by another process")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", denied), contextlib.redirect_stderr(output), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main([str(self.input), str(self.output)]), 0)
        self.assertTrue(self.output.read_bytes().startswith(b"%PDF-"))
        self.assertIn("PDF sudah tersimpan", output.getvalue())
        for path in self.leftovers():
            original(path)

    def test_cleanup_failure_does_not_mask_original_conversion_error(self):
        self.image()
        error = io.StringIO()
        original = Path.unlink
        def denied(path, *args, **kwargs):
            if path.name.startswith(".image-pdf-"):
                raise PermissionError("shared by another process")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", denied), patch.object(Image.Image, "save", side_effect=OSError("disk full")), contextlib.redirect_stderr(error):
            self.assertEqual(app.main([str(self.input), str(self.output)]), 2)
        self.assertIn("Konversi belum selesai", error.getvalue())
        self.assertIn("Gagal: PDF belum tersimpan", error.getvalue())
        self.assertFalse(self.output.exists())
        for path in self.leftovers():
            original(path)

    def test_filesystem_without_hardlinks_reports_failure_without_output(self):
        self.image()
        with patch.object(app.os, "link", side_effect=OSError("unsupported")):
            with self.assertRaisesRegex(app.ConversionError, "hard link"):
                app.convert(self.input, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.leftovers(), [])

    def test_resolution_changes_page_dimensions_without_resampling(self):
        self.image(size=(600, 300))
        app.convert(self.input, self.output, dpi=150)
        with self.output.open("rb") as stream:
            page = PdfReader(stream).pages[0]
            self.assertEqual(tuple(map(float, page.mediabox[2:])), (288.0, 144.0))
            self.assertEqual(page.images[0].image.size, (600, 300))

    def test_exif_orientation_applies_before_pdf(self):
        exif = Image.Exif()
        exif[274] = 6
        self.image(size=(60, 30), exif=exif)
        app.convert(self.input, self.output, dpi=72)
        with self.output.open("rb") as stream:
            page = PdfReader(stream).pages[0]
            self.assertEqual(tuple(map(float, page.mediabox[2:])), (30.0, 60.0))

    def test_transparency_becomes_white_and_metadata_is_not_carried(self):
        self.image("1.png", (255, 0, 0, 0), mode="RGBA")
        app.convert(self.input, self.output)
        with self.output.open("rb") as stream:
            reader = PdfReader(stream)
            with reader.pages[0].images[0].image as image:
                self.assertTrue(all(v >= 250 for v in image.getpixel((10, 10))))
            self.assertIn(reader.metadata.author, [None, ""])
            self.assertEqual(reader.metadata.title, "Image batch")

    def test_palette_transparency(self):
        with Image.new("P", (10, 10)) as image:
            image.putpalette([255, 0, 0] + [0, 0, 0] * 255)
            image.save(self.input / "1.png", transparency=0)
        app.convert(self.input, self.output)
        with self.output.open("rb") as stream:
            with PdfReader(stream).pages[0].images[0].image as image:
                self.assertTrue(all(v >= 250 for v in image.getpixel((5, 5))))

    def test_grayscale_and_cmyk_pages_have_readable_rgb_output(self):
        self.image("1.jpg", 128, mode="L")
        self.image("2.jpg", (0, 255, 255, 0), mode="CMYK")
        app.convert(self.input, self.output)
        with self.output.open("rb") as stream:
            reader = PdfReader(stream)
            self.assertEqual(len(reader.pages), 2)
            for page in reader.pages:
                self.assertEqual(page.images[0].image.mode, "RGB")

    def test_truncated_jpeg_during_decode_leaves_no_output(self):
        path = self.image(size=(100, 100))
        path.write_bytes(path.read_bytes()[:-15])
        with self.assertRaises(app.ConversionError):
            app.convert(self.input, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.leftovers(), [])

    def test_animated_png_rejected(self):
        with Image.new("RGB", (10, 10), "red") as first, Image.new("RGB", (10, 10), "blue") as second:
            first.save(self.input / "1.png", save_all=True, append_images=[second], duration=100)
        with self.assertRaisesRegex(app.ConversionError, "animasi"):
            app.convert(self.input, self.output)
        self.assertFalse(self.output.exists())

    def test_all_resource_budgets_are_checked_before_pdf_output(self):
        self.image("1.jpg", size=(10, 10))
        self.image("2.jpg", size=(10, 10))
        for limits in [app.Limits(pages=1), app.Limits(page_pixels=99), app.Limits(total_pixels=199), app.Limits(total_bytes=1)]:
            with self.subTest(limits=limits), self.assertRaises(app.ConversionError):
                app.convert(self.input, self.output, limits=limits)
        self.assertFalse(self.output.exists())

    def test_changed_input_after_plan_rejected(self):
        original_path = self.image()
        original_plan = app.plan
        def changed(*args, **kwargs):
            pages = original_plan(*args, **kwargs)
            original_path.write_bytes(b"changed after validation")
            return pages
        with patch.object(app, "plan", side_effect=changed):
            with self.assertRaisesRegex(app.ConversionError, "berubah"):
                app.convert(self.input, self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_dpi_output_type_or_missing_parent(self):
        self.image()
        for dpi in [0, 35, 601, 1.5, True]:
            with self.subTest(dpi=dpi), self.assertRaises(app.ConversionError):
                app.convert(self.input, self.output, dpi=dpi)
        for output in [self.root / "result.jpg", self.root / "missing" / "out.pdf", self.root / "folder.pdf"]:
            if output.name == "folder.pdf":
                output.mkdir()
            with self.assertRaises(app.ConversionError):
                app.convert(self.input, output)

    def test_list_and_error_cli_exit_codes(self):
        self.image()
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            self.assertEqual(app.main([str(self.input), "--list"]), 0)
        self.assertIn("001  1.jpg", text.getvalue())
        self.assertFalse(self.output.exists())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main([str(self.root / "missing"), str(self.output)]), 2)


if __name__ == "__main__":
    unittest.main()
