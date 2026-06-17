from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, send_file)
from database import db, Customer, RFMResult, CustomerSegment, User
from sqlalchemy import func, text
from dotenv import load_dotenv
from datetime import date
from functools import wraps
import pandas as pd
import io
import json
import math
import os
import re
from urllib.parse import quote

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-ganti-di-production')

# ── Database: otomatis pilih SQLite (lokal) atau PostgreSQL (hosting) ─────────
database_url = os.environ.get('DATABASE_URL', 'sqlite:///nuansa_wangi.db')
# Railway/Heroku kadang kirim "postgres://" — Flask-SQLAlchemy butuh "postgresql://"
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Kredensial admin dari .env
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'nuansa123')

db.init_app(app)

ROLES = {
    'super_admin': {
        'label': 'Super Admin',
        'description': 'Akses penuh, termasuk kelola user dan pengaturan sistem.',
    },
    'customer_service': {
        'label': 'Customer Service',
        'description': 'Kelola data customer, upload data, export, dan hubungi customer.',
    },
    'staff': {
        'label': 'Staff',
        'description': 'Akses baca dashboard, data customer, RFM, segmentasi, insight, dan laporan.',
    },
}

PERMISSIONS = {
    'manage_users': {'super_admin'},
    'manage_settings': {'super_admin'},
    'upload_data': {'super_admin', 'customer_service'},
    'create_customer': {'super_admin', 'customer_service'},
    'edit_customer': {'super_admin', 'customer_service'},
    'delete_customer': {'super_admin'},
    'export_report': {'super_admin', 'customer_service', 'staff'},
    'contact_customer': {'super_admin', 'customer_service'},
}

# Warna & urutan per segment
SEGMENT_CONFIG = {
    'Champion Customer':  {'color': '#f59e0b', 'bg': 'bg-warning',  'badge': 'warning',  'icon': 'fa-crown',       'order': 2},
    'Loyal Customer':     {'color': '#10b981', 'bg': 'bg-success',  'badge': 'success',  'icon': 'fa-heart',       'order': 3},
    'Potential Customer': {'color': '#3b82f6', 'bg': 'bg-primary',  'badge': 'primary',  'icon': 'fa-seedling',    'order': 0},
    'At Risk Customer':   {'color': '#ef4444', 'bg': 'bg-danger',   'badge': 'danger',   'icon': 'fa-exclamation', 'order': 1},
}

FINAL_UPLOAD_REQUIRED = {'Recency', 'Frequency', 'Monetary', 'Segment'}

RAW_COLUMN_CANDIDATES = {
    'name': ['Nama Pelanggan', 'Nama Customer', 'Customer', 'Nama'],
    'date': ['Tanggal', 'Tanggal Pesanan', 'Tanggal Transaksi', 'Tanggal Order', 'Date'],
    'amount': ['Total Transaksi', 'Total', 'Total Pembayaran', 'Nominal', 'Jumlah', 'Monetary'],
    'phone': ['Nomor HP', 'No HP', 'Nomor HP / WhatsApp', 'No WhatsApp', 'WhatsApp'],
    'location': ['Lokasi', 'Alamat', 'Kota', 'Area'],
    'category': ['Kategori', 'Kategori Produk', 'Produk', 'Jenis Produk'],
}

SEGMENT_MESSAGES = {
    'Champion Customer': (
        'Halo {name}, terima kasih sudah menjadi pelanggan terbaik Nuansa Wangi. '
        'Kami punya rekomendasi dan penawaran khusus untuk kamu. Mau kami bantu pilihkan?'
    ),
    'Loyal Customer': (
        'Halo {name}, terima kasih sudah setia belanja di Nuansa Wangi. '
        'Kami ada pilihan produk yang cocok untuk repeat order kamu. Mau kami kirimkan rekomendasinya?'
    ),
    'Potential Customer': (
        'Halo {name}, terima kasih sudah pernah belanja di Nuansa Wangi. '
        'Kami ada beberapa produk yang mungkin cocok untuk kebutuhan kamu. Mau kami bantu rekomendasikan?'
    ),
    'At Risk Customer': (
        'Halo {name}, sudah cukup lama belum belanja lagi di Nuansa Wangi. '
        'Kami ingin bantu cek kebutuhan kamu dan ada rekomendasi terbaru yang bisa dipilih.'
    ),
}


def role_label(role):
    return ROLES.get(role, {}).get('label', role or '-')


def has_permission(permission):
    role = session.get('role')
    return role in PERMISSIONS.get(permission, set())


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return User.query.get(user_id)


def whatsapp_number(nomor_hp):
    if not nomor_hp:
        return None

    number = str(nomor_hp).strip()
    if number.lower() in {'nan', 'none', '-', 'tidak diketahui'}:
        return None

    digits = re.sub(r'\D', '', number)
    if not digits:
        return None

    if digits.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0'):
        digits = '62' + digits[1:]
    elif digits.startswith('8'):
        digits = '62' + digits

    return digits if len(digits) >= 8 else None


def whatsapp_message(segment_label, nama_pelanggan):
    template = SEGMENT_MESSAGES.get(
        segment_label,
        'Halo {name}, terima kasih sudah menjadi pelanggan Nuansa Wangi. '
        'Kami ingin follow up kebutuhan kamu.'
    )
    name = (nama_pelanggan or 'Kak').strip() or 'Kak'
    return template.format(name=name)


def whatsapp_url(nomor_hp, segment_label=None, nama_pelanggan=None):
    number = whatsapp_number(nomor_hp)
    if not number:
        return None
    message = whatsapp_message(segment_label, nama_pelanggan)
    return f'https://wa.me/{number}?text={quote(message)}'


