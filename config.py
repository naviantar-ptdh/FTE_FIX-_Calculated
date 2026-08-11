"""
Konfigurasi global untuk FTE Calculator.
"""

# ID Google Spreadsheet (BACKEND) - sumber data referensi
SPREADSHEET_ID = "1YRvXt0AE-dVBVwRvLtsb57Qz8DYd9YbVQlVbRD31C7I"
BACKEND_SHEET_NAME = "BACKEND"
UNIT_SHEET_NAME = "Sheet9"           # data input Unit per site (v2, auto-lookup)
STAFF_SHEET_NAME = "Hasil Staff"     # data FTE Staff (Foreman/SPV/Planner) per site (v2)

# Fallback gid untuk tab Unit (Sheet9), dipakai jika fetch berbasis nama tab
# ("sheet=Sheet9") gagal/mengambil tab yang salah. Ambil dari URL saat tab itu
# dibuka: https://docs.google.com/spreadsheets/d/<ID>/edit?gid=<GID_INI>
UNIT_SHEET_GID = "433093577"

# Sama seperti UNIT_SHEET_GID di atas, tapi untuk tab "Hasil Staff". Ambil dari
# URL saat tab itu dibuka: https://docs.google.com/spreadsheets/d/<ID>/edit?gid=<GID_INI>
STAFF_SHEET_GID = "997738201"

# Password sederhana untuk membuka mode edit tabel unit (edit hanya sesi ini, tidak
# tersimpan ke Google Sheets, dan akan kembali normal jika halaman di-refresh).
UNIT_EDIT_PASSWORD = "DHRising"

# Endpoint export CSV publik (spreadsheet harus di-share minimal "Anyone with link - Viewer")
#
# Parameter `_cb` (cache buster) WAJIB ada. Endpoint gviz/export Google
# dilayani lewat CDN dan sering mengembalikan salinan LAMA selama beberapa
# menit walau spreadsheet-nya sudah diubah. Karena URL-nya persis sama, CDN
# menganggapnya permintaan yang sama. Menyisipkan nilai yang selalu berubah
# membuat tiap pengambilan jadi URL unik, sehingga selalu menembus ke sumber.
#
# Ini tidak membuat aplikasi jadi sering menembak Google: URL hanya dibangun
# saat cache Streamlit meleset (lihat CACHE_TTL_SECONDS), bukan tiap rerun.
def gsheet_csv_url(sheet_name: str, spreadsheet_id: str = SPREADSHEET_ID) -> str:
    from urllib.parse import quote
    import time
    return (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={quote(sheet_name)}"
        f"&_cb={int(time.time())}"
    )

# Konstanta rumus (sesuai sheet "Final Calculation")
BASE_MECHANIC_HOURS = 12       # basis jam kerja mekanik/hari sebelum dikurangi Lost Time & travel
HOURS_PER_DAY = 24             # basis 24 jam untuk breakdown hours
TRAVEL_DIVISOR = 30            # pembagi Jarak (KM) -> jam perjalanan (D4/30)
# Sebelumnya 40 — itu salah salin. Kedua sheet acuan ('Final Calculation' dan
# 'Final Calculation RACI Granular') sama-sama memakai =12-$D$5-($D$4/30).
# Diverifikasi: dengan 30, kolom Mechanic cocok persis di 67 dari 67 baris
# sheet RACI Granular; dengan 40 tidak ada satu pun yang cocok.

# Cost rate per FTE (Rp) - ditetapkan eksplisit oleh user, bukan dari BACKEND
COST_RATE = {
    "M1": 10_000_000,
    "M2": 8_500_000,
    "M3": 6_500_000,
}

# Cost rate per staff FTE (Rp) - asumsi manual dari user, bukan dari BACKEND
STAFF_COST_RATE = {
    "Foreman": 9_000_000,
    "Supervisor": 12_000_000,
    "Planner": 9_000_000,      # sama dengan Foreman, sesuai arahan user
    # PLACEHOLDER - user belum memberi tarif Superintendent. Ganti angka ini
    # begitu tarif resminya ada; seluruh tabel Cost membacanya dari sini.
    "Superintendent": 17_000_000,
}

ROLES = ["Mechanic", "Electric", "Welder"]
MONTH_COLS = ["M1", "M2", "M3"]

# Cache TTL untuk data BACKEND (detik).
#
# Sengaja pendek: ketiga form (Engineering, OD & HCM, Plant & Maintenance)
# menulis langsung ke spreadsheet, dan orang yang baru menyimpan nilai wajar
# berharap angkanya segera terlihat di dashboard. TTL panjang membuat
# perubahan seolah "tidak tersimpan" padahal sudah masuk ke sheet.
#
# Nilai ini dibaca app.py; jangan menulis angka ttl langsung di dekorator
# @st.cache_data, nanti konstanta ini jadi tidak berpengaruh.
CACHE_TTL_SECONDS = 60
