import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

CODE_RE = re.compile(r"\b(D\d{4})\b")
CODE_LINE_RE = re.compile(r"^(D\d{4})\b")
PAGE_MARK_RE = re.compile(r"--\s*(\d+)\s+of\s+\d+\s*--")
RULE_HINT_RE = re.compile(
    r"(requires|limited to|not billable|cannot be billed|prior approval|prior authorization|"
    r"report needed|per year|per month|per 12 months|through \d+ years|years of age|"
    r"only approvable|reimbursable|frequency|pos code|place of service)",
    re.IGNORECASE,
)
SKIP_LINE_RE = re.compile(
    r"^(Policy and Procedure Codes|Dental Manual|Version \d{4}|Code\s+Description\s+Fee|Table of Contents)\b",
    re.IGNORECASE,
)
CHANGELOG_RE = re.compile(
    r"(addition of|updated language|removal of|new procedure codes|change made)",
    re.IGNORECASE,
)


@dataclass
class CodeEntry:
    code: str
    description: str = ""
    section: str | None = None
    page: int | None = None
    raw_lines: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)


def clean_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    return line.replace("’", "'")


def infer_rule_type(text: str) -> str:
    t = text.lower()
    if "prior approval" in t or "prior authorization" in t or "pa request" in t:
        return "prior_auth"
    if "not billable" in t or "cannot be billed" in t:
        return "billing_exclusion"
    if "limited to" in t or "through" in t or "years of age" in t:
        return "age_limit"
    if "per year" in t or "per month" in t or "frequency" in t or "12 months" in t:
        return "frequency_limit"
    if "pos code" in t or "place of service" in t:
        return "place_of_service"
    if "requires" in t or "report needed" in t:
        return "documentation_requirement"
    return "coverage_rule"


def escape_sql_text(value: str) -> str:
    return value.replace("'", "''")


def parse_pdf(pdf_path: Path) -> dict[str, CodeEntry]:
    reader = PdfReader(str(pdf_path))
    entries: dict[str, CodeEntry] = {}
    current_code: str | None = None
    current_page_num: int | None = None
    current_section: str | None = None

    in_code_section = False

    for page_idx, pdf_page in enumerate(reader.pages, start=1):
        current_page_num = page_idx
        text = pdf_page.extract_text() or ""
        lines = [clean_line(line) for line in text.splitlines()]

        for line in lines:
            if not line:
                continue
            if SKIP_LINE_RE.search(line):
                continue

            if "Section V - Dental Procedure Codes" in line:
                in_code_section = True
                continue

            page_match = PAGE_MARK_RE.search(line)
            if page_match:
                continue

            if line.startswith(("I. ", "II. ", "III. ", "IV. ", "V. ", "VI. ", "VII. ", "VIII. ", "IX. ", "X. ", "XI. ", "XII. ")):
                current_section = line
                continue

            code_line = CODE_LINE_RE.match(line)
            if code_line and in_code_section:
                primary = code_line.group(1)
                if CHANGELOG_RE.search(line):
                    current_code = None
                    continue
                current_code = primary
                desc = line.split(primary, 1)[1].strip(" -:\t")
                if primary not in entries:
                    entries[primary] = CodeEntry(
                        code=primary,
                        description=desc[:1000],
                        section=current_section,
                        page=current_page_num,
                        raw_lines=[line],
                    )
                else:
                    if desc and len(desc) > len(entries[primary].description):
                        entries[primary].description = desc[:1000]
                    if current_section and "...." not in current_section:
                        entries[primary].section = current_section
                    entries[primary].raw_lines.append(line)

                if RULE_HINT_RE.search(line):
                    entries[primary].rules.append(line)
                continue

            if current_code and current_code in entries:
                if RULE_HINT_RE.search(line):
                    entries[current_code].rules.append(line)
                if (
                    not entries[current_code].description
                    and 3 <= len(line) <= 220
                    and not CODE_RE.search(line)
                ):
                    entries[current_code].description = line
                entries[current_code].raw_lines.append(line)

    return entries


