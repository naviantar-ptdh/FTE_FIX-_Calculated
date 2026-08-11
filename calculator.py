"""
Reimplementasi logika perhitungan sheet "Final Calculation" (FTE Calculator).

Alur rumus (per unit / kategori equipment terpilih), mengikuti Final Calculation:

    G   = Target Physical Availability (PA%)              -> input user
    H   = 1 - G                                             (Breakdown %)
    I   = 24 * H                                            (Breakdown Hours/hari)
    J   = 12 - LostTime(Site) - (Jarak/40)                  (EMHD, jam efektif/hari)

    FTE_Mechanic    = ((I/J) * LoadMechanic    * Populasi * RatioShift(Site)) / CF * RACI_Mechanic
    FTE_Electrician = ((I/J) * LoadElectrican  * Populasi * RatioShift(Site)) / CF * RACI_Electrician
    FTE_Welder      = ((I/J) * LoadWelder      * Populasi * RatioShift(Site)) / CF * RACI_Welder

    (CF = Competency Factor, input user)

Kemudian setiap FTE role di-split ke M1/M2/M3 berdasarkan rasio dari BACKEND:
    Mechanic    : M1 = FTE*a, M2 = FTE*b, M3 = FTE*c      (a+b+c = 1, mis. 0.2/0.3/0.5)
    Electrician : M1 = FTE*a, M2 = FTE*b, M3 = 0          (mis. 3/7, 4/7)
    Welder      : M1 = FTE*a, M2 = FTE*b, M3 = 0          (mis. 3/7, 4/7)

── SKEMA ROUND (PENTING — disamakan persis dengan sheet "Final Calculation") ──
Di Excel, baris per-unit (baris 10:46, kolom P:AB) TIDAK PERNAH dibulatkan --
nilainya tetap desimal mentah (raw). Pembulatan HANYA terjadi SATU KALI, di
baris ringkasan "Summary Manpower" (baris 47), dengan formula:

    P47 = ROUND(SUM(P9:P46), 0)   -> jumlahkan dulu SEMUA unit (raw), baru ROUND
    Q47 = ROUND(SUM(Q9:Q46), 0)
    R47 = ROUND(SUM(R9:R46), 0)
    ... dst untuk T/U (Welder) dan W/X (Electrician)

Baris "Total" (AH10 = SUM(AE10:AG10)) lalu menjumlahkan M1+M2+M3 yang SUDAH
dibulatkan itu.

Ini BUKAN skema "round per-unit lalu jumlahkan" (round-then-sum) — itu keliru
karena round(a) + round(b) != round(a+b) secara umum, dan itulah sumber
perbedaan hasil antara versi lama app ini dengan sheet Excel aslinya.

Jadi di modul ini:
  - `compute_fte_raw()`  -> hasil PER UNIT, TANPA pembulatan (persis baris 10:46).
  - `aggregate_units()`  -> jumlahkan raw dari semua unit dulu, BARU dibulatkan
                             SATU KALI (persis baris 47 / Summary Manpower).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

from config import BASE_MECHANIC_HOURS, HOURS_PER_DAY, TRAVEL_DIVISOR, COST_RATE, ROLES, MONTH_COLS
from data_loader import BackendData, UnitRow, StaffRow


def excel_round(value: float, digits: int = 0) -> float:
    """Round-half-up seperti fungsi ROUND() Excel (bukan banker's rounding Python)."""
    if value is None:
        return 0.0
    q = Decimal("1") if digits == 0 else Decimal("1." + "0" * digits)
    result = Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP)
    return float(result)


@dataclass
class FTEInput:
    site: str
    competency_factor: float          # D3, misal 0.6
    jarak_km: float                   # D4
    sub_category: str                 # Sub Section, mis. "Big Exca"
    jenis_unit: str                   # Attribute terkait sub_category (informasi/konfirmasi)
    pa_percent: float                 # 1-100, Target Physical Availability
    populasi: float = 1.0             # Equipment Population (tidak ada di form asal, default 1 unit)


class CalculationError(RuntimeError):
    pass


