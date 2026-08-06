"""
Chart builders (Plotly) — FTE Calculator PT Darma Henwa (v6).

Aturan yang dipakai konsisten di seluruh file ini:

*   Satu chart menjawab satu pertanyaan. Tidak ada dua chart yang menampilkan
    angka yang sama dengan bentuk berbeda (masalah v4: line chart & bar chart
    isinya sama).

*   Pembagian warna tegas: chart yang membandingkan ROLE memakai
    `theme.ROLE_COLORS` (3 hue berbeda), chart yang membandingkan LEVEL
    memakai `theme.LEVEL_SHADES` (satu ramp orange, M1 paling pekat). Jadi
    pembaca tidak perlu menghafal dua sistem warna sekaligus.

*   Welder & Electrician nilainya company-wide, bukan per-section. Di v4
    keduanya digambar sebagai garis datar melintasi semua section, yang
    secara visual seolah-olah "ada nilainya di tiap section". Sekarang
    keduanya digambar sebagai batang terpisah di sebelah kanan, dipisahkan
    garis putus-putus + label "Company-wide", jadi bedanya jelas.

*   Angka ditulis dengan format Indonesia (ribuan pakai titik, desimal pakai
    koma). Plotly tidak bisa melakukan ini sendiri, jadi semua label & hover
    text disiapkan sebagai string dari Python.
"""
from __future__ import annotations

from typing import Dict, List

import plotly.graph_objects as go

from theme import (
    BRAND,
    LEVEL_SHADES,
    NEUTRAL,
    ROLE_COLORS,
    ROLE_LABEL,
)

FONT = dict(family="Public Sans, Segoe UI, sans-serif", size=11, color=NEUTRAL["text"])
HOVER = dict(
    bgcolor=BRAND["navy"],
    bordercolor=BRAND["navy"],
    font=dict(family="Public Sans, Segoe UI, sans-serif", color="#FFFFFF", size=11.5),
)
LEVELS = ["M1", "M2", "M3"]


def _layout(height: int, **kw) -> dict:
    base = dict(
        height=height,
        margin=dict(l=6, r=10, t=6, b=6),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=FONT,
        hoverlabel=HOVER,
        hovermode="closest",
        showlegend=False,
        dragmode=False,
    )
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Format angka gaya Indonesia
# ---------------------------------------------------------------------------
def num(x: float, dec: int = 0) -> str:
    s = f"{x:,.{dec}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def rp(x: float) -> str:
    return "Rp " + num(x)


def rp_short(x: float, jt_dec: int = 0) -> str:
    """Rp 1,72 M / Rp 320 jt / Rp 4.500 — dipakai di chart & KPI.

    `jt_dec` menaikkan presisi di rentang juta; dipakai untuk angka seperti
    tarif Rp 11,5 jt yang kalau dibulatkan ke "Rp 12 jt" jadi salah baca.
    """
    ax = abs(x)
    if ax >= 1_000_000_000:
        return "Rp " + num(x / 1_000_000_000, 2) + " M"
    if ax >= 1_000_000:
        return "Rp " + num(x / 1_000_000, jt_dec) + " jt"
    return rp(x)


def _empty(height: int, msg: str = "No data to display yet") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_layout(height, xaxis=dict(visible=False), yaxis=dict(visible=False)))
    fig.add_annotation(
        text=msg, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        font=dict(family="Public Sans", size=12, color=NEUTRAL["text_soft"]),
    )
    return fig


# ---------------------------------------------------------------------------
# 1) Total MPP per section & per role — batang horizontal, warna per ROLE
# ---------------------------------------------------------------------------
def level_donut(level_totals: Dict[str, float], height: int = 286) -> go.Figure:
    """Menjawab: 'seberapa berat komposisi tenaga senior vs junior?'

    Center hole dipakai untuk total MPP supaya tidak perlu KPI tambahan.
    """
    vals = [level_totals.get(l, 0) for l in LEVELS]
    total = sum(vals)
    if total <= 0:
        return _empty(height)

    from theme import LEVEL_NOTE

    fig = go.Figure(
        go.Pie(
            labels=[f"{l} · {LEVEL_NOTE[l]}" for l in LEVELS],
            values=vals,
            hole=0.62,
            sort=False,
            direction="clockwise",
            marker=dict(colors=[LEVEL_SHADES[l] for l in LEVELS], line=dict(color="#fff", width=2)),
            text=[f"{num(v / total * 100, 1)}%" for v in vals],
            textinfo="text",
            textposition="inside",
            insidetextorientation="horizontal",
            textfont=dict(family="Archivo", size=12, color="#fff"),
            hovertext=[
                f"<b>{l} ({LEVEL_NOTE[l]})</b><br>{num(v)} MPP · {num(v / total * 100, 1)}%"
                for l, v in zip(LEVELS, vals)
            ],
            hovertemplate="%{hovertext}<extra></extra>",
        )
    )
    fig.add_annotation(
        text=(
            f"<span style='font-family:Archivo;font-size:26px;font-weight:800;color:{NEUTRAL['text']}'>{num(total)}</span>"
            f"<br><span style='font-size:10px;color:{NEUTRAL['text_muted']};letter-spacing:.08em'>TOTAL MPP</span>"
        ),
        showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5, align="center",
    )
    fig.update_layout(
        **_layout(
            height,
            margin=dict(l=6, r=6, t=6, b=26),
            showlegend=True,
            legend=dict(
                orientation="h", yanchor="top", y=0.02, xanchor="center", x=0.5,
                font=dict(family="Public Sans", size=10.5, color=NEUTRAL["text_muted"]),
            ),
        )
    )
    return fig


