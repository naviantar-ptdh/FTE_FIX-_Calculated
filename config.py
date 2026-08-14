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

# Tarif khusus per role. Role yang TIDAK terdaftar di sini memakai COST_RATE
# di atas. Kunci role memakai nama internal ("Mechanic" / "Electric" /
# "Welder"); "Electric" itulah yang tampil sebagai "Electrician" di layar.
#
# Electrician punya tarif sendiri karena rentang gajinya jauh lebih rapat
# antar level (9 / 8,5 / 8 juta) dibanding mekanik (10 / 8,5 / 6,5 juta).
ROLE_COST_RATE = {
    "Electric": {
        "M1": 9_000_000,
        "M2": 8_500_000,
        "M3": 8_000_000,
    },
}


def cost_rate(role: str, month: str) -> int:
    """Tarif per FTE untuk sebuah role di sebuah level.

    Selalu pakai fungsi ini, jangan membaca COST_RATE langsung — kalau tidak,
    tarif khusus per role akan terlewat di sebagian tabel saja dan angkanya
    jadi tidak konsisten antar bagian dashboard.
    """
    return ROLE_COST_RATE.get(role, COST_RATE)[month]

# Cost rate per staff FTE (Rp) - asumsi manual dari user, bukan dari BACKEND
STAFF_COST_RATE = {
    "Foreman": 9_000_000,
    "Supervisor": 12_000_000,
    "Planner": 9_000_000,      # sama dengan Foreman, sesuai arahan user
    # PLACEHOLDER - user belum memberi tarif Superintendent. Ganti angka ini
    # begitu tarif resminya ada; seluruh tabel Cost membacanya dari sini.
    "Superintendent": 17_000_000,
}

# Span of control pada sheet 'PLM Operation'. SoC Supervisor dipakai untuk
# menurunkan "Jam supervisi per Foreman/hari" = JamSupMekanik / SoC Supervisor
# (C20 = C19/C17 di sheet). SoC Pengawas dicantumkan sebagai rujukan: nilainya
# sudah terkandung dalam kolom Jam Supervisi di sheet Hasil Staff.
SOC_MAX_PENGAWAS = 6      # 1 Foreman : 6 mekanik (referensi)
SOC_MAX_SUPERVISOR = 3    # 1 Supervisor : 3 Foreman

# --- Maintenance Planning (sheet 'PLM Planner') -----------------------------
# Beban "Material & Parts Requirement Planning" dipecah per SECTION planner.
# Section planner hanya 4, sedangkan Category unit di BACKEND ada 5 — Auxilary
# Track dan Auxilary Wheel digabung jadi satu section "Auxilary".
PLANNER_SECTIONS = ["Digger", "Hauler", "Auxilary", "Support"]

PLANNER_SECTION_MAP = {
    "digger": "Digger",
    "hauler": "Hauler",
    "auxilary track": "Auxilary",
    "auxilary wheel": "Auxilary",
    "auxiliary track": "Auxilary",
    "auxiliary wheel": "Auxilary",
    "support & facility": "Support",
    "support": "Support",
}

# Durasi (jam) per kegiatan Material & Parts Requirement Planning — kolom H
# pada sheet 'PLM Planner'. Bernilai 4 untuk keempat section.
PLANNER_MATERIAL_DURATION = 4.0

# Nama posisi di sheet 'Hasil Staff' yang memakai rumus dengan beban material.
# Posisi Planner LAIN memakai rumus sederhana (BebanAdmin / JamEfektif).
MAINTENANCE_PLANNING_POSITION = "Maintenance Planning"

# v9: rumus FTE Supv Planner berubah. Sebelumnya beban material dibagi
# (1 + jumlah Foreman section itu) — pola itu ternyata bug salin-rumus di
# Excel (tiap baris ikut mereferensi F baris SEBELUMNYA). Sekarang beban
# material dibagi konstanta "SoC max Supervisi" (baris baru di sheet
# 'PLM Planner', nilainya 3 di kedua site contoh — 1 Supervisor mengawasi
# rata-rata 3 unit beban).
SOC_MAX_SUPERVISI_PLANNER = 3

# --- Maintenance Training (sheet 'Maintenance Training') --------------------
# Formula: beban training = AllowancePerMech x (TotalMekanik ^ K_TRAINING)
#          x DurasiPerEvent, lalu ikut pola Foreman/Supervisor yang sama
#          dengan Planner (Foreman = (BebanAdmin+Beban)/JamEfektif;
#          Supervisor = (BebanAdmin+Beban/SoC)/JamEfektif).
#
# Ketiga angka di bawah SERAGAM untuk semua site, jadi disimpan sebagai
# konstanta di sini — bukan sebagai kolom di sheet 'Hasil Staff'. Menaruhnya
# di sheet hanya akan mengundang salah isi antar-site padahal nilainya
# memang tidak boleh berbeda.
#
# K_TRAINING: skor checklist 14 pertanyaan Ya/Tidak di sheet 'k Training'
# (C16 = SUM(Ya=1/Tidak=0)/14). Checklist itu menilai KARAKTERISTIK jenis
# pekerjaan training (butuh sertifikasi, risiko K3, dll), bukan kondisi
# per-site — karena itu satu nilai untuk semua site.
K_TRAINING = 12 / 14

# Berapa kali seorang mekanik ditraining dalam setahun.
TRAINING_ALLOWANCE_PER_MECH = 3

# Jam per event training = (720 - 78 - 60) / 60 = 9,7 jam.
TRAINING_DURATION_PER_EVENT = (720 - 78 - 60) / 60

MAINTENANCE_TRAINING_POSITION = "Maintenance Training"

# SoC max Trainer TIDAK dijadikan konstanta seperti SOC_MAX_SUPERVISI_PLANNER,
# karena nilainya di sheet (kolom "SoC max Trainer"/"SoC max Pengawas")
# ternyata identik dengan SOC_MAX_SUPERVISI_PLANNER (=3) di kedua contoh.
# Dipakai ulang supaya tidak ada dua konstanta yang harus dijaga tetap sama
# secara manual; kalau nanti perlu beda, pisahkan lagi jadi konstanta sendiri.

# --- Sisi Operation (panduan "Standardisasi Perhitungan MPP Operation") ----
# Jumlah shift yang berlaku. Dipakai rumus Foreman; rumus operator TIDAK
# memakainya (faktor shift sudah terkandung di Faktor Rasio Operator).
OPERATION_SHIFT = 2

# Kategori unit yang TIDAK memerlukan operator, jadi dikecualikan dari
# perhitungan jumlah operator. Di sheet FTE Operation kolom Operator untuk
# kategori ini memang dikosongkan (Pump dioperasikan dari jarak jauh /
# tidak berawak). Ditulis huruf kecil; pencocokan case-insensitive.
NO_OPERATOR_CATEGORIES = {"pump"}

# Rasio Supervisor : Foreman di fungsi Operation (panduan poin 1 = 1:4).
SUPERVISOR_PER_FOREMAN_OPS = 4

# Kategori alat yang TIDAK memerlukan operator, jadi dikeluarkan dari
# perhitungan jumlah operator. Alat-alat ini statis/ditinggal menyala
# (genset, lighting tower, pompa) — tidak ada orang yang mengoperasikannya
# sepanjang shift.
#
# Dicocokkan longgar (huruf/angka saja, tanpa spasi), jadi "Light Tower",
# "light tower", dan "LightTower" sama-sama kena.
OPERATOR_EXCLUDED_CATEGORIES = ["Genset", "Light Tower", "Pump"]

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