def compute_fte_raw(inputs: FTEInput, backend: BackendData) -> dict:
    """Hitung FTE untuk SATU unit/baris, TANPA pembulatan sama sekali —
    persis nilai mentah di baris 10:46 pada sheet 'Final Calculation'.
    Jangan bulatkan hasil fungsi ini sebelum dijumlahkan lintas unit; gunakan
    `aggregate_units()` untuk itu, supaya skema round-nya identik dengan Excel
    (ROUND(SUM(...)) di level total, bukan round per-unit).
    """
    if inputs.sub_category not in backend.load_factor.index:
        raise CalculationError(f"Sub Category '{inputs.sub_category}' tidak ditemukan di BACKEND.")
    if inputs.site not in backend.ratio_shift or inputs.site not in backend.lost_time:
        raise CalculationError(f"Site '{inputs.site}' tidak memiliki data Ratio Shift / Lost Time di BACKEND.")
    if inputs.competency_factor <= 0:
        raise CalculationError("Competency Factor harus lebih besar dari 0.")

    row = backend.load_factor.loc[inputs.sub_category]
    load_mechanic = row["Load Mechanic"]

    # PEMETAAN ROLE MENGIKUTI EXCEL, BUKAN JUDUL KOLOM BACKEND.
    #
    # Tabel Load Factor di BACKEND berjudul: Load Mechanic | Load Electrican |
    # Load Welder (kolom D | E | F). Tapi kedua sheet acuan mengambil kolom E
    # untuk output berjudul "Welder" dan kolom F untuk "Electrician":
    #     Welder      = XLOOKUP(...,E$95:E$112)   -> kolom "Load Electrican"
    #     Electrician = XLOOKUP(...,F$95:F$112)   -> kolom "Load Welder"
    # Jadi Excel bertentangan dengan judul tabelnya sendiri.
    #
    # Kode ini sengaja mengikuti Excel supaya angka aplikasi bisa
    # direkonsiliasi dengan laporan yang beredar. Diverifikasi pada 67 baris
    # sheet RACI Granular: total Welder 30,54 dan Electrician 25,15 — persis
    # sama dengan Excel.
    #
    # CATATAN yang belum terselesaikan: bukti dari data justru menunjukkan
    # judul tabelnya yang benar dan label Excel yang tertukar. Contoh paling
    # jelas Light Tower — Load Electrican 0,072 vs Load Welder 0,036; alat itu
    # genset + lampu, beban listriknya memang wajar lebih besar dari beban las.
    # Kalau nanti disepakati bahwa judul tabel yang dipakai, cukup tukar dua
    # baris di bawah ini (dan perbaiki juga label kolom di Excel).
    load_welder = row["Load Electrican"]
    load_electrican = row["Load Welder"]

    ratio_shift = backend.ratio_shift[inputs.site]
    lost_time = backend.lost_time[inputs.site]

    pa = max(1.0, min(100.0, inputs.pa_percent)) / 100.0
    breakdown_pct = 1 - pa                       # H
    breakdown_hours = HOURS_PER_DAY * breakdown_pct  # I
    emhd = BASE_MECHANIC_HOURS - lost_time - (inputs.jarak_km / TRAVEL_DIVISOR)  # J

    if emhd <= 0:
        raise CalculationError(
            "EMHD (Effective Mechanic Hours a Day) <= 0. "
            "Periksa kembali Lost Time & Jarak Area Kerja."
        )

    base_factor = (breakdown_hours / emhd) * inputs.populasi * ratio_shift / inputs.competency_factor

    fte_mechanic = base_factor * load_mechanic 
    fte_electric = base_factor * load_electrican 
    fte_welder = base_factor * load_welder 

    m_a, m_b, m_c = backend.split_mechanic
    e_a, e_b = backend.split_electrician
    w_a, w_b = backend.split_welder

    # Raw = TIDAK dibulatkan (sama seperti kolom P:X baris 10:46 di Excel)
    raw = {
        "Mechanic": {
            "M1": fte_mechanic * m_a,
            "M2": fte_mechanic * m_b,
            "M3": fte_mechanic * m_c,
        },
        "Electric": {
            "M1": fte_electric * e_a,
            "M2": fte_electric * e_b,
            "M3": 0.0,
        },
        "Welder": {
            "M1": fte_welder * w_a,
            "M2": fte_welder * w_b,
            "M3": 0.0,
        },
    }
    for role in ROLES:
        raw[role]["Tot"] = sum(raw[role][m] for m in MONTH_COLS)

    total_row = {col: sum(raw[role][col] for role in ROLES) for col in MONTH_COLS}
    total_row["Tot"] = sum(total_row[m] for m in MONTH_COLS)
    raw["Total"] = total_row

    return {
        "raw": raw,
        "intermediate": {
            "Target PA (%)": inputs.pa_percent,
            "Breakdown % (H)": breakdown_pct,
            "Breakdown Hours/hari (I)": breakdown_hours,
            "EMHD - jam efektif/hari (J)": emhd,
            "Lost Time (Site)": lost_time,
            "Ratio Shift (Site)": ratio_shift,
            "Load Mechanic": load_mechanic,
            "Load utk Welder (kolom 'Load Electrican')": load_welder,
            "Load utk Electrician (kolom 'Load Welder')": load_electrican,
            # Proporsi RACI SENGAJA tidak ditampilkan di sini. Model yang
            # dipakai adalah "RACI Granular": tiap role sudah punya kolom load
            # factor sendiri, jadi tidak ada lagi pengali RACI seperti di sheet
            # 'Final Calculation' yang lama. Menampilkannya di panel diagnostik
            # membuat orang mengira angka ini ikut dikalikan — padahal tidak.
            # (Membacanya juga sempat bikin crash saat seksi RACI tidak ada di
            # BACKEND, karena backend.raci kosong.)
            "FTE Mechanic (raw)": fte_mechanic,
            "FTE Electric (raw)": fte_electric,
            "FTE Welder (raw)": fte_welder,
        },
    }


