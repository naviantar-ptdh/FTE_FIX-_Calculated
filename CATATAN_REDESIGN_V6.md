# Catatan redesign v6 — "Looker light"

File yang diubah: `app.py`, `theme.py`, `charts.py` (ditulis ulang) dan
`requirements.txt`. File baru: `demo_data.py`, `assets/images/placeholder.jpg`.

**`calculator.py`, `data_loader.py`, dan `config.py` tidak disentuh sama sekali.**
Semua rumus, pembulatan, dan cara baca spreadsheet tetap sama seperti sebelumnya.

---

## Arah desain

Mengikuti nuansa dashboard Looker Studio PTDH: header band gradasi orange
dengan logo putih, kartu putih di atas background abu terang, judul kartu
berwarna sesuai aksen, dan strip navy untuk legenda.

---

## Tiga bug yang diperbaiki (bukan cuma soal selera)

### 1. Kartu tidak pernah membungkus isinya

v5 menulis `<div class="ptdh-card">` di satu `st.markdown`, lalu `</div>` di
`st.markdown` berikutnya. Itu tidak bisa bekerja: Streamlit merender tiap
elemen di container terpisah, jadi browser menutup sendiri div yang
menggantung. Akibatnya chart jatuh **di luar** kartu dan yang tampil hanya
strip putih kosong di atas chart.

Sekarang kartu dibangun dari `st.container(key="card_...")` lalu di-style lewat
selector `div[class*="st-key-card_"]`. Sudah dicek dari DOM: setiap
`.js-plotly-plot` benar-benar berada di dalam elemen kartunya.

### 2. Placeholder 8,9 MB di-encode ulang setiap render

`gambar1.png` berukuran 4000×3000 (8,9 MB) dan di-base64 ke dalam HTML setiap
kali halaman digambar. Itu penyebab utama aplikasi terasa berat. Sekarang area
kosong memakai empty state HTML (nol byte gambar). File aslinya dibiarkan di
`assets/` supaya tidak ada yang hilang, tapi tidak pernah dimuat lagi.

### 3. Welder & Electrician digambar sebagai garis datar per section

Nilai kedua role itu company-wide, bukan per-section. Menggambarnya sebagai
garis yang melintasi semua section membuatnya seolah-olah punya nilai di tiap
section. Sekarang keduanya jadi batang terpisah, dipisahkan garis putus-putus
dan diberi label "Company-wide".

---

## Warna

Masalah v5 bukan gradasi M1–M3-nya, tapi jarak hue antar role: orange
`#FF6805`, salmon `#FF9182`, dan kuning-tua `#E0A400` semuanya berada di hue
15–45°, jadi ketiganya terlihat seperti satu warna yang buram.

| | v5 | v6 | alasan |
|---|---|---|---|
| Mekanik | `#FF6805` | `#FF6805` | populasi terbesar, layak dapat warna brand |
| Electrician | `#E0A400` | `#FFC300` | kuning listrik, dari swatch Yellow |
| Welder | `#FF9182` | `#0E7C86` | biru-teal busur las, turunan swatch Steel Blue |

Pembagian tugas warna dibuat tegas supaya tidak ada dua sistem warna yang
bersaing dalam satu layar:

* Chart yang membandingkan **role** → 3 hue berbeda (`ROLE_COLORS`).
* Chart yang membandingkan **level** → satu ramp orange (`LEVEL_SHADES`),
  M1 paling pekat karena paling senior dan paling mahal.

Arah gradasi juga dibalik dari v5: sekarang nilai yang lebih "berat" selalu
lebih pekat, jadi bobot visualnya sejalan dengan bobot maknanya.

---

## Susunan layar mode Basecase

Sesuai permintaan: total dulu, komposisi di sampingnya, lalu rincian section
dengan cost di sampingnya.

```
header band (orange, logo putih)
strip legenda role
KPI: Total FTE │ Mekanik │ Electrician │ Welder │ Cost per bulan
Total FTE per Section (bar horizontal)   │ Komposisi M1–M3 (donut)
Persebaran M1–M3 per Section (stacked)   │ Cost per bulan (speedometer)
Tab: Ringkasan │ Foreman & SPV │ Planner │ Data Unit │ Detail per Unit
```