def _clean_optional_value(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {'nan', 'none', '-', 'tidak diketahui'}:
        return None
    return text


def _detect_column(df, candidates, required=True):
    normalized = {str(col).strip().lower(): col for col in df.columns}
    for candidate in candidates:
        found = normalized.get(candidate.lower())
        if found is not None:
            return found
    if required:
        raise KeyError(f'Kolom tidak ditemukan. Gunakan salah satu: {", ".join(candidates)}')
    return None


def _to_number_series(series):
    numeric = pd.to_numeric(series, errors='coerce')
    if numeric.notna().all():
        return numeric

    cleaned = (
        series.astype(str)
        .str.replace(r'[^\d,.-]', '', regex=True)
        .str.replace('.', '', regex=False)
        .str.replace(',', '.', regex=False)
    )
    fallback = pd.to_numeric(cleaned, errors='coerce')
    return numeric.fillna(fallback)


def _normalize_raw_transactions(df):
    name_col = _detect_column(df, RAW_COLUMN_CANDIDATES['name'])
    date_col = _detect_column(df, RAW_COLUMN_CANDIDATES['date'])
    amount_col = _detect_column(df, RAW_COLUMN_CANDIDATES['amount'])
    phone_col = _detect_column(df, RAW_COLUMN_CANDIDATES['phone'], required=False)
    location_col = _detect_column(df, RAW_COLUMN_CANDIDATES['location'], required=False)
    category_col = _detect_column(df, RAW_COLUMN_CANDIDATES['category'], required=False)

    normalized = pd.DataFrame({
        'Nama Pelanggan': df[name_col].astype(str).str.strip().str.title(),
        'Tanggal': pd.to_datetime(df[date_col], errors='coerce'),
        'Total Transaksi': _to_number_series(df[amount_col]),
    })
    normalized['Nomor HP'] = df[phone_col].apply(_clean_optional_value) if phone_col else None
    normalized['Lokasi'] = df[location_col].apply(_clean_optional_value) if location_col else None
    normalized['Kategori'] = df[category_col].apply(_clean_optional_value) if category_col else None

    normalized = normalized.dropna(subset=['Nama Pelanggan', 'Tanggal', 'Total Transaksi'])
    normalized = normalized[
        ~normalized['Nama Pelanggan'].isin(['-', '', 'Tidak Diketahui', 'Nan', 'None'])
    ].copy()
    normalized = normalized[normalized['Total Transaksi'] > 0].copy()

    today = pd.Timestamp.today().normalize()
    normalized = normalized[normalized['Tanggal'] <= today].copy()
    if normalized.empty:
        raise ValueError('Tidak ada transaksi valid setelah pembersihan data.')

    return normalized


def _label_clusters(rfm):
    summary = rfm.groupby('Cluster')[['Recency', 'Frequency', 'Monetary']].mean()
    score = (
        summary['Frequency'].rank(method='first')
        + summary['Monetary'].rank(method='first')
        + summary['Recency'].rank(method='first', ascending=False)
    )
    ordered = score.sort_values(ascending=False).index.tolist()
    labels = ['Champion Customer', 'Loyal Customer', 'Potential Customer', 'At Risk Customer']
    cluster_labels = {}
    for idx, cluster in enumerate(ordered):
        cluster_labels[cluster] = labels[min(idx, len(labels) - 1)]
    return cluster_labels


def _rfm_score_segments(rfm):
    scored = rfm.copy()
    scored['R_Score'] = (
        scored['Recency'].rank(method='average', ascending=False, pct=True)
        .mul(4).apply(lambda value: max(1, min(4, math.ceil(value))))
    )
    scored['F_Score'] = (
        scored['Frequency'].rank(method='average', pct=True)
        .mul(4).apply(lambda value: max(1, min(4, math.ceil(value))))
    )
    scored['M_Score'] = (
        scored['Monetary'].rank(method='average', pct=True)
        .mul(4).apply(lambda value: max(1, min(4, math.ceil(value))))
    )
    scored['RFM_Score'] = scored['R_Score'] + scored['F_Score'] + scored['M_Score']

    def label(row):
        if row['F_Score'] >= 4 and row['M_Score'] >= 4:
            return 'Champion Customer'
        if row['RFM_Score'] >= 10 and row['F_Score'] >= 3 and row['M_Score'] >= 3:
            return 'Champion Customer'
        if row['R_Score'] <= 1 and row['RFM_Score'] <= 7:
            return 'At Risk Customer'
        if row['RFM_Score'] >= 8 and (row['F_Score'] >= 3 or row['M_Score'] >= 3):
            return 'Loyal Customer'
        return 'Potential Customer'

    return scored.apply(label, axis=1)


def _build_rfm_from_transactions(df):
    clean_df = _normalize_raw_transactions(df)
    snapshot_date = pd.Timestamp.today().normalize()

    rfm = clean_df.groupby('Nama Pelanggan').agg(
        Last_Transaction_Date=('Tanggal', 'max'),
        Recency=('Tanggal', lambda x: (snapshot_date - x.max()).days),
        Frequency=('Nama Pelanggan', 'count'),
        Monetary=('Total Transaksi', 'sum'),
        Nomor_HP=('Nomor HP', 'last'),
        Lokasi=('Lokasi', 'last'),
        Kategori=('Kategori', lambda x: x.mode().iloc[0] if not x.mode().empty else None),
    ).reset_index()

    cluster_count = min(4, len(rfm))
    if cluster_count > 1:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler

        features = rfm[['Recency', 'Frequency', 'Monetary']]
        scaled = StandardScaler().fit_transform(features)
        rfm['Cluster'] = KMeans(
            n_clusters=cluster_count,
            random_state=42,
            n_init=10
        ).fit_predict(scaled)
    else:
        rfm['Cluster'] = 0

    rfm['Segment'] = _rfm_score_segments(rfm)

    return rfm.rename(columns={'Nomor_HP': 'Nomor HP'})


def _recalculate_all_customer_segments():
    rows = (
        db.session.query(Customer, RFMResult)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .all()
    )
    if not rows:
        return

    rfm = pd.DataFrame([{
        'customer_id': customer.id,
        'Recency': calculate_recency(result.last_transaction_date, result.recency),
        'Frequency': result.frequency,
        'Monetary': result.monetary,
    } for customer, result in rows])

    cluster_count = min(4, len(rfm))
    if cluster_count > 1:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler

        features = rfm[['Recency', 'Frequency', 'Monetary']]
        scaled = StandardScaler().fit_transform(features)
        rfm['Cluster'] = KMeans(
            n_clusters=cluster_count,
            random_state=42,
            n_init=10
        ).fit_predict(scaled)
    else:
        rfm['Cluster'] = 0

    rfm['Segment'] = _rfm_score_segments(rfm)

    for item in rfm.itertuples(index=False):
        segment = CustomerSegment.query.filter_by(customer_id=item.customer_id).first()
        if segment:
            segment.cluster = int(item.Cluster)
            segment.segment_label = item.Segment
        else:
            db.session.add(CustomerSegment(
                customer_id=int(item.customer_id),
                cluster=int(item.Cluster),
                segment_label=item.Segment,
            ))


def _is_final_upload(df):
    name_exists = 'Nama Pelanggan' in df.columns or 'Nama Customer' in df.columns
    return name_exists and FINAL_UPLOAD_REQUIRED.issubset(set(df.columns))


def _latest_date(current_date, new_date):
    if current_date and new_date:
        return max(current_date, new_date)
    return new_date or current_date


def _safe_int(value, default=0):
    if value is None or pd.isna(value):
        return default
    text = str(value).strip()
    if not text or text.lower() in {'nan', 'none', '-'}:
        return default
    return int(float(text))


def _safe_float(value, default=0):
    if value is None or pd.isna(value):
        return default
    text = str(value).strip()
    if not text or text.lower() in {'nan', 'none', '-'}:
        return default
    return float(text)


def _save_customer_upload(df, merge_existing=False):
    nama_col = 'Nama Customer' if 'Nama Customer' in df.columns else 'Nama Pelanggan'
    count = 0

    for _, row in df.iterrows():
        nama = str(row[nama_col]).strip()
        if not nama or nama.lower() in {'nan', 'none', '-', 'tidak diketahui'}:
            continue

        segment_label = str(row['Segment']).strip()
        if segment_label not in SEGMENT_CONFIG:
            raise ValueError(f'Segment tidak valid untuk {nama}: {segment_label}')

        hp_col = 'No HP' if 'No HP' in df.columns else 'Nomor HP'
        nomor_hp = _clean_optional_value(row.get(hp_col))

        try:
            last_date = pd.to_datetime(row.get('Last_Transaction_Date'), errors='coerce')
            last_date = last_date.date() if pd.notna(last_date) else None
        except Exception:
            last_date = None

        recency_value = calculate_recency(last_date, _safe_int(row['Recency']))
        frequency = _safe_int(row['Frequency'])
        monetary = _safe_float(row['Monetary'])
        cluster = _safe_int(row.get('Cluster'), 0)

        existing = Customer.query.filter_by(nama_pelanggan=nama).first()
        if existing:
            existing_frequency = existing.rfm.frequency if existing.rfm else existing.total_transaksi or 0
            existing_monetary = existing.rfm.monetary if existing.rfm else 0
            existing_last_date = existing.rfm.last_transaction_date if existing.rfm else None
            if merge_existing:
                existing_recency = calculate_recency(
                    existing_last_date,
                    existing.rfm.recency if existing.rfm else None
                )
                frequency = existing_frequency + frequency
                monetary = existing_monetary + monetary
                last_date = _latest_date(existing_last_date, last_date)
                recency_value = (
                    calculate_recency(last_date, min(existing_recency, recency_value))
                    if last_date else min(existing_recency, recency_value)
                )

            existing.nomor_hp = nomor_hp or existing.nomor_hp
            existing.lokasi = _clean_optional_value(row.get('Lokasi')) or existing.lokasi
            existing.kategori = _clean_optional_value(row.get('Kategori')) or existing.kategori
            existing.total_transaksi = frequency
            if existing.rfm:
                existing.rfm.recency = recency_value
                existing.rfm.frequency = frequency
                existing.rfm.monetary = monetary
                existing.rfm.last_transaction_date = last_date
            else:
                db.session.add(RFMResult(
                    customer_id=existing.id,
                    recency=recency_value,
                    frequency=frequency,
                    monetary=monetary,
                    last_transaction_date=last_date,
                ))
            if existing.segment and not merge_existing:
                existing.segment.cluster = cluster
                existing.segment.segment_label = segment_label
            elif not existing.segment:
                db.session.add(CustomerSegment(
                    customer_id=existing.id,
                    cluster=cluster,
                    segment_label=segment_label,
                ))
        else:
            customer = Customer(
                nama_pelanggan=nama,
                nomor_hp=nomor_hp,
                lokasi=_clean_optional_value(row.get('Lokasi')),
                kategori=_clean_optional_value(row.get('Kategori')),
                total_transaksi=frequency,
            )
            db.session.add(customer)
            db.session.flush()
            db.session.add(RFMResult(
                customer_id=customer.id,
                recency=recency_value,
                frequency=frequency,
                monetary=monetary,
                last_transaction_date=last_date,
            ))
            db.session.add(CustomerSegment(
                customer_id=customer.id,
                cluster=cluster,
                segment_label=segment_label,
            ))
        count += 1

    if count == 0:
        raise ValueError('Tidak ada data pelanggan valid untuk diproses.')
    return count


@app.context_processor
def inject_auth_context():
    return {
        'roles': ROLES,
        'role_label': role_label,
        'has_permission': has_permission,
        'whatsapp_number': whatsapp_number,
        'whatsapp_url': whatsapp_url,
        'current_role': session.get('role'),
    }


def ensure_default_users():
    if User.query.count() > 0:
        return

    admin = User(
        username=ADMIN_USERNAME,
        name='Super Admin',
        role='super_admin',
        is_active=True,
    )
    admin.set_password(ADMIN_PASSWORD)
    db.session.add(admin)
    db.session.commit()


def ensure_postgres_sequences():
    if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql'):
        return

    for model in (User, Customer, RFMResult, CustomerSegment):
        table_name = model.__tablename__
        pk_name = next(iter(model.__table__.primary_key.columns)).name
        db.session.execute(
            text(
                f"""
                SELECT setval(
                    pg_get_serial_sequence(:table_name, :pk_name),
                    COALESCE((SELECT MAX({pk_name}) FROM {table_name}), 0) + 1,
                    false
                )
                """
            ),
            {'table_name': table_name, 'pk_name': pk_name},
        )
    db.session.commit()


def ensure_postgres_schema_updates():
    if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql'):
        return

    db.session.execute(text(
        "ALTER TABLE customers ALTER COLUMN lokasi TYPE TEXT"
    ))
    db.session.commit()


def calculate_recency(last_transaction_date, fallback=None):
    if last_transaction_date:
        return max((date.today() - last_transaction_date).days, 0)
    return fallback if fallback is not None else 0


def apply_display_recency(rows):
    for _, rfm, _ in rows:
        rfm.display_recency = calculate_recency(
            rfm.last_transaction_date,
            rfm.recency
        )
    return rows


def get_rfm_summary():
    rfms = RFMResult.query.all()
    if not rfms:
        return {
            'avg_r': 0, 'avg_f': 0, 'avg_m': 0,
            'min_r': 0, 'max_r': 0, 'min_m': 0, 'max_m': 0,
            'total_m': 0,
        }

    recencies = [calculate_recency(r.last_transaction_date, r.recency) for r in rfms]
    frequencies = [r.frequency for r in rfms]
    monetary = [r.monetary for r in rfms]

    return {
        'avg_r': sum(recencies) / len(recencies),
        'avg_f': sum(frequencies) / len(frequencies),
        'avg_m': sum(monetary) / len(monetary),
        'min_r': min(recencies),
        'max_r': max(recencies),
        'min_m': min(monetary),
        'max_m': max(monetary),
        'total_m': sum(monetary),
    }


def get_segment_rfm_summary():
    rows = (
        db.session.query(CustomerSegment.segment_label, RFMResult)
        .join(RFMResult, CustomerSegment.customer_id == RFMResult.customer_id)
        .all()
    )
    grouped = {}
    for segment_label, rfm in rows:
        item = grouped.setdefault(segment_label, {'r': [], 'f': [], 'm': []})
        item['r'].append(calculate_recency(rfm.last_transaction_date, rfm.recency))
        item['f'].append(rfm.frequency)
        item['m'].append(rfm.monetary)

    summary = {}
    for label, values in grouped.items():
        summary[label] = {
            'avg_r': sum(values['r']) / len(values['r']),
            'avg_f': sum(values['f']) / len(values['f']),
            'avg_m': sum(values['m']) / len(values['m']),
        }
    return summary


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def permission_required(permission):
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'logged_in' not in session:
                return redirect(url_for('login'))
            if not has_permission(permission):
                return render_template(
                    '403.html',
                    permission=permission,
                    role=role_label(session.get('role')),
                ), 403
            return f(*args, **kwargs)
        return decorated
    return wrapper


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'logged_in' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            session['logged_in'] = True
            session['user_id'] = user.id
            session['username'] = user.username
            session['name'] = user.name
            session['role'] = user.role
            return redirect(url_for('dashboard'))
        error = 'Username atau password salah!'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/users', methods=['GET', 'POST'])
@permission_required('manage_users')
def users():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', '').strip()

        if not username or not name or not password or role not in ROLES:
            error = 'Lengkapi nama, username, password, dan role yang valid.'
        elif User.query.filter_by(username=username).first():
            error = 'Username sudah digunakan.'
        else:
            user = User(username=username, name=name, role=role, is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            return redirect(url_for('users'))

    rows = User.query.order_by(User.created_at.desc()).all()
    return render_template('users.html', users=rows, roles=ROLES, error=error)


@app.route('/users/<int:user_id>/edit', methods=['POST'])
@permission_required('manage_users')
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    username = request.form.get('username', '').strip()
    name = request.form.get('name', '').strip()
    role = request.form.get('role', '').strip()
    password = request.form.get('password', '').strip()

    if username and username != user.username:
        if User.query.filter_by(username=username).first():
            return redirect(url_for('users'))
        user.username = username
    if name:
        user.name = name
    if role in ROLES:
        user.role = role
    user.is_active = request.form.get('is_active') == '1'
    if password:
        user.set_password(password)
    if user.id == session.get('user_id'):
        user.role = 'super_admin'
        user.is_active = True
    db.session.commit()

    if user.id == session.get('user_id'):
        session['username'] = user.username
        session['name'] = user.name
        session['role'] = user.role

    return redirect(url_for('users'))


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@permission_required('manage_users')
def delete_user(user_id):
    if user_id == session.get('user_id'):
        return redirect(url_for('users'))
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('users'))


@app.route('/settings')
@permission_required('manage_settings')
def settings():
    db_type = 'PostgreSQL' if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql') else 'SQLite'
    return render_template('settings.html', db_type=db_type)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    total = Customer.query.count()

    # Total transaksi (sum dari kolom total_transaksi)
    total_transaksi = db.session.query(func.sum(Customer.total_transaksi)).scalar() or 0

    # Total revenue (sum monetary dari RFM)
    total_revenue = db.session.query(func.sum(RFMResult.monetary)).scalar() or 0
    rfm_stats = get_rfm_summary()

    # Hitung jumlah per segmen
    counts = (
        db.session.query(CustomerSegment.segment_label, func.count())
        .group_by(CustomerSegment.segment_label)
        .all()
    )
    seg_counts = {label: cnt for label, cnt in counts}

    champion  = seg_counts.get('Champion Customer', 0)
    loyal     = seg_counts.get('Loyal Customer', 0)
    potential = seg_counts.get('Potential Customer', 0)
    at_risk   = seg_counts.get('At Risk Customer', 0)

    # Data pie chart - diurutkan berdasarkan order dari config
    sorted_segments_pie = sorted(SEGMENT_CONFIG.items(), key=lambda x: x[1]['order'])
    pie_labels = [segment[0] for segment in sorted_segments_pie]
    pie_data   = [seg_counts.get(l, 0) for l in pie_labels]
    pie_colors = [SEGMENT_CONFIG[l]['color'] for l in pie_labels]

    # Top 5 Monetary
    top_monetary = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(RFMResult.monetary.desc())
        .limit(5).all()
    )
    top_frequency = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(RFMResult.frequency.desc())
        .limit(5).all()
    )
    top_recent = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .filter(RFMResult.last_transaction_date.isnot(None))
        .order_by(RFMResult.last_transaction_date.desc())
        .limit(5).all()
    )

    rfm_by_segment = get_segment_rfm_summary()
    sorted_segments_bar = sorted(SEGMENT_CONFIG.items(), key=lambda x: x[1]['order'])
    rfm_labels = [segment[0] for segment in sorted_segments_bar]
    rfm_recency = [round(rfm_by_segment.get(name, {}).get('avg_r', 0), 1)
                   for name, _ in sorted_segments_bar]
    rfm_frequency = [round(rfm_by_segment.get(name, {}).get('avg_f', 0), 1)
                     for name, _ in sorted_segments_bar]
    rfm_monetary = [round(rfm_by_segment.get(name, {}).get('avg_m', 0) / 1000, 1)
                    for name, _ in sorted_segments_bar]

    return render_template('dashboard.html',
                           total=total,
                           total_transaksi=total_transaksi,
                           total_revenue=total_revenue,
                           rfm_stats=rfm_stats,
                           champion=champion,
                           loyal=loyal,
                           potential=potential,
                           at_risk=at_risk,
                           pie_labels=json.dumps(pie_labels),
                           pie_data=json.dumps(pie_data),
                           pie_colors=json.dumps(pie_colors),
                           top_monetary=top_monetary,
                           top_frequency=top_frequency,
                           top_recent=top_recent,
                           rfm_labels=json.dumps(rfm_labels),
                           rfm_recency=json.dumps(rfm_recency),
                           rfm_frequency=json.dumps(rfm_frequency),
                           rfm_monetary=json.dumps(rfm_monetary),
                           segment_config=SEGMENT_CONFIG)