# ---------------------------------------------------------------------------
# 3) Persebaran M1–M3 per section — stacked bar, warna per LEVEL
# ---------------------------------------------------------------------------
def level_stack_by_section(
    mechanic_by_category: Dict[str, Dict[str, float]],
    welder_total: dict,
    electric_total: dict,
    height: int = 286,
) -> go.Figure:
    """Menjawab: 'di section mana komposisi seniornya paling berat?'

    Section mekanik di kiri; Welder & Electrician di kanan setelah garis
    pemisah, karena angkanya company-wide (bukan per-section).
    """
    sections = list(mechanic_by_category.keys())
    extras = []
    if welder_total.get("Tot", 0):
        extras.append(("Welder", welder_total))
    if electric_total.get("Tot", 0):
        extras.append(("Electrician", electric_total))

    x = sections + [e[0] for e in extras]
    if not x:
        return _empty(height)

    fig = go.Figure()
    for lvl in LEVELS:
        y, hover = [], []
        for cat in sections:
            v = mechanic_by_category[cat].get(lvl, 0)
            y.append(v)
            hover.append(f"<b>{cat}</b><br>Mechanic {lvl}: {num(v)} MPP")
        for name, tot in extras:
            v = tot.get(lvl, 0)
            y.append(v)
            hover.append(f"<b>{name}</b> (company-wide)<br>{lvl}: {num(v)} MPP")
        fig.add_trace(
            go.Bar(
                x=x, y=y, name=lvl,
                marker=dict(color=LEVEL_SHADES[lvl], line=dict(width=0)),
                hovertext=hover,
                hovertemplate="%{hovertext}<extra></extra>",
            )
        )

    totals = [
        sum(mechanic_by_category[c].get(l, 0) for l in LEVELS) for c in sections
    ] + [sum(t.get(l, 0) for l in LEVELS) for _n, t in extras]
    fig.add_trace(
        go.Scatter(
            x=x, y=totals, mode="text",
            text=[num(t) for t in totals],
            textposition="top center",
            textfont=dict(family="Archivo", size=10.5, color=NEUTRAL["text"]),
            hoverinfo="skip", showlegend=False, cliponaxis=False,
        )
    )

    # garis pemisah antara section dan entitas company-wide
    if sections and extras:
        cut = len(sections) - 0.5
        fig.add_vline(x=cut, line=dict(color=NEUTRAL["border"], width=1.5, dash="dot"))
        fig.add_annotation(
            x=cut + (len(extras) / 2), xref="x", yref="paper", y=1.0,
            text="Company-wide", showarrow=False,
            font=dict(family="Public Sans", size=9.5, color=NEUTRAL["text_soft"]),
        )

    fig.update_layout(
        **_layout(
            height,
            barmode="stack",
            margin=dict(l=6, r=10, t=18, b=6),
            xaxis=dict(
                tickangle=-18, showgrid=False,
                tickfont=dict(family="Public Sans", size=10, color=NEUTRAL["text"]),
            ),
            yaxis=dict(
                gridcolor=NEUTRAL["border_soft"], zerolinecolor=NEUTRAL["border"],
                tickfont=dict(family="Public Sans", size=10, color=NEUTRAL["text_muted"]),
                range=[0, max(totals) * 1.16] if totals else None,
            ),
            bargap=0.34,
        )
    )
    return fig


