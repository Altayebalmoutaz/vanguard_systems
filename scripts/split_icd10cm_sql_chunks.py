from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source_sql = root / "supabase" / "migrations" / "012_icd10cm_2026_full_load.sql"
    out_dir = root / "supabase" / "migrations"

    if not source_sql.exists():
        raise FileNotFoundError(f"Missing source SQL: {source_sql}")

    lines = source_sql.read_text(encoding="utf-8").splitlines()

    header = []
    idx = 0
    while idx < len(lines):
        header.append(lines[idx])
        if (
            lines[idx].strip().lower()
            == "insert into public.icd10_codes (code, description) values"
        ):
            break
        idx += 1

    # rewind one line so first insert statement is included in data body
    body_start = idx
    body = lines[body_start:]

    # Split by insert blocks (each block ends at updated_at = now();)
    blocks = []
    current = []
    for ln in body:
        current.append(ln)
        if ln.strip().lower() == "updated_at = now();":
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    blocks_per_file = 8
    file_count = 0
    for i in range(0, len(blocks), blocks_per_file):
        chunk_blocks = blocks[i : i + blocks_per_file]
        file_count += 1
        out = out_dir / f"012_icd10cm_2026_full_load_part_{file_count:02d}.sql"
        out_lines = []
        if file_count == 1:
            # include full header only in first part (contains create table + truncate)
            out_lines.extend(header[:-1])  # header currently includes first "insert into..." line
            out_lines.append("")
        else:
            out_lines.append("-- Continuation chunk for ICD-10-CM 2026 full load")
            out_lines.append("")
        for b in chunk_blocks:
            out_lines.extend(b)
            out_lines.append("")
        out.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")

    print(f"Created {file_count} chunk files.")


if __name__ == "__main__":
    main()
