# data_loader.py
"""
BACKEND CSV/XLSX loader + parser — versi dinamis, anti hardcoded-row-index.

Struktur sheet BACKEND (Kolom A berisi judul seksi UNIK, dicari via scanning teks,
bukan nomor baris):

    Blok 1: "Load Factor"
        -> header berikutnya: Sub Category | Attribute | Load Mechanic | Load Electrican | Load Welder
        -> baris data sampai ketemu baris kosong

    Blok 2: "Ratio Shift"
        -> header berikutnya (opsional): Site | Ratio
        -> baris data Site,Ratio sampai baris kosong

    Blok 3: "Proporsi RACI"
        -> baris vertikal langsung di bawah judul: label di Kolom A
           ("Mechanic" / "Electric(ian)" / "Welder"), nilai di Kolom B
        -> sampai baris kosong

    Blok 4: "Split Ratio Mechanic"
        -> baris vertikal (M1/M2/M3, nilai di Kolom B) sampai baris kosong

    Blok 5: "Split Ratio Welder"
        -> baris vertikal (M1/M2, nilai di Kolom B) sampai baris kosong

    Blok 6: "Split Ratio Electrician"
        -> baris vertikal (M1/M2, nilai di Kolom B) sampai baris kosong

    Blok 7: "Lost Time"
        -> baris data Site,Value sampai baris kosong / akhir file

Prinsip desain:
 - TIDAK ADA nomor baris hardcoded di mana pun. Semua seksi dicari dengan
   mencocokkan teks Kolom A (case-insensitive, di-strip) terhadap judul seksi.
 - Pencarian Split Ratio dimulai SETELAH blok RACI selesai (raci_end_idx),
   supaya urut dan tidak pernah salah tabrakan walau ada perubahan sheet di masa depan.
 - `load_factor` diberi index = Sub Category (bukan RangeIndex default!) supaya
   `backend.load_factor.loc[sub_category]` di calculator.py benar-benar bekerja.
 - Key dict `raci` distandardisasi menjadi persis "Mechanic" / "Electric" / "Welder"
   (sesuai config.ROLES dan apa yang dipakai calculator.py) — bukan "mechanic"/"electrician".
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union
import pandas as pd
import math
import logging
import os
import re

logger = logging.getLogger(__name__)


class BackendDataError(Exception):
    """Raised when backend data cannot be loaded or parsed cleanly."""
    pass


@dataclass
class BackendData:
    load_factor: pd.DataFrame                                       # index = Sub Category
    ratio_shift: Dict[str, float]                                    # Site -> ratio
    raci: Dict[str, float]                                           # "Mechanic"/"Electric"/"Welder" -> fraction
    split_mechanic: List[float]                                      # [M1, M2, M3]
    split_welder: List[float]                                        # [M1, M2]
    split_electrician: List[float]                                   # [M1, M2]
    lost_time: Dict[str, float]                                      # Site -> jam
    sites: List[str]
    sub_categories: List[str] = field(default_factory=list)          # untuk dropdown UI
    units_map: Dict[str, List[str]] = field(default_factory=dict)    # canonical sub -> [Attribute,...]
    _norm_to_orig: Dict[str, str] = field(default_factory=dict)      # canonical -> original Sub Category
    jarak: Dict[str, float] = field(default_factory=dict)            # Site -> km (v2, ganti input manual)
    classification: Dict[str, str] = field(default_factory=dict)     # canonical sub-category -> Category (v2)
    classification_order: List[str] = field(default_factory=list)    # urutan kemunculan Category asli (v2)
    # v7: Technical Competency Factor per site, diisi lewat form Plant &
    # Maintenance (Apps Script) dan dibaca dari seksi BACKEND, bukan lagi
    # slider manual di sidebar.
    competency_factor: Dict[str, float] = field(default_factory=dict)  # Site -> TCF

    DEFAULT_COMPETENCY_FACTOR = 0.8

    def tcf_for(self, site: Optional[str]) -> float:
        """Technical Competency Factor untuk sebuah site.

        Kalau site tidak terdaftar di seksi BACKEND (mis. site baru yang belum
        diisi lewat form Plant & Maintenance), dipakai nilai default supaya
        perhitungan tetap jalan alih-alih gagal total.
        """
        if not site:
            return self.DEFAULT_COMPETENCY_FACTOR
        val = self.competency_factor.get(site)
        if val is None:
            for k, v in self.competency_factor.items():
                if k.strip().lower() == site.strip().lower():
                    val = v
                    break
        if val is None or not (val == val) or val <= 0:   # None / NaN / <= 0
            return self.DEFAULT_COMPETENCY_FACTOR
        return float(val)

    def first_site(self) -> Optional[str]:
        return self.sites[0] if self.sites else None

    def category_for(self, sub_category_input: Optional[str]) -> Optional[str]:
        """Cari nama Category (v2 classification, mis. 'Digger') untuk sebuah Sub Category."""
        if not sub_category_input:
            return None
        return self.classification.get(self._normalize(sub_category_input))

    @staticmethod
    def _normalize(s: Optional[str]) -> str:
        if s is None:
            return ""
        s = str(s).strip().lower()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[^\w\s]", "", s)
        return s

    def units_for(self, sub_category_input: Optional[str]) -> List[str]:
        if not sub_category_input:
            return []
        return self.units_map.get(self._normalize(sub_category_input), [])

    def original_sub_name(self, sub_category_input: Optional[str]) -> Optional[str]:
        """Cocokkan input (bebas huruf besar/kecil, spasi) ke nama asli di load_factor.index."""
        if not sub_category_input:
            return None
        nk = self._normalize(sub_category_input)
        if self._norm_to_orig:
            hit = self._norm_to_orig.get(nk)
            if hit:
                return hit
        for orig in self.sub_categories or []:
            if self._normalize(orig) == nk:
                return orig
        return None


# -----------------------
# Helper utilities
# -----------------------
def _cell(df: pd.DataFrame, r: int, c: int):
    if r < 0 or r >= len(df) or c < 0 or c >= df.shape[1]:
        return None
    v = df.iat[r, c]
    return None if pd.isna(v) else v


def _col_text(df: pd.DataFrame, r: int, c: int = 0) -> str:
    v = _cell(df, r, c)
    return "" if v is None else str(v).strip()


def _is_blank_row(df: pd.DataFrame, r: int) -> bool:
    if r >= len(df):
        return True
    for c in df.iloc[r].tolist():
        if pd.isna(c):
            continue
        if str(c).strip() != "":
            return False
    return True


def _safe_float(value) -> float:
    """Konversi ke float secara aman. Mengembalikan math.nan jika gagal.
    Menerima '1,5', '68%', '  ', None, tipe numerik."""
    if value is None:
        return math.nan
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    s = str(value).strip()
    if s == "" or s.lower() in {"nan", "none", "-"}:
        return math.nan
    s = s.replace("%", "").replace(" ", "")
    if "," in s and "." not in s and s.count(",") == 1:
        s = s.replace(",", ".")
    s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        logger.debug("Safe float parse failed for %r", value)
        return math.nan


def _parse_fraction_cell(cell) -> float:
    """Parse sel yang merepresentasikan proporsi/pecahan (mis. RACI, Split Ratio).
    Menerima '68%', '0.68', '68', '1,5' dll. Heuristik: nilai > 1 dianggap persen."""
    if cell is None:
        return math.nan
    s = str(cell).strip()
    if s == "":
        return math.nan
    if "%" in s:
        v = _safe_float(s)
        return math.nan if math.isnan(v) else v / 100.0
    v = _safe_float(s)
    if math.isnan(v):
        return math.nan
    return v / 100.0 if v > 1.0 else v


def _looks_like_html_or_error(raw: pd.DataFrame) -> Optional[str]:
    """Deteksi kasus umum: Google mengembalikan halaman HTML (butuh izin/login)
    alih-alih CSV asli. Mengembalikan pesan diagnosis jika terdeteksi, None jika aman."""
    if raw is None or raw.empty:
        return "Response kosong (0 baris)."
    first_cell = str(raw.iat[0, 0]) if raw.shape[0] > 0 and raw.shape[1] > 0 else ""
    lowered = first_cell.strip().lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html") or "<html" in lowered:
        return "Response berupa halaman HTML, bukan CSV — kemungkinan besar Google Sheets menolak akses (izin sharing belum 'Anyone with the link - Viewer')."
    joined_sample = " ".join(str(x) for x in raw.head(10).values.flatten().tolist()).lower()
    if "sign in" in joined_sample or "accounts.google.com" in joined_sample or "you need access" in joined_sample or "request access" in joined_sample:
        return "Response berisi halaman login/permintaan akses Google — sheet belum di-share publik (View)."
    if raw.shape[1] == 1 and raw.shape[0] < 5:
        return "Response tidak terlihat seperti CSV multi-kolom yang valid."
    return None


def _raw_preview(raw: pd.DataFrame, n: int = 8) -> str:
    try:
        return raw.head(n).to_string(max_colwidth=40)
    except Exception:
        return "(gagal menampilkan preview)"



def _find_title_row(df: pd.DataFrame, title: str, start: int = 0) -> Optional[int]:
    """Cari baris yang Kolom A-nya PERSIS sama dengan judul seksi (case/space-insensitive)."""
    t = title.strip().lower()
    for i in range(start, len(df)):
        if _col_text(df, i, 0).strip().lower() == t:
            return i
    return None


def _read_vertical_pairs_safe(df: pd.DataFrame, start_row: int):
    """Baca pasangan (label_kolomA, nilai_kolomB) mulai dari start_row sampai baris kosong.
    Mengembalikan (list_pairs, baris_setelah_blok)."""
    pairs = []
    r = start_row
    while r < len(df) and not _is_blank_row(df, r):
        label = _col_text(df, r, 0)
        val = _cell(df, r, 1)
        if label:
            pairs.append((label, val))
        r += 1
    return pairs, r


# -----------------------
# Main parser
# -----------------------
def parse_backend(raw: pd.DataFrame) -> BackendData:
    df = raw.copy().reset_index(drop=True)

    # =========================================================
    # Blok 1: Load Factor
    # =========================================================
    lf_title_idx = _find_title_row(df, "load factor")
    if lf_title_idx is None:
        html_issue = _looks_like_html_or_error(df)
        preview = _raw_preview(df)
        detail = f"\n\nKemungkinan penyebab: {html_issue}" if html_issue else ""
        raise BackendDataError(
            "Seksi 'Load Factor' tidak ditemukan di Kolom A BACKEND."
            f"{detail}\n\nPreview 8 baris pertama data yang benar-benar diterima:\n{preview}"
        )

    header_idx = lf_title_idx + 1
    while header_idx < len(df) and _is_blank_row(df, header_idx):
        header_idx += 1
    if header_idx >= len(df):
        raise BackendDataError("Header tabel 'Load Factor' tidak ditemukan.")

    header_cells = [str(x).strip() if not pd.isna(x) else "" for x in df.iloc[header_idx].tolist()]
    header_lower = [h.lower() for h in header_cells]

    def _find_col(*keywords) -> Optional[int]:
        for idx, h in enumerate(header_lower):
            if all(kw in h for kw in keywords):
                return idx
        return None

    col_sub = _find_col("sub")
    col_attr = _find_col("attribute")
    col_mech = _find_col("mechanic")
    col_elec = _find_col("electr")  # menangkap "Electrican"/"Electrician" (typo aman)
    col_weld = _find_col("welder")

    if col_sub is None:
        raise BackendDataError("Kolom 'Sub Category' tidak ditemukan pada header Load Factor.")

    data_start = header_idx + 1
    data_end = data_start
    while data_end < len(df) and not _is_blank_row(df, data_end):
        data_end += 1

    lf_records = []
    for r in range(data_start, data_end):
        sub = _col_text(df, r, col_sub)
        if not sub or sub.lower() == "nan":
            continue
        rec = {
            "Sub Category": sub,
            "Attribute": _col_text(df, r, col_attr) if col_attr is not None else "",
            "Load Mechanic": _safe_float(_cell(df, r, col_mech)) if col_mech is not None else math.nan,
            "Load Electrican": _safe_float(_cell(df, r, col_elec)) if col_elec is not None else math.nan,
            "Load Welder": _safe_float(_cell(df, r, col_weld)) if col_weld is not None else math.nan,
        }
        lf_records.append(rec)

    if not lf_records:
        raise BackendDataError("Tabel 'Load Factor' ditemukan tapi tidak ada baris data.")

    lf_df = pd.DataFrame(lf_records)
    # Buang duplikat Sub Category (pertahankan kemunculan pertama) lalu jadikan index.
    dupes = lf_df["Sub Category"][lf_df["Sub Category"].duplicated()].unique().tolist()
    if dupes:
        logger.warning("Sub Category duplikat di Load Factor (dipertahankan yang pertama): %s", dupes)
    lf_df = lf_df.drop_duplicates(subset="Sub Category", keep="first").set_index("Sub Category")

    sub_categories = lf_df.index.tolist()

    # units_map / norm map (dipakai app.py untuk dropdown & original_sub_name)
    units_map: Dict[str, List[str]] = {}
    norm_to_orig: Dict[str, str] = {}
    for sub, row in lf_df.iterrows():
        nk = BackendData._normalize(sub)
        norm_to_orig.setdefault(nk, sub)
        attr_val = str(row.get("Attribute", "")).strip()
        if attr_val and attr_val.lower() not in ("nan", "-", ""):
            units_map.setdefault(nk, [])
            if attr_val not in units_map[nk]:
                units_map[nk].append(attr_val)

    # =========================================================
    # Blok 2: Ratio Shift
    # =========================================================
    ratio_shift: Dict[str, float] = {}
    rs_title_idx = _find_title_row(df, "ratio shift", start=data_end)
    if rs_title_idx is not None:
        j = rs_title_idx + 1
        while j < len(df) and _is_blank_row(df, j):
            j += 1
        # skip baris header "Site | Ratio" jika ada
        if j < len(df) and "site" in _col_text(df, j, 0).lower():
            j += 1
        pairs, rs_end = _read_vertical_pairs_safe(df, j)
        for site, val in pairs:
            ratio_shift[site] = _safe_float(val)
    else:
        logger.warning("Seksi 'Ratio Shift' tidak ditemukan.")
        rs_end = data_end

    # =========================================================
    # Blok 3: Proporsi RACI (vertikal: label di Kol A, nilai di Kol B)
    # =========================================================
    raci: Dict[str, float] = {}
    raci_title_idx = _find_title_row(df, "proporsi raci", start=rs_end)
    if raci_title_idx is None:
        raci_title_idx = _find_title_row(df, "raci", start=rs_end)

    raci_end_idx = rs_end
    if raci_title_idx is not None:
        pairs, raci_end_idx = _read_vertical_pairs_safe(df, raci_title_idx + 1)
        for label, val in pairs:
            ll = label.strip().lower()
            if ll.startswith("mechanic"):
                key = "Mechanic"
            elif ll.startswith("electr"):
                key = "Electric"
            elif ll.startswith("welder"):
                key = "Welder"
            else:
                continue
            raci[key] = _parse_fraction_cell(val)
    else:
        logger.warning("Seksi 'Proporsi RACI' tidak ditemukan.")

    for key in ("Mechanic", "Electric", "Welder"):
        raci.setdefault(key, math.nan)

    # =========================================================
    # Blok 4-6: Split Ratio Mechanic / Welder / Electrician
    # Pencarian dimulai SETELAH blok RACI selesai (raci_end_idx), sesuai urutan sheet.
    # =========================================================
    def _extract_split(section_title: str, start: int) -> List[float]:
        title_idx = _find_title_row(df, section_title, start=start)
        if title_idx is None:
            logger.warning("Seksi '%s' tidak ditemukan.", section_title)
            return []
        pairs, _end = _read_vertical_pairs_safe(df, title_idx + 1)
        values = []
        for _label, val in pairs:
            v = _parse_fraction_cell(val)
            if not math.isnan(v):
                values.append(v)
        return values

    split_mechanic = _extract_split("split ratio mechanic", raci_end_idx)
    split_welder = _extract_split("split ratio welder", raci_end_idx)
    split_electrician = _extract_split("split ratio electrician", raci_end_idx)

    # =========================================================
    # Blok 7: Lost Time
    # =========================================================
    lost_time: Dict[str, float] = {}
    lt_title_idx = _find_title_row(df, "lost time", start=raci_end_idx)
    lt_end = raci_end_idx
    if lt_title_idx is not None:
        j = lt_title_idx + 1
        while j < len(df) and _is_blank_row(df, j):
            j += 1
        if j < len(df) and "site" in _col_text(df, j, 0).lower():
            j += 1
        pairs, lt_end = _read_vertical_pairs_safe(df, j)
        for site, val in pairs:
            lost_time[site] = _safe_float(val)
    else:
        logger.warning("Seksi 'Lost Time' tidak ditemukan.")

    # =========================================================
    # Blok 8 (v2): Jarak (Site -> KM) — menggantikan input manual Jarak di v1
    # =========================================================
    jarak: Dict[str, float] = {}
    jarak_title_idx = _find_title_row(df, "jarak", start=lt_end)
    jarak_end = lt_end
    if jarak_title_idx is not None:
        j = jarak_title_idx + 1
        while j < len(df) and _is_blank_row(df, j):
            j += 1
        if j < len(df) and "site" in _col_text(df, j, 0).lower():
            j += 1
        pairs, jarak_end = _read_vertical_pairs_safe(df, j)
        for site, val in pairs:
            jarak[site] = _safe_float(val)
    else:
        logger.warning("Seksi 'Jarak' tidak ditemukan (v2) — jarak per site tidak akan ter-lookup otomatis.")

    # =========================================================
    # Blok 9 (v2): Clasification (Category -> Sub Category1) — untuk mengelompokkan
    # hasil Mechanic pada Summary v2 (mis. Digger, Hauler, Auxilary Track, dst.)
    # =========================================================
    classification: Dict[str, str] = {}
    classification_order: List[str] = []
    cls_title_idx = _find_title_row(df, "clasification", start=jarak_end)
    if cls_title_idx is None:
        cls_title_idx = _find_title_row(df, "classification", start=jarak_end)
    if cls_title_idx is not None:
        j = cls_title_idx + 1
        while j < len(df) and _is_blank_row(df, j):
            j += 1
        # lewati baris header "Category | Sub Category1" jika ada
        if j < len(df) and "category" in _col_text(df, j, 0).lower():
            j += 1
        while j < len(df) and not _is_blank_row(df, j):
            cat = _col_text(df, j, 0)
            sub = _col_text(df, j, 1)
            if cat and sub:
                classification[BackendData._normalize(sub)] = cat
                if cat not in classification_order:
                    classification_order.append(cat)
            j += 1
    else:
        logger.warning("Seksi 'Clasification' tidak ditemukan (v2) — summary tidak akan dikelompokkan per kategori.")

    # =========================================================
    # Blok 10 (v7): Technical Competency Factor (Site -> faktor)
    # Diisi lewat form Plant & Maintenance (Apps Script). Dicari dari awal
    # sheet, bukan dari `jarak_end`, karena seksi ini ditulis di bawah blok
    # Clasification yang panjangnya berubah-ubah.
    # =========================================================
    competency_factor: Dict[str, float] = {}
    # Dicoba beberapa variasi judul karena form Plant & Maintenance menulis
    # "Technical Competency Factor (Mechanic)" (lihat HOW_TO_USE di app.py),
    # bukan "Technical Competency Factor" polos. `_find_title_row` mencocokkan
    # PERSIS (exact match), jadi kalau hanya dicoba judul polos, baris ini
    # tidak pernah ketemu di sheet asli -> competency_factor selalu kosong ->
    # tcf_for() diam-diam jatuh ke DEFAULT_COMPETENCY_FACTOR (0.8) untuk semua
    # site, walau datanya sudah ada di BACKEND. Ini yang bikin hasil kalkulasi
    # beda jauh dari sebelumnya (slider manual 0.6 vs default diam-diam 0.8).
    tcf_title_idx = None
    for candidate in (
        "technical competency factor (mechanic)",
        "technical competency factor",
        "competency factor (mechanic)",
        "competency factor",
    ):
        tcf_title_idx = _find_title_row(df, candidate)
        if tcf_title_idx is not None:
            break
    if tcf_title_idx is None:
        # Fallback terakhir: cari baris yang Kolom A-nya MENGANDUNG frasa
        # "competency factor" di mana saja (bukan exact match), untuk
        # menangani variasi judul lain (spasi ganda, awalan nomor blok, dst.)
        # yang belum terdaftar di daftar kandidat di atas.
        for i in range(len(df)):
            if "competency factor" in _col_text(df, i, 0).strip().lower():
                tcf_title_idx = i
                break
    if tcf_title_idx is not None:
        j = tcf_title_idx + 1
        while j < len(df) and _is_blank_row(df, j):
            j += 1
        if j < len(df) and "site" in _col_text(df, j, 0).lower():
            j += 1
        pairs, _tcf_end = _read_vertical_pairs_safe(df, j)
        for site, val in pairs:
            v = _safe_float(val)
            if v == v and v > 0:          # buang NaN / nol
                competency_factor[site] = v
    else:
        logger.warning(
            "Seksi 'Technical Competency Factor' tidak ditemukan — dipakai nilai default %.2f.",
            BackendData.DEFAULT_COMPETENCY_FACTOR,
        )

    sites = list(ratio_shift.keys()) or list(lost_time.keys())

    bd = BackendData(
        load_factor=lf_df,
        ratio_shift=ratio_shift,
        raci=raci,
        split_mechanic=split_mechanic,
        split_welder=split_welder,
        split_electrician=split_electrician,
        lost_time=lost_time,
        sites=sites,
        sub_categories=sub_categories,
        units_map=units_map,
        _norm_to_orig=norm_to_orig,
        jarak=jarak,
        classification=classification,
        classification_order=classification_order,
        competency_factor=competency_factor,
    )
    return bd


# =========================================================
# (v2) Sheet9 — Data Input Unit per Site
#
# Struktur: baris 1 = judul Site per blok kolom (mis. "KCP" di kolom A, "ACP" di
# kolom F, "BCP" di kolom K, dst — TIDAK dihardcode jumlah/posisi bloknya, dicari
# dengan scan baris 1 & 2), baris 2 = header "Category | Jenis Unit | Jumlah Unit
# | PA" untuk tiap blok, baris 3+ = data sampai baris kosong pada blok tsb.
# =========================================================
@dataclass
class UnitRow:
    category: str
    jenis_unit: str
    jumlah_unit: float
    pa: float


def _find_site_blocks(df: pd.DataFrame) -> "tuple[List[tuple], int]":
    """Cari baris header (baris yang punya sel 'Category') di mana pun posisinya
    (tidak diasumsikan selalu baris index 1), lalu untuk tiap kolom 'Category'
    cari judul Site di baris tepat di atasnya — mundur ke kiri jika sel judul
    kosong akibat merge cell di Google Sheets, dibatasi sampai kolom blok
    sebelumnya supaya tidak "mencuri" judul blok lain.

    Return: (blocks, header_row) dengan blocks = [(site_title, category_col), ...].
    """
    header_row = None
    cat_cols: List[int] = []
    max_scan = min(len(df), 20)
    for r in range(max_scan):
        cols = [c for c in range(df.shape[1]) if _col_text(df, r, c).strip().lower() == "category"]
        if cols:
            header_row = r
            cat_cols = cols
            break
    if header_row is None or header_row == 0:
        return [], -1

    title_row = header_row - 1
    blocks = []
    for i, col in enumerate(cat_cols):
        left_bound = cat_cols[i - 1] + 1 if i > 0 else 0
        title = ""
        for back in range(col, left_bound - 1, -1):
            t = _col_text(df, title_row, back)
            if t:
                title = t
                break
        if not title:
            title = f"Site{i + 1}"
        blocks.append((title, col))
    return blocks, header_row


def parse_unit_sheet(raw: pd.DataFrame) -> Dict[str, List[UnitRow]]:
    """Parse sheet 'Sheet9' (data input Unit per Site, v2).
    Mengembalikan dict: Site -> list of UnitRow (Category, Jenis Unit, Jumlah Unit, PA)."""
    df = raw.copy().reset_index(drop=True)
    blocks, header_row = _find_site_blocks(df)
    if not blocks:
        html_issue = _looks_like_html_or_error(df)
        detail = f"\n\nKemungkinan penyebab: {html_issue}" if html_issue else ""
        raise BackendDataError(
            "Tidak ditemukan blok Site (baris berisi header 'Category') pada sheet Unit."
            f"{detail}"
        )

    result: Dict[str, List[UnitRow]] = {}
    for site, col in blocks:
        rows: List[UnitRow] = []
        r = header_row + 1
        while r < len(df):
            cat = _col_text(df, r, col)
            if cat == "" or cat.lower() == "nan":
                break
            jenis = _col_text(df, r, col + 1)
            jumlah = _safe_float(_cell(df, r, col + 2))
            pa = _safe_float(_cell(df, r, col + 3))
            rows.append(UnitRow(
                category=cat,
                jenis_unit=jenis,
                jumlah_unit=jumlah if not math.isnan(jumlah) else 0.0,
                pa=pa if not math.isnan(pa) else 85.0,
            ))
            r += 1
        result[site] = rows
    return result


# =========================================================
# (v2) Hasil Staff — Data FTE Staff (Foreman/SPV/Planner) per Site
#
# Kolom: Posisi | Category posisi | Site | Rasio Roster | Area Kerja |
#        Beban Kerja Administratif | Jam kerja Efektif Staff | Jam Supervisi |
#        EWDY | k | k spv | Contoh FTE | Rumus FTE | Contoh FTE SPV
#
# k dan k spv adalah eksponen pada rumus Foreman/Supervisor (v3). Kolom
# "Contoh FTE SPV" (indeks 13) dibaca sebagai fte_spv_lookup: baris Planner
# TIDAK menghitung supervisornya sendiri, melainkan melookup nilai di kolom
# itu, supaya perubahan di spreadsheet langsung terbawa.
# Dicari via header row (Kolom A == "Posisi"), bukan nomor baris hardcoded.
# =========================================================
@dataclass
class StaffRow:
    posisi: str
    category_posisi: str
    site: str
    rasio_roster: float
    area_kerja: float
    beban_admin: float
    jam_efektif: float
    jam_supervisi: float
    ewdy: float
    k: float = float("nan")
    k_spv: float = float("nan")
    fte_spv_lookup: float = float("nan")


def parse_staff_sheet(raw: pd.DataFrame) -> List[StaffRow]:
    """Parse sheet 'Hasil Staff' (v2). Baris dengan data tidak lengkap (mis. Area
    Kerja kosong) tetap disertakan di sini apa adanya — penyaringan/skip dilakukan
    di layer kalkulasi (calculator.py), bukan di parser."""
    df = raw.copy().reset_index(drop=True)
    header_idx = _find_title_row(df, "posisi")
    if header_idx is None:
        html_issue = _looks_like_html_or_error(df)
        detail = f"\n\nKemungkinan penyebab: {html_issue}" if html_issue else ""
        raise BackendDataError(f"Header 'Posisi' tidak ditemukan pada sheet Hasil Staff.{detail}")

    records: List[StaffRow] = []
    r = header_idx + 1
    while r < len(df):
        if _is_blank_row(df, r):
            r += 1
            continue
        posisi = _col_text(df, r, 0)
        if not posisi:
            r += 1
            continue
        records.append(StaffRow(
            posisi=posisi,
            category_posisi=_col_text(df, r, 1),
            site=_col_text(df, r, 2),
            # Indeks kolom mengikuti layout sheet 'Hasil Staff':
            #   0 Posisi | 1 Category posisi | 2 Site | 3 Jumlah Mechanic
            #   4 Rasio Roster | 5 Area Kerja | 6 Beban Kerja Administratif
            #   7 Jam kerja Efektif Staff | 8 Jam Supervisi | 9 EWDY
            #   10 k | 11 k spv | 12 Contoh FTE | 13 Rumus FTE | 14 FTE SPV
            # Sebelumnya semua indeks dari Rasio Roster ke kanan bergeser satu
            # ke kiri, sehingga `k` justru membaca kolom EWDY (nilainya 253).
            # Karena k dipakai sebagai eksponen (base ** k), itu membuat hasil
            # meledak jadi angka astronomis atau OverflowError.
            rasio_roster=_safe_float(_cell(df, r, 4)),
            area_kerja=_safe_float(_cell(df, r, 5)),
            beban_admin=_safe_float(_cell(df, r, 6)),
            jam_efektif=_safe_float(_cell(df, r, 7)),
            jam_supervisi=_safe_float(_cell(df, r, 8)),
            ewdy=_safe_float(_cell(df, r, 9)),
            k=_safe_float(_cell(df, r, 10)),
            k_spv=_safe_float(_cell(df, r, 11)),
            fte_spv_lookup=_safe_float(_cell(df, r, 14)),
        ))
        r += 1
    return records


# -----------------------
# Loader dengan fallback sumber
# -----------------------
def load_backend_data(source: Optional[Union[str, pd.DataFrame]] = None) -> BackendData:
    """
    Urutan sumber data:
     1. DataFrame langsung (jika diberikan)
     2. str path/URL (jika diberikan)
     3. env var BACKEND_CSV_PATH
     4. env var BACKEND_CSV_URL
     5. file lokal default "FTE - BACKEND (2).csv"
     6. Google Sheets export URL, dibangun dari config.py (SPREADSHEET_ID + BACKEND_SHEET_NAME)
        via gsheet_csv_url() — supaya SATU sumber kebenaran, tidak ada URL/gid duplikat/hardcoded
        yang bisa berbeda dari config.py.
    """
    try:
        if isinstance(source, pd.DataFrame):
            return parse_backend(source)

        if isinstance(source, str):
            try:
                raw = pd.read_csv(source.strip(), header=None, dtype=str)
            except Exception as e:
                raise BackendDataError(f"Gagal membaca CSV dari {source}") from e
            return parse_backend(raw)

        env_path = os.getenv("BACKEND_CSV_PATH")
        if env_path and os.path.exists(env_path):
            raw = pd.read_csv(env_path, header=None, dtype=str)
            return parse_backend(raw)

        env_url = os.getenv("BACKEND_CSV_URL")
        if env_url:
            raw = pd.read_csv(env_url, header=None, dtype=str)
            return parse_backend(raw)

        default_fname = "FTE - BACKEND (2).csv"
        if os.path.exists(default_fname):
            raw = pd.read_csv(default_fname, header=None, dtype=str)
            return parse_backend(raw)

        # Sumber Google Sheets — dibangun dari config.py, bukan hardcoded di sini.
        try:
            from config import SPREADSHEET_ID, BACKEND_SHEET_NAME, gsheet_csv_url
            primary_url = gsheet_csv_url(BACKEND_SHEET_NAME, SPREADSHEET_ID)
            spreadsheet_id = SPREADSHEET_ID
        except ImportError:
            spreadsheet_id = "1YRvXt0AE-dVBVwRvLtsb57Qz8DYd9YbVQlVbRD31C7I"
            primary_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet=BACKEND"

        errors = []
        try:
            raw = pd.read_csv(primary_url, header=None, dtype=str)
            return parse_backend(raw)
        except BackendDataError as e:
            errors.append(f"[gviz sheet-name URL] {e}")
        except Exception as e:
            errors.append(f"[gviz sheet-name URL] gagal fetch: {e}")

        # Fallback: coba URL export berbasis gid spesifik tab BACKEND (kadang gviz
        # butuh setting sharing yang berbeda dari endpoint export biasa).
        known_backend_gid = "1437049322"
        fallback_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={known_backend_gid}"
        try:
            raw = pd.read_csv(fallback_url, header=None, dtype=str)
            return parse_backend(raw)
        except BackendDataError as e:
            errors.append(f"[export gid=0 fallback] {e}")
        except Exception as e:
            errors.append(f"[export gid=0 fallback] gagal fetch: {e}")

        raise BackendDataError(
            "Gagal memuat BACKEND dari kedua metode URL Google Sheets.\n\n"
            + "\n\n".join(errors)
            + "\n\nCek: (1) sheet sudah di-share 'Anyone with the link - Viewer', "
              "(2) nama tab persis 'BACKEND' (config.BACKEND_SHEET_NAME), "
              "(3) SPREADSHEET_ID di config.py masih benar."
        )

    except BackendDataError:
        raise
    except Exception as e:
        logger.exception("Gagal memuat backend data")
        raise BackendDataError("Gagal memuat backend data") from e


# =========================================================
# (v2) Loader Sheet9 (Unit) & Hasil Staff (Staff) — mengikuti pola fallback yang
# sama seperti load_backend_data(): coba fetch via nama tab dulu, kalau gagal
# atau kontennya tidak sesuai (mis. ke-fetch tab yang salah), coba lagi via gid
# spesifik tab tsb (config.UNIT_SHEET_GID / config.STAFF_SHEET_GID).
# =========================================================


def load_unit_data(source: Optional[Union[str, pd.DataFrame]] = None) -> Dict[str, List[UnitRow]]:
    """Muat data Unit per Site (Sheet9, v2).

    Urutan sumber: DataFrame/str langsung -> env UNIT_CSV_PATH -> env UNIT_CSV_URL ->
    file lokal default -> Google Sheets (nama tab) -> Google Sheets (gid fallback,
    config.UNIT_SHEET_GID) — pola sama seperti load_backend_data(), supaya kalau
    fetch berbasis nama tab gagal atau (tanpa error) mengembalikan tab yang salah,
    tetap ada percobaan kedua yang tidak bergantung nama tab.
    """
    try:
        if isinstance(source, pd.DataFrame):
            return parse_unit_sheet(source)
        if isinstance(source, str):
            raw = pd.read_csv(source.strip(), header=None, dtype=str)
            return parse_unit_sheet(raw)

        env_path = os.getenv("UNIT_CSV_PATH")
        if env_path and os.path.exists(env_path):
            return parse_unit_sheet(pd.read_csv(env_path, header=None, dtype=str))

        env_url = os.getenv("UNIT_CSV_URL")
        if env_url:
            return parse_unit_sheet(pd.read_csv(env_url, header=None, dtype=str))

        default_fname = "FTE - Sheet9.csv"
        if os.path.exists(default_fname):
            return parse_unit_sheet(pd.read_csv(default_fname, header=None, dtype=str))

        from config import SPREADSHEET_ID, UNIT_SHEET_NAME, gsheet_csv_url
        try:
            from config import UNIT_SHEET_GID
        except ImportError:
            UNIT_SHEET_GID = None

        errors = []
        try:
            raw = pd.read_csv(gsheet_csv_url(UNIT_SHEET_NAME, SPREADSHEET_ID), header=None, dtype=str)
            return parse_unit_sheet(raw)
        except BackendDataError as e:
            errors.append(f"[gviz sheet-name url, sheet='{UNIT_SHEET_NAME}'] {e}")
        except Exception as e:
            errors.append(f"[gviz sheet-name url, sheet='{UNIT_SHEET_NAME}'] gagal fetch: {e}")

        if UNIT_SHEET_GID:
            fallback_url = (
                f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
                f"/export?format=csv&gid={UNIT_SHEET_GID}"
            )
            try:
                raw = pd.read_csv(fallback_url, header=None, dtype=str)
                return parse_unit_sheet(raw)
            except BackendDataError as e:
                errors.append(f"[export gid={UNIT_SHEET_GID} fallback] {e}")
            except Exception as e:
                errors.append(f"[export gid={UNIT_SHEET_GID} fallback] gagal fetch: {e}")

        raise BackendDataError(
            "Gagal memuat sheet Unit (Sheet9) dari Google Sheets.\n\n"
            + "\n\n".join(errors)
            + "\n\nCek: (1) sheet sudah di-share 'Anyone with the link - Viewer', "
              f"(2) nama tab persis '{UNIT_SHEET_NAME}' (config.UNIT_SHEET_NAME), "
              "(3) gid fallback di config.UNIT_SHEET_GID masih sesuai tab Sheet9 yang sebenarnya."
        )
    except BackendDataError:
        raise
    except Exception as e:
        logger.exception("Gagal memuat data Unit (Sheet9)")
        raise BackendDataError("Gagal memuat data Unit (Sheet9)") from e


def load_staff_data(source: Optional[Union[str, pd.DataFrame]] = None) -> List[StaffRow]:
    """Muat data FTE Staff (Hasil Staff, v2).

    Urutan sumber sama seperti load_unit_data(): DataFrame/str langsung -> env
    STAFF_CSV_PATH -> env STAFF_CSV_URL -> file lokal default -> Google Sheets
    (nama tab) -> Google Sheets (gid fallback, config.STAFF_SHEET_GID).
    """
    try:
        if isinstance(source, pd.DataFrame):
            return parse_staff_sheet(source)
        if isinstance(source, str):
            raw = pd.read_csv(source.strip(), header=None, dtype=str)
            return parse_staff_sheet(raw)

        env_path = os.getenv("STAFF_CSV_PATH")
        if env_path and os.path.exists(env_path):
            return parse_staff_sheet(pd.read_csv(env_path, header=None, dtype=str))

        env_url = os.getenv("STAFF_CSV_URL")
        if env_url:
            return parse_staff_sheet(pd.read_csv(env_url, header=None, dtype=str))

        default_fname = "FTE - Hasil Staff.csv"
        if os.path.exists(default_fname):
            return parse_staff_sheet(pd.read_csv(default_fname, header=None, dtype=str))

        from config import SPREADSHEET_ID, STAFF_SHEET_NAME, gsheet_csv_url
        try:
            from config import STAFF_SHEET_GID
        except ImportError:
            STAFF_SHEET_GID = None

        errors = []
        try:
            raw = pd.read_csv(gsheet_csv_url(STAFF_SHEET_NAME, SPREADSHEET_ID), header=None, dtype=str)
            return parse_staff_sheet(raw)
        except BackendDataError as e:
            errors.append(f"[gviz sheet-name url, sheet='{STAFF_SHEET_NAME}'] {e}")
        except Exception as e:
            errors.append(f"[gviz sheet-name url, sheet='{STAFF_SHEET_NAME}'] gagal fetch: {e}")

        if STAFF_SHEET_GID:
            fallback_url = (
                f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
                f"/export?format=csv&gid={STAFF_SHEET_GID}"
            )
            try:
                raw = pd.read_csv(fallback_url, header=None, dtype=str)
                return parse_staff_sheet(raw)
            except BackendDataError as e:
                errors.append(f"[export gid={STAFF_SHEET_GID} fallback] {e}")
            except Exception as e:
                errors.append(f"[export gid={STAFF_SHEET_GID} fallback] gagal fetch: {e}")

        raise BackendDataError(
            "Gagal memuat sheet Hasil Staff dari Google Sheets.\n\n"
            + "\n\n".join(errors)
            + "\n\nCek: (1) sheet sudah di-share 'Anyone with the link - Viewer', "
              f"(2) nama tab persis '{STAFF_SHEET_NAME}' (config.STAFF_SHEET_NAME), "
              "(3) isi config.STAFF_SHEET_GID dengan gid tab 'Hasil Staff' "
              "(buka tab-nya, lihat angka setelah 'gid=' di URL) untuk fallback otomatis."
        )
    except BackendDataError:
        raise
    except Exception as e:
        logger.exception("Gagal memuat data Hasil Staff")
        raise BackendDataError("Gagal memuat data Hasil Staff") from e
