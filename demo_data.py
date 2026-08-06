"""
Data contoh untuk menjalankan aplikasi tanpa Google Sheets.

Aktif hanya bila environment variable `FTE_DEMO=1` diset:

    FTE_DEMO=1 streamlit run app.py

Gunanya dua: (1) bisa mengecek tampilan tanpa akses spreadsheet, misal saat
mengerjakan desain atau saat jaringan kantor memblokir docs.google.com, dan
(2) jadi contoh bentuk data yang diharapkan tiap sheet.

Angkanya karangan tapi rentangnya dibuat masuk akal untuk tambang batu bara —
JANGAN dipakai untuk keputusan manpower.
"""
from __future__ import annotations

import random

import pandas as pd

from data_loader import BackendData, StaffRow, UnitRow

_norm = BackendData._normalize

# sub category -> (Category, Load Mechanic, Load Electrican, Load Welder, [jenis unit])
_CATALOG = {
    "Big Exca": ("Digger", 1.55, 0.28, 0.22, ["PC2000", "PC3000", "EX1900"]),
    "Medium Exca": ("Digger", 1.10, 0.20, 0.16, ["PC1250", "PC800"]),
    "Small Exca": ("Digger", 0.72, 0.12, 0.10, ["PC300", "PC200"]),
    "Off Highway Truck": ("Hauler", 1.32, 0.22, 0.18, ["HD785", "HD1500", "777D"]),
    "Dump Truck": ("Hauler", 0.86, 0.14, 0.12, ["Scania P460", "Hino 500"]),
    "Bulldozer": ("Auxilary Track", 1.18, 0.16, 0.24, ["D375A", "D155A", "D85ESS"]),
    "Track Drill": ("Auxilary Track", 0.94, 0.18, 0.20, ["ROC L8", "DX800"]),
    "Motor Grader": ("Auxilary Wheel", 0.78, 0.14, 0.12, ["GD825", "16M"]),
    "Compactor": ("Auxilary Wheel", 0.64, 0.10, 0.11, ["SD160", "CS683"]),
    "Water Truck": ("Auxilary Wheel", 0.70, 0.12, 0.13, ["HD465 WT", "Scania WT"]),
    "Genset": ("Support & Facility", 0.58, 0.42, 0.08, ["Cat 500 kVA", "Cummins 350 kVA"]),
    "Lighting Tower": ("Support & Facility", 0.34, 0.30, 0.06, ["LT 6 kVA"]),
    "Fuel Truck": ("Support & Facility", 0.66, 0.12, 0.14, ["FT 20 kL"]),
}

_CATEGORY_ORDER = ["Digger", "Hauler", "Auxilary Track", "Auxilary Wheel", "Support & Facility"]

_SITES = ["KCP", "ACP", "BCP"]

# site -> (ratio shift, lost time jam, jarak km)
_SITE_PARAMS = {
    "KCP": (2.0, 2.0, 12.0),
    "ACP": (1.8, 2.5, 28.0),
    "BCP": (2.2, 1.8, 45.0),
}


def backend() -> BackendData:
    load_factor = pd.DataFrame(
        [
            {"Load Mechanic": v[1], "Load Electrican": v[2], "Load Welder": v[3]}
            for v in _CATALOG.values()
        ],
        index=list(_CATALOG.keys()),
    )

    return BackendData(
        load_factor=load_factor,
        ratio_shift={s: p[0] for s, p in _SITE_PARAMS.items()},
        raci={"Mechanic": 0.72, "Electric": 0.14, "Welder": 0.14},
        split_mechanic=[0.25, 0.35, 0.40],
        split_welder=[0.55, 0.45],
        split_electrician=[0.50, 0.50],
        lost_time={s: p[1] for s, p in _SITE_PARAMS.items()},
        sites=list(_SITES),
        sub_categories=list(_CATALOG.keys()),
        units_map={_norm(k): v[4] for k, v in _CATALOG.items()},
        _norm_to_orig={_norm(k): k for k in _CATALOG},
        jarak={s: p[2] for s, p in _SITE_PARAMS.items()},
        classification={_norm(k): v[0] for k, v in _CATALOG.items()},
        classification_order=list(_CATEGORY_ORDER),
        # Sengaja dibuat berbeda antar site supaya mode Summary benar-benar
        # menguji jalur "tiap site memakai faktornya sendiri".
        competency_factor={"KCP": 0.80, "ACP": 0.75, "BCP": 0.85},
    )


def units() -> dict:
    """Populasi unit per site. Tiap site punya bauran alat yang berbeda supaya
    bentuk chart-nya tidak seragam."""
    rng = random.Random(20260730)
    out: dict[str, list[UnitRow]] = {}

    # skala populasi per site: KCP tambang besar, ACP menengah, BCP baru buka
    scale = {"KCP": 1.0, "ACP": 0.62, "BCP": 0.38}

    for site in _SITES:
        rows: list[UnitRow] = []
        for sub, (_cat, _lm, _le, _lw, jenis_list) in _CATALOG.items():
            # BCP belum punya track drill & fuel truck
            if site == "BCP" and sub in ("Track Drill", "Fuel Truck"):
                continue
            for jenis in jenis_list:
                base = rng.randint(3, 26)
                jumlah = max(1, round(base * scale[site]))
                pa = round(rng.uniform(78, 92), 1)
                rows.append(UnitRow(category=sub, jenis_unit=jenis,
                                    jumlah_unit=float(jumlah), pa=pa))
        out[site] = rows
    return out


def staff() -> list[StaffRow]:
    """Hasil Staff untuk ketiga site.

    Sengaja dibuat tidak seragam supaya perilaku 'baris tidak lengkap
    dilewati' ikut terlihat: site BCP hanya punya sebagian posisi.
    """
    operational_positions = [
        "Digger", "Hauler", "Auxilary Track", "Auxilary Wheel",
        "Support & Facility", "Electrician", "Welding & Fabrication",
    ]
    # Sengaja lebih banyak daripada posisi operational, meniru sheet asli —
    # ketimpangan jumlah baris inilah yang memperlihatkan apakah kartu kiri dan
    # kanan benar-benar sejajar.
    planner_positions = [
        "Maintenance Planning", "PLM Scheduling & PCR", "PLM Engineering",
        "Condition Monitoring", "Plant Manpower", "Plant Asset",
        "External Repair & Warranty", "Maintenance Training",
    ]

    rows: list[StaffRow] = []
    for site in _SITES:
        area = {"KCP": 1.0, "ACP": 0.85, "BCP": 0.7}[site]
        keep = operational_positions if site != "BCP" else operational_positions[:4]
        for pos in keep:
            rows.append(StaffRow(
                posisi=pos, category_posisi="Operational", site=site,
                rasio_roster=1.43, area_kerja=area * 3, beban_admin=1539.0,
                jam_efektif=2656.5, jam_supervisi=1.0708, ewdy=253.0,
                k=0.51, k_spv=0.64,
            ))
        for pos in planner_positions:
            rows.append(StaffRow(
                posisi=pos, category_posisi="Planner", site=site,
                rasio_roster=1.43, area_kerja=area, beban_admin=1813.0,
                jam_efektif=2656.5, jam_supervisi=float("nan"), ewdy=float("nan"),
                k=0.51, k_spv=1.0, fte_spv_lookup=1.0,
            ))
    return rows
