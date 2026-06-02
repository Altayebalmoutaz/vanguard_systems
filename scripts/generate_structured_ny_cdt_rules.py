import json
import re
from pathlib import Path

from extract_ny_medicaid_cdt import CodeEntry, parse_pdf

RULE_SENTENCE_RE = re.compile(
    r"(requires|limited to|not billable|cannot be billed|prior approval|prior authorization|"
    r"report needed|per year|per month|per 12 months|years of age|only approvable|reimbursable|"
    r"frequency|pos code|place of service|minimum interval|once per|times in)",
    re.IGNORECASE,
)


def esc(value: str) -> str:
    return value.replace("'", "''")


def extract_fee(text: str) -> float | None:
    m = re.search(r"\$([0-9]+\.[0-9]{2})", text)
    return float(m.group(1)) if m else None


def extract_age_bounds(text: str) -> tuple[int | None, int | None]:
    t = text.lower()
    age_min: int | None = None
    age_max: int | None = None

    m = re.search(r"between\s+(\d+)\s+and\s+(\d+)\s+years", t)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = re.search(r"through\s+age\s+(\d+)", t)
    if m:
        age_max = int(m.group(1))

    m = re.search(r"through\s+(\d+)\s+years", t)
    if m:
        age_max = int(m.group(1))

    m = re.search(r"under\s+(\d+)\s+years?", t)
    if m:
        age_max = int(m.group(1)) - 1

    m = re.search(r"for members\s+(\d+)\s+years of age and older", t)
    if m:
        age_min = int(m.group(1))

    m = re.search(r"(\d+)\s+years of age and older", t)
    if m and age_min is None:
        age_min = int(m.group(1))

    m = re.search(r"for members\s+(\d+)\s+to\s+(\d+)\s+years", t)
    if m:
        age_min = int(m.group(1))
        age_max = int(m.group(2))

    return age_min, age_max


def extract_frequency(text: str) -> tuple[int | None, int | None, str | None]:
    t = text.lower()

    m = re.search(r"once per\s+[a-z\-]+\s+\((\d+)\)\s+(month|months|year|years)", t)
    if m:
        period = int(m.group(1))
        unit = m.group(2)
        period_months = period * 12 if "year" in unit else period
        return 1, period_months, "once per interval"

    m = re.search(r"once per\s+(\d+)\s+(month|months|year|years)", t)
    if m:
        period = int(m.group(1))
        unit = m.group(2)
        period_months = period * 12 if "year" in unit else period
        return 1, period_months, "once per interval"

    m = re.search(r"minimum interval of\s+[a-z\-]+\s+\((\d+)\)\s+(month|months|year|years)", t)
    if m:
        period = int(m.group(1))
        unit = m.group(2)
        period_months = period * 12 if "year" in unit else period
        return 1, period_months, "minimum interval"

    m = re.search(r"minimum interval of\s+(\d+)\s+(month|months|year|years)", t)
    if m:
        period = int(m.group(1))
        unit = m.group(2)
        period_months = period * 12 if "year" in unit else period
        return 1, period_months, "minimum interval"

    m = re.search(r"(\d+)\s+times in\s+(\d+)\s+(month|months|year|years)", t)
    if m:
        count = int(m.group(1))
        period = int(m.group(2))
        unit = m.group(3)
        period_months = period * 12 if "year" in unit else period
        return count, period_months, "times in interval"

    return None, None, None


def extract_pos_codes(text: str) -> list[str]:
    found = re.findall(r"\b(?:pos|place of service)\s*[-:]?\s*(\d{2})\b", text, flags=re.IGNORECASE)
    for n in re.findall(r"\b(?:use|codes added:)\s+(\d{2})\b", text, flags=re.IGNORECASE):
        found.append(n)
    for n in re.findall(r"\b(?:use|codes added:)\s+\d{2},\s*(\d{2})\b", text, flags=re.IGNORECASE):
        found.append(n)
    for n in re.findall(
        r"\b(?:use|codes added:)\s+\d{2},\s*\d{2},\s*(\d{2})\b", text, flags=re.IGNORECASE
    ):
        found.append(n)
    dedup = sorted(set(found))
    return dedup


def extract_re_codes(text: str) -> list[str]:
    return sorted(set(re.findall(r"\bRE\s*[\"“]?(\d{2})\b", text)))


def extract_not_billable_with(text: str, code: str) -> list[str]:
    low = text.lower()
    if "not billable" not in low and "cannot be billed" not in low:
        return []
    codes = sorted(set(re.findall(r"\bD\d{4}\b", text)))
    return [c for c in codes if c != code]