# ── Customer Intelligence ─────────────────────────────────────────────────────

@app.route('/customer-intelligence')
@login_required
def customer_intelligence():
    search  = request.args.get('search', '').strip()
    segment = request.args.get('segment', '').strip()
    page    = request.args.get('page', 1, type=int)
    per_page = 15

    # Hitung ringkasan
    total = Customer.query.count()
    total_transaksi = db.session.query(func.sum(Customer.total_transaksi)).scalar() or 0
    total_revenue   = db.session.query(func.sum(RFMResult.monetary)).scalar() or 0

    counts = (
        db.session.query(CustomerSegment.segment_label, func.count())
        .group_by(CustomerSegment.segment_label).all()
    )
    seg_counts = {label: cnt for label, cnt in counts}

    # Query utama
    query = (
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
    )
    if search:
        query = query.filter(Customer.nama_pelanggan.ilike(f'%{search}%'))
    if segment:
        query = query.filter(CustomerSegment.segment_label == segment)

    total_filtered = query.count()
    offset = (page - 1) * per_page
    rows = apply_display_recency(
        query.order_by(RFMResult.monetary.desc()).offset(offset).limit(per_page).all()
    )
    total_pages = (total_filtered + per_page - 1) // per_page

    # Top 5 monetary & frequency
    top_monetary = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(RFMResult.monetary.desc()).limit(5).all()
    )
    top_frequency = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(RFMResult.frequency.desc()).limit(5).all()
    )

    # RFM stats
    stats = get_rfm_summary()

    return render_template('customer_intelligence.html',
                           rows=rows,
                           page=page,
                           total_pages=total_pages,
                           total_filtered=total_filtered,
                           search=search,
                           segment=segment,
                           total=total,
                           total_transaksi=total_transaksi,
                           total_revenue=total_revenue,
                           seg_counts=seg_counts,
                           top_monetary=top_monetary,
                           top_frequency=top_frequency,
                           stats=stats,
                           segment_config=SEGMENT_CONFIG,
                           segments=list(SEGMENT_CONFIG.keys()))


