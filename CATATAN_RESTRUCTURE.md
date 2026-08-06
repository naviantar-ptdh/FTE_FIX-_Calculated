# Catatan Restructure (baca ini dulu)

## Soal branch yang banyak
Setelah dicek: kode di branch `main`, `restructure-folders`, dan `Aset` itu **identik**
(app.py/calculator.py/charts.py/config.py/data_loader.py/theme.py sama persis).
Bedanya cuma branch `Aset` punya file icon/gambar tambahan yang belum dipakai kodenya.
Jadi tidak ada kode yang "hilang" atau konflik — aman untuk disederhanakan.

Saran beres-beres repo:
1. Jadikan `main` sebagai satu-satunya branch aktif.
2. Merge folder `assets/` dari paket ini (hasil kurasi icon dari branch `Aset`) ke `main`.
3. Hapus branch `restructure-folders` dan `Aset` (isinya sudah masuk semua ke sini).

## Apa yang diubah di paket ini
- **theme.py** — sistem warna dibangun dari kode HEX asli swatch DH (Primary: Orange/Black/White,
  Secondary: Salmon/Yellow/Blue/Steel/Gray). Tiap Role (Mechanic/Welder/Electrician) punya satu warna
  dasar, lalu M1→M3 diturunkan otomatis via interpolasi HSL (terang → gelap) — jadi gradasinya selalu
  senada, tidak ada lagi warna M1-M3 yang jomplang. Tambah typography Public Sans, layout sidebar gelap
  + topbar, dan component library kecil (card, kpi-card, badge, tabel dengan total baris & kolom).
- **charts.py** —
  - Cost chart lama pakai `go.Indicator` (gauge) yang memang **tidak bisa di-hover** di Plotly. Diganti
    jadi horizontal stacked-bar 1 baris yang tetap terlihat seperti ringkasan cost, tapi tiap segmennya
    sekarang bisa di-hover sama seperti chart lain (nama role+level, Rp, dan % dari total).
  - Line chart lama isinya breakdown M1-M3 per section — sama persis fungsinya dengan bar chart di
    bawahnya. Sekarang line chart cuma nampilin **total per Role** (Mekanik per section + garis putus-putus
    Welder/Electrician company-wide), sementara bar chart di bawahnya yang jadi tempat lihat persebaran
    M1-M3-nya. Biar orang baca dari atas: total dulu, baru breakdown di bawahnya.
  - Semua warna bar M1-M3 sekarang ikut warna dasar per-Role (bukan warna Level global lintas-entitas),
    supaya Mekanik/Welder/Electrician masing-masing punya identitas warna sendiri yang natural.
- **app.py** —
  - Layout dipindah dari toolbar horizontal ke **sidebar gelap** (nav + parameter Site/Competency Factor/
    Refresh/Hitung) + **topbar** (logo + breadcrumb mode & site aktif) — pola dashboard standar.
  - Tabel Summary (Mechanic per Kategori, Welder/Electrician Total, Foreman/Supervisor, Planner) sekarang
    render pakai `theme.summary_table_html()`: total per baris **dan** per kolom, plus baris TOTAL di
    paling bawah — bukan cuma total vertikal seperti sebelumnya.
  - KPI card sekarang pakai icon PNG mechanic/welding/electrician dari `assets/images/` (bukan emoji).
- **calculator.py / data_loader.py / config.py** — TIDAK diubah sama sekali. Logika perhitungan (yang
  sudah melalui banyak proses debugging & validasi ke Excel) dibiarkan seperti apa adanya, ini murni
  redesign tampilan.
- **assets/** — folder baru, hasil kurasi dari branch `Aset`: `assets/icons/*.svg` (18 icon terpilih,
  sudah dinormalisasi ke `currentColor` supaya bisa diwarnai lewat CSS) dan `assets/images/*.png`
  (logo, mechanic/welding/electrician, placeholder).

## Yang masih perlu kamu cek
- Warna final (`theme.ROLE_COLORS` / `ROLE_SHADES`) dibangun otomatis dari swatch — kalau ada warna yang
  menurutmu kurang pas secara visual, tinggal ubah 3 baris di `ROLE_COLORS` (theme.py), sisanya (gradasi
  M1-M3, legend, chart) ikut menyesuaikan otomatis.
- Belum sempat generate screenshot langsung dari Streamlit (butuh Chrome headless yang tidak tersedia di
  sandbox ini) — coba jalankan `streamlit run app.py` di lokal untuk lihat hasilnya, lalu kabari kalau ada
  yang perlu disesuaikan lagi.