Diukur dari DOM di viewport 1920×1080: elemen terbawah dari dua baris chart
berakhir di **y = 1072**, jadi seluruh bagian utama muat dalam satu layar
tanpa scroll. Tab rincian di bawahnya memang perlu discroll sedikit.

Di laptop 1366×768 baris kedua sudah di bawah lipatan — ini konsekuensi dari
pilihan "boleh sedikit scroll".

---

## Speedometer cost

Skalanya relatif: **0 sampai cost site tertinggi**, sesuai pilihan Anda. Cost
tiap site dihitung sekali lalu disimpan per nilai competency factor
(`site_cost_scale`), supaya angka pembandingnya konsisten.

* Jarum = cost site yang sedang dibuka.
* Garis navy = rata-rata antar-site.
* Keterangan skala selalu ditulis di bawah gauge, karena skala relatif tanpa
  keterangan mudah disalahartikan sebagai persentase pencapaian target.

Konsekuensi yang perlu diketahui: site termahal akan selalu menunjuk 100%.
Kalau nanti mau jarum yang bisa dibaca sebagai "boros/hemat", perlu angka
budget — beri tahu saya dan itu bisa ditambahkan sebagai input.

Soal alasan v5 menggantinya jadi stacked bar: `go.Indicator` memang tidak
mendukung hover, itu batas Plotly. Tapi menghapus gauge bukan jawabannya.
Sekarang angkanya ditulis besar di tengah gauge dan rinciannya disediakan
sebagai batang komposisi yang bisa di-hover plus tabel FTE/cost per role —
informasi penting tidak disembunyikan di dalam tooltip.

---

## Mode demo (untuk cek tampilan tanpa Google Sheets)

```bash
FTE_DEMO=1 streamlit run app.py
```

Data contoh di `demo_data.py`: 3 site (KCP/ACP/BCP), 13 sub category, 5
kategori unit, lengkap dengan Hasil Staff. Angkanya karangan — jangan dipakai
untuk keputusan manpower. Tanpa `FTE_DEMO=1`, aplikasi tetap membaca Google
Sheets seperti biasa.

---

## Detail kecil yang ikut dibenahi

* Angka pakai format Indonesia (`Rp 3.310.000.000`, `26,8%`, `0,60`) —
  sebelumnya Plotly menulis `26.8%` dengan titik.
* Semua kolom angka pakai tabular numerals, jadi tidak "goyang" saat berubah.
* Tinggi KPI card dipatok seragam; sebelumnya kartu Cost jadi 119px sementara
  yang lain 91px di laptop 1366, membuat barisnya tidak rata.
* Tiap KPI card sekarang membawa informasi berbeda. Sebelumnya beberapa kartu
  mengulang angka yang sudah ada di donut.
* Stacked bar diberi label total di atas tiap tumpukan, mengikuti kebiasaan
  dashboard Looker rujukan yang selalu menempelkan angka pada batang.
* Data Unit dan tabel detail dipindah ke tab, supaya bagian atas layar bersih.
* Teks kosong/error ditulis sebagai arahan tindakan, bukan sekadar pesan
  ("Lengkapi kolom Area Kerja … di sheet Hasil Staff", bukan "data tidak ada").
* `requirements.txt` dinaikkan ke `streamlit>=1.49` karena
  `st.container(key=...)` dan `width="stretch"` butuh versi itu.

---

## Yang masih perlu Anda cek

* Gap data yang dilaporkan di README sebelumnya (site BCP tidak ada di sheet
  "Hasil Staff", Area Kerja ACP kosong, baris Planner KCP dobel) belum
  tersentuh — itu perlu diperbaiki di spreadsheet, bukan di kode.
* Warna final: kalau menurut Anda teal Welder terlalu keluar dari brand,
  tinggal ubah satu baris di `ROLE_COLORS` (`theme.py`), sisanya (gradasi,
  legenda, semua chart) menyesuaikan otomatis.