def _apportion_ceil(level_values: dict) -> dict:
    """Bulatkan KE ATAS total sebuah role, lalu bagikan hasilnya ke M1/M2/M3
    sebagai bilangan bulat yang jumlahnya persis sama dengan total itu.

    Kenapa tidak membulatkan tiap level satu per satu? Karena pembulatan ke
    atas per level menggelembungkan hasil dengan cepat. Untuk 1 Big Exca,
    Mechanic 2,64 pecah jadi 0,53 / 0,79 / 1,32 — kalau masing-masing
    di-ceil hasilnya 1 + 1 + 2 = 4 orang, padahal kebutuhan sebenarnya 2,64
    yang wajarnya jadi 3 orang. Jadi yang dibulatkan ke atas adalah TOTAL
    role-nya, baru dibagi ke level memakai metode sisa terbesar (largest
    remainder) supaya tiap level tetap bulat dan jumlahnya tetap pas.
    """
    total_raw = sum(level_values[m] for m in MONTH_COLS)
    target = int(math.ceil(total_raw - 1e-9))
    out = {m: 0 for m in MONTH_COLS}
    if target <= 0:
        return out

    # bagian bulat dulu, lalu sisa kursi diberikan ke pecahan terbesar
    shares = {}
    for m in MONTH_COLS:
        exact = (level_values[m] / total_raw * target) if total_raw > 0 else 0.0
        out[m] = int(math.floor(exact))
        shares[m] = exact - out[m]
    sisa = target - sum(out.values())
    for m in sorted(MONTH_COLS, key=lambda x: shares[x], reverse=True)[:sisa]:
        out[m] += 1
    return out


def aggregate_units(raw_results: List[dict], round_mode: str = "round") -> dict:
    """Jumlahkan nilai RAW (belum dibulatkan) dari seluruh unit/baris,
    BARU dibulatkan SATU KALI per role/kolom -- persis formula
    `P47 = ROUND(SUM(P9:P46), 0)` di sheet 'Final Calculation'.

    `raw_results` adalah list dari output `compute_fte_raw()["raw"]` untuk
    setiap unit yang dihitung.

    round_mode:
      "round" — pembulatan biasa per level (dipakai Basecase & Summary,
                setara ROUND(SUM(...)) di baris total Excel).
      "ceil"  — total tiap role dibulatkan KE ATAS lalu dibagi ke level.
                Dipakai mode Kalkulator: hasilnya jumlah ORANG, dan kebutuhan
                0,70 orang tetap berarti harus menyiapkan 1 orang.
      "none"  — tanpa pembulatan, sama persis dengan satu baris Excel.
    """
    sums = {role: {m: 0.0 for m in MONTH_COLS} for role in ROLES}
    for raw in raw_results:
        for role in ROLES:
            for m in MONTH_COLS:
                sums[role][m] += raw[role][m]

    fte_table = {}
    for role in ROLES:
        if round_mode == "ceil":
            fte_table[role] = _apportion_ceil(sums[role])
        elif round_mode == "none":
            fte_table[role] = {m: sums[role][m] for m in MONTH_COLS}
        else:
            fte_table[role] = {m: excel_round(sums[role][m]) for m in MONTH_COLS}
        # Tot = SUM(M1:M3) yang SUDAH dibulatkan, persis AH10 = SUM(AE10:AG10)
        fte_table[role]["Tot"] = sum(fte_table[role][m] for m in MONTH_COLS)

    total_row = {m: sum(fte_table[role][m] for role in ROLES) for m in MONTH_COLS}
    total_row["Tot"] = sum(total_row[m] for m in MONTH_COLS)
    fte_table["Total"] = total_row

    cost_table = {}
    for role in ROLES + ["Total"]:
        cost_table[role] = {
            month: fte_table[role][month] * COST_RATE[month] for month in MONTH_COLS
        }
        cost_table[role]["Tot"] = sum(cost_table[role][m] for m in MONTH_COLS)

    return {"fte": fte_table, "cost": cost_table}


