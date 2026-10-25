"""Convert a bounded local JPEG/PNG batch into one image-only PDF."""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import tempfile
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


class ConversionError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    pages: int = 100
    page_pixels: int = 10_000_000
    total_pixels: int = 40_000_000
    total_bytes: int = 100 * 1024 * 1024


@dataclass(frozen=True)
class Page:
    path: Path
    size: tuple[int, int]
    stamp: tuple[int, int, int, int]


def _stamp(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def natural_key(path):
    parts = re.split(r"([0-9]+)", path.name.casefold())
    tokens = tuple((1, int(part)) if part.isascii() and part.isdigit() else (0, part) for part in parts)
    return tokens, path.name.casefold(), path.name


def plan(input_directory, limits=Limits()):
    directory = Path(input_directory)
    if directory.is_symlink():
        raise ConversionError("Folder input tidak boleh berupa tautan simbolis.")
    if not directory.is_dir():
        raise ConversionError("Folder input tidak ditemukan.")
    candidates = []
    try:
        paths = list(directory.iterdir())
    except OSError as error:
        raise ConversionError("Folder input tidak dapat dibaca.") from error
    for path in paths:
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        if path.is_symlink():
            raise ConversionError(f"Tautan simbolis tidak diterima: {path.name}")
        if path.is_file():
            candidates.append(path)
    candidates.sort(key=natural_key)
    if not candidates:
        raise ConversionError("Tidak ada JPG, JPEG, atau PNG di folder input.")
    if len(candidates) > limits.pages:
        raise ConversionError(f"Maksimal {limits.pages} gambar per PDF.")
    pages = []
    total_pixels = total_bytes = 0
    for path in candidates:
        try:
            with path.open("rb") as stream, warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                stamp = _stamp(os.fstat(stream.fileno()))
                total_bytes += stamp[2]
                if total_bytes > limits.total_bytes:
                    raise ConversionError("Ukuran file batch melampaui batas byte.")
                with Image.open(stream, formats=["JPEG", "PNG"]) as image:
                    width, height = image.size
                    pixels = width * height
                    total_pixels += pixels
                    if pixels < 1 or pixels > limits.page_pixels:
                        raise ConversionError(f"Ukuran piksel gambar melampaui batas: {path.name}")
                    if total_pixels > limits.total_pixels:
                        raise ConversionError("Jumlah piksel batch melampaui batas.")
                    if getattr(image, "n_frames", 1) != 1:
                        raise ConversionError(f"Gambar animasi tidak diterima: {path.name}")
                    image.verify()
                if _stamp(os.fstat(stream.fileno())) != stamp:
                    raise ConversionError(f"Gambar berubah ketika diperiksa: {path.name}")
                pages.append(Page(path, (width, height), stamp))
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ConversionError(f"Gambar tidak dapat dibaca: {path.name}") from error
    return pages


def _rgb_page(page):
    try:
        with page.path.open("rb") as stream, warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            if page.path.is_symlink() or _stamp(os.fstat(stream.fileno())) != page.stamp:
                raise ConversionError(f"Gambar berubah setelah diperiksa: {page.path.name}")
            with Image.open(stream, formats=["JPEG", "PNG"]) as image:
                if image.size != page.size or getattr(image, "n_frames", 1) != 1:
                    raise ConversionError(f"Gambar berubah setelah diperiksa: {page.path.name}")
                with ImageOps.exif_transpose(image) as oriented:
                    rgb = Image.new("RGB", oriented.size, "white")
                    try:
                        if "A" in oriented.getbands() or "transparency" in oriented.info:
                            with oriented.convert("RGBA") as rgba:
                                with rgba.getchannel("A") as alpha:
                                    rgb.paste(rgba, (0, 0), alpha)
                        else:
                            with oriented.convert("RGB") as converted:
                                rgb.paste(converted)
                        if _stamp(os.fstat(stream.fileno())) != page.stamp:
                            raise ConversionError(f"Gambar berubah saat dibaca: {page.path.name}")
                        return rgb
                    except BaseException:
                        rgb.close()
                        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ConversionError(f"Gambar tidak dapat didekode: {page.path.name}") from error


def convert(input_directory, output, *, dpi=150, overwrite=False, limits=Limits()):
    if type(dpi) is not int or not 36 <= dpi <= 600:
        raise ConversionError("DPI harus bilangan bulat dari 36 sampai 600.")
    destination = Path(output).absolute()
    if destination.suffix.lower() != ".pdf":
        raise ConversionError("Output harus memakai ekstensi .pdf.")
    if not destination.parent.is_dir():
        raise ConversionError("Folder tujuan belum ada.")
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise ConversionError("Output harus berupa file biasa, bukan folder atau tautan.")
    if destination.exists() and not overwrite:
        raise ConversionError("Output sudah ada. Pilih nama baru atau gunakan --overwrite.")
    pages = plan(input_directory, limits)
    images = []
    temporary = None
    published = False
    try:
        for page in pages:
            images.append(_rgb_page(page))
        with tempfile.NamedTemporaryFile(prefix=".image-pdf-", suffix=".tmp", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            images[0].save(stream, format="PDF", save_all=True, append_images=images[1:],
                           resolution=dpi, quality=95, title="Image batch", author="", creator="image-batch-to-pdf")
            stream.flush()
            os.fsync(stream.fileno())
        if destination.is_symlink():
            raise ConversionError("Output berubah menjadi tautan simbolis.")
        if overwrite:
            os.replace(temporary, destination)
        else:
            # A successful existence check followed by replace could overwrite a concurrent writer.
            os.link(temporary, destination)
        published = True
        return pages
    except FileExistsError as error:
        raise ConversionError("Output dibuat proses lain. Pilih nama baru.") from error
    except OSError as error:
        raise ConversionError("PDF belum tersimpan. Periksa ruang/izin tujuan; mode tanpa overwrite memerlukan hard link pada filesystem tujuan.") from error
    finally:
        for image in images:
            image.close()
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                outcome = "PDF sudah tersimpan" if published else "Konversi belum selesai"
                print(f"Peringatan: {outcome}; file sementara belum terhapus: {temporary}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gabungkan JPG/JPEG/PNG lokal menjadi PDF gambar, urutan nama natural.")
    parser.add_argument("input", type=Path, help="folder gambar, hanya file langsung di dalamnya")
    parser.add_argument("output", type=Path, nargs="?", help="file PDF baru; folder tujuannya harus sudah ada")
    parser.add_argument("--dpi", type=int, default=150, help="ukuran fisik halaman, 36..600 (default150)")
    parser.add_argument("--overwrite", action="store_true", help="ganti PDF tujuan yang sudah ada secara eksplisit")
    parser.add_argument("--list", action="store_true", help="periksa dan tampilkan urutan tanpa membuat PDF")
    args = parser.parse_args(argv)
    if not args.list and args.output is None:
        parser.error("output wajib diisi kecuali memakai --list")
    try:
        pages = plan(args.input) if args.list else convert(args.input, args.output, dpi=args.dpi, overwrite=args.overwrite)
        for index, page in enumerate(pages, 1):
            print(f"{index:03d}  {page.path.name}")
        print(f"{len(pages)} gambar diperiksa." if args.list else f"PDF tersimpan: {args.output} ({len(pages)} halaman).")
        return 0
    except ConversionError as error:
        print(f"Gagal: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
