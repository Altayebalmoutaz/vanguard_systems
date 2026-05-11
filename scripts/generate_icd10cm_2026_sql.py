from pathlib import Path


def dotted_icd10(code: str) -> str:
    code = code.strip().upper()
    if len(code) <= 3:
        return code
    return f"{code[:3]}.{code[3:]}"


def esc(value: str) -> str:
    return value.replace("'", "''")


def parse_lines(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        code_raw, desc = parts[0].strip(), parts[1].strip()
        code = dotted_icd10(code_raw)
        if not code or not desc:
            continue
        rows.append((code, desc))
    return rows


def build_sql(rows: list[tuple[str, str]]) -> str:
    chunks: list[str] = []
    chunks.append("-- Auto-generated from icd10cm_codes_2026.txt")
    chunks.append("create table if not exists public.icd10_codes (")
    chunks.append("  code text primary key,")
    chunks.append("  description text,")
    chunks.append("  updated_at timestamptz not null default now()")
    chunks.append(");")
    chunks.append("")
    chunks.append("truncate table public.icd10_codes;")
    chunks.append("")

    batch_size = 1000
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        values = ",\n".join(f"  ('{esc(code)}', '{esc(desc)}')" for code, desc in batch)
        chunks.append("insert into public.icd10_codes (code, description) values")
        chunks.append(values)
        chunks.append("on conflict (code) do update set")
        chunks.append("  description = excluded.description,")
        chunks.append("  updated_at = now();")
        chunks.append("")
    return "\n".join(chunks) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source = Path(r"c:\Users\ZT\Downloads\Telegram Desktop\icd10cm_codes_2026.txt")
    if not source.exists():
        raise FileNotFoundError(f"Missing source file: {source}")

    rows = parse_lines(source)
    out = root / "supabase" / "migrations" / "012_icd10cm_2026_full_load.sql"
    out.write_text(build_sql(rows), encoding="utf-8")
    print(f"Wrote {out} with {len(rows)} ICD-10-CM rows.")


if __name__ == "__main__":
    main()
