"""
Deidentify resident names in schedule-residents input CSV files.

Reads resident-key.txt by default. Expected columns:
  Name    Code

Then replaces names in:
  input/template-a.csv           resident column headers only
  input/residents.csv            Resident column
  input/vac_pref.csv             Resident column
  input/resident_blackouts.csv   Resident column

By default this overwrites the input files and writes timestamped .bak files
beside each changed file.
"""

import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path


TEMPLATE_FIXED_COLUMNS = {"Week", "Date"}


def load_key(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters="\t,")
        except csv.Error:
            dialect = csv.excel_tab

        reader = csv.DictReader(f, dialect=dialect)
        required = {"Name", "Code"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"{path} must contain columns: Name and Code")

        mapping = {}
        reverse = {}
        for row in reader:
            name = (row.get("Name") or "").strip()
            code = (row.get("Code") or "").strip()
            if not name or not code:
                continue
            if name in mapping and mapping[name] != code:
                raise ValueError(f"Conflicting code for {name!r} in {path}")
            if code in reverse and reverse[code] != name:
                raise ValueError(f"Code {code!r} is assigned to multiple names")
            mapping[name] = code
            reverse[code] = name

    if not mapping:
        raise ValueError(f"No usable mappings found in {path}")
    return mapping


def backup_file(path):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def mapped(value, mapping):
    return mapping.get(value.strip(), value)


def rewrite_dict_csv(path, transform_fieldnames, transform_row, dry_run, make_backup):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header row")
        old_fieldnames = list(reader.fieldnames)
        rows = [dict(row) for row in reader]

    new_fieldnames = transform_fieldnames(old_fieldnames)
    new_rows = [transform_row(row) for row in rows]

    changed = old_fieldnames != new_fieldnames or rows != new_rows
    if dry_run or not changed:
        return changed, None

    backup = backup_file(path) if make_backup else None
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=new_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(new_rows)
    return changed, backup


def collect_input_names(input_dir, template_name):
    names = {}

    template = input_dir / template_name
    if template.exists():
        with template.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
        names[template] = [h for h in header if h not in TEMPLATE_FIXED_COLUMNS]

    for filename in ("residents.csv", "vac_pref.csv", "resident_blackouts.csv"):
        path = input_dir / filename
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "Resident" not in reader.fieldnames:
                continue
            names[path] = sorted({
                (row.get("Resident") or "").strip()
                for row in reader
                if (row.get("Resident") or "").strip()
            })

    return names


def main():
    parser = argparse.ArgumentParser(description="Deidentify schedule-residents input CSV names.")
    parser.add_argument("--key", default="resident-key.txt", help="Path to Name/Code key file.")
    parser.add_argument("--input-dir", default="input", help="Directory containing input CSV files.")
    parser.add_argument("--template", default="template-a.csv", help="Template CSV filename inside input-dir.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing files.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak files before overwriting.")
    parser.add_argument(
        "--allow-unmapped",
        action="store_true",
        help="Proceed even if names in the input files are missing from the key.",
    )
    args = parser.parse_args()

    key_path = Path(args.key)
    input_dir = Path(args.input_dir)
    mapping = load_key(key_path)

    discovered = collect_input_names(input_dir, args.template)
    known_identifiers = set(mapping) | set(mapping.values())
    unmapped = sorted({
        name
        for names in discovered.values()
        for name in names
        if name not in known_identifiers
    })
    if unmapped and not args.allow_unmapped:
        print("Unmapped names found; no files were changed:")
        for name in unmapped:
            print(f"  {name}")
        raise SystemExit(1)

    changed_files = []
    backup_files = []

    template = input_dir / args.template
    if template.exists():
        template_header_map = {}

        def template_fields(fields):
            template_header_map.clear()
            for field in fields:
                template_header_map[field] = (
                    mapped(field, mapping) if field not in TEMPLATE_FIXED_COLUMNS else field
                )
            return [template_header_map[field] for field in fields]

        def template_row_transform(row):
            return {
                template_header_map.get(field, field): value
                for field, value in row.items()
            }

        changed, backup = rewrite_dict_csv(
            template,
            template_fields,
            template_row_transform,
            args.dry_run,
            not args.no_backup,
        )
        if changed:
            changed_files.append(template)
        if backup:
            backup_files.append(backup)

    for filename in ("residents.csv", "vac_pref.csv", "resident_blackouts.csv"):
        path = input_dir / filename
        if not path.exists():
            continue

        def row_transform(row):
            new_row = dict(row)
            if "Resident" in new_row:
                new_row["Resident"] = mapped(new_row["Resident"], mapping)
            return new_row

        changed, backup = rewrite_dict_csv(
            path,
            lambda fields: fields,
            row_transform,
            args.dry_run,
            not args.no_backup,
        )
        if changed:
            changed_files.append(path)
        if backup:
            backup_files.append(backup)

    verb = "Would update" if args.dry_run else "Updated"
    if changed_files:
        print(f"{verb}:")
        for path in changed_files:
            print(f"  {path}")
    else:
        print("No changes needed.")

    if backup_files:
        print("Backups written:")
        for path in backup_files:
            print(f"  {path}")

    if unmapped and args.allow_unmapped:
        print("Unmapped names left unchanged:")
        for name in unmapped:
            print(f"  {name}")


if __name__ == "__main__":
    main()