# ── Data Customer ─────────────────────────────────────────────────────────────

@app.route('/customers')
@login_required
def customers():
    page    = request.args.get('page', 1, type=int)
    search  = request.args.get('search', '').strip()
    segment = request.args.get('segment', '').strip()
    per_page = 15

    query = (
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
    )

    if search:
        query = query.filter(Customer.nama_pelanggan.ilike(f'%{search}%'))
    if segment:
        query = query.filter(CustomerSegment.segment_label == segment)

    total_filtered = query.count()
    offset = (page - 1) * per_page
    rows = apply_display_recency(
        query.order_by(RFMResult.monetary.desc()).offset(offset).limit(per_page).all()
    )

    total_pages = (total_filtered + per_page - 1) // per_page

    return render_template('customers.html',
                           rows=rows,
                           page=page,
                           total_pages=total_pages,
                           total_filtered=total_filtered,
                           search=search,
                           segment=segment,
                           segment_config=SEGMENT_CONFIG,
                           segments=list(SEGMENT_CONFIG.keys()))


@app.route('/api/customer/<int:cid>')
@login_required
def get_customer(cid):
    c = Customer.query.get_or_404(cid)
    return jsonify({'success': True, 'data': c.to_dict()})


def _parse_customer_form():
    last_date = None
    ltd = request.form.get('last_transaction_date', '').strip()
    if ltd:
        last_date = pd.to_datetime(ltd, errors='coerce')
        last_date = last_date.date() if pd.notna(last_date) else None

    recency = int(request.form.get('recency') or 0)
    return {
        'nama_pelanggan': request.form.get('nama_pelanggan', '').strip(),
        'nomor_hp': request.form.get('nomor_hp', '').strip() or None,
        'lokasi': request.form.get('lokasi', '').strip() or None,
        'kategori': request.form.get('kategori', '').strip() or None,
        'total_transaksi': int(request.form.get('total_transaksi') or request.form.get('frequency') or 0),
        'recency': calculate_recency(last_date, recency),
        'frequency': int(request.form.get('frequency') or 0),
        'monetary': float(request.form.get('monetary') or 0),
        'last_transaction_date': last_date,
        'cluster': int(request.form.get('cluster') or 0),
        'segment_label': request.form.get('segment_label', '').strip(),
    }


