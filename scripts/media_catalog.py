from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import media_catalog


def _cmd_build(args) -> int:
    assets = media_catalog.collect_assets(
        contracts_dir=args.contracts_dir,
        sessions_dir=args.sessions_dir,
        jobs_dir=args.jobs_dir,
    )
    catalog = media_catalog.build_catalog(
        assets, reference_deployment=args.reference_deployment,
    )
    out = Path(args.out) if args.out else None
    if args.dry_run:
        print(json.dumps(catalog, indent=2))
        return 0
    if out is None:
        print("error: --out is required unless --dry-run")
        return 2
    media_catalog.write_catalog(out, catalog)
    if args.public_out:
        media_catalog.write_public_catalog(args.public_out, catalog)
        print(f"wrote public metadata catalog -> {args.public_out}")
    print(f"wrote {catalog['asset_count']} asset(s) -> {out}")
    for asset in catalog["assets"]:
        print(f"  [{asset['asset_type']}] {asset['asset_id']}  {asset['title']}")
    return 0


def _cmd_validate(args) -> int:
    path = Path(args.catalog)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"error: catalog not found: {path}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: catalog is not valid JSON: {path}: {exc}")
        return 2
    errors = media_catalog.validate_catalog(data)
    if errors:
        print(f"invalid catalog: {path}")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"valid catalog: {path} ({data.get('asset_count')} asset(s), schema {data.get('catalog_schema')})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 7.3 unified media catalog exporter (read-only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a catalog from Clipper runtime records.")
    build.add_argument("--contracts-dir", help="Directory of *.contract.json (Phase 7/7.2) records")
    build.add_argument("--sessions-dir", help="Directory of *.manifest.json (Phase 7.2) live-capture sessions")
    build.add_argument("--jobs-dir", help="Managed pilot job-record root")
    build.add_argument("--reference-deployment", default=media_catalog.DEFAULT_REFERENCE_DEPLOYMENT)
    build.add_argument("--out", help="Output catalog path")
    build.add_argument(
        "--public-out",
        help="Explicitly write a sanitized metadata-only catalog for reviewed publication",
    )
    build.add_argument("--dry-run", action="store_true", help="Print the catalog without writing")
    build.set_defaults(func=_cmd_build)

    validate = sub.add_parser("validate", help="Validate an existing catalog file.")
    validate.add_argument("--catalog", required=True)
    validate.set_defaults(func=_cmd_validate)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
