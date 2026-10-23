"""Reconcile geo_location/country_code with the current ip2region resolution.

Re-resolves every distinct ip_address in login_sessions / visit_events and updates
the rows whose stored value is out of sync with what resolve_ip_geo() now returns.
This covers both the historical "未知" rows (pre-ip2region) and any rows written
with an older display format (e.g. an English country name before 美国-style
localization). Idempotent: a second run changes nothing.

Guarded: dry-run by default, commits only with --write, never overwrites a good
value with "未知" (IPs that no longer resolve are skipped), and refuses to run if
the ip2region xdb is unavailable.

Usage:
    python scripts/backfill_geo.py            # dry-run: report only, no writes
    python scripts/backfill_geo.py --write    # apply updates (single committed txn)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.shared.services.geo_service import _get_searcher, resolve_ip_geo
from sqlalchemy import func, or_, select, update

from app.shared.core.database import SessionLocal
from app.shared.models.login_session import LoginSession
from app.shared.models.visit_event import VisitEvent


def reconcile(session, model, has_country_code: bool, apply: bool) -> int:
    total = session.scalar(select(func.count()).select_from(model))
    ips = [row[0] for row in session.execute(select(model.ip_address).distinct()).all()]
    print(f"\n[{model.__tablename__}] rows={total} distinct_ips={len(ips)}")

    changed_rows = 0
    sample: list[str] = []
    for ip in ips:
        geo = resolve_ip_geo(ip)
        name = geo["name"]
        if name == "未知":
            continue  # unresolvable now — never overwrite an existing value with 未知

        # rows for this ip whose stored geo is out of sync with current resolution
        mismatch = or_(model.geo_location.is_(None), model.geo_location != name)
        if has_country_code:
            mismatch = or_(mismatch, model.country_code.is_(None), model.country_code != geo["country_code"])
        where = (model.ip_address == ip, mismatch)  # multi-column guard

        n_match = session.scalar(select(func.count()).select_from(model).where(*where)) or 0
        if n_match == 0:
            continue  # already in sync

        values = {"geo_location": name}
        if has_country_code:
            values["country_code"] = geo["country_code"]
        if apply:
            result = session.execute(update(model).where(*where).values(**values))
            changed_rows += result.rowcount or 0
        else:
            changed_rows += n_match
        if len(sample) < 10:
            sample.append(f"  {ip:>16s} -> {name}")

    for line in sample:
        print(line)
    if changed_rows and len(sample) >= 10:
        print("  ...")
    print(f"[{model.__tablename__}] {'UPDATED' if apply else 'WOULD UPDATE'} rows: {changed_rows}")
    return changed_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply updates (default: dry-run)")
    args = parser.parse_args()

    if _get_searcher() is None:
        print("ERROR: ip2region xdb unavailable — refusing to run "
              "(nothing would resolve, so reconcile would be a no-op at best).")
        sys.exit(1)

    session = SessionLocal()
    try:
        total_changed = 0
        total_changed += reconcile(session, LoginSession, has_country_code=False, apply=args.write)
        total_changed += reconcile(session, VisitEvent, has_country_code=True, apply=args.write)

        if args.write:
            session.commit()
            print(f"\nCOMMITTED. {total_changed} row(s) updated.")
        else:
            session.rollback()
            print(f"\nDRY-RUN: {total_changed} row(s) would change. Re-run with --write to apply.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