@app.route('/customers/add', methods=['POST'])
@permission_required('create_customer')
def add_customer():
    data = _parse_customer_form()
    if not data['nama_pelanggan'] or data['segment_label'] not in SEGMENT_CONFIG:
        return redirect(url_for('customers'))

    customer = Customer(
        nama_pelanggan=data['nama_pelanggan'],
        nomor_hp=data['nomor_hp'],
        lokasi=data['lokasi'],
        kategori=data['kategori'],
        total_transaksi=data['total_transaksi'],
    )
    db.session.add(customer)
    db.session.flush()
    db.session.add(RFMResult(
        customer_id=customer.id,
        recency=data['recency'],
        frequency=data['frequency'],
        monetary=data['monetary'],
        last_transaction_date=data['last_transaction_date'],
    ))
    db.session.add(CustomerSegment(
        customer_id=customer.id,
        cluster=data['cluster'],
        segment_label=data['segment_label'],
    ))
    db.session.commit()
    return redirect(url_for('customers'))


@app.route('/customers/<int:cid>/edit', methods=['POST'])
@permission_required('edit_customer')
def edit_customer(cid):
    customer = Customer.query.get_or_404(cid)
    data = _parse_customer_form()
    if not data['nama_pelanggan'] or data['segment_label'] not in SEGMENT_CONFIG:
        return redirect(url_for('customers'))

    customer.nama_pelanggan = data['nama_pelanggan']
    customer.nomor_hp = data['nomor_hp']
    customer.lokasi = data['lokasi']
    customer.kategori = data['kategori']
    customer.total_transaksi = data['total_transaksi']

    if customer.rfm:
        customer.rfm.recency = data['recency']
        customer.rfm.frequency = data['frequency']
        customer.rfm.monetary = data['monetary']
        customer.rfm.last_transaction_date = data['last_transaction_date']
    if customer.segment:
        customer.segment.cluster = data['cluster']
        customer.segment.segment_label = data['segment_label']

    db.session.commit()
    return redirect(url_for('customers'))