# =========================================================================
# (v2) Summary per Kategori (Digger/Hauler/Auxilary Track/dst.) + Foreman/SPV/Planner
# =========================================================================

def compute_site_summary(
    site: str,
    unit_rows: List[UnitRow],
    backend: BackendData,
    competency_factor: float,
) -> dict:
    """Hitung ringkasan v2 untuk satu Site: Mechanic dikelompokkan per Category
    (Digger/Hauler/dst dari BACKEND Clasification), sedangkan Welder & Electrician
    langsung Total keseluruhan (tidak per kategori) — sesuai 'Final Calculation'.

    Jarak (km) diambil otomatis dari backend.jarak[site] (bukan input manual).
    Kategori yang hasilnya nihil (semua 0) tidak disertakan (disembunyikan di UI).

    Return dict:
        {
          "mechanic_by_category": { category_name: {"M1","M2","M3","Tot"} , ... },
          "welder_total": {"M1","M2","M3","Tot"},
          "electric_total": {"M1","M2","M3","Tot"},
          "detail_rows": [ {category, jenis_unit, jumlah_unit, pa, raw: {...}}, ... ],
          "skipped_units": [ (category, jenis_unit, reason), ... ],
        }
    """
    jarak_km = backend.jarak.get(site)
    if jarak_km is None:
        raise CalculationError(f"Jarak untuk site '{site}' tidak ditemukan di BACKEND (seksi 'Jarak').")

    raw_by_category: Dict[str, Dict[str, float]] = {}
    welder_sum = {m: 0.0 for m in MONTH_COLS}
    electric_sum = {m: 0.0 for m in MONTH_COLS}
    detail_rows: List[dict] = []
    skipped_units: List[tuple] = []

    for u in unit_rows:
        orig_sc = backend.original_sub_name(u.category) or u.category
        if orig_sc not in backend.load_factor.index:
            skipped_units.append((u.category, u.jenis_unit, "Sub Category tidak ditemukan di BACKEND Load Factor"))
            continue
        try:
            inputs = FTEInput(
                site=site,
                competency_factor=competency_factor,
                jarak_km=jarak_km,
                sub_category=orig_sc,
                jenis_unit=u.jenis_unit,
                pa_percent=u.pa,
                populasi=u.jumlah_unit,
            )
            res = compute_fte_raw(inputs, backend)
        except CalculationError as exc:
            skipped_units.append((u.category, u.jenis_unit, str(exc)))
            continue

        raw = res["raw"]
        cat_name = backend.category_for(u.category) or "Lainnya"
        bucket = raw_by_category.setdefault(cat_name, {m: 0.0 for m in MONTH_COLS})
        for m in MONTH_COLS:
            bucket[m] += raw["Mechanic"][m]
            welder_sum[m] += raw["Welder"][m]
            electric_sum[m] += raw["Electric"][m]

        detail_rows.append({
            "category": u.category,
            "jenis_unit": u.jenis_unit,
            "jumlah_unit": u.jumlah_unit,
            "pa": u.pa,
            "raw": raw,
        })

    mechanic_by_category: Dict[str, Dict[str, float]] = {}
    # pertahankan urutan Category sesuai BACKEND Clasification, lalu sisanya (mis. "Lainnya")
    ordered_cats = [c for c in backend.classification_order if c in raw_by_category]
    ordered_cats += [c for c in raw_by_category if c not in ordered_cats]
    for cat in ordered_cats:
        vals = raw_by_category[cat]
        rounded = {m: excel_round(vals[m]) for m in MONTH_COLS}
        rounded["Tot"] = sum(rounded[m] for m in MONTH_COLS)
        if rounded["Tot"] > 0:
            mechanic_by_category[cat] = rounded

    welder_total = {m: excel_round(welder_sum[m]) for m in MONTH_COLS}
    welder_total["Tot"] = sum(welder_total[m] for m in MONTH_COLS)
    electric_total = {m: excel_round(electric_sum[m]) for m in MONTH_COLS}
    electric_total["Tot"] = sum(electric_total[m] for m in MONTH_COLS)

    return {
        "mechanic_by_category": mechanic_by_category,
        "welder_total": welder_total,
        "electric_total": electric_total,
        "detail_rows": detail_rows,
        "skipped_units": skipped_units,
        "jarak_km": jarak_km,
    }


