# app.py (v7 — "Looker light": orange header band, white cards, English UI)
"""
FTE Calculator — PT Darma Henwa

Three modes (picked from the sidebar nav):

*   **Calculator**        — one equipment type, result shown straight away.
*   **Basecase All Unit** — every unit of ONE site, shown as a dashboard.
*   **Summary**           — the same dashboard, aggregated across ALL sites.

Basecase and Summary share one dashboard body (`render_dashboard_body`), so a
layout change lands in both at once:

    header band  ->  formula parameters (collapsed)  ->  role legend
    ->  KPI row  ->  section chart + M1-M3 donut
    ->  monthly cost (half circle + detail) + yearly cost
    ->  details (collapsed expander -> tabs)

The section chart used to be two charts (totals, then the M1-M3 split); they
are one stacked chart now, with each section total printed above its bar. The
cost chart used to be a speedometer scaled against the most expensive site —
that comparison is gone; it is a plain half circle whose colours are the
per-role share, paired with a yearly twin.

Calculation logic (`calculator.py`, `data_loader.py`, `config.py`) is
untouched — this file is purely the presentation layer.
"""
import math
import os
from typing import List

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import charts
import theme
from calculator import (
    CalculationError,
    FTEInput,
    compute_fte,
    compute_site_cost,
    compute_site_summary,
    compute_staff_fte,
)
from charts import num, rp, rp_short
from config import (
    BASE_MECHANIC_HOURS,
    COST_RATE,
    MONTH_COLS,
    STAFF_COST_RATE,
    TRAVEL_DIVISOR,
    UNIT_EDIT_PASSWORD,
)
from data_loader import (
    BackendDataError,
    UnitRow,
    load_backend_data,
    load_staff_data,
    load_unit_data,
)

st.set_page_config(
    page_title="FTE Calculator — PT Darma Henwa",
    page_icon="🟠",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGE_SIZE = 10
DEMO = os.getenv("FTE_DEMO") == "1"

theme.inject_css()


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner="Loading BACKEND reference data…")
def get_backend():
    if DEMO:
        import demo_data
        return demo_data.backend()
    return load_backend_data()


@st.cache_data(ttl=600, show_spinner="Loading unit data per site…")
def get_units():
    if DEMO:
        import demo_data
        return demo_data.units()
    return load_unit_data()


@st.cache_data(ttl=600, show_spinner="Loading staff data…")
def get_staff():
    if DEMO:
        import demo_data
        return demo_data.staff()
    return load_staff_data()


def units_to_df(rows: List[UnitRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Category": r.category, "Jenis Unit": r.jenis_unit,
             "Jumlah Unit": r.jumlah_unit, "PA": r.pa}
            for r in rows
        ],
        columns=["Category", "Jenis Unit", "Jumlah Unit", "PA"],
    )


def df_to_units(df: pd.DataFrame) -> List[UnitRow]:
    rows = []
    for _, r in df.iterrows():
        try:
            rows.append(UnitRow(
                category=str(r["Category"]),
                jenis_unit=str(r["Jenis Unit"]),
                jumlah_unit=float(r["Jumlah Unit"]),
                pa=float(r["PA"]),
            ))
        except Exception:
            continue
    return rows


def _blank_levels() -> dict:
    return {"M1": 0.0, "M2": 0.0, "M3": 0.0, "Tot": 0.0}


def _add_levels(dst: dict, src_: dict) -> dict:
    for k in ("M1", "M2", "M3", "Tot"):
        dst[k] = dst.get(k, 0.0) + src_.get(k, 0.0)
    return dst


def aggregate_all_sites(backend, units_all) -> dict | None:
    """Roll every site up into one summary/cost pair shaped like a single site.

    Summary mode reuses the exact dashboard body that Basecase uses, so the
    aggregate has to mimic the shape `compute_site_summary` returns:
    mechanic_by_category, welder_total, electric_total, detail_rows,
    skipped_units. Each site is still computed by the untouched calculator —
    only the results are summed here.

    Setiap site memakai Technical Competency Factor-nya SENDIRI dari BACKEND
    (v7); sebelumnya satu nilai slider dipakai untuk semua site sekaligus,
    yang keliru begitu tiap site punya faktornya masing-masing.

    Cache di session_state dikunci pada kombinasi TCF seluruh site, jadi hasil
    lama otomatis dibuang begitu ada satu site yang faktornya berubah.
    """
    tcf = {s: backend.tcf_for(s) for s in (backend.sites or [])}
    key = "_allsites_" + "_".join(f"{s}{tcf[s]:.2f}" for s in sorted(tcf))
    if key in st.session_state:
        return st.session_state[key]

    mech_by_cat: dict = {}
    weld = _blank_levels()
    elec = _blank_levels()
    cost_total: dict = {r: _blank_levels() for r in ("Mechanic", "Electric", "Welder")}
    cost_total["Total"] = _blank_levels()
    detail_rows, skipped, per_site, ok_sites = [], [], {}, []
    oper_acc: dict = {}
    plan_acc: dict = {}
    supt_oper_acc = 0
    supt_plan_acc = 0

    for s in (backend.sites or []):
        rows = units_all.get(s) or []
        if not rows:
            continue
        try:
            summ = compute_site_summary(s, rows, backend, tcf.get(s, backend.tcf_for(s)))
            c = compute_site_cost(
                summ["mechanic_by_category"], summ["welder_total"], summ["electric_total"]
            )
        except (CalculationError, BackendDataError, KeyError, ValueError):
            continue

        ok_sites.append(s)
        per_site[s] = c["Total"]["Tot"]

        # Staff dihitung per site lalu dijumlahkan per posisi, supaya section
        # Staff di mode Summary memakai blok tampilan yang sama persis dengan
        # mode Basecase.
        try:
            staff = compute_staff_fte(
                s, summ["mechanic_by_category"], summ["welder_total"],
                summ["electric_total"], get_staff(),
            )
        except (CalculationError, BackendDataError, KeyError, ValueError):
            staff = {"operational": [], "planner": [],
                     "superintendent_operational": 0, "superintendent_planner": 0}
        for r in staff["operational"]:
            acc = oper_acc.setdefault(
                r["posisi"], {"posisi": r["posisi"], "jumlah_mekanik": 0,
                              "foreman": 0, "supervisor": 0})
            acc["jumlah_mekanik"] += r["jumlah_mekanik"]
            acc["foreman"] += r["foreman"]
            acc["supervisor"] += r["supervisor"]
        for r in staff["planner"]:
            acc = plan_acc.setdefault(
                r["posisi"], {"posisi": r["posisi"], "foreman": 0,
                              "fte": 0, "supervisor": 0})
            acc["foreman"] += r["foreman"]
            acc["fte"] += r["fte"]
            acc["supervisor"] += r["supervisor"]
        supt_oper_acc += staff.get("superintendent_operational", 0)
        supt_plan_acc += staff.get("superintendent_planner", 0)
        for cat, v in summ["mechanic_by_category"].items():
            mech_by_cat.setdefault(cat, _blank_levels())
            _add_levels(mech_by_cat[cat], v)
        _add_levels(weld, summ["welder_total"])
        _add_levels(elec, summ["electric_total"])
        for role in ("Mechanic", "Electric", "Welder", "Total"):
            _add_levels(cost_total[role], c.get(role, {}))
        for d in summ["detail_rows"]:
            detail_rows.append({**d, "site": s})
        for sk in summ["skipped_units"]:
            skipped.append([s, *sk] if isinstance(sk, (list, tuple)) else [s, sk])

    if not ok_sites:
        st.session_state[key] = None
        return None

    # kategori diurutkan mengikuti urutan resmi di BACKEND supaya sumbu X
    # chart section konsisten dengan mode Basecase
    order = [c for c in (backend.classification_order or []) if c in mech_by_cat]
    order += [c for c in mech_by_cat if c not in order]
    mech_by_cat = {c: mech_by_cat[c] for c in order}

    out = {
        "summary": {
            "mechanic_by_category": mech_by_cat,
            "welder_total": weld,
            "electric_total": elec,
            "detail_rows": detail_rows,
            "skipped_units": skipped,
        },
        "cost": cost_total,
        "sites": ok_sites,
        "per_site_cost": per_site,
        "unit_rows": sum(len(units_all.get(s) or []) for s in ok_sites),
        "operational": list(oper_acc.values()),
        "planner": list(plan_acc.values()),
        # Superintendent dijumlahkan per site dan per grup (masing-masing sudah
        # dibulatkan 1:5 di sitenya), bukan dihitung ulang dari total SPV semua
        # site.
        "superintendent_operational": supt_oper_acc,
        "superintendent_planner": supt_plan_acc,
        "superintendent": supt_oper_acc + supt_plan_acc,
    }
    st.session_state[key] = out
    return out


