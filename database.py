from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import date, datetime

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default='staff')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Customer(db.Model):
    __tablename__ = 'customers'

    id = db.Column(db.Integer, primary_key=True)
    nama_pelanggan = db.Column(db.String(150), nullable=False)
    nomor_hp = db.Column(db.String(30), nullable=True)
    lokasi = db.Column(db.Text, nullable=True)
    kategori = db.Column(db.String(50), nullable=True)
    total_transaksi = db.Column(db.Integer, default=0)   # jumlah baris transaksi dari dataset
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    rfm = db.relationship('RFMResult', backref='customer', uselist=False,
                          cascade='all, delete-orphan')
    segment = db.relationship('CustomerSegment', backref='customer', uselist=False,
                              cascade='all, delete-orphan')

    def to_dict(self):
        last_date = None
        if self.rfm and self.rfm.last_transaction_date:
            last_date = self.rfm.last_transaction_date.strftime('%d %b %Y')
        return {
            'id': self.id,
            'nama_pelanggan': self.nama_pelanggan,
            'nomor_hp': self.nomor_hp or '-',
            'lokasi': self.lokasi or '-',
            'kategori': self.kategori or '-',
            'total_transaksi': self.total_transaksi,
            'recency': (
                max((date.today() - self.rfm.last_transaction_date).days, 0)
                if self.rfm and self.rfm.last_transaction_date
                else self.rfm.recency if self.rfm else None
            ),
            'frequency': self.rfm.frequency if self.rfm else None,
            'monetary': self.rfm.monetary if self.rfm else None,
            'last_transaction_date': last_date,
            'segment': self.segment.segment_label if self.segment else '-',
            'cluster': self.segment.cluster if self.segment else None,
        }


class RFMResult(db.Model):
    __tablename__ = 'rfm_results'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    recency = db.Column(db.Integer, nullable=False)           # hari sejak snapshot date (untuk analisis)
    frequency = db.Column(db.Integer, nullable=False)         # jumlah transaksi
    monetary = db.Column(db.Float, nullable=False)            # total nilai transaksi
    last_transaction_date = db.Column(db.Date, nullable=True) # tanggal transaksi terakhir aktual
    calculated_at = db.Column(db.DateTime, default=datetime.utcnow)


class CustomerSegment(db.Model):
    __tablename__ = 'customer_segments'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    cluster = db.Column(db.Integer, nullable=False)       # 0,1,2,3
    segment_label = db.Column(db.String(50), nullable=False)  # Champion, Loyal, dll
    segmented_at = db.Column(db.DateTime, default=datetime.utcnow)
