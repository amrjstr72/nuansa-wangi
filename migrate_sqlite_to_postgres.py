import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database import Customer, CustomerSegment, RFMResult, User, db


TABLES = [User, Customer, RFMResult, CustomerSegment]


def normalize_postgres_url(url):
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url


def default_sqlite_path():
    instance_db = Path('instance') / 'nuansa_wangi.db'
    root_db = Path('nuansa_wangi.db')
    if instance_db.exists() and instance_db.stat().st_size > 0:
        return instance_db
    return root_db


def sqlite_url_from_path(path):
    return f"sqlite:///{Path(path).resolve().as_posix()}"


def row_to_dict(row):
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
    }


def copy_table(source_session, target_session, model):
    rows = source_session.query(model).order_by(model.id).all()
    for row in rows:
        target_session.add(model(**row_to_dict(row)))
    return len(rows)


def table_counts(session):
    return {model.__tablename__: session.query(model).count() for model in TABLES}


def reset_postgres_sequences(session):
    for model in TABLES:
        table_name = model.__tablename__
        pk_name = next(iter(model.__table__.primary_key.columns)).name
        session.execute(
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


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description='Migrate Nuansa Wangi data from SQLite to PostgreSQL.'
    )
    parser.add_argument(
        '--sqlite',
        default=str(default_sqlite_path()),
        help='Path ke database SQLite sumber. Default: instance/nuansa_wangi.db jika ada.'
    )
    parser.add_argument(
        '--postgres-url',
        default=os.environ.get('POSTGRES_DATABASE_URL') or os.environ.get('DATABASE_URL'),
        help='URL PostgreSQL target. Bisa juga lewat env POSTGRES_DATABASE_URL.'
    )
    parser.add_argument(
        '--replace',
        action='store_true',
        help='Hapus data target PostgreSQL sebelum migrasi.'
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists() or sqlite_path.stat().st_size == 0:
        print(f'ERROR: SQLite source tidak valid: {sqlite_path}', file=sys.stderr)
        return 1

    postgres_url = normalize_postgres_url(args.postgres_url)
    if not postgres_url or not postgres_url.startswith('postgresql://'):
        print(
            'ERROR: Isi POSTGRES_DATABASE_URL atau --postgres-url dengan URL PostgreSQL.',
            file=sys.stderr
        )
        return 1

    source_engine = create_engine(sqlite_url_from_path(sqlite_path))
    target_engine = create_engine(postgres_url)
    SourceSession = sessionmaker(bind=source_engine)
    TargetSession = sessionmaker(bind=target_engine)

    db.metadata.create_all(target_engine)

    source_session = SourceSession()
    target_session = TargetSession()
    try:
        source_counts = table_counts(source_session)
        target_counts = table_counts(target_session)
        if any(target_counts.values()) and not args.replace:
            print('ERROR: Target PostgreSQL sudah berisi data.')
            print('Target counts:', target_counts)
            print('Jalankan dengan --replace kalau ingin menimpa data target.')
            return 1

        if args.replace:
            for model in reversed(TABLES):
                target_session.query(model).delete()
            target_session.commit()

        migrated = {}
        for model in TABLES:
            migrated[model.__tablename__] = copy_table(source_session, target_session, model)

        reset_postgres_sequences(target_session)
        target_session.commit()
        print('Migrasi berhasil.')
        print('Source SQLite:', sqlite_path)
        print('Source counts:', source_counts)
        print('Migrated counts:', migrated)
        print('Target counts:', table_counts(target_session))
        return 0
    except Exception as exc:
        target_session.rollback()
        print(f'ERROR: Migrasi gagal: {exc}', file=sys.stderr)
        return 1
    finally:
        source_session.close()
        target_session.close()


if __name__ == '__main__':
    raise SystemExit(main())