def build_sql(entries: dict[str, CodeEntry], pdf_name: str) -> str:
    source_slug = "nys_medicaid_dental_2026"
    source_title = "NYS Medicaid Dental Policy and Procedure Code Manual 2026"
    payer = "New York State Medicaid"

    rows = []
    for code in sorted(entries):
        entry = entries[code]
        desc = entry.description.strip() or "Description unavailable in parser output"
        desc = desc[:1500]
        page_num = "null" if entry.page is None else str(entry.page)
        section = "null" if not entry.section else f"'{escape_sql_text(entry.section[:250])}'"
        raw_text = " ".join(entry.raw_lines)[:5000]
        rules = []
        seen = set()
        for rule in entry.rules:
            r = rule.strip()
            if r and r.lower() not in seen:
                seen.add(r.lower())
                rules.append(r[:1200])

        rows.append(
            {
                "code": code,
                "description": desc,
                "page": page_num,
                "section": section,
                "raw_text": raw_text,
                "rules": rules,
            }
        )

    sql_lines: list[str] = []
    sql_lines.append("-- Auto-generated from NY Medicaid Dental 2026 PDF")
    sql_lines.append("")
    sql_lines.append(
        "create table if not exists public.rule_sources ("
        " id bigserial primary key,"
        " source_slug text not null unique,"
        " title text not null,"
        " payer_name text not null,"
        " source_file text,"
        " effective_date date,"
        " ingested_at timestamptz not null default now()"
        ");"
    )
    sql_lines.append("")
    sql_lines.append(
        "create table if not exists public.cdt_code_master ("
        " code text primary key,"
        " short_description text not null,"
        " section_label text,"
        " source_id bigint not null references public.rule_sources(id) on delete cascade,"
        " source_page int,"
        " raw_text text,"
        " created_at timestamptz not null default now()"
        ");"
    )
    sql_lines.append("")
    sql_lines.append(
        "create table if not exists public.cdt_payer_rules ("
        " id bigserial primary key,"
        " code text not null references public.cdt_code_master(code) on delete cascade,"
        " payer_name text not null,"
        " rule_type text not null,"
        " rule_text text not null,"
        " conditions jsonb not null default '{}'::jsonb,"
        " source_id bigint not null references public.rule_sources(id) on delete cascade,"
        " source_page int,"
        " created_at timestamptz not null default now()"
        ");"
    )
    sql_lines.append("")
    sql_lines.append("create index if not exists cdt_payer_rules_code_idx on public.cdt_payer_rules(code);")
    sql_lines.append("create index if not exists cdt_payer_rules_type_idx on public.cdt_payer_rules(rule_type);")
    sql_lines.append("")
    sql_lines.append(
        f"insert into public.rule_sources (source_slug, title, payer_name, source_file, effective_date) values "
        f"('{source_slug}', '{escape_sql_text(source_title)}', '{escape_sql_text(payer)}', '{escape_sql_text(pdf_name)}', '2026-01-01') "
        "on conflict (source_slug) do update set "
        "title = excluded.title, payer_name = excluded.payer_name, source_file = excluded.source_file, effective_date = excluded.effective_date;"
    )
    sql_lines.append("")
    sql_lines.append("with src as (")
    sql_lines.append(f"  select id from public.rule_sources where source_slug = '{source_slug}'")
    sql_lines.append(")")
    sql_lines.append("insert into public.cdt_code_master (code, short_description, section_label, source_id, source_page, raw_text)")
    sql_lines.append("values")

    code_value_lines = []
    for r in rows:
        code_value_lines.append(
            "  ("
            f"'{r['code']}', "
            f"'{escape_sql_text(r['description'])}', "
            f"{r['section']}, "
            "(select id from src), "
            f"{r['page']}, "
            f"'{escape_sql_text(r['raw_text'])}'"
            ")"
        )
    sql_lines.append(",\n".join(code_value_lines))
    sql_lines.append(
        "on conflict (code) do update set "
        "short_description = excluded.short_description, "
        "section_label = excluded.section_label, "
        "source_id = excluded.source_id, "
        "source_page = excluded.source_page, "
        "raw_text = excluded.raw_text;"
    )
    sql_lines.append("")
    sql_lines.append("with src as (")
    sql_lines.append(f"  select id from public.rule_sources where source_slug = '{source_slug}'")
    sql_lines.append(")")
    sql_lines.append("insert into public.cdt_payer_rules (code, payer_name, rule_type, rule_text, conditions, source_id, source_page)")

    rule_values = []
    for r in rows:
        for rule in r["rules"]:
            rule_type = infer_rule_type(rule)
            cond = {"parser": "keyword", "source_slug": source_slug}
            cond_json = escape_sql_text(json.dumps(cond))
            rule_values.append(
                "select "
                f"'{r['code']}' as code, "
                f"'{escape_sql_text(payer)}' as payer_name, "
                f"'{rule_type}' as rule_type, "
                f"'{escape_sql_text(rule)}' as rule_text, "
                f"'{cond_json}'::jsonb as conditions, "
                "(select id from src) as source_id, "
                f"{r['page']} as source_page"
            )
    if rule_values:
        sql_lines.append("\nunion all\n".join(rule_values))
        sql_lines.append(";")
    else:
        sql_lines.append("select null::text, null::text, null::text, null::text, '{}'::jsonb, (select id from src), null::int where false;")

    return "\n".join(sql_lines) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pdf_path = Path(r"c:\Users\ZT\Downloads\dental_policy_and_procedure_manual.pdf")
    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing source PDF: {pdf_path}")

    entries = parse_pdf(pdf_path)
    output_path = root / "supabase" / "migrations" / "006_nys_medicaid_cdt_rules.sql"
    sql = build_sql(entries, pdf_path.name)
    output_path.write_text(sql, encoding="utf-8")
    print(f"Wrote {output_path} with {len(entries)} CDT code rows.")


if __name__ == "__main__":
    main()