def compute_site_cost(
    mechanic_by_category: Dict[str, Dict[str, float]],
    welder_total: Dict[str, float],
    electric_total: Dict[str, float],
) -> dict:
    """Hitung estimasi cost/bulan (Rp) untuk hasil Basecase satu Site, dari
    total FTE (M1/M2/M3) per Role x COST_RATE per level.

    Mechanic dijumlahkan dari seluruh kategori (mechanic_by_category) karena
    di layer summary dia sudah dipecah per-kategori (Digger/Hauler/dst).
    Welder & Electrician sudah dalam bentuk total per Site.

    Return: {"Mechanic": {...}, "Welder": {...}, "Electric": {...}, "Total": {...}}
    tiap dict berisi M1/M2/M3 (nilai Rp) + Tot.
    """
    mechanic_total = {m: 0.0 for m in MONTH_COLS}
    for cat_vals in mechanic_by_category.values():
        for m in MONTH_COLS:
            mechanic_total[m] += cat_vals.get(m, 0.0)

    fte_by_role = {
        "Mechanic": mechanic_total,
        "Welder": welder_total,
        "Electric": electric_total,
    }

    cost_table = {}
    for role, fte in fte_by_role.items():
        cost_table[role] = {m: fte.get(m, 0.0) * COST_RATE[m] for m in MONTH_COLS}
        cost_table[role]["Tot"] = sum(cost_table[role][m] for m in MONTH_COLS)

    total_row = {m: sum(cost_table[role][m] for role in fte_by_role) for m in MONTH_COLS}
    total_row["Tot"] = sum(total_row[m] for m in MONTH_COLS)
    cost_table["Total"] = total_row

    return cost_table


SUPERINTENDENT_SPAN = 5