ROLE_LEGEND = [
    ("Mechanic", theme.ROLE_COLORS["Mechanic"]),
    ("Electrician", theme.ROLE_COLORS["Electric"]),
    ("Welder", theme.ROLE_COLORS["Welder"]),
]
# Label level sengaja polos (M1/M2/M3 saja) — sebutan Senior/Middle/Junior
# dihapus atas permintaan user.
LEVEL_LEGEND = [
    ("M1", theme.LEVEL_SHADES["M1"]),
    ("M2", theme.LEVEL_SHADES["M2"]),
    ("M3", theme.LEVEL_SHADES["M3"]),
]


# ---------------------------------------------------------------------------
# Formula parameters panel
# ---------------------------------------------------------------------------
# Konstanta pengali yang berlaku sama di seluruh site. Nilainya statis dan
# tidak berasal dari BACKEND, jadi ia dideklarasikan di sini dan ditampilkan
# apa adanya sebagai kartu parameter.
GLOBAL_CONSTANT = 0.78


def formula_items(backend, unit_qty: float, site: str | None = None) -> list:
    """Enam masukan yang menggerakkan rumus manpower.

    Effective working hour, travelling hour, dan konstanta masing-masing
    berdiri sebagai kartu sendiri — sebelumnya dua yang pertama menumpang di
    baris catatan kartu lain, jadi angkanya tidak bisa dibaca sekilas.
    """
    if site:
        jarak = backend.jarak.get(site)
        ratio = backend.ratio_shift.get(site)
        lost = backend.lost_time.get(site)
        return [
            ("Unit quantity", num(unit_qty), "total units in scope"),
            ("Shift ratio", num(ratio, 2) if ratio is not None else "", f"site {site}"),
            ("Distance", f"{num(jarak, 2)} km" if jarak is not None else "",
             f"work area · site {site}"),
            ("Travelling hour",
             f"{num(jarak / TRAVEL_DIVISOR, 2)} h" if jarak is not None else "",
             f"distance / {TRAVEL_DIVISOR}"),
            ("Effective working hour",
             f"{num(BASE_MECHANIC_HOURS - lost, 2)} h" if lost is not None else "",
             f"mechanic · site {site} · lost time {num(lost, 2)} h"
             if lost is not None else ""),
            ("Constant", num(GLOBAL_CONSTANT, 2), "all sites · fixed value"),
        ]

    # Di mode Summary nilainya berbeda-beda per site, jadi ditulis per site
    # ("ACP 1,46 · BCP 2,00 · KCP 1,43") alih-alih sebagai rentang — rentang
    # menyembunyikan site mana yang punya angka mana.
    sites = sorted(set(backend.jarak) | set(backend.ratio_shift) | set(backend.lost_time))

    def per_site(mapping, dec=2, unit="", transform=None):
        parts = []
        for s_ in sites:
            v = mapping.get(s_)
            if v is None:
                continue
            if transform:
                v = transform(v)
            parts.append(f"{s_} <b>{num(v, dec)}{unit}</b>")
        return " · ".join(parts)

    return [
        ("Unit quantity", num(unit_qty), "total units across all sites"),
        ("Shift ratio", per_site(backend.ratio_shift), "per site"),
        ("Distance", per_site(backend.jarak, 2, " km"), "work area · per site"),
        ("Travelling hour",
         per_site(backend.jarak, 2, " h", lambda v: v / TRAVEL_DIVISOR),
         f"distance / {TRAVEL_DIVISOR}"),
        ("Effective working hour",
         per_site(backend.lost_time, 2, " h", lambda v: BASE_MECHANIC_HOURS - v),
         f"mechanic · {num(BASE_MECHANIC_HOURS)} − lost time"),
        ("Constant", num(GLOBAL_CONSTANT, 2), "all sites · fixed value"),
    ]


def unit_list_df(detail_rows: list, with_site: bool = False) -> pd.DataFrame:
    """The unit list that used to live in the "Per-unit detail" tab.

    It moved into the parameters panel because that is where a reader asks
    "which units is this built from?". In Summary the same unit type shows up
    once per site, so a Site column is prepended to keep the rows distinct.
    """
    cols = (["Site"] if with_site else []) + ["Category", "Unit Type", "Qty", "PA (%)"]
    rows = []
    for d in detail_rows:
        row = {
            "Category": d["category"],
            "Unit Type": d["jenis_unit"],
            "Qty": d["jumlah_unit"],
            "PA (%)": d["pa"],
        }
        if with_site:
            row = {"Site": d.get("site", ""), **row}
        rows.append(row)
    return pd.DataFrame(rows, columns=cols)


def render_formula_panel(items: list, detail_rows: list, with_site: bool = False,
                         skipped: list | None = None):
    """Parameter selalu terlihat; hanya daftar unitnya yang bisa dilipat.

    Sebelumnya keduanya berada di dalam satu expander, jadi empat angka yang
    paling sering dicek justru tersembunyi di balik satu klik.
    """
    st.markdown(theme.info_grid(items), unsafe_allow_html=True)

    df = unit_list_df(detail_rows, with_site)
    with st.container(key="param_panel"):
        with st.expander(f"Unit list — {len(df)} rows", expanded=False):
            if len(df):
                st.dataframe(
                    df, width="stretch", hide_index=True,
                    height=min(360, 40 + 35 * len(df)),
                    column_config={
                        col: st.column_config.Column(col, width="medium")
                        for col in df.columns
                    },
                )
            else:
                st.info("No unit rows were calculated.")
            if skipped:
                st.caption(
                    f"{len(skipped)} rows skipped — Sub Category not registered in BACKEND."
                )


# ---------------------------------------------------------------------------
# Dashboard body shared by Basecase All Unit and Summary
#
# Three sections, in this order:
#   1  Non-Staff  — MPP per section + level composition
#   2  Staff      — Foreman / Supervisor / Superintendent, grouped
#   3  Cost       — non-staff and staff cost, monthly or yearly via dropdown
# ---------------------------------------------------------------------------
MONTHS_PER_YEAR = 12

STAFF_COLORS = {
    "Foreman": theme.BRAND["orange"],
    "Supervisor": theme.BRAND["navy"],
    "Superintendent": theme.BRAND["amber"],
}

# Operational vs Planner dibedakan lewat pergeseran nada YANG KECIL, bukan
# warna berlawanan: keduanya jabatan sejenis, jadi mereka harus terbaca
# serumpun. Planner memakai versi 26% lebih terang dari warna dasarnya.
_SOFT = 0.26
GROUP_SHADE = {
    ("Operational", "Foreman"): theme.BRAND["orange"],
    ("Planner", "Foreman"): theme.tint(theme.BRAND["orange"], _SOFT),
    ("Operational", "Supervisor"): theme.BRAND["navy"],
    ("Planner", "Supervisor"): theme.tint(theme.BRAND["navy"], _SOFT),
    ("Operational", "Superintendent"): theme.BRAND["amber"],
    ("Planner", "Superintendent"): theme.tint(theme.BRAND["amber"], _SOFT),
}
STAFF_ROLES = ("Foreman", "Supervisor", "Superintendent")

# Kunci internal grup staf tetap "Planner" (bentuk yang dipakai
# calculator.py dan data_loader.py); yang berubah hanya label yang tampil.
GROUP_LABEL = {"Operational": "Operational", "Planner": "Planning"}


def grp_label(grp: str) -> str:
    return GROUP_LABEL.get(grp, grp)


def section_totals(summary: dict) -> tuple[dict, dict, dict, dict, float]:
    mech_total = {m: sum(v.get(m, 0) for v in summary["mechanic_by_category"].values())
                  for m in MONTH_COLS}
    mech_total["Tot"] = sum(mech_total[m] for m in MONTH_COLS)
    weld = summary["welder_total"]
    elec = summary["electric_total"]
    level_totals = {m: mech_total.get(m, 0) + weld.get(m, 0) + elec.get(m, 0)
                    for m in MONTH_COLS}
    return mech_total, weld, elec, level_totals, sum(level_totals.values())