# ---------------------------------------------------------------------------
# 4) Cost — busur komposisi (skala relatif antar-site)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 4) Cost — setengah lingkaran komposisi per role
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Section chart — horizontal stacked bar (M1-M3), total di ujung batang
# ---------------------------------------------------------------------------
def level_stack_by_section_h(
    mechanic_by_category: Dict[str, Dict[str, float]],
    welder_total: Dict[str, float],
    electric_total: Dict[str, float],
    height: int = 340,
) -> go.Figure:
    """Batang MENDATAR bertumpuk: panjang total = MPP section, warna = level.

    Versi mendatar dipilih karena nama section ("Support & Facility",
    "Auxilary Track") terpotong atau miring ketika dipakai sebagai label sumbu
    X pada batang vertikal.
    """
    cats = list(mechanic_by_category.keys())
    rows = [(c, mechanic_by_category[c]) for c in cats]
    rows.append(("Welder", welder_total))
    rows.append(("Electrician", electric_total))
    rows = [(n, v) for n, v in rows if sum(v.get(m, 0) for m in LEVELS) > 0]
    if not rows:
        return _empty(height)

    # dibalik supaya section terbesar berada di ATAS saat digambar Plotly
    rows = rows[::-1]
    names = [n for n, _ in rows]
    totals = [sum(v.get(m, 0) for m in LEVELS) for _, v in rows]

    fig = go.Figure()
    for lvl in LEVELS:
        vals = [v.get(lvl, 0) for _, v in rows]
        fig.add_bar(
            y=names, x=vals, orientation="h", name=lvl,
            marker=dict(color=LEVEL_SHADES[lvl], line=dict(width=0)),
            customdata=[[t, (val / t * 100 if t else 0)] for val, t in zip(vals, totals)],
            hovertemplate=(
                "<b>%{y}</b><br>" + lvl + ": %{x:.0f} MPP"
                "<br>Section total: %{customdata[0]:.0f} MPP"
                "<br>Share of section: %{customdata[1]:.1f}%<extra></extra>"
            ),
        )

    fig.update_layout(barmode="stack", **_layout(height, margin=dict(l=4, r=44, t=6, b=6)))
    fig.update_layout(bargap=0.32, showlegend=False)
    fig.update_xaxes(showgrid=True, gridcolor=NEUTRAL["border_soft"], zeroline=False,
                     tickfont=dict(family="Public Sans", size=10.5,
                                   color=NEUTRAL["text_muted"]))
    fig.update_yaxes(showgrid=False, zeroline=False,
                     tickfont=dict(family="Public Sans", size=11,
                                   color=NEUTRAL["text"]))
    for name, tot in zip(names, totals):
        fig.add_annotation(
            x=tot, y=name, text=f"<b>{num(tot)}</b>", showarrow=False,
            xanchor="left", xshift=7,
            font=dict(family="Public Sans", size=10.5, color=NEUTRAL["text"]),
        )
    return fig


# ---------------------------------------------------------------------------
# Donut generik — dipakai untuk semua pie chart di dashboard
# ---------------------------------------------------------------------------
def share_donut(
    labels: List[str],
    values: List[float],
    colors: List[str],
    center_value: str,
    center_note: str = "",
    height: int = 300,
    money: bool = False,
) -> go.Figure:
    """Donut penuh dengan tooltip yang selalu menyebut nilai DAN persentase.

    `money=True` memformat nilai sebagai rupiah. Legend-nya tidak dipakai dari
    Plotly melainkan dari `theme.donut_legend`, supaya persentasenya ikut
    terbaca tanpa harus hover.
    """
    pairs = [(l, v, c) for l, v, c in zip(labels, values, colors) if v > 0]
    if not pairs:
        return _empty(height)
    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    colors = [p[2] for p in pairs]
    total = sum(values)
    fmt = rp if money else (lambda v: f"{num(v)} MPP")

    fig = go.Figure(
        go.Pie(
            labels=labels, values=values, hole=0.62, sort=False,
            direction="clockwise",
            marker=dict(colors=colors, line=dict(color="#FFFFFF", width=2)),
            texttemplate="%{percent:.1%}",
            textposition="inside",
            insidetextfont=dict(family="Public Sans", size=11, color="#FFFFFF"),
            hovertext=[f"<b>{l}</b><br>{fmt(v)}<br>{num(v / total * 100, 1)}% of total"
                       for l, v in zip(labels, values)],
            hovertemplate="%{hovertext}<extra></extra>",
        )
    )
    # Ukuran teks tengah mengikuti tinggi chart. Nilai tetap 25px membuat
    # angka panjang seperti "Rp 15,19 M" melewati lubang donut dan menabrak
    # irisannya pada chart yang pendek.
    size = max(13, min(24, round(height * 0.078)))
    fig.add_annotation(
        x=0.5, y=0.55, xref="paper", yref="paper", showarrow=False, text=center_value,
        font=dict(family="Archivo", size=size, color=NEUTRAL["text"]),
    )
    if center_note:
        fig.add_annotation(
            x=0.5, y=0.43, xref="paper", yref="paper", showarrow=False, text=center_note,
            font=dict(family="Public Sans", size=max(8, round(size * 0.42)),
                      color=NEUTRAL["text_muted"]),
        )
    fig.update_layout(**_layout(height, margin=dict(l=6, r=6, t=6, b=6)))
    return fig