@app.route('/customers/<int:cid>/delete', methods=['POST'])
@permission_required('delete_customer')
def delete_customer(cid):
    customer = Customer.query.get_or_404(cid)
    db.session.delete(customer)
    db.session.commit()
    return redirect(url_for('customers'))


# ── RFM Analysis ──────────────────────────────────────────────────────────────

@app.route('/rfm')
@login_required
def rfm():
    page    = request.args.get('page', 1, type=int)
    search  = request.args.get('search', '').strip()
    sort_by = request.args.get('sort', 'monetary')
    per_page = 15

    sort_col = {
        'recency':   RFMResult.last_transaction_date.desc(),
        'frequency': RFMResult.frequency.desc(),
        'monetary':  RFMResult.monetary.desc(),
    }.get(sort_by, RFMResult.monetary.desc())

    query = (
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
    )
    if search:
        query = query.filter(Customer.nama_pelanggan.ilike(f'%{search}%'))

    total_filtered = query.count()
    offset = (page - 1) * per_page
    rows = apply_display_recency(
        query.order_by(sort_col).offset(offset).limit(per_page).all()
    )
    total_pages = (total_filtered + per_page - 1) // per_page

    # Statistik RFM
    stats = get_rfm_summary()

    return render_template('rfm.html',
                           rows=rows,
                           page=page,
                           total_pages=total_pages,
                           total_filtered=total_filtered,
                           search=search,
                           sort_by=sort_by,
                           stats=stats,
                           segment_config=SEGMENT_CONFIG)


# ── Segmentasi ────────────────────────────────────────────────────────────────

@app.route('/segmentation')
@login_required
def segmentation():
    segment = request.args.get('segment', '').strip()
    search  = request.args.get('search', '').strip()
    page    = request.args.get('page', 1, type=int)
    per_page = 15

    # Hitung jumlah per segmen
    counts = (
        db.session.query(CustomerSegment.segment_label, func.count())
        .group_by(CustomerSegment.segment_label)
        .all()
    )
    seg_counts = {label: cnt for label, cnt in counts}

    query = (
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
    )
    if segment:
        query = query.filter(CustomerSegment.segment_label == segment)
    if search:
        query = query.filter(Customer.nama_pelanggan.ilike(f'%{search}%'))

    total_filtered = query.count()
    offset = (page - 1) * per_page
    rows = apply_display_recency(
        query.order_by(RFMResult.monetary.desc()).offset(offset).limit(per_page).all()
    )
    total_pages = (total_filtered + per_page - 1) // per_page

    return render_template('segmentation.html',
                           rows=rows,
                           page=page,
                           total_pages=total_pages,
                           total_filtered=total_filtered,
                           search=search,
                           segment=segment,
                           seg_counts=seg_counts,
                           segment_config=SEGMENT_CONFIG,
                           segments=list(SEGMENT_CONFIG.keys()))


# ── Insight ───────────────────────────────────────────────────────────────────