def cost_rows_by_section(summary: dict) -> list[tuple[str, dict]]:
    """Cost per section (bukan per role) untuk tabel Cost / Non-Staff."""
    rows = []
    for cat, v in summary["mechanic_by_category"].items():
        rows.append((cat, {m: v.get(m, 0) * COST_RATE[m] for m in MONTH_COLS}))
    rows.append(("Welder", {m: summary["welder_total"].get(m, 0) * COST_RATE[m]
                            for m in MONTH_COLS}))
    rows.append(("Electrician", {m: summary["electric_total"].get(m, 0) * COST_RATE[m]
                                 for m in MONTH_COLS}))
    out = []
    for name, lv in rows:
        lv["Tot"] = sum(lv[m] for m in MONTH_COLS)
        if lv["Tot"] > 0:
            out.append((name, lv))
    return out


def staff_group_counts(staff: dict) -> dict:
    """Ringkas hasil compute_staff_fte jadi hitungan per grup.

    Superintendent ikut masuk ke dalam grupnya masing-masing: SPV Operational
    dijumlahkan lalu 1:5 dibulatkan ke atas, dan SPV Planner dihitung terpisah
    dengan cara yang sama.
    """
    oper = staff.get("operational", [])
    plan = staff.get("planner", [])
    return {
        "Operational": {
            "Foreman": sum(r["foreman"] for r in oper),
            "Supervisor": sum(r["supervisor"] for r in oper),
            "Superintendent": staff.get("superintendent_operational", 0),
        },
        "Planner": {
            "Foreman": sum(r["foreman"] for r in plan),
            "Supervisor": sum(r["supervisor"] for r in plan),
            "Superintendent": staff.get("superintendent_planner", 0),
        },
    }


def group_totals(g: dict) -> dict:
    """Total per jabatan lintas grup + grand total headcount staff."""
    out = {role: sum(g[grp][role] for grp in ("Operational", "Planner"))
           for role in STAFF_ROLES}
    out["Tot"] = sum(out.values())
    return out


# --- Matriks headcount per section -----------------------------------------
# Kolom tabel Total Headcount: section mekanik dulu (Digger, Hauler, ...),
# lalu dua role company-wide, lalu Planning. Superintendent tidak punya nilai
# per-section (dihitung 1:5 dari total SPV per grup), jadi kolom sectionnya
# diisi "–" dan angkanya hanya muncul di kolom Total.
PLANNING_COL = "Planning"


