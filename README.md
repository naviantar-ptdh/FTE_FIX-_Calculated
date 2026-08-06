# FTE Calculator v2 — PT Dharma Henwa

> **v6 (redesign tampilan).** Lihat `CATATAN_REDESIGN_V6.md` untuk daftar
> perubahan tampilan, alasan pemilihan warna, dan cara pakai mode demo.
> Logika perhitungan tidak berubah dari v5.

## Menjalankan cepat tanpa Google Sheets

```bash
pip install -r requirements.txt
FTE_DEMO=1 streamlit run app.py
```

Mode demo memakai data contoh bawaan (`demo_data.py`). Tanpa `FTE_DEMO=1`,
aplikasi membaca Google Sheets sesuai `config.SPREADSHEET_ID`.


## Apa yang berubah dari v1

1. **Data unit tidak diinput manual lagi.** Otomatis di-lookup dari sheet **`Sheet9`**
   berdasarkan Site yang dipilih (kolom Category, Jenis Unit, Jumlah Unit, PA per blok Site).
2. **Jarak (km) otomatis di-lookup** dari `BACKEND` (seksi baru **"Jarak"**, baris 60–63),
   tidak ada input manual/slider lagi.
3. **Site, Competency Factor, dan tombol "Hitung FTE" dipindah ke sidebar.** Area utama
   kosong (placeholder) sampai tombol Hitung ditekan.
4. **Summary Mechanic dikelompokkan per Kategori** memakai BACKEND seksi baru
   **"Clasification"** (baris 66–87): Digger, Hauler, Auxilary Track, Auxilary wheel,
   Support & Facility, dst. Kategori yang hasilnya nihil untuk Site tsb otomatis disembunyikan.
5. **Welder & Electrician langsung Total keseluruhan** (tidak dipecah per kategori unit),
   sama seperti baris Summary Manpower di "Final Calculation".
6. **Foreman & Supervisor** dihitung otomatis per kategori Operational (Digger, Hauler,
   Auxilary Wheel, Auxilary Track, Support & Facility, Electrician, Welding & Fabrication)
   memakai data sheet **"Hasil Staff"**:
   - Foreman = `CEILING((BebanAdmin + JumlahMekanik(M1) * JamSupervisi * EWDY) * AreaKerja / JamEfektif, 1)`
   - Supervisor = `ROUND(Foreman * 0.5, 0)`
   - "Jumlah Mekanik" = **M1** dari kategori Mechanic terkait (untuk Digger/Hauler/dst),
     atau M1 Electrician/Welder Total (untuk posisi Electrician/Welding & Fabrication).
7. **FTE Planner** (Maintenance Planning, PLM Scheduling & PCR, dst.) dihitung terpisah,
   tidak butuh input Jumlah Mekanik:
   `FTE = CEILING((BebanAdmin / JamEfektif * AreaKerja) * RasioRoster, 1)`
8. Tombol **"Tampilkan Detail"** menampilkan rincian per-unit (seperti v1) + rincian
   Foreman/Supervisor/Planner.
9. Tabel unit **read-only secara default**; tersedia 10 baris per halaman (Sebelumnya/Berikutnya).
   Mode edit (session-only, TIDAK tersimpan ke Google Sheets, otomatis kembali normal
   setelah refresh) dibuka lewat tombol kecil **"🔒 Edit"** di pojok kanan atas dengan
   password (lihat `config.UNIT_EDIT_PASSWORD`, default `DHRising`).
10. Placeholder gambar (`gambar1.png`) ditampilkan di area hasil sebelum tombol Hitung
    ditekan. **Taruh file `gambar1.png` di folder yang sama dengan `app.py`** untuk
    menggantikan placeholder bawaan (kotak putus-putus).

## ⚠️ Catatan gap data yang saya temukan di file Excel yang di-upload

Ini bukan bug kode — perlu dilengkapi di sumber data (Google Sheets) sebelum deploy:

- **Sheet "Hasil Staff"**: hanya ada baris untuk site **KCP** dan **ACP**. Site **BCP**
  belum punya baris sama sekali → saat BCP dipilih, section Foreman/Supervisor & Planner
  otomatis kosong (sesuai instruksi Anda).
- **Site ACP** di "Hasil Staff": kolom **Area Kerja** kosong untuk posisi Digger, Hauler,
  Support & Facility, Electrician, dan Welding & Fabrication → posisi-posisi itu otomatis
  tidak muncul untuk ACP sampai Area Kerja-nya diisi.
- **Baris Planner untuk KCP tampil dobel** (baris 10–17 dan 25–32 di "Hasil Staff" sama-sama
  berisi `Site = KCP`, padahal baris 25–32 sepertinya dimaksudkan untuk site lain). Perlu
  diperbaiki nilai kolom Site-nya.
- **Site BCP** (di Sheet9) berisi ±53 baris unit dengan Category yang belum ada di BACKEND
  "Load Factor" (mis. "Stemming Truck", "Slurry Set") → baris ini dilewati otomatis oleh
  aplikasi (tidak error) dan akan terlihat di expander "⚠️ baris unit dilewati" saat
  "Tampilkan Detail" dibuka. Tambahkan Sub Category tsb ke BACKEND agar ikut terhitung.

## Menjalankan secara lokal (testing tanpa Google Sheets)

Set environment variable ke file CSV lokal (header=None, hasil export sheet apa adanya):

```bash
export BACKEND_CSV_PATH=/path/ke/BACKEND.csv
export UNIT_CSV_PATH=/path/ke/Sheet9.csv
export STAFF_CSV_PATH=/path/ke/HasilStaff.csv
streamlit run app.py
```

Tanpa env var di atas, aplikasi otomatis fetch dari Google Sheets sesuai `config.SPREADSHEET_ID`.