def share_semicircle(
    labels: List[str],
    values: List[float],
    colors: List[str],
    center_value: str,
    center_note: str = "",
    height: int = 210,
    hover_fmt=None,
) -> go.Figure:
    """Half donut: one headline number in the middle, colour = share of each part.

    Deliberately not a speedometer — there is no scale arc, no needle, and no
    comparison against another entity. The half-circle shape comes from adding
    one transparent slice the size of the total and rotating the pie by 270
    degrees, so exactly the top half stays visible.
    """
    pairs = [(l, v, c) for l, v, c in zip(labels, values, colors) if v > 0]
    if not pairs:
        return _empty(height)
    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    colors = [p[2] for p in pairs]
    total = sum(values)

    if hover_fmt is None:
        def hover_fmt(label, value):
            return f"<b>{label}</b><br>{rp(value)}<br>{num(value / total * 100, 1)}% of total"

    fig = go.Figure(
        go.Pie(
            labels=labels + ["_pad"],
            values=values + [total],
            hole=0.66,
            rotation=270,
            sort=False,
            direction="clockwise",
            marker=dict(colors=colors + ["rgba(0,0,0,0)"],
                        line=dict(color="#FFFFFF", width=2)),
            textinfo="none",
            hovertext=[hover_fmt(l, v) for l, v in zip(labels, values)] + [""],
            hovertemplate="%{hovertext}<extra></extra>",
            hoverinfo="text",
        )
    )
    fig.add_annotation(
        x=0.5, y=0.40, xref="paper", yref="paper", showarrow=False,
        text=center_value,
        font=dict(family="Archivo", size=26, color=BRAND["orange_deep"]),
    )
    if center_note:
        fig.add_annotation(
            x=0.5, y=0.25, xref="paper", yref="paper", showarrow=False,
            text=center_note,
            font=dict(family="Public Sans", size=10.5, color=NEUTRAL["text_muted"]),
        )
    fig.update_layout(**_layout(height, margin=dict(l=6, r=6, t=6, b=0)))
    return fig


def cost_semicircle(cost_breakdown: dict, center_value: str, center_note: str = "",
                    height: int = 210, factor: int = 1) -> go.Figure:
    """Half donut of cost split by role.

    `factor` menskalakan NILAI IRISANNYA, bukan cuma teks di tengah. Tanpa ini,
    kartu tahunan menampilkan total tahunan di tengah tetapi tooltipnya masih
    memperlihatkan rupiah bulanan.
    """
    roles = ["Mechanic", "Electric", "Welder"]
    return share_semicircle(
        [ROLE_LABEL[r] for r in roles],
        [cost_breakdown.get(r, {}).get("Tot", 0) * factor for r in roles],
        [ROLE_COLORS[r] for r in roles],
        center_value, center_note, height,
    )


def role_donut(fte_table: dict, height: int = 210, show_legend: bool = True) -> go.Figure:
    roles = ["Mechanic", "Electric", "Welder"]
    vals = [fte_table.get(r, {}).get("Tot", 0) for r in roles]
    total = sum(vals)
    if total <= 0:
        return _empty(height)

    fig = go.Figure(
        go.Pie(
            labels=[ROLE_LABEL[r] for r in roles],
            values=vals,
            hole=0.58, sort=False,
            marker=dict(colors=[ROLE_COLORS[r] for r in roles], line=dict(color="#fff", width=2)),
            textinfo="none",
            hovertext=[
                f"<b>{ROLE_LABEL[r]}</b><br>{num(v)} MPP · {num(v / total * 100, 1)}%"
                for r, v in zip(roles, vals)
            ],
            hovertemplate="%{hovertext}<extra></extra>",
        )
    )
    fig.add_annotation(
        text=(
            f"<span style='font-family:Archivo;font-size:22px;font-weight:800;color:{NEUTRAL['text']}'>{num(total)}</span>"
            f"<br><span style='font-size:9.5px;color:{NEUTRAL['text_muted']};letter-spacing:.08em'>TOTAL MPP</span>"
        ),
        showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
    )
    fig.update_layout(
        **_layout(
            height,
            margin=dict(l=4, r=4, t=4, b=24 if show_legend else 4),
            showlegend=show_legend,
            legend=dict(
                orientation="h", yanchor="top", y=0.04, xanchor="center", x=0.5,
                font=dict(family="Public Sans", size=10, color=NEUTRAL["text_muted"]),
            ),
        )
    )
    return fig
