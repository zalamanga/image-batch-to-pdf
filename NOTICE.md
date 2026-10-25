# Asal source

Source awal adalah skrip lokal `jpg_to_pdf.py` berukuran 389 byte. Skrip mencari `.jpg`, mengurutkan stem dengan `int`, mengonversi RGB, lalu menyimpan `katalog.pdf`. Tidak ditemukan nama author, lisensi, atau remote Git pada sumber ini. Tidak ada lisensi baru yang dinyatakan untuk skrip awal.

Versi ini mempertahankan tugas konversi gambar ke PDF dan dependency Pillow. Perubahannya mencakup CLI eksplisit, nama natural, PNG/JPEG, orientasi, transparansi, batas batch, penutupan gambar, penulisan sementara dan perlindungan output yang sudah ada. Source asli tetap dipertahankan di cadangan kerja pribadi.

Scraper sertifikasi dan empat spreadsheet yang berdampingan dengan skrip asal adalah pekerjaan berbeda. Isi dan data bisnisnya tidak menjadi bagian repository ini.

Tiga preview README berasal dari PDF hasil CLI dengan fixture warna/teks sintetis. Preview bukan screenshot aplikasi desktop atau salinan dokumen pengguna. Font pada fixture hanya diraster sebagai bagian gambar; tidak ada file font yang disertakan.

Dependency memiliki lisensinya sendiri: [Pillow](https://github.com/python-pillow/Pillow/blob/main/LICENSE) dan, untuk tes, [pypdf](https://github.com/py-pdf/pypdf/blob/main/LICENSE). Lihat proyek upstream untuk ketentuan dependency; catatan ini tidak mengganti lisensi mereka.