@app.route('/insight')
@login_required
def insight():
    total = Customer.query.count()

    counts = (
        db.session.query(CustomerSegment.segment_label, func.count())
        .group_by(CustomerSegment.segment_label)
        .all()
    )
    seg_counts = {label: cnt for label, cnt in counts}

    at_risk_pct   = round(seg_counts.get('At Risk Customer', 0) / total * 100, 1) if total else 0
    champion_pct  = round(seg_counts.get('Champion Customer', 0) / total * 100, 1) if total else 0
    loyal_pct     = round(seg_counts.get('Loyal Customer', 0) / total * 100, 1) if total else 0
    potential_pct = round(seg_counts.get('Potential Customer', 0) / total * 100, 1) if total else 0

    # Top 5 Monetary
    top_monetary = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(RFMResult.monetary.desc())
        .limit(5).all()
    )

    # Top 5 Frequency
    top_frequency = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(RFMResult.frequency.desc())
        .limit(5).all()
    )

    # Rata-rata RFM per segmen
    rfm_summary = get_segment_rfm_summary()
    rfm_by_seg = [{
        'segment_label': label,
        'avg_r': round(values['avg_r'], 1),
        'avg_f': round(values['avg_f'], 1),
        'avg_m': round(values['avg_m'], 0),
    } for label, values in rfm_summary.items()]

    # Lokasi terbanyak
    top_lokasi = (
        db.session.query(Customer.lokasi, func.count().label('cnt'))
        .filter(Customer.lokasi.isnot(None))
        .filter(Customer.lokasi != '')
        .group_by(Customer.lokasi)
        .order_by(func.count().desc())
        .limit(5).all()
    )

    # Kategori terbanyak
    top_kategori = (
        db.session.query(Customer.kategori, func.count().label('cnt'))
        .filter(Customer.kategori.isnot(None))
        .group_by(Customer.kategori)
        .order_by(func.count().desc())
        .limit(5).all()
    )

    # Segment terbesar
    largest_segment = max(seg_counts, key=seg_counts.get) if seg_counts else '-'

    return render_template('insight.html',
                           total=total,
                           seg_counts=seg_counts,
                           at_risk_pct=at_risk_pct,
                           champion_pct=champion_pct,
                           loyal_pct=loyal_pct,
                           potential_pct=potential_pct,
                           top_monetary=top_monetary,
                           top_frequency=top_frequency,
                           rfm_by_seg=rfm_by_seg,
                           top_lokasi=top_lokasi,
                           top_kategori=top_kategori,
                           largest_segment=largest_segment,
                           segment_config=SEGMENT_CONFIG)


# ── Laporan ───────────────────────────────────────────────────────────────────

@app.route('/report')
@login_required
def report():
    total = Customer.query.count()
    counts = (
        db.session.query(CustomerSegment.segment_label, func.count())
        .group_by(CustomerSegment.segment_label).all()
    )
    seg_counts = {label: cnt for label, cnt in counts}

    top5 = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(RFMResult.monetary.desc())
        .limit(5).all()
    )

    rfm_stats = get_rfm_summary()

    pie_labels = list(SEGMENT_CONFIG.keys())
    pie_data   = [seg_counts.get(l, 0) for l in pie_labels]
    pie_colors = [SEGMENT_CONFIG[l]['color'] for l in pie_labels]

    return render_template('report.html',
                           total=total,
                           seg_counts=seg_counts,
                           top5=top5,
                           rfm_stats=rfm_stats,
                           pie_labels=json.dumps(pie_labels),
                           pie_data=json.dumps(pie_data),
                           pie_colors=json.dumps(pie_colors),
                           segment_config=SEGMENT_CONFIG)


@app.route('/export/excel')
@permission_required('export_report')
def export_excel():
    rows = (
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(CustomerSegment.segment_label, RFMResult.monetary.desc())
        .all()
    )

    data = [{
        'Nama Pelanggan':  c.nama_pelanggan,
        'Nomor HP':        c.nomor_hp or '-',
        'Lokasi':          c.lokasi or '-',
        'Kategori':        c.kategori or '-',
        'Recency (hari)':  calculate_recency(r.last_transaction_date, r.recency),
        'Frequency':       r.frequency,
        'Monetary (Rp)':   r.monetary,
        'Cluster':         s.cluster,
        'Segment':         s.segment_label,
    } for c, r, s in rows]

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Customer Segmentation')
    output.seek(0)

    return send_file(output,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True,
                     download_name='customer_segmentation_nuansa_wangi.xlsx')