def _norm_key(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def headcount_matrix(summary: dict, staff: dict) -> tuple[list[str], list[dict]]:
    """Bangun kolom + baris tabel Total Headcount per section.

    Mengembalikan (columns, rows). Tiap row berupa dict:
        {"label": str, "kind": "head"|"data"|"total",
         "cells": {col: value|None}, "total": float}
    `None` pada sebuah sel berarti angka itu memang tidak ada per-section
    (bukan nol), dan dirender sebagai "–".
    """
    mech_cats = list(summary["mechanic_by_category"].keys())
    weld_tot = summary["welder_total"].get("Tot", 0)
    elec_tot = summary["electric_total"].get("Tot", 0)

    cols = list(mech_cats)
    if elec_tot:
        cols.append("Electrician")
    if weld_tot:
        cols.append("Welder")
    cols.append(PLANNING_COL)

    # peta posisi staf -> kolom
    col_by_norm = {_norm_key(c): c for c in cols}
    col_by_norm[_norm_key("Welding & Fabrication")] = "Welder" if weld_tot else None

    def blank():
        return {c: 0.0 for c in cols}

    # --- Non-staff ---
    ns = blank()
    for cat, lv in summary["mechanic_by_category"].items():
        ns[cat] = sum(lv.get(m, 0) for m in MONTH_COLS)
    if elec_tot:
        ns["Electrician"] = elec_tot
    if weld_tot:
        ns["Welder"] = weld_tot
    ns[PLANNING_COL] = 0.0

    # --- Staff per jabatan ---
    fore, supv = blank(), blank()
    for r in staff.get("operational", []):
        col = col_by_norm.get(_norm_key(r["posisi"]))
        if col:
            fore[col] += r["foreman"]
            supv[col] += r["supervisor"]
    for r in staff.get("planner", []):
        fore[PLANNING_COL] += r["foreman"]
        supv[PLANNING_COL] += r["supervisor"]

    supt_total = (staff.get("superintendent_operational", 0)
                  + staff.get("superintendent_planner", 0))

    rows = [
        {"label": "NON-STAFF", "kind": "head", "cells": blank(), "total": 0.0},
        {"label": "Mechanic", "kind": "data", "cells": ns, "total": sum(ns.values())},
        {"label": "STAFF", "kind": "head", "cells": blank(), "total": 0.0},
        {"label": "Foreman", "kind": "data", "cells": fore, "total": sum(fore.values())},
        {"label": "Supervisor", "kind": "data", "cells": supv, "total": sum(supv.values())},
        {"label": "Superintendent", "kind": "data",
         "cells": {c: None for c in cols}, "total": supt_total},
    ]
    grand = {c: ns[c] + fore[c] + supv[c] for c in cols}
    rows.append({"label": "TOTAL", "kind": "total", "cells": grand,
                 "total": sum(grand.values()) + supt_total})
    return cols, rows


# ---------------------------------------------------------------------------
# 1 — Non-Staff
# ---------------------------------------------------------------------------
def render_summary_section(summary: dict, staff: dict):
    """Section 1 — ringkasan headcount, dibaca lebih dulu sebelum rinciannya.

    Sebelumnya tabel Total Headcount berada di paling bawah (di dalam blok
    Cost). Dipindah ke atas karena angka inilah yang paling sering dicari,
    dan rinciannya (Non-Staff / Staff / Cost) baru dibaca sesudahnya.
    """
    _m, _w, _e, _lv, head_ns = section_totals(summary)
    g = staff_group_counts(staff)
    tot_staff = group_totals(g)["Tot"]
    grand = head_ns + tot_staff

    st.markdown(
        theme.section_heading(1, "Summary", "total manpower at a glance"),
        unsafe_allow_html=True,
    )

    c = st.columns([1.2, 1, 1], gap="small")
    with c[0]:
        st.markdown(
            theme.kpi_card("Total MPP", num(grand),
                           "non-staff + staff, all positions",
                           accent=theme.BRAND["navy"], emoji="👥", value_size=30),
            unsafe_allow_html=True,
        )
    with c[1]:
        share = f"{num(head_ns / grand * 100, 1)}%" if grand else "0%"
        st.markdown(
            theme.kpi_card("Non-Staff", num(head_ns),
                           f"<b>{share}</b> of total MPP",
                           accent=theme.BRAND["orange"], emoji="🔧"),
            unsafe_allow_html=True,
        )
    with c[2]:
        share = f"{num(tot_staff / grand * 100, 1)}%" if grand else "0%"
        st.markdown(
            theme.kpi_card("Staff", num(tot_staff),
                           f"<b>{share}</b> of total MPP",
                           accent=theme.BRAND["amber"], emoji="🧑‍💼"),
            unsafe_allow_html=True,
        )

    st.write("")
    cols, rows = headcount_matrix(summary, staff)
    with theme.card("total_head", "Total headcount by section",
                    "non-staff and staff, per section",
                    accent=theme.BRAND["navy"]):
        body = []
        for r in rows:
            if r["kind"] == "head":
                body.append([f'<b>{r["label"]}</b>'] + [""] * (len(cols) + 1))
                continue
            cells = [
                "–" if r["cells"][c] is None else num(r["cells"][c]) for c in cols
            ]
            label = "TOTAL" if r["kind"] == "total" else f'　{r["label"]}'
            body.append([label] + cells + [num(r["total"])])
        total_row = body.pop()
        st.markdown(
            theme.table_html(["Level"] + cols + ["Total"], body,
                             total_row=total_row, total_col=len(cols) + 1),
            unsafe_allow_html=True,
        )


def render_non_staff(summary: dict):
    mech_total, weld, elec, level_totals, grand = section_totals(summary)

    st.markdown(
        theme.section_heading(
            2, "Non-Staff", "mechanic, electrician and welder manpower",
            tag=f"{num(grand)} MPP"),
        unsafe_allow_html=True,
    )

    # KPI: kartu Cost per month dihapus (cost punya sectionnya sendiri) dan
    # ketiga kartu role kini memakai satuan yang SAMA — persentase terhadap
    # total MPP non-staff — supaya bisa dibandingkan langsung.
    k = st.columns(4, gap="small")
    with k[0]:
        st.markdown(
            theme.kpi_card("Total MPP", num(grand), "mechanic + electrician + welder",
                           accent=theme.BRAND["navy"], emoji="👷"),
            unsafe_allow_html=True,
        )
    for col, (label, vals, role) in zip(
        k[1:],
        [("Mechanic", mech_total, "Mechanic"),
         ("Electrician", elec, "Electric"),
         ("Welder", weld, "Welder")],
    ):
        with col:
            share = vals["Tot"] / grand * 100 if grand else 0
            st.markdown(
                theme.kpi_card(label, num(vals["Tot"]),
                               f"<b>{num(share, 1)}%</b> of MPP", role=role),
                unsafe_allow_html=True,
            )

    st.write("")
    a, b = st.columns([1.55, 1], gap="small")
    with a:
        with theme.card("total_section", "MPP per section",
                        "bar length = section MPP · colour = level",
                        accent=theme.LEVEL_SHADES["M2"]):
            st.plotly_chart(
                charts.level_stack_by_section_h(
                    summary["mechanic_by_category"], weld, elec, height=340),
                width="stretch", config={"displayModeBar": False},
            )
            st.markdown(theme.legend_html(LEVEL_LEGEND), unsafe_allow_html=True)
    with b:
        with theme.card("level_donut", "Level composition", "M1 – M3 across all roles",
                        accent=theme.LEVEL_SHADES["M1"]):
            st.plotly_chart(
                charts.share_donut(
                    MONTH_COLS, [level_totals[m] for m in MONTH_COLS],
                    [theme.LEVEL_SHADES[m] for m in MONTH_COLS],
                    num(grand), "TOTAL MPP", height=259),
                width="stretch", config={"displayModeBar": False},
            )
            st.markdown(
                theme.donut_legend([
                    (theme.LEVEL_SHADES[m], m, f"{num(level_totals[m])} MPP",
                     f"{num(level_totals[m] / grand * 100, 1)}%" if grand else "0%")
                    for m in MONTH_COLS
                ]),
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# 2 — Staff
# ---------------------------------------------------------------------------
def _group_rows(rows: list, g: dict, grp: str) -> list[list]:
    """Baris satu grup: SUM di paling atas, lalu rincian per section di bawahnya."""
    out = [[
        f'<b>{grp_label(grp).upper()}</b>',
        f'<b>{num(g[grp]["Foreman"])}</b>',
        f'<b>{num(g[grp]["Supervisor"])}</b>',
        f'<b>{num(g[grp]["Superintendent"])}</b>',
        f'<b>{num(sum(g[grp][r] for r in STAFF_ROLES))}</b>',
    ]]
    for r in rows:
        out.append([f"　{r['posisi']}", num(r["foreman"]), num(r["supervisor"]),
                    "–", num(r["foreman"] + r["supervisor"])])
    return out


def staff_parts(g: dict, values: dict | None = None) -> list[tuple[str, float, str]]:
    """Irisan pie staf: enam kombinasi jabatan x grup, warna serumpun."""
    src = values if values is not None else g
    return [
        (f"{role} · {grp_label(grp)}", src[grp][role], GROUP_SHADE[(grp, role)])
        for role in STAFF_ROLES
        for grp in ("Operational", "Planner")
    ]


def render_staff(staff: dict):
    oper = staff.get("operational", [])
    plan = staff.get("planner", [])
    g = staff_group_counts(staff)
    tot = group_totals(g)

    st.markdown(
        theme.section_heading(3, "Staff", "foreman, supervisor and superintendent",
                              tag=f"{num(tot['Tot'])} MPP"),
        unsafe_allow_html=True,
    )

    if not oper and not plan:
        st.markdown(
            theme.empty_state(
                "No staff data",
                "Fill in Area Kerja, Beban Admin, Jam Efektif, k and k spv on the "
                "Hasil Staff sheet.", "🗂️"),
            unsafe_allow_html=True,
        )
        return

    a, b = st.columns([1.55, 1], gap="small")

    with a:
        with theme.card("staff_table", "Staff per section",
                        "group total first, section detail below",
                        accent=theme.BRAND["navy"]):
            rows = _group_rows(oper, g, "Operational") + _group_rows(plan, g, "Planner")
            st.markdown(
                theme.table_html(
                    ["Section", "Foreman", "Supervisor", "Superintendent", "Total"],
                    rows,
                    total_row=["TOTAL", num(tot["Foreman"]), num(tot["Supervisor"]),
                               num(tot["Superintendent"]), num(tot["Tot"])],
                    total_col=4,
                ),
                unsafe_allow_html=True,
            )

    with b:
        parts = staff_parts(g)
        with theme.card("staff_donut", "Staff composition",
                        "deeper tone = Operational, lighter = Planning",
                        accent=theme.BRAND["orange_deep"]):
            st.plotly_chart(
                charts.share_donut(
                    [p[0] for p in parts], [p[1] for p in parts], [p[2] for p in parts],
                    num(tot["Tot"]), "STAFF MPP", height=258),
                width="stretch", config={"displayModeBar": False},
            )
            st.markdown(
                theme.donut_legend([
                    (c, lbl, f"{num(v)} MPP",
                     f"{num(v / tot['Tot'] * 100, 1)}%" if tot["Tot"] else "0%")
                    for lbl, v, c in parts if v > 0
                ]),
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# 3 — Cost
# ---------------------------------------------------------------------------
def render_cost(summary: dict, cost: dict, staff: dict):
    head = st.columns([3, 1], gap="small")
    with head[0]:
        st.markdown(
            theme.section_heading(4, "Cost", "non-staff and staff, one period at a time"),
            unsafe_allow_html=True,
        )
    with head[1]:
        # Satu pemilih periode, digayakan seperti tombol navy agar terbaca
        # sebagai kontrol, bukan sebagai input form biasa.
        with st.container(key="period_pick"):
            per = st.selectbox("Period", ["Monthly", "Yearly"], index=0,
                               key="cost_period", label_visibility="collapsed")
    factor = 1 if per == "Monthly" else MONTHS_PER_YEAR
    suffix = "per month" if factor == 1 else "per year"

    # ---------------- Non-Staff ----------------
    sect_rows = cost_rows_by_section(summary)
    ns_level = {m: sum(lv[m] for _n, lv in sect_rows) * factor for m in MONTH_COLS}
    ns_total = sum(ns_level.values())

    a, b = st.columns([1.55, 1], gap="small")
    with a:
        with theme.card("cost_ns_table", f"Non-Staff cost · {suffix}",
                        "per section, split by level"):
            st.markdown(
                theme.table_html(
                    ["Section", "M1", "M2", "M3", "Total"],
                    [[name] + [rp_short(lv[m] * factor) for m in MONTH_COLS]
                     + [rp_short(lv["Tot"] * factor)] for name, lv in sect_rows],
                    total_row=["TOTAL"] + [rp_short(ns_level[m]) for m in MONTH_COLS]
                    + [rp_short(ns_total)],
                    total_col=4,
                ),
                unsafe_allow_html=True,
            )
            # Tarifnya asumsi tetap (config.COST_RATE), bukan dari BACKEND —
            # ditulis di sini supaya angka tabel bisa ditelusuri tanpa membuka kode.
            st.markdown(
                theme.assumption_note(
                    "Rate per MPP / month",
                    [(m, rp_short(COST_RATE[m])) for m in MONTH_COLS],
                ),
                unsafe_allow_html=True,
            )
    with b:
        with theme.card("cost_ns_donut", "Level share", "proportion of non-staff cost",
                        accent=theme.LEVEL_SHADES["M1"]):
            st.plotly_chart(
                charts.share_donut(
                    MONTH_COLS, [ns_level[m] for m in MONTH_COLS],
                    [theme.LEVEL_SHADES[m] for m in MONTH_COLS],
                    rp_short(ns_total), suffix.upper(), height=176, money=True),
                width="stretch", config={"displayModeBar": False},
            )
            st.markdown(
                theme.donut_legend([
                    (theme.LEVEL_SHADES[m], m, rp_short(ns_level[m]),
                     f"{num(ns_level[m] / ns_total * 100, 1)}%" if ns_total else "0%")
                    for m in MONTH_COLS
                ]),
                unsafe_allow_html=True,
            )

    # ---------------- Staff ----------------
    g = staff_group_counts(staff)
    rate = {r: STAFF_COST_RATE[r] for r in STAFF_ROLES}
    gc = {grp: {r: g[grp][r] * rate[r] * factor for r in STAFF_ROLES}
          for grp in ("Operational", "Planner")}
    st_total = sum(sum(v.values()) for v in gc.values())

    def _cost_group_rows(rows, grp):
        out = [[
            f'<b>{grp_label(grp).upper()}</b>',
            f'<b>{rp_short(gc[grp]["Foreman"])}</b>',
            f'<b>{rp_short(gc[grp]["Supervisor"])}</b>',
            f'<b>{rp_short(gc[grp]["Superintendent"])}</b>',
            f'<b>{rp_short(sum(gc[grp].values()))}</b>',
        ]]
        for r in rows:
            out.append([
                f"　{r['posisi']}",
                rp_short(r["foreman"] * rate["Foreman"] * factor),
                rp_short(r["supervisor"] * rate["Supervisor"] * factor),
                "–",
                rp_short((r["foreman"] * rate["Foreman"]
                          + r["supervisor"] * rate["Supervisor"]) * factor),
            ])
        return out

    c, d = st.columns([1.55, 1], gap="small")
    with c:
        with theme.card("cost_st_table", f"Staff cost · {suffix}",
                        "group total first, section detail below",
                        accent=theme.BRAND["navy"]):
            rows = (_cost_group_rows(staff.get("operational", []), "Operational")
                    + _cost_group_rows(staff.get("planner", []), "Planner"))
            st.markdown(
                theme.table_html(
                    ["Section", "Foreman", "Supervisor", "Superintendent", "Total"],
                    rows,
                    total_row=["TOTAL"] + [
                        rp_short(gc["Operational"][r] + gc["Planner"][r])
                        for r in STAFF_ROLES
                    ] + [rp_short(st_total)],
                    total_col=4,
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                theme.assumption_note(
                    "Rate per staff / month",
                    [(r, rp_short(STAFF_COST_RATE[r])) for r in STAFF_ROLES],
                ),
                unsafe_allow_html=True,
            )
    with d:
        parts = staff_parts(g, gc)
        with theme.card("cost_st_donut", "Staff cost share",
                        "deeper tone = Operational, lighter = Planning",
                        accent=theme.BRAND["orange_deep"]):
            st.plotly_chart(
                charts.share_donut(
                    [p[0] for p in parts], [p[1] for p in parts], [p[2] for p in parts],
                    rp_short(st_total), suffix.upper(), height=258, money=True),
                width="stretch", config={"displayModeBar": False},
            )
            st.markdown(
                theme.donut_legend([
                    (c_, lbl, rp_short(v),
                     f"{num(v / st_total * 100, 1)}%" if st_total else "0%")
                    for lbl, v, c_ in parts if v > 0
                ]),
                unsafe_allow_html=True,
            )

    # ---------------- Totals ----------------
    mech, weld, elec, _lv, head_ns = section_totals(summary)
    tot_head = group_totals(g)
    grand_cost = ns_total + st_total
    grand_head = head_ns + tot_head["Tot"]

    ns_cost_role = {
        "Mechanic": sum(mech[m] * COST_RATE[m] for m in MONTH_COLS) * factor,
        "Welder": sum(weld.get(m, 0) * COST_RATE[m] for m in MONTH_COLS) * factor,
        "Electrician": sum(elec.get(m, 0) * COST_RATE[m] for m in MONTH_COLS) * factor,
    }
    st_cost_role = {r: (gc["Operational"][r] + gc["Planner"][r]) for r in STAFF_ROLES}

    def _breakdown(sum_ns, ns_items, sum_st, st_items, fmt):
        rows = [[f"<b>Non-Staff</b>", f"<b>{fmt(sum_ns)}</b>"]]
        rows += [[f"　{k}", fmt(v)] for k, v in ns_items]
        rows += [[f"<b>Staff</b>", f"<b>{fmt(sum_st)}</b>"]]
        rows += [[f"　{k}", fmt(v)] for k, v in st_items]
        return rows

    e, f = st.columns(2, gap="small")
    with e:
        cpm = rp_short(grand_cost / grand_head if grand_head else 0)
        with theme.card("total_cost", f"Total cost · {suffix}",
                        f"{cpm} per MPP · follows the period filter",
                        accent=theme.BRAND["orange_deep"]):
            st.markdown(
                theme.table_html(
                    ["Level", f"Total cost {suffix}"],
                    _breakdown(
                        ns_total, list(ns_cost_role.items()),
                        st_total, [(r, st_cost_role[r]) for r in STAFF_ROLES],
                        rp_short,
                    ),
                    total_row=["TOTAL", rp_short(grand_cost)], total_col=1,
                ),
                unsafe_allow_html=True,
            )
    with f:
        # Slot ini dulunya tabel Total Headcount — tabel itu sudah pindah ke
        # section Summary di atas, jadi diisi sebaran Non-Staff vs Staff yang
        # sebelumnya hanya berupa dua baris persentase di bawah tabel.
        with theme.card("cost_split_donut", f"Non-Staff vs Staff · {suffix}",
                        "share of total cost", accent=theme.BRAND["navy"]):
            parts_split = [
                ("Non-Staff", ns_total, theme.BRAND["orange"]),
                ("Staff", st_total, theme.BRAND["navy"]),
            ]
            st.plotly_chart(
                charts.share_donut(
                    [p[0] for p in parts_split], [p[1] for p in parts_split],
                    [p[2] for p in parts_split],
                    rp_short(grand_cost), suffix.upper(), height=232, money=True),
                width="stretch", config={"displayModeBar": False},
            )
            st.markdown(
                theme.donut_legend([
                    (c_, lbl, rp_short(v),
                     f"{num(v / grand_cost * 100, 1)}%" if grand_cost else "0%")
                    for lbl, v, c_ in parts_split if v > 0
                ]),
                unsafe_allow_html=True,
            )


def render_dashboard_body(summary: dict, cost: dict, staff: dict) -> dict:
    render_summary_section(summary, staff)
    render_non_staff(summary)
    render_staff(staff)
    render_cost(summary, cost, staff)
    mech_total, weld, elec, _lv, grand = section_totals(summary)
    return {"mech_total": mech_total, "weld_total": weld, "elec_total": elec,
            "grand_total": grand, "cost_total": cost["Total"]["Tot"]}


def render_unit_tab(site: str):
    df = st.session_state.working_units_df
    total_rows = len(df)
    total_pages = max(1, math.ceil(total_rows / PAGE_SIZE))
    st.session_state.page_num = min(st.session_state.get("page_num", 0), total_pages - 1)
    start = st.session_state.page_num * PAGE_SIZE
    page_df = df.iloc[start:start + PAGE_SIZE].reset_index(drop=True)

    head, lock = st.columns([6, 1.2])
    with head:
        st.caption(
            f"{total_rows} unit rows for site **{site}**, pulled from Sheet9 — "
            f"page {st.session_state.page_num + 1} of {total_pages}."
        )
    with lock:
        with st.popover("Edit", width="stretch"):
            if st.session_state.get("edit_unlocked"):
                st.success("Edit mode active for this session.")
                st.caption("Changes are not written back to the spreadsheet and are lost on reload.")
                if st.button("Lock again", width="stretch"):
                    st.session_state.edit_unlocked = False
                    st.rerun()
                if st.button("Reset to source data", width="stretch"):
                    st.session_state.working_units_df = units_to_df(get_units().get(site, []))
                    st.rerun()
            else:
                pwd = st.text_input("Password", type="password", key="edit_pwd_input")
                if st.button("Unlock edit mode", width="stretch"):
                    if pwd == UNIT_EDIT_PASSWORD:
                        st.session_state.edit_unlocked = True
                        st.rerun()
                    else:
                        st.error("Wrong password.")

    if st.session_state.get("edit_unlocked"):
        edited = st.data_editor(
            page_df, num_rows="fixed", width="stretch",
            key=f"editor_{site}_{st.session_state.page_num}",
            column_config={
                "Jumlah Unit": st.column_config.NumberColumn("Unit Count", min_value=0, step=1),
                "PA": st.column_config.NumberColumn("PA (%)", min_value=1, max_value=100, step=1),
            },
        )
        df.iloc[start:start + PAGE_SIZE] = edited.values
        st.session_state.working_units_df = df
    else:
        st.dataframe(page_df, width="stretch", hide_index=True)

    p1, p2, _ = st.columns([1, 1, 5])
    with p1:
        if st.button("← Previous", disabled=st.session_state.page_num <= 0,
                     width="stretch", key="bc_prev"):
            st.session_state.page_num -= 1
            st.rerun()
    with p2:
        if st.button("Next →", disabled=st.session_state.page_num >= total_pages - 1,
                     width="stretch", key="bc_next"):
            st.session_state.page_num += 1
            st.rerun()


# ===========================================================================
# Mode: Kalkulator
# ===========================================================================
def render_calculator_mode(backend):
    st.markdown(
        theme.header_band(
            "FTE Calculator — Single Unit Type",
            "Quick manpower estimate for one equipment type",
            chips=["Mode <b>Calculator</b>"],
        ),
        unsafe_allow_html=True,
    )

    sites = backend.sites or []
    sub_cats = backend.sub_categories or list(backend.load_factor.index)

    # ---------------- Parameter: satu kartu MELEBAR penuh ----------------
    # Empat input utama berjajar dalam satu baris, technical competency factor
    # di baris sendiri (butuh lebar untuk track slider), lalu catatan jarak +
    # tombol. Hasil perhitungan menyusul DI BAWAH kartu ini, bukan di sampingnya.
    #
    # Tiap parameter diberi tombol "!" di UJUNG KANAN barisnya sendiri:
    # label widget disembunyikan lalu digambar ulang di kolom kiri, sehingga
    # penanda bantuan sejajar dengan label, bukan menggantung di bawah input.
    def param_label(text: str, key: str, title: str, body: str):
        lab, hlp = st.columns([5, 1], gap="small")
        with lab:
            st.markdown(f'<div class="dh-plabel">{text}</div>', unsafe_allow_html=True)
        with hlp:
            with st.container(key=f"help_{key}"):
                with st.popover("i", width="content"):
                    st.markdown(f"**{title}**")
                    st.markdown(body)

    with theme.card("calc_param", "Parameters", "all fields required"):
        p1, p2, p3, p4 = st.columns(4, gap="medium")
        with p1:
            param_label(
                "Site", "site", "Site",
                "The mine site the calculation belongs to. It decides three "
                "values that are read from BACKEND and not typed here: "
                "**ratio shift**, **lost time**, and **work area distance**.",
            )
            site = st.selectbox("Site", options=sites, index=None,
                                placeholder="Select site…", key="calc1_site",
                                label_visibility="collapsed")
        with p2:
            param_label(
                "Equipment type", "subcat", "Equipment type (sub category)",
                "The equipment class, e.g. *Big Exca* or *Medium Hauler*. Each "
                "class carries its own **load factor** for mechanic, "
                "electrician and welder — that factor is what turns a unit "
                "count into working hours.",
            )
            sub_category = st.selectbox("Equipment type", options=sub_cats, index=None,
                                        placeholder="Select sub category…",
                                        key="calc1_subcat",
                                        label_visibility="collapsed")
        with p3:
            param_label(
                "Unit count", "qty", "Unit count",
                "How many units of the selected equipment type the site "
                "operates. Manpower scales directly with this number.",
            )
            jumlah_unit = st.number_input("Unit count", min_value=0.0, value=1.0,
                                          step=1.0, key="calc1_jml",
                                          label_visibility="collapsed")
        with p4:
            param_label(
                "PA target (%)", "pa", "PA target (Physical Availability)",
                "The share of time a unit must be available to operate. A "
                "**higher** PA target means less tolerance for downtime, so "
                "more maintenance manpower is required.",
            )
            pa = st.number_input("PA target (%)", min_value=1.0, max_value=100.0,
                                 value=85.0, step=1.0, key="calc1_pa",
                                 label_visibility="collapsed")

        param_label(
            "Technical Competency Factor", "tcf", "Technical Competency Factor",
            "How productive one mechanic actually is compared with the ideal "
            "standard. **Lower** factor means each person delivers fewer "
            "effective hours, so more people are needed for the same work.\n\n"
            "In Basecase and Summary this value is not typed in — it is read "
            "from BACKEND, maintained through the *Plant & Maintenance* form. "
            "Here it stays adjustable so you can test scenarios.",
        )
        cf = st.slider("Technical Competency Factor", min_value=0.1, max_value=1.0,
                       value=0.6, step=0.01, key="calc1_cf",
                       label_visibility="collapsed")

        jarak_km = backend.jarak.get(site) if site else None
        n1, n2 = st.columns([2.6, 1], gap="medium")
        with n1:
            if site and jarak_km is None:
                st.markdown(
                    theme.inline_note(
                        f"No distance on record for site <b>{site}</b>. "
                        f"The calculation cannot run until that value exists.",
                        warn=True,
                    ),
                    unsafe_allow_html=True,
                )
            elif jarak_km is not None:
                st.markdown(
                    theme.inline_note(
                        f"Work area distance for site {site}: <b>{num(jarak_km, 2)} km</b>"
                    ),
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    theme.inline_note("Select a site and equipment type to enable the calculation."),
                    unsafe_allow_html=True,
                )
        with n2:
            with st.container(key="calc_go"):
                compute_clicked = st.button(
                    "Calculate FTE", width="stretch", key="calc1_button",
                    disabled=not (site and sub_category and jarak_km is not None),
                )

    if compute_clicked:
        try:
            orig_sc = backend.original_sub_name(sub_category) or sub_category
            st.session_state.calc1_result = compute_fte(
                FTEInput(
                    site=site, competency_factor=cf, jarak_km=jarak_km,
                    sub_category=orig_sc, jenis_unit="",
                    pa_percent=pa, populasi=jumlah_unit,
                ),
                backend,
            )
        except CalculationError as exc:
            st.error(f"Calculation failed: {exc}")
            st.session_state.calc1_result = None
        st.rerun()

    result = st.session_state.get("calc1_result")

    st.write("")

    if not result:
        st.markdown(
            theme.empty_state(
                "No result yet",
                "Fill in the parameters above, then press Calculate FTE.",
                "🧮",
            ),
            unsafe_allow_html=True,
        )
        return

    # ---------------- Hasil: tiga panel berjajar di bawah parameter ----------
    tot = result["fte"]["Total"]
    cost_lv = result["cost"]["Total"]

    r1, r2, r3 = st.columns([1, 1.12, 1.24], gap="medium")

    with r1:
        with theme.card("calc_donut", "Split per role", "share of each role",
                        accent=theme.ROLE_COLORS["Mechanic"]):
            st.plotly_chart(
                charts.role_donut(result["fte"], height=177, show_legend=False),
                width="stretch", config={"displayModeBar": False},
            )
            # Legend bawaan Plotly hanya menampilkan nama role. Diganti legend
            # sendiri supaya FTE dan persentase share-nya ikut terbaca.
            grand = sum(result["fte"][r]["Tot"] for r in ("Mechanic", "Electric", "Welder")) or 1
            st.markdown(
                theme.donut_legend([
                    (theme.ROLE_COLORS[r], theme.ROLE_LABEL[r],
                     f"{num(result['fte'][r]['Tot'])} MPP",
                     f"{num(result['fte'][r]['Tot'] / grand * 100, 1)}%")
                    for r in ("Mechanic", "Electric", "Welder")
                ]),
                unsafe_allow_html=True,
            )

    with r2:
        # Qty & cost di readout sengaja TOTAL lintas role (mekanik +
        # electrician + welder digabung) — rincian per role sudah ada di panel
        # sebelahnya, jadi readout cukup menjawab "berapa orang dan berapa
        # biayanya di tiap level".
        with theme.card("calc_out", "Calculator result", "total across all roles",
                        accent=theme.BRAND["orange_deep"]):
            st.markdown(
                theme.calc_readout(
                    total_fte=num(tot["Tot"]),
                    levels=[
                        (m, theme.LEVEL_NOTE[m], num(tot[m]), rp(cost_lv[m]))
                        for m in MONTH_COLS
                    ],
                    grand_label="Estimated cost<br/>per month",
                    grand_value=rp(cost_lv["Tot"]),
                ),
                unsafe_allow_html=True,
            )

    with r3:
        with theme.card("calc_table", "Breakdown per role", "FTE per level"):
            rows, sums = [], {"M1": 0.0, "M2": 0.0, "M3": 0.0, "Tot": 0.0}
            for role in ("Mechanic", "Electric", "Welder"):
                v = result["fte"][role]
                rows.append([theme.ROLE_LABEL[role], num(v["M1"]), num(v["M2"]),
                             num(v["M3"]), num(v["Tot"])])
                for kk in sums:
                    sums[kk] += v.get(kk, 0)
            st.markdown(
                theme.table_html(
                    ["Role", "M1", "M2", "M3", "Total"], rows,
                    total_row=["TOTAL", num(sums["M1"]), num(sums["M2"]),
                               num(sums["M3"]), num(sums["Tot"])],
                    total_col=4,
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                theme.stat_list([
                    (f"Cost · {theme.ROLE_LABEL[role]}", rp(result["cost"][role]["Tot"]))
                    for role in ("Mechanic", "Electric", "Welder")
                ]),
                unsafe_allow_html=True,
            )


# ===========================================================================
# Mode: Basecase All Unit  /  Summary
# ===========================================================================
def render_basecase_sidebar(backend):
    st.sidebar.markdown('<div class="dh-side-label">Site parameters</div>',
                        unsafe_allow_html=True)
    site = st.sidebar.selectbox("Site", options=(backend.sites or []), index=None,
                                placeholder="Select site…", key="bc_site")

    # Technical Competency Factor tidak lagi diatur di sini: nilainya milik
    # form Plant & Maintenance dan dibaca dari BACKEND. Ditampilkan read-only
    # supaya tetap jelas angka mana yang sedang dipakai.
    if site:
        st.sidebar.markdown(
            theme.side_readout("Technical Competency Factor",
                               num(backend.tcf_for(site), 2), site),
            unsafe_allow_html=True,
        )

    with st.sidebar:
        with st.container(key="sb_refresh"):
            refresh = st.button("Reload", width="stretch", key="sb_refresh_btn")

    return site, refresh


def render_summary_sidebar(backend):
    st.sidebar.markdown('<div class="dh-side-label">Summary parameters</div>',
                        unsafe_allow_html=True)
    st.sidebar.markdown(
        theme.side_readout_list(
            "Technical Competency Factor",
            [(s, num(backend.tcf_for(s), 2)) for s in (backend.sites or [])],
        ),
        unsafe_allow_html=True,
    )
    with st.sidebar:
        with st.container(key="sm_refresh"):
            refresh = st.button("Reload", width="stretch", key="sm_refresh_btn")
    return refresh


def _clear_caches():
    get_backend.clear()
    get_units.clear()
    get_staff.clear()
    for k in list(st.session_state.keys()):
        if k.startswith("_allsites_"):
            del st.session_state[k]


def render_basecase_mode(backend):
    site, refresh = render_basecase_sidebar(backend)

    if refresh:
        _clear_caches()
        st.rerun()

    if site is None:
        st.markdown(
            theme.header_band(
                "Basecase All Unit",
                "Manpower requirement for every unit within one site",
            ),
            unsafe_allow_html=True,
        )
        st.session_state.current_site = None
        st.markdown(
            theme.empty_state(
                "Pick a site to start",
                "Choose a site in the left panel — the dashboard builds itself.",
                "📍",
            ),
            unsafe_allow_html=True,
        )
        return

    try:
        units_all = get_units()
    except BackendDataError as exc:
        st.error(f"Unit data (Sheet9) failed to load: {exc}")
        return

    if st.session_state.get("current_site") != site:
        st.session_state.current_site = site
        st.session_state.working_units_df = units_to_df(units_all.get(site, []))
        st.session_state.page_num = 0
        st.session_state.edit_unlocked = False

    # Dihitung langsung begitu site dipilih — tidak ada lagi tombol Calculate
    # maupun slider, karena satu-satunya input yang tersisa (Technical
    # Competency Factor) sudah datang dari BACKEND.
    cf = backend.tcf_for(site)
    try:
        summary = compute_site_summary(
            site, df_to_units(st.session_state.working_units_df), backend, cf
        )
        staff_res = compute_staff_fte(
            site, summary["mechanic_by_category"], summary["welder_total"],
            summary["electric_total"], get_staff(),
        )
        cost = compute_site_cost(
            summary["mechanic_by_category"], summary["welder_total"],
            summary["electric_total"],
        )
        result = {"summary": summary, "staff": staff_res, "cost": cost}
    except CalculationError as exc:
        st.error(f"Calculation failed: {exc}")
        result = None
    except BackendDataError as exc:
        st.error(f"Staff data failed to load: {exc}")
        result = None

    # Tanpa chip: nilainya sudah tampil sebagai kartu parameter tepat di
    # bawah band ini, jadi mengulangnya hanya menambah keramaian.
    st.markdown(
        theme.header_band(
            f"Basecase All Unit — {site}",
            "Manpower requirement for every unit within one site",
        ),
        unsafe_allow_html=True,
    )

    if not result:
        with st.expander(f"View unit data for site {site}"):
            render_unit_tab(site)
        return

    summary, staff_res, cost = result["summary"], result["staff"], result["cost"]
    unit_qty = sum(d["jumlah_unit"] for d in summary["detail_rows"])

    render_formula_panel(
        formula_items(backend, unit_qty, site=site),
        summary["detail_rows"], with_site=False, skipped=summary["skipped_units"],
    )
    render_dashboard_body(summary, cost, staff_res)




def render_summary_mode(backend):
    refresh = render_summary_sidebar(backend)

    if refresh:
        _clear_caches()
        st.rerun()

    try:
        units_all = get_units()
    except BackendDataError as exc:
        st.error(f"Unit data (Sheet9) failed to load: {exc}")
        return

    st.markdown(
        theme.header_band(
            "Summary — All Sites",
            "Every unit across every site, rolled up into one view",
        ),
        unsafe_allow_html=True,
    )

    # Langsung dihitung: tiap site memakai Technical Competency Factor-nya
    # sendiri dari BACKEND, jadi tidak ada lagi satu nilai global yang harus
    # dipilih dulu sebelum hasilnya muncul.
    with st.spinner("Calculating every site…"):
        agg = aggregate_all_sites(backend, units_all)

    if not agg:
        st.markdown(
            theme.empty_state(
                "Nothing to aggregate",
                "No site produced a usable result. Check the unit data on Sheet9.",
                "⚠️",
            ),
            unsafe_allow_html=True,
        )
        return

    summary, cost = agg["summary"], agg["cost"]
    unit_qty = sum(d["jumlah_unit"] for d in summary["detail_rows"])

    render_formula_panel(
        formula_items(backend, unit_qty),
        summary["detail_rows"], with_site=True, skipped=summary["skipped_units"],
    )
    render_dashboard_body(summary, cost, {
        "operational": agg["operational"],
        "planner": agg["planner"],
        "superintendent_operational": agg["superintendent_operational"],
        "superintendent_planner": agg["superintendent_planner"],
    })


# ===========================================================================
# Landing page + embedded form pages
# ===========================================================================
DIRECTORATES = {
    "engineer": {
        "icon": "mechanical-engineer.png",
        "title": "Engineering",
        "accent": theme.BRAND["navy"],
        "wash": "#EAEFF6",
        "desc": "Submit the unit population that the MPP calculation is "
                "built from.",
        "fills": "Fills in the <b>Unit Form</b> — category, unit type and PA.",
        "url": "https://script.google.com/macros/s/AKfycbyCDxxEYFCMfgghj4KD_X1iqo49lkKlQfzpXj8FK-nGp30R1YLgIJnAeMlsEMXPtCSQ9w/exec",
    },
    "hcm": {
        "icon": "value.png",
        "title": "OD & HCM Strategy",
        "accent": theme.BRAND["amber"],
        "wash": "#FFF4DC",
        "desc": "Record field that sets how many hours a mechanic "
                "actually works.",
        "fills": "Fills in the <b>Observation Form</b> — effective "
                 "mechanic working hour.",
        "url": "https://script.google.com/macros/s/AKfycbxTMCA17k_yqY-WjZWXera6D_LYfk3M5lwwxRU08O-WLZeT5iASFe6_Vsbg6vIvDMPB2w/exec",
    },
    "plant": {
        "icon": "optimizing.png",
        "title": "Plant & Maintenance",
        "accent": theme.BRAND["orange"],
        "wash": "#FFEEE0",
        "desc": "Set the Technical Competency Factor each site is calculated "
                "with.",
        "fills": "Fills in the <b>Technical Competency Factor</b> — per site.",
        "url": "https://script.google.com/macros/s/AKfycbwTQQ4JPpecGH3DGpOAJ1a54DRjqn28LmH2mXGo2b8G8kSrXDIXxE4YR_u7EaLnWWTp/exec",
    },
}

# Ketiga form di atas mengisi BACKEND; kalkulator memakainya. Karena itu
# kalkulator berdiri sendiri di baris bawah landing page, bukan sebagai kolom
# keempat yang sejajar dengan ketiga form input.
CALCULATOR_CARD = {
    "icon": "optimizing (1).png",
    "title": "FTE Calculator",
    "accent": theme.BRAND["navy"],
    "wash": "#EAEFF6",
    "desc": "Turn unit population and site parameters into manpower and cost.",
    "fills": "Reads everything the three forms above write into BACKEND.",
}

# Panduan per departemen. Di-key sesuai `DIRECTORATES` supaya tiap kartu
# memunculkan panduannya sendiri lewat ikon info di pojoknya, bukan satu
# panel panjang berisi ketiganya.
HOW_TO_USE = {
    "hcm": {
        "title": "Organization Development & HCM",
        "subtitle": "OD & HCM Strategy Form",
        "steps": [
            "Choose one of the two modules below: <b>Ratio Manpower</b> to edit ratio "
            "manpower, or <b>Mechanic Observation</b> to record a new observation.",
            "In <b>Ratio Manpower</b>, all sites (BCP, ACP, KCP) are shown at once "
            "with their current ratio — update the value for any site and click "
            "<b>Save</b> to write it directly to the BACKEND sheet.",
            "In <b>Mechanic Observation</b>, fill in the observation details and "
            "activity table, then submit — the data is automatically routed to the "
            "correct sheet based on the selected site.",
        ],
    },
    "engineer": {
        "title": "Engineering Form",
        "subtitle": "Load Factor Data Editor",
        "steps": [
            "Click one of the three site cards below (KCP, ACP, or BCP) to open that "
            "site's workspace.",
            "Once a site is selected, the <b>Edit Distance Parameter</b> panel will "
            "appear first — this value is read from and saved directly to the BACKEND "
            "sheet for that site.",
            "Below it, the <b>Unit Data</b> table shows all existing units for the "
            "selected site. Edit any cell directly in the table — changed rows are "
            "highlighted, then click <b>Save Changes</b> to write them back to the "
            "spreadsheet.",
            "Use the <b>Add New Unit</b> panel at the bottom to register a new unit "
            "type for the selected site.",
        ],
    },
    "plant": {
        "title": "Plant & Maintenance",
        "subtitle": "Technical Competency Factor",
        "steps": [
            "All three sites (BCP, ACP, KCP) are shown below with their current "
            "<b>Technical Competency Factor (Mechanic)</b>. Update the value for any "
            "site and click <b>Save</b> to write it directly to the BACKEND sheet.",
        ],
    },
}


def render_landing():
    # Band hero + tombol Open Calculator dibungkus satu container: container
    # inilah jangkar `position: relative`-nya, sehingga tombol bisa ditempel
    # di pojok kanan band. Kalkulator satu-unit dipisah ke sini karena alurnya
    # berdiri sendiri — Summary dan Basecase satu alur dengan ketiga form di
    # bawah, kalkulator tidak.
    with st.container(key="hero_wrap"):
        st.markdown(
            theme.hero(theme.image_uri("logo_putih (2).png"),
                       "PT Darma Henwa · Workforce Planning",
                       "Manpower Planning Workspace", action=True),
            unsafe_allow_html=True,
        )
        with st.container(key="hero_calc"):
            if st.button("Open Calculator", width="stretch", key="btn_hero_calc"):
                st.session_state.page = "calculator"
                st.rerun()
    st.write("")

    cols = st.columns(3, gap="medium")
    for col, key in zip(cols, ("engineer", "hcm", "plant")):
        d = DIRECTORATES[key]
        with col:
            # Kartu + ikon info dibungkus satu container: container inilah yang
            # jadi jangkar `position: relative`, sehingga ikonnya bisa duduk
            # tepat di pojok kanan-atas kartu, bukan mengambang di bawahnya.
            with st.container(key=f"dept_{key}"):
                st.markdown(
                    theme.choice_card(theme.image_uri(d["icon"]), d["title"],
                                      d["desc"], d["fills"], d["accent"], d["wash"]),
                    unsafe_allow_html=True,
                )
                with st.container(key=f"info_{key}"):
                    with st.popover("i", width="content"):
                        guide = HOW_TO_USE[key]
                        st.markdown(theme.how_to_use([guide]), unsafe_allow_html=True)
            with st.container(key=f"go_{key}"):
                if st.button("Open form", width="stretch", key=f"btn_go_{key}"):
                    st.session_state.page = key
                    st.rerun()

    st.write("")
    st.markdown(theme.landing_divider("Then run the calculation"),
                unsafe_allow_html=True)

    d = CALCULATOR_CARD
    st.markdown(
        theme.choice_card(theme.image_uri(d["icon"]), d["title"], d["desc"],
                          d["fills"], d["accent"], d["wash"], wide=True),
        unsafe_allow_html=True,
    )
    with st.container(key="go_calc"):
        if st.button("Run Calculation", width="stretch", key="btn_go_calc",
                     type="primary"):
            st.session_state.page = "calc"
            st.rerun()


def render_embed_page(key: str):
    """A form page that should not look like an embed.

    The Apps Script form is served inside an iframe — there is no way around
    that — so everything else is stripped back: no sidebar, no dashboard
    chrome, just a slim bar with the title and a way home. The frame is given a
    tall fixed height so the form scrolls as one page instead of inside a small
    inner scroller, which is what usually makes embeds look bad.
    """
    d = DIRECTORATES[key]

    st.markdown(
        theme.header_band(d["title"], d["desc"]),
        unsafe_allow_html=True,
    )

    nav1, _nav2 = st.columns([1, 4])
    with nav1:
        with st.container(key="embed_home"):
            if st.button("← Back to home", width="stretch", key=f"home_{key}"):
                st.session_state.page = "landing"
                st.rerun()

    with st.container(key="embed_frame"):
        components.iframe(d["url"], height=1500, scrolling=True)


# ===========================================================================
def _sidebar_brand():
    """Blok merek di puncak sidebar — dipakai halaman kalkulator maupun
    halaman Summary/Basecase, jadi ditarik keluar supaya tidak dua salinan."""
    logo = theme.image_uri("logo_putih (2).png")
    mark = (f'<div class="mark"><img src="{logo}" alt=""/></div>'
            if logo else '<div class="mark"></div>')
    st.markdown(
        f'<div class="dh-side-brand">{mark}'
        f'<div><div class="title">FTE Calculator</div>'
        f'<div class="subtitle">PT Darma Henwa</div></div></div>',
        unsafe_allow_html=True,
    )


def main():
    st.session_state.setdefault("page", "landing")
    page = st.session_state.page

    if page == "landing":
        render_landing()
        return
    if page in ("engineer", "hcm", "plant"):
        render_embed_page(page)
        return

    try:
        backend = get_backend()
    except BackendDataError as exc:
        st.markdown(theme.header_band("FTE Calculator", "PT Darma Henwa"),
                    unsafe_allow_html=True)
        st.error(f"BACKEND data failed to load: {exc}")
        return

    # Kalkulator satu-unit berdiri sendiri: alurnya tidak berbagi apa pun
    # dengan Summary/Basecase (tidak memilih site, tidak memakai Sheet9), jadi
    # ia punya halamannya sendiri dan tidak ikut di nav mode.
    if page == "calculator":
        with st.sidebar:
            _sidebar_brand()
            with st.container(key="side_home_calc"):
                if st.button("← Home", width="stretch", key="btn_home_calc"):
                    st.session_state.page = "landing"
                    st.rerun()
        render_calculator_mode(backend)
        if DEMO:
            st.caption("Demo mode: bundled sample data, not Google Sheets. "
                       "Run without FTE_DEMO=1 for live data.")
        return

    # Summary dibuka lebih dulu: begitu kalkulator dibuka, angka gabungan
    # seluruh site langsung terlihat tanpa perlu memilih apa pun.
    st.session_state.setdefault("app_mode", "summary")
    if st.session_state.app_mode == "calculator":      # sisa state versi lama
        st.session_state.app_mode = "summary"

    with st.sidebar:
        _sidebar_brand()
        with st.container(key="side_home"):
            if st.button("← Home", width="stretch", key="btn_home"):
                st.session_state.page = "landing"
                st.rerun()

        st.markdown('<div class="dh-side-label">Mode</div>', unsafe_allow_html=True)
        for key, label, container in (
            ("summary", "Summary", "nav_summary"),
            ("multisite", "Basecase All Unit", "nav_basecase"),
        ):
            with st.container(key=container):
                if st.button(label, width="stretch", key=f"btn_mode_{key}",
                             type="primary" if st.session_state.app_mode == key else "secondary"):
                    st.session_state.app_mode = key
                    st.rerun()

    if st.session_state.app_mode == "summary":
        render_summary_mode(backend)
    else:
        render_basecase_mode(backend)

    if DEMO:
        st.caption("Demo mode: bundled sample data, not Google Sheets. "
                   "Run without FTE_DEMO=1 for live data.")


if __name__ == "__main__":
    main()
