"""
Script untuk seed database dari file Excel hasil analisis RFM + K-Means.
Otomatis dipanggil dari app.py saat pertama kali startup.
"""

import os
from datetime import date
import pandas as pd
from database import db, Customer, RFMResult, CustomerSegment

DATA_DIR       = os.path.join(os.path.dirname(__file__), 'customer-intelligence', 'data')
SEGMENT_FILE   = os.path.join(DATA_DIR, 'customer_segment.xlsx')
RFM_FILE       = os.path.join(DATA_DIR, 'rfm.xlsx')
TRANSAKSI_FILE = os.path.join(DATA_DIR, 'transaksi_bersih.xlsx')


def _detect_col(df, *candidates):
    """Kembalikan nama kolom pertama yang ditemukan di df."""
    for col in candidates:
        if col in df.columns:
            return col
    raise KeyError(f"Tidak ada kolom yang cocok dari: {candidates}")


def _calculate_recency(last_transaction_date, fallback):
    if pd.notna(last_transaction_date):
        return max((date.today() - pd.to_datetime(last_transaction_date).date()).days, 0)
    return int(fallback)


def load_data():
    """
    Gabungkan:
    - customer_segment.xlsx  → RFM + Cluster + Segment
    - rfm.xlsx               → Last_Transaction_Date (jika ada)
    - transaksi_bersih.xlsx  → No HP, Lokasi, Kategori
    """
    seg_df = pd.read_excel(SEGMENT_FILE)
    nama_col_seg = _detect_col(seg_df, 'Nama Customer', 'Nama Pelanggan')
    seg_df = seg_df.rename(columns={nama_col_seg: 'nama_key'})

    # Ambil Last_Transaction_Date dari rfm.xlsx jika kolom tersedia
    last_date_map = {}
    if os.path.exists(RFM_FILE):
        rfm_df = pd.read_excel(RFM_FILE)
        nama_col_rfm = _detect_col(rfm_df, 'Nama Customer', 'Nama Pelanggan')
        if 'Last_Transaction_Date' in rfm_df.columns:
            rfm_df['Last_Transaction_Date'] = pd.to_datetime(
                rfm_df['Last_Transaction_Date'], errors='coerce'
            )
            last_date_map = dict(zip(
                rfm_df[nama_col_rfm].str.strip(),
                rfm_df['Last_Transaction_Date']
            ))

    # Agregasi info dari transaksi_bersih
    trx_df = pd.read_excel(TRANSAKSI_FILE)
    nama_col_trx = _detect_col(trx_df, 'Nama Customer', 'Nama Pelanggan')
    hp_col       = _detect_col(trx_df, 'No HP', 'Nomor HP / WhatsApp')

    trx_count = (
        trx_df.groupby(nama_col_trx).size()
        .reset_index(name='total_trx')
        .rename(columns={nama_col_trx: 'nama_key'})
    )
    trx_agg = (
        trx_df.groupby(nama_col_trx)
        .agg(
            nomor_hp=(hp_col, 'last'),
            lokasi=('Lokasi', 'last'),
            kategori=('Kategori', lambda x: x.mode()[0] if len(x) > 0 else '-'),
        )
        .reset_index()
        .rename(columns={nama_col_trx: 'nama_key'})
    )

    merged = seg_df.merge(trx_agg,  on='nama_key', how='left')
    merged = merged.merge(trx_count, on='nama_key', how='left')

    # Tambahkan kolom Last_Transaction_Date dari map
    merged['Last_Transaction_Date'] = merged['nama_key'].map(last_date_map)

    return merged


def seed_database():
    """Seed database dari Excel. Skip jika sudah ada data."""
    if Customer.query.count() > 0:
        return

    if not os.path.exists(SEGMENT_FILE):
        print(f"[SEED] File tidak ditemukan: {SEGMENT_FILE}")
        return

    print("[SEED] Memulai seed database dari Excel...")
    df = load_data()

    for _, row in df.iterrows():
        nama = str(row['nama_key']).strip()

        nomor_hp = str(row.get('nomor_hp', '')).strip() if pd.notna(row.get('nomor_hp')) else None
        if nomor_hp in ('Tidak Diketahui', 'nan', ''):
            nomor_hp = None

        # Ambil last_transaction_date
        ltd = row.get('Last_Transaction_Date')
        last_date = pd.to_datetime(ltd).date() if pd.notna(ltd) else None

        customer = Customer(
            nama_pelanggan=nama,
            nomor_hp=nomor_hp,
            lokasi=str(row.get('lokasi', '')).strip() if pd.notna(row.get('lokasi')) else None,
            kategori=str(row.get('kategori', '')).strip() if pd.notna(row.get('kategori')) else None,
            total_transaksi=int(row.get('total_trx', row.get('Frequency', 0))),
        )
        db.session.add(customer)
        db.session.flush()

        db.session.add(RFMResult(
            customer_id=customer.id,
            recency=_calculate_recency(last_date, row['Recency']),
            frequency=int(row['Frequency']),
            monetary=float(row['Monetary']),
            last_transaction_date=last_date,
        ))
        db.session.add(CustomerSegment(
            customer_id=customer.id,
            cluster=int(row['Cluster']),
            segment_label=str(row['Segment']).strip(),
        ))

    db.session.commit()
    print(f"[SEED] Selesai. {Customer.query.count()} customer berhasil di-seed.")


def reseed_database():
    """Hapus semua data lama dan seed ulang dari Excel."""
    print("[RESEED] Menghapus data lama...")
    CustomerSegment.query.delete()
    RFMResult.query.delete()
    Customer.query.delete()
    db.session.commit()
    seed_database()