@app.route('/export/pdf')
@permission_required('export_report')
def export_pdf():
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.units import cm
    except ImportError:
        return jsonify({'error': 'reportlab tidak terinstall. Jalankan: pip install reportlab'}), 500

    counts = (
        db.session.query(CustomerSegment.segment_label, func.count())
        .group_by(CustomerSegment.segment_label).all()
    )
    seg_counts = {label: cnt for label, cnt in counts}
    total = Customer.query.count()

    top20 = apply_display_recency(
        db.session.query(Customer, RFMResult, CustomerSegment)
        .join(RFMResult, Customer.id == RFMResult.customer_id)
        .join(CustomerSegment, Customer.id == CustomerSegment.customer_id)
        .order_by(RFMResult.monetary.desc())
        .limit(20).all()
    )

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    # Judul
    story.append(Paragraph('<b>Laporan Customer Intelligence System</b>', styles['Title']))
    story.append(Paragraph('Nuansa Wangi Cilendek — RFM Analysis &amp; K-Means Clustering',
                            styles['Normal']))
    story.append(Spacer(1, 0.5*cm))

    # Ringkasan segmentasi
    story.append(Paragraph('<b>Ringkasan Segmentasi</b>', styles['Heading2']))
    sum_data = [['Segment', 'Jumlah Customer', 'Persentase']]
    for label in SEGMENT_CONFIG:
        cnt = seg_counts.get(label, 0)
        pct = f"{cnt/total*100:.1f}%" if total else "0%"
        sum_data.append([label, str(cnt), pct])
    sum_data.append(['TOTAL', str(total), '100%'])

    t = Table(sum_data, colWidths=[8*cm, 5*cm, 4*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4361ee')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN',      (0,0), (-1,-1), 'CENTER'),
        ('GRID',       (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#f0f0f0')),
        ('FONTNAME',   (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#f8f9ff')]),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.5*cm))

    # Top 20 customer
    story.append(Paragraph('<b>Top 20 Customer berdasarkan Monetary</b>', styles['Heading2']))
    tbl_data = [['No', 'Nama Pelanggan', 'Recency', 'Frequency', 'Monetary (Rp)', 'Segment']]
    for i, (c, r, s) in enumerate(top20, 1):
        tbl_data.append([
            str(i), c.nama_pelanggan,
            str(r.display_recency), str(r.frequency),
            f"Rp {r.monetary:,.0f}",
            s.segment_label
        ])

    t2 = Table(tbl_data, colWidths=[1*cm, 7*cm, 3*cm, 3*cm, 5*cm, 6*cm])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4361ee')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 8),
        ('ALIGN',      (0,0), (-1,-1), 'CENTER'),
        ('ALIGN',      (1,1), (1,-1), 'LEFT'),
        ('GRID',       (0,0), (-1,-1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8f9ff')]),
    ]))
    story.append(t2)

    doc.build(story)
    output.seek(0)
    return send_file(output, mimetype='application/pdf',
                     as_attachment=True,
                     download_name='laporan_customer_intelligence.pdf')


# ── Upload (re-seed dari file baru) ───────────────────────────────────────────

@app.route('/upload/template')
@permission_required('upload_data')
def download_upload_template():
    template_rows = [{
        'Nama Pelanggan': 'Contoh Pelanggan',
        'Nomor HP': '6281234567890',
        'Lokasi': 'Bogor',
        'Kategori': 'Parfum',
        'Recency': 7,
        'Frequency': 3,
        'Monetary': 450000,
        'Last_Transaction_Date': '2026-06-04',
        'Cluster': 0,
        'Segment': 'Potential Customer',
    }]
    raw_rows = [{
        'Tanggal': '2026-06-04',
        'Nama Pelanggan': 'Contoh Pelanggan',
        'Nomor HP': '081234567890',
        'Lokasi': 'Bogor',
        'Kategori': 'Parfum',
        'Total Transaksi': 150000,
    }]
    guide_rows = [
        {'Kolom': 'Mode', 'Wajib': '-', 'Keterangan': 'Sistem menerima Template Upload final atau Data Transaksi Mentah.'},
        {'Kolom': 'Nama Pelanggan', 'Wajib': 'Ya', 'Keterangan': 'Nama pelanggan. Dipakai sebagai kunci update data.'},
        {'Kolom': 'Recency', 'Wajib': 'Ya', 'Keterangan': 'Hari sejak transaksi terakhir. Jika Last_Transaction_Date diisi, sistem akan hitung ulang otomatis.'},
        {'Kolom': 'Frequency', 'Wajib': 'Ya', 'Keterangan': 'Jumlah transaksi pelanggan.'},
        {'Kolom': 'Monetary', 'Wajib': 'Ya', 'Keterangan': 'Total belanja pelanggan dalam Rupiah, tanpa simbol Rp.'},
        {'Kolom': 'Last_Transaction_Date', 'Wajib': 'Disarankan', 'Keterangan': 'Format YYYY-MM-DD. Dipakai untuk recency dinamis.'},
        {'Kolom': 'Cluster', 'Wajib': 'Tidak', 'Keterangan': 'Nomor cluster 0, 1, 2, atau 3. Jika kosong akan menjadi 0.'},
        {'Kolom': 'Segment', 'Wajib': 'Ya', 'Keterangan': 'Harus sesuai salah satu nilai segment valid.'},
        {'Kolom': 'Nomor HP', 'Wajib': 'Tidak', 'Keterangan': 'Nomor WhatsApp pelanggan.'},
        {'Kolom': 'Lokasi', 'Wajib': 'Tidak', 'Keterangan': 'Alamat atau area pelanggan.'},
        {'Kolom': 'Kategori', 'Wajib': 'Tidak', 'Keterangan': 'Kategori produk dominan.'},
        {'Kolom': 'Tanggal', 'Wajib': 'Ya untuk transaksi mentah', 'Keterangan': 'Tanggal transaksi. Alternatif: Tanggal Pesanan, Tanggal Transaksi.'},
        {'Kolom': 'Total Transaksi', 'Wajib': 'Ya untuk transaksi mentah', 'Keterangan': 'Nilai transaksi per baris. Sistem akan menjumlahkan menjadi Monetary.'},
    ]
    segment_rows = [
        {'Segment': label, 'Cluster': idx}
        for idx, label in enumerate(SEGMENT_CONFIG.keys())
    ]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(template_rows).to_excel(writer, index=False, sheet_name='Template Upload')
        pd.DataFrame(raw_rows).to_excel(writer, index=False, sheet_name='Contoh Transaksi Mentah')
        pd.DataFrame(guide_rows).to_excel(writer, index=False, sheet_name='Panduan Kolom')
        pd.DataFrame(segment_rows).to_excel(writer, index=False, sheet_name='Segment Valid')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='template_upload_data_pelanggan.xlsx'
    )


@app.route('/upload', methods=['GET', 'POST'])
@permission_required('upload_data')
def upload():
    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'Tidak ada file.'})

        file = request.files['file']
        if not file.filename:
            return jsonify({'success': False, 'message': 'Tidak ada file dipilih.'})

        try:
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)

            if _is_final_upload(df):
                import_df = df
                merge_existing = request.form.get('merge_existing') == '1'
                mode = (
                    'template final, sudah ditambahkan ke data yang ada'
                    if merge_existing else
                    'template final, mengganti data customer yang sudah ada'
                )
            else:
                import_df = _build_rfm_from_transactions(df)
                mode = 'transaksi mentah, sudah ditambahkan ke data yang ada'
                merge_existing = True

            count = _save_customer_upload(import_df, merge_existing=merge_existing)
            if merge_existing:
                _recalculate_all_customer_segments()

            db.session.commit()
            return jsonify({'success': True,
                            'message': f'Berhasil memproses {count} data pelanggan dari {mode}.',
                            'count': count})

        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error: {str(e)}'})

    return render_template('upload.html')


# ── Main ──────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()
    ensure_default_users()
    ensure_postgres_schema_updates()
    ensure_postgres_sequences()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_default_users()
        ensure_postgres_schema_updates()
        ensure_postgres_sequences()
        # Seed dari Excel saat pertama kali
        from seed import seed_database
        seed_database()
    app.run(debug=True, port=5000)
