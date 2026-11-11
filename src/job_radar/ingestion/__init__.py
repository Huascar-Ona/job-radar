"""Crawl orchestration: configuration, collectors, normalizers and the store.

Source-agnostic by construction. Nothing here imports a source package,
transport or SQLAlchemy; source wiring and the database adapter are supplied
by the caller (SPEC, "Ingestion run"). The boundary is enforced by
tests/unit/domain/test_domain_boundary.py.
"""
