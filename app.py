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


def aggregate_all_sites(backend, units_all, competency_factor: float) -> dict | None:
    """Roll every site up into one summary/cost pair shaped like a single site.

    Summary mode reuses the exact dashboard body that Basecase uses, so the
    aggregate has to mimic the shape `compute_site_summary` returns:
    mechanic_by_category, welder_total, electric_total, detail_rows,
    skipped_units. Each site is still computed by the untouched calculator —
    only the results are summed here.

    Cached in session_state per competency factor: recomputing every site on
    each rerun is the single most expensive thing this app does.
    """
    key = f"_allsites_{competency_factor:.2f}"
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
            summ = compute_site_summary(s, rows, backend, competency_factor)
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


# ---------------------------------------------------------------------------
# 1 — Non-Staff
# ---------------------------------------------------------------------------
def render_non_staff(summary: dict):
    mech_total, weld, elec, level_totals, grand = section_totals(summary)

    st.markdown(
        theme.section_heading(
            1, "Non-Staff", "mechanic, electrician and welder manpower",
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
        f'<b>{grp.upper()}</b>',
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
        (f"{role} · {grp}", src[grp][role], GROUP_SHADE[(grp, role)])
        for role in STAFF_ROLES
        for grp in ("Operational", "Planner")
    ]


def render_staff(staff: dict):
    oper = staff.get("operational", [])
    plan = staff.get("planner", [])
    g = staff_group_counts(staff)
    tot = group_totals(g)

    st.markdown(
        theme.section_heading(2, "Staff", "foreman, supervisor and superintendent",
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
                        "deeper tone = Operational, lighter = Planner",
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
            theme.section_heading(3, "Cost", "non-staff and staff, one period at a time"),
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
            f'<b>{grp.upper()}</b>',
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
    with d:
        parts = staff_parts(g, gc)
        with theme.card("cost_st_donut", "Staff cost share",
                        "deeper tone = Operational, lighter = Planner",
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
        with theme.card("total_head", "Total headcount", "non-staff and staff",
                        accent=theme.BRAND["navy"]):
            st.markdown(
                theme.table_html(
                    ["Level", "Total headcount"],
                    _breakdown(
                        head_ns,
                        [("Mechanic", mech["Tot"]), ("Welder", weld.get("Tot", 0)),
                         ("Electrician", elec.get("Tot", 0))],
                        tot_head["Tot"],
                        [(r, tot_head[r]) for r in STAFF_ROLES],
                        num,
                    ),
                    total_row=["TOTAL", num(grand_head)], total_col=1,
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                theme.stat_list([
                    ("Non-Staff share",
                     f"{num(head_ns / grand_head * 100, 1)}%" if grand_head else "0%"),
                    ("Staff share",
                     f"{num(tot_head['Tot'] / grand_head * 100, 1)}%" if grand_head else "0%"),
                ]),
                unsafe_allow_html=True,
            )
    with f:
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
            st.markdown(
                theme.stat_list([
                    ("Non-Staff share",
                     f"{num(ns_total / grand_cost * 100, 1)}%" if grand_cost else "0%"),
                    ("Staff share",
                     f"{num(st_total / grand_cost * 100, 1)}%" if grand_cost else "0%"),
                ]),
                unsafe_allow_html=True,
            )


def render_dashboard_body(summary: dict, cost: dict, staff: dict) -> dict:
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
    # Empat input utama berjajar dalam satu baris, competency factor di baris
    # sendiri (butuh lebar untuk track slider), lalu catatan jarak + tombol.
    # Hasil perhitungan menyusul DI BAWAH kartu ini, bukan di sampingnya.
    with theme.card("calc_param", "Parameters", "all fields required"):
        p1, p2, p3, p4 = st.columns(4, gap="medium")
        with p1:
            site = st.selectbox("Site", options=sites, index=None,
                                placeholder="Select site…", key="calc1_site")
        with p2:
            sub_category = st.selectbox("Equipment type", options=sub_cats, index=None,
                                        placeholder="Select sub category…", key="calc1_subcat")
        with p3:
            jumlah_unit = st.number_input("Unit count", min_value=0.0, value=1.0,
                                          step=1.0, key="calc1_jml")
        with p4:
            pa = st.number_input("PA target (%)", min_value=1.0, max_value=100.0,
                                 value=85.0, step=1.0, key="calc1_pa")

        cf = st.slider("Competency factor", min_value=0.1, max_value=1.0,
                       value=0.6, step=0.01, key="calc1_cf")

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
    cf = st.sidebar.slider("Competency factor", min_value=0.1, max_value=1.0,
                           value=0.6, step=0.01, key="bc_cf")

    c1, c2 = st.sidebar.columns(2)
    with c1:
        with st.container(key="sb_refresh"):
            refresh = st.button("Reload", width="stretch", key="sb_refresh_btn")
    with c2:
        with st.container(key="sb_compute"):
            compute = st.button("Calculate", width="stretch",
                                disabled=site is None, key="sb_compute_btn")

    return site, cf, refresh, compute


def render_summary_sidebar():
    st.sidebar.markdown('<div class="dh-side-label">Summary parameters</div>',
                        unsafe_allow_html=True)
    cf = st.sidebar.slider("Competency factor", min_value=0.1, max_value=1.0,
                           value=0.6, step=0.01, key="sm_cf")
    c1, c2 = st.sidebar.columns(2)
    with c1:
        with st.container(key="sm_refresh"):
            refresh = st.button("Reload", width="stretch", key="sm_refresh_btn")
    with c2:
        with st.container(key="sm_compute"):
            compute = st.button("Calculate", width="stretch", key="sm_compute_btn")
    return cf, refresh, compute


def _clear_caches():
    get_backend.clear()
    get_units.clear()
    get_staff.clear()
    for k in list(st.session_state.keys()):
        if k.startswith("_allsites_"):
            del st.session_state[k]


def render_basecase_mode(backend):
    site, cf, refresh, compute_clicked = render_basecase_sidebar(backend)

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
        st.session_state.calc_result = None
        st.markdown(
            theme.empty_state(
                "Pick a site to start",
                "Choose a site and competency factor in the left panel, then press Calculate.",
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
        st.session_state.calc_result = None
        st.session_state.edit_unlocked = False

    if compute_clicked:
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
            st.session_state.calc_result = {"summary": summary, "staff": staff_res, "cost": cost}
        except CalculationError as exc:
            st.error(f"Calculation failed: {exc}")
            st.session_state.calc_result = None
        except BackendDataError as exc:
            st.error(f"Staff data failed to load: {exc}")
            st.session_state.calc_result = None

    result = st.session_state.get("calc_result")

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
        st.markdown(
            theme.empty_state(
                f"Site {site} is ready",
                f"{len(st.session_state.working_units_df)} unit rows loaded. "
                f"Press Calculate in the left panel to build the dashboard.",
                "▶️",
            ),
            unsafe_allow_html=True,
        )
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
    cf, refresh, compute_clicked = render_summary_sidebar()

    if refresh:
        _clear_caches()
        st.rerun()

    try:
        units_all = get_units()
    except BackendDataError as exc:
        st.error(f"Unit data (Sheet9) failed to load: {exc}")
        return

    if compute_clicked:
        st.session_state.pop(f"_allsites_{cf:.2f}", None)
        st.session_state.summary_ready = True

    st.markdown(
        theme.header_band(
            "Summary — All Sites",
            "Every unit across every site, rolled up into one view",
        ),
        unsafe_allow_html=True,
    )

    if not st.session_state.get("summary_ready"):
        st.markdown(
            theme.empty_state(
                "Ready to aggregate",
                "Set the competency factor in the left panel, then press Calculate. "
                "Every site is computed individually and the results are summed.",
                "🗺️",
            ),
            unsafe_allow_html=True,
        )
        return

    with st.spinner("Calculating every site…"):
        agg = aggregate_all_sites(backend, units_all, cf)

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
        "title": "Engineer",
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
        "desc": "Turn unit population and site parameters into manpower and "
                "cost.",
        "fills": "Opens the <b>FTE Calculator</b> — MPP, staff and cost.",
    },
}


def render_landing():
    st.markdown(
        theme.hero(theme.image_uri("logo_putih (2).png"),
                   "PT Darma Henwa · Workforce Planning",
                   "Manpower Planning Workspace"),
        unsafe_allow_html=True,
    )
    st.write("")

    cols = st.columns(3, gap="medium")
    for col, key in zip(cols, ("engineer", "hcm", "plant")):
        d = DIRECTORATES[key]
        with col:
            st.markdown(
                theme.choice_card(theme.image_uri(d["icon"]), d["title"],
                                  d["desc"], d["fills"], d["accent"], d["wash"]),
                unsafe_allow_html=True,
            )
            with st.container(key=f"go_{key}"):
                label = "Open calculator" if key == "plant" else "Open form"
                if st.button(label, width="stretch", key=f"btn_go_{key}",
                             type="primary" if key == "plant" else "secondary"):
                    st.session_state.page = key
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
def main():
    st.session_state.setdefault("page", "landing")
    page = st.session_state.page

    if page == "landing":
        render_landing()
        return
    if page in ("engineer", "hcm"):
        render_embed_page(page)
        return

    try:
        backend = get_backend()
    except BackendDataError as exc:
        st.markdown(theme.header_band("FTE Calculator", "PT Darma Henwa"),
                    unsafe_allow_html=True)
        st.error(f"BACKEND data failed to load: {exc}")
        return

    st.session_state.setdefault("app_mode", "calculator")

    with st.sidebar:
        logo = theme.image_uri("logo_putih (2).png")
        mark = f'<div class="mark"><img src="{logo}" alt=""/></div>' if logo else '<div class="mark"></div>'
        st.markdown(
            f'<div class="dh-side-brand">{mark}'
            f'<div><div class="title">FTE Calculator</div>'
            f'<div class="subtitle">PT Darma Henwa</div></div></div>',
            unsafe_allow_html=True,
        )
        with st.container(key="side_home"):
            if st.button("← Home", width="stretch", key="btn_home"):
                st.session_state.page = "landing"
                st.rerun()

        st.markdown('<div class="dh-side-label">Mode</div>', unsafe_allow_html=True)
        for key, label, container in (
            ("calculator", "Calculator", "nav_calc"),
            ("multisite", "Basecase All Unit", "nav_basecase"),
            ("summary", "Summary", "nav_summary"),
        ):
            with st.container(key=container):
                if st.button(label, width="stretch", key=f"btn_mode_{key}",
                             type="primary" if st.session_state.app_mode == key else "secondary"):
                    st.session_state.app_mode = key
                    st.rerun()

    if st.session_state.app_mode == "calculator":
        render_calculator_mode(backend)
    elif st.session_state.app_mode == "summary":
        render_summary_mode(backend)
    else:
        render_basecase_mode(backend)

    if DEMO:
        st.caption("Demo mode: bundled sample data, not Google Sheets. "
                   "Run without FTE_DEMO=1 for live data.")


if __name__ == "__main__":
    main()
