"""Unique CRM identity resolution shared by reads and mutations (no network I/O)."""
import re
import sqlite3
from pathlib import Path


class AmbiguousCustomerError(ValueError):
    pass


def normalize_phone(value):
    raw = str(value or '').strip()
    if not re.fullmatch(r'\+?\d[\d ()-]*', raw):
        return ''
    digits = re.sub(r'\D', '', raw)
    return '27' + digits[1:] if len(digits) == 10 and digits.startswith('0') else digits


def lookup_customer(db_path, query):
    query = str(query or '').strip()
    if not query:
        raise ValueError('Customer query must not be empty')
    if not Path(db_path).exists():
        return None
    # Read-only mode still observes committed WAL transactions; immutable mode
    # is inappropriate for a database concurrently updated by the monitor.
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.create_function('normalized_phone', 1, normalize_phone)
        columns = 'custid, name, phone, vehicle_model, contact_count'
        rows = conn.execute(f'SELECT {columns} FROM prospects WHERE custid = ?', (query,)).fetchall()
        if not rows:
            phone = normalize_phone(query)
            if phone:
                rows = conn.execute(f'SELECT {columns} FROM prospects WHERE normalized_phone(phone) = ? LIMIT 2', (phone,)).fetchall()
            else:
                rows = conn.execute(f'SELECT {columns} FROM prospects WHERE instr(lower(name), lower(?)) > 0 LIMIT 2', (query,)).fetchall()
        if len(rows) > 1:
            raise AmbiguousCustomerError('Multiple CRM customers match; specify the exact customer ID.')
        if not rows:
            return None
        row = rows[0]
        return dict(custid=row['custid'], name=row['name'], phone=row['phone'],
                    vehicle=row['vehicle_model'], contact_count=row['contact_count'])
    finally:
        conn.close()