def infer_rule_type_from_features(
    text: str,
    has_pa: bool,
    has_report: bool,
    age: tuple[int | None, int | None],
    freq_count: int | None,
    pos_codes: list[str],
    excludes: list[str],
) -> str:
    low = text.lower()
    if has_pa:
        return "prior_auth"
    if has_report:
        return "documentation_requirement"
    if excludes or "not billable" in low or "cannot be billed" in low:
        return "billing_exclusion"
    if age[0] is not None or age[1] is not None:
        return "age_limit"
    if freq_count is not None:
        return "frequency_limit"
    if pos_codes:
        return "place_of_service"
    return "coverage_rule"


def build_structured_rule_text(entry: CodeEntry) -> str:
    raw_text = " ".join(entry.raw_lines)
    chunks = re.split(r"(?<=[.!?])\s+", raw_text)
    picked: list[str] = []
    for c in chunks:
        line = c.strip()
        if not line:
            continue
        if RULE_SENTENCE_RE.search(line):
            picked.append(line)
        if len(picked) >= 6:
            break
    if picked:
        return " ".join(picked)[:2400]
    return raw_text[:2400]


def build_sql(entries: dict[str, CodeEntry], pdf_name: str) -> str:
    source_slug = "nys_medicaid_dental_2026"
    payer = "New York State Medicaid"

    values: list[str] = []
    for code in sorted(entries):
        entry = entries[code]
        raw_text = " ".join(entry.raw_lines)
        rule_text = build_structured_rule_text(entry)
        fee = extract_fee(raw_text)
        age_min, age_max = extract_age_bounds(raw_text)
        if age_min is not None and age_max is not None and age_min > age_max:
            age_min = None
            age_max = None
        freq_count, freq_period_months, freq_rule = extract_frequency(raw_text)
        requires_pa = bool(
            re.search(
                r"(PA REQUIRED|prior approval.*required|prior authorization.*required)",
                raw_text,
                flags=re.IGNORECASE,
            )
        )
        requires_report = bool(re.search(r"REPORT NEEDED", raw_text, flags=re.IGNORECASE))
        pos_codes = extract_pos_codes(raw_text)
        re_codes = extract_re_codes(raw_text)
        not_billable_with = extract_not_billable_with(raw_text, code)
        rule_type = infer_rule_type_from_features(
            raw_text,
            requires_pa,
            requires_report,
            (age_min, age_max),
            freq_count,
            pos_codes,
            not_billable_with,
        )
        conditions = {
            "frequency_rule": freq_rule,
            "parser": "heuristic_v1",
            "source_slug": source_slug,
        }

        section_label = entry.section[:250] if entry.section else None
        page = entry.page

        fee_sql = "null" if fee is None else f"{fee:.2f}"
        age_min_sql = "null" if age_min is None else str(age_min)
        age_max_sql = "null" if age_max is None else str(age_max)
        freq_count_sql = "null" if freq_count is None else str(freq_count)
        freq_period_sql = "null" if freq_period_months is None else str(freq_period_months)
        section_sql = "null" if section_label is None else f"'{esc(section_label)}'"
        page_sql = "null" if page is None else str(page)
        pos_sql = "null" if not pos_codes else "'{" + ",".join(pos_codes) + "}'::text[]"
        re_sql = "null" if not re_codes else "'{" + ",".join(re_codes) + "}'::text[]"
        excl_sql = (
            "null" if not not_billable_with else "'{" + ",".join(not_billable_with) + "}'::text[]"
        )
        cond_sql = esc(json.dumps(conditions))

        values.append(
            "("
            f"'{code}', "
            f"'{esc(payer)}', "
            f"'{esc(rule_type)}', "
            f"'{esc(rule_text)}', "
            f"{fee_sql}, "
            f"{age_min_sql}, "
            f"{age_max_sql}, "
            f"{freq_count_sql}, "
            f"{freq_period_sql}, "
            f"{'true' if requires_pa else 'false'}, "
            f"{'true' if requires_report else 'false'}, "
            f"{pos_sql}, "
            f"{re_sql}, "
            f"{excl_sql}, "
            f"{section_sql}, "
            f"{page_sql}, "
            f"'{cond_sql}'::jsonb"
            ")"
        )

    sql: list[str] = []
    sql.append("-- Auto-generated structured rules from NY Medicaid Dental 2026 PDF")
    sql.append("")
    sql.append(
        "create table if not exists public.cdt_payer_rules_structured ("
        " id bigserial primary key,"
        " code text not null references public.cdt_code_master(code) on delete cascade,"
        " payer_name text not null,"
        " rule_type text not null,"
        " rule_text text not null,"
        " fee numeric(10,2),"
        " age_min int,"
        " age_max int,"
        " frequency_count int,"
        " frequency_period_months int,"
        " requires_prior_auth boolean not null default false,"
        " requires_report boolean not null default false,"
        " allowed_pos_codes text[],"
        " restriction_exception_codes text[],"
        " not_billable_with_codes text[],"
        " section_label text,"
        " source_page int,"
        " conditions jsonb not null default '{}'::jsonb,"
        " source_id bigint references public.rule_sources(id) on delete cascade,"
        " created_at timestamptz not null default now(),"
        " unique (code, payer_name)"
        ");"
    )
    sql.append("")
    sql.append(
        "create index if not exists cdt_rules_structured_code_idx on public.cdt_payer_rules_structured(code);"
    )
    sql.append(
        "create index if not exists cdt_rules_structured_type_idx on public.cdt_payer_rules_structured(rule_type);"
    )
    sql.append(
        "create index if not exists cdt_rules_structured_pa_idx on public.cdt_payer_rules_structured(requires_prior_auth);"
    )
    sql.append("")
    sql.append("with src as (")
    sql.append(f"  select id from public.rule_sources where source_slug = '{source_slug}'")
    sql.append(")")
    sql.append(
        "insert into public.cdt_payer_rules_structured ("
        "code, payer_name, rule_type, rule_text, fee, age_min, age_max, frequency_count, frequency_period_months, "
        "requires_prior_auth, requires_report, allowed_pos_codes, restriction_exception_codes, not_billable_with_codes, "
        "section_label, source_page, conditions, source_id"
        ") values"
    )
    sql.append(",\n".join([v[:-1] + ", (select id from src))" for v in values]))
    sql.append(
        "on conflict (code, payer_name) do update set "
        "rule_type = excluded.rule_type, "
        "rule_text = excluded.rule_text, "
        "fee = excluded.fee, "
        "age_min = excluded.age_min, "
        "age_max = excluded.age_max, "
        "frequency_count = excluded.frequency_count, "
        "frequency_period_months = excluded.frequency_period_months, "
        "requires_prior_auth = excluded.requires_prior_auth, "
        "requires_report = excluded.requires_report, "
        "allowed_pos_codes = excluded.allowed_pos_codes, "
        "restriction_exception_codes = excluded.restriction_exception_codes, "
        "not_billable_with_codes = excluded.not_billable_with_codes, "
        "section_label = excluded.section_label, "
        "source_page = excluded.source_page, "
        "conditions = excluded.conditions, "
        "source_id = excluded.source_id;"
    )
    sql.append("")
    sql.append(
        "create or replace function public.match_cdt_rule("
        " p_code text,"
        " p_payer_name text,"
        " p_age int default null,"
        " p_pos_code text default null,"
        " p_check_pa boolean default false"
        ") returns table ("
        " code text,"
        " payer_name text,"
        " rule_type text,"
        " rule_text text,"
        " fee numeric,"
        " requires_prior_auth boolean,"
        " requires_report boolean,"
        " allowed_pos_codes text[],"
        " not_billable_with_codes text[]"
        ") language sql stable as $$"
        "  select"
        "    r.code,"
        "    r.payer_name,"
        "    r.rule_type,"
        "    r.rule_text,"
        "    r.fee,"
        "    r.requires_prior_auth,"
        "    r.requires_report,"
        "    r.allowed_pos_codes,"
        "    r.not_billable_with_codes"
        "  from public.cdt_payer_rules_structured r"
        "  where r.code = p_code"
        "    and lower(r.payer_name) = lower(p_payer_name)"
        "    and (p_age is null or (coalesce(r.age_min, -1) <= p_age and coalesce(r.age_max, 999) >= p_age))"
        "    and (p_pos_code is null or r.allowed_pos_codes is null or p_pos_code = any(r.allowed_pos_codes))"
        "    and (not p_check_pa or r.requires_prior_auth = true);"
        "$$;"
    )
    sql.append("")
    sql.append(
        "grant select on public.cdt_payer_rules_structured to service_role, authenticated, anon;"
    )
    sql.append(
        "grant execute on function public.match_cdt_rule(text, text, int, text, boolean) to service_role, authenticated, anon;"
    )

    return "\n".join(sql) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pdf_path = Path(r"c:\Users\ZT\Downloads\dental_policy_and_procedure_manual.pdf")
    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing source PDF: {pdf_path}")

    entries = parse_pdf(pdf_path)
    sql = build_sql(entries, pdf_path.name)
    out = root / "supabase" / "migrations" / "007_nys_medicaid_cdt_rules_structured.sql"
    out.write_text(sql, encoding="utf-8")
    print(f"Wrote {out} with {len(entries)} structured rule rows.")


if __name__ == "__main__":
    main()