def compute_staff_fte(
    site: str,
    mechanic_by_category: Dict[str, Dict[str, float]],
    welder_total: Dict[str, float],
    electric_total: Dict[str, float],
    staff_rows: List[StaffRow],
) -> dict:
    """Foreman / Supervisor / Superintendent per site, sheet 'Hasil Staff' (v3).

    Rumus v3.1, mengikuti kolom L dan N di sheet (workbook FTE__8_):

        Jam Supervisi H = (12 - lost time site) / 8        <- sudah tersimpan di sheet

        Foreman (Operational)
            = CEILING((BebanAdmin + (TotalMekanik ^ k) * H * EWDY * AreaKerja)
                      * RasioRoster / JamEfektif)
        Foreman (Planner)
            = CEILING((BebanAdmin / JamEfektif * AreaKerja) * RasioRoster)
        Supervisor (Operational)
            = CEILING((BebanAdmin + (Foreman ^ k_spv) * EWDY * AreaKerja)
                      * RasioRoster / JamEfektif)          <- TANPA H
        Supervisor (Planner)
            = dibaca dari kolom "FTE SPV" (lookup, bukan dihitung ulang)
        Superintendent
            = CEILING(total SPV Operational / 5) + CEILING(total SPV Planner / 5)

    Perubahan dari v2:

    1.  Basis pangkat Foreman adalah TOTAL mekanik section (M1+M2+M3), bukan
        lagi M1 saja.
    2.  Supervisor tidak lagi `ROUND(Foreman * 0.5)`; ia memakai bentuk rumus
        yang sama dengan Foreman, dengan Foreman sebagai basis pangkat.
    3.  Area Kerja pindah KE DALAM suku supervisi, dan pengali di luar kurung
        sekarang Rasio Roster - bukan lagi Area Kerja.
    4.  Suku supervisi Supervisor tidak memakai Jam Supervisi (H); hanya
        Foreman^k_spv * EWDY * AreaKerja.

    Superintendent dihitung PER GRUP: SPV Operational dijumlahkan dulu lalu
    dirasiokan 1:5 dan dibulatkan ke atas, dan hal yang sama berlaku terpisah
    untuk SPV Planner. Totalnya adalah penjumlahan kedua hasil itu - bukan
    1:5 atas gabungan keduanya, karena pembulatan ke atas per grup memberi
    hasil yang berbeda.

    Baris dengan data tidak lengkap tetap dilewati (tidak mengubah angka),
    tapi kini dicatat di kunci 'skipped' lengkap dengan alasannya.
    """
    def norm(s: str) -> str:
        return BackendData._normalize(s)

    def _tot(d: Dict[str, float]) -> float:
        """Total mekanik section: pakai 'Tot' kalau ada, kalau tidak jumlahkan M1-M3."""
        if "Tot" in d:
            return d["Tot"]
        return sum(d.get(m, 0.0) for m in ("M1", "M2", "M3"))

    def _staff_fte(beban, base, k, h, ewdy, area, rasio, jam_efektif):
        """(BebanAdmin + base^k * H * EWDY * AreaKerja) * RasioRoster / JamEfektif.

        `h` diisi 1.0 untuk Supervisor, karena rumus SPV di sheet tidak lagi
        mengalikan Jam Supervisi.

        Mengembalikan None (bukan crash) kalau `base ** k` meledak di luar
        jangkauan float — ini terjadi kalau nilai k / k spv di sheet Hasil
        Staff tidak wajar (harusnya sekitar 1-2, bukan puluhan/ratusan).
        Baris dengan hasil None dilewati oleh pemanggilnya, konsisten dengan
        baris berdata tidak lengkap lain yang juga dilewati diam-diam.
        """
        if base <= 0:
            beban_supervisi = 0.0
        else:
            try:
                beban_supervisi = (base ** k) * h * ewdy * area
            except OverflowError:
                return None
        try:
            return math.ceil((beban + beban_supervisi) * rasio / jam_efektif)
        except (OverflowError, ValueError):
            return None

    mech_by_norm = {norm(k): v for k, v in mechanic_by_category.items()}
    site_rows = [r for r in staff_rows if r.site.strip().lower() == site.strip().lower()]

    # Jam supervisi dipakai bersama satu site; baris Planner mengosongkannya di
    # sheet, jadi nilainya diambil dari baris Operational site yang sama.
    site_h = next(
        (r.jam_supervisi for r in site_rows
         if not math.isnan(r.jam_supervisi) and r.jam_supervisi > 0),
        float("nan"),
    )
    site_ewdy = next(
        (r.ewdy for r in site_rows if not math.isnan(r.ewdy) and r.ewdy > 0),
        float("nan"),
    )

    operational: List[dict] = []
    planner: List[dict] = []
    skipped: List[dict] = []      # (posisi, kategori, alasan) — lihat docstring

    def _skip(row, reason: str):
        skipped.append({
            "posisi": row.posisi or "(tanpa nama)",
            "category": row.category_posisi or "(kosong)",
            "reason": reason,
        })

    for row in site_rows:
        cat_pos = row.category_posisi.strip().lower()

        missing = [
            name for name, val in (
                ("Area Kerja", row.area_kerja),
                ("Beban Admin", row.beban_admin),
                ("Jam Efektif", row.jam_efektif),
            ) if math.isnan(val)
        ]
        if missing:
            _skip(row, "kolom kosong: " + ", ".join(missing))
            continue

        if cat_pos not in ("operational", "planner"):
            _skip(row, f"Category Posisi '{row.category_posisi}' tidak dikenali "
                       f"(harus persis 'Operational' atau 'Planner')")
            continue

        if cat_pos == "operational":
            h = row.jam_supervisi if not math.isnan(row.jam_supervisi) else site_h
            ewdy = row.ewdy if not math.isnan(row.ewdy) else site_ewdy
            if math.isnan(h) or math.isnan(ewdy):
                kosong = []
                if math.isnan(h):
                    kosong.append("Jam Supervisi")
                if math.isnan(ewdy):
                    kosong.append("EWDY")
                _skip(row, "kolom kosong di semua baris site ini: " + ", ".join(kosong))
                continue

            pnorm = norm(row.posisi)
            if pnorm == norm("Electrician"):
                jumlah_mekanik = _tot(electric_total)
            elif pnorm == norm("Welding & Fabrication"):
                jumlah_mekanik = _tot(welder_total)
            else:
                match = mech_by_norm.get(pnorm)
                if match is None:
                    _skip(row, f"Posisi '{row.posisi}' tidak cocok dengan section unit "
                               f"mana pun di site ini")
                    continue
                jumlah_mekanik = _tot(match)

            k = row.k if not math.isnan(row.k) else 1.0
            k_spv = row.k_spv if not math.isnan(row.k_spv) else 1.0
            rasio = row.rasio_roster if not math.isnan(row.rasio_roster) else 1.0

            foreman = _staff_fte(row.beban_admin, jumlah_mekanik, k, h, ewdy,
                                 row.area_kerja, rasio, row.jam_efektif)
            if foreman is None:
                _skip(row, f"nilai k = {row.k} tidak wajar (hasilnya di luar "
                           f"jangkauan angka)")
                continue
            supervisor = _staff_fte(row.beban_admin, foreman, k_spv, 1.0, ewdy,
                                    row.area_kerja, rasio, row.jam_efektif)
            if supervisor is None:
                _skip(row, f"nilai k spv = {row.k_spv} tidak wajar (hasilnya di "
                           f"luar jangkauan angka)")
                continue
            operational.append({
                "posisi": row.posisi,
                "jumlah_mekanik": jumlah_mekanik,
                "foreman": foreman,
                "supervisor": supervisor,
            })

        elif cat_pos == "planner":
            if row.rasio_roster is None or math.isnan(row.rasio_roster):
                _skip(row, "kolom kosong: Rasio Roster")
                continue
            foreman = math.ceil(
                (row.beban_admin / row.jam_efektif * row.area_kerja) * row.rasio_roster
            )
            # Supervisor Planner TIDAK dihitung ulang: nilainya dilookup dari
            # kolom "FTE SPV" di sheet (di workbook terbaru kolom itu memang
            # berisi angka, bukan rumus), supaya perubahan di spreadsheet
            # langsung terbawa ke sini.
            supervisor = (
                int(row.fte_spv_lookup)
                if not math.isnan(row.fte_spv_lookup) else 0
            )
            planner.append({
                "posisi": row.posisi,
                "foreman": foreman,
                "fte": foreman,      # alias lama, masih dipakai sebagian UI
                "supervisor": supervisor,
            })

    spv_oper = sum(r["supervisor"] for r in operational)
    spv_plan = sum(r["supervisor"] for r in planner)
    supt_oper = math.ceil(spv_oper / SUPERINTENDENT_SPAN) if spv_oper else 0
    supt_plan = math.ceil(spv_plan / SUPERINTENDENT_SPAN) if spv_plan else 0

    return {
        "operational": operational,
        "planner": planner,
        "superintendent_operational": supt_oper,
        "superintendent_planner": supt_plan,
        "superintendent": supt_oper + supt_plan,
        # Baris yang tidak ikut dihitung, berikut alasannya. Sebelumnya baris
        # semacam ini dibuang tanpa jejak, sehingga data yang sudah diisi di
        # sheet tampak "tidak masuk" tanpa petunjuk apa pun.
        "skipped": skipped,
        "rows_found": len(site_rows),
    }


def compute_fte(inputs: FTEInput, backend: BackendData,
                round_mode: str = "round") -> dict:
    """Kompatibilitas mundur: hitung SATU unit lalu langsung agregasi
    (setara dengan aggregate_units([raw]) untuk satu unit saja).
    Untuk banyak unit sekaligus, JANGAN panggil fungsi ini per-unit lalu
    dijumlahkan manual -- pakai compute_fte_raw() + aggregate_units() supaya
    skema round-nya benar (round sekali di total, bukan round per-unit).

    Mode Kalkulator memanggilnya dengan `round_mode="ceil"` karena hasilnya
    dibaca sebagai jumlah orang yang harus disiapkan.
    """
    result = compute_fte_raw(inputs, backend)
    agg = aggregate_units([result["raw"]], round_mode=round_mode)
    agg["intermediate"] = result["intermediate"]
    return agg
