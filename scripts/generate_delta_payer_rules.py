import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

CODE_RE = re.compile(r"\b(D\d{4})\b")
CODE_LINE_RE = re.compile(r"^(D\d{4})\b")
PAGE_MARK_RE = re.compile(r"--\s*(\d+)\s+of\s+\d+\s*--")
POLICY_HINT_RE = re.compile(
    r"(processed as|not billable to the patient|not billable|denied|deny|"
    r"allowed with|in conjunction with|frequency limitation|frequency limitations|"
    r"by report|documentation|narrative|radiograph|tele-?health|alternative benefit|"
    r"payable as|miscoded|prior authorization|prior approval)",
    re.IGNORECASE,
)
SKIP_LINE_RE = re.compile(
    r"^(Dentist Handbook|DeltaUSA Dentist Handbook|P a g e \||CDT\s*Code|ADA CDT Nomenclature|ADA CDT Descriptor|Delta Dental Policy)\b",
    re.IGNORECASE,
)


@dataclass
class DeltaEntry:
    code: str
    section: str | None = None
    page: int | None = None
    descriptor: str = ""
    policy_lines: list[str] = field(default_factory=list)


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().replace("’", "'")


def esc(value: str) -> str:
    return value.replace("'", "''")


def classify_rule(text: str) -> str:
    t = text.lower()
    if "processed as" in t or "payable as" in t:
        return "processed_as"
    if "not billable to the patient" in t:
        return "not_billable_to_patient"
    if "not billable" in t:
        return "billing_exclusion"
    if "denied" in t or "deny" in t:
        return "deny"
    if "alternative benefit" in t:
        return "alternative_benefit"
    if "prior authorization" in t or "prior approval" in t:
        return "prior_auth"
    if "frequency limitation" in t or "frequency limitations" in t:
        return "frequency_limit"
    if "documentation" in t or "narrative" in t or "radiograph" in t or "by report" in t:
        return "documentation_required"
    if "telehealth" in t or "tele-health" in t:
        return "telehealth_policy"
    if "in conjunction with" in t:
        return "bundling_rule"
    return "coverage_rule"


def parse_pdf(pdf_path: Path) -> dict[str, DeltaEntry]:
    reader = PdfReader(str(pdf_path))
    entries: dict[str, DeltaEntry] = {}
    current_page = 1
    current_section: str | None = None
    current_code: str | None = None
    in_cdt_section = False

    for page_idx, page in enumerate(reader.pages, start=1):
        current_page = page_idx
        text = page.extract_text() or ""
        lines = [clean_line(ln) for ln in text.splitlines()]
        for line in lines:
            if not line:
                continue
            if SKIP_LINE_RE.search(line):
                continue
            if PAGE_MARK_RE.search(line):
                continue

            if re.match(r"^[A-Z]\.\s+D\d{4}\s*-\s*D\d{4}", line):
                current_section = line
                in_cdt_section = True
                continue
            if re.match(r"^D\d{4}\s*-\s*D\d{4}", line):
                current_section = line
                in_cdt_section = True
                continue

            m = CODE_LINE_RE.match(line)
            if m and in_cdt_section:
                code = m.group(1)
                current_code = code
                descriptor = line.split(code, 1)[1].strip(" -:\t")
                entry = entries.get(code)
                if not entry:
                    entry = DeltaEntry(code=code, section=current_section, page=current_page)
                    entries[code] = entry
                if descriptor and len(descriptor) > len(entry.descriptor):
                    entry.descriptor = descriptor[:800]
                if POLICY_HINT_RE.search(line):
                    entry.policy_lines.append(line[:1800])
                continue

            if current_code and current_code in entries:
                if POLICY_HINT_RE.search(line):
                    entries[current_code].policy_lines.append(line[:1800])
                elif not entries[current_code].descriptor and 5 <= len(line) <= 200 and not CODE_RE.search(line):
                    entries[current_code].descriptor = line

    return entries


def build_sql(entries: dict[str, DeltaEntry], pdf_name: str) -> str:
    source_slug = "delta_dentist_handbook_2026"
    payer_name = "Delta Dental"
    source_title = "Delta Dental Dentist Handbook 2026"

    lines: list[str] = []
    lines.append("-- Auto-generated Delta Dental payer rules from 2026 Dentist Handbook")
    lines.append("")
    lines.append(
        "create table if not exists public.payer_rules ("
        " id bigserial primary key,"
        " payer_name text not null,"
        " payer_plan_scope text not null default 'model_policy',"
        " rule_type text not null,"
        " code text,"
        " transforms_to_code text,"
        " related_codes text[],"
        " rule_text text not null,"
        " conditions jsonb not null default '{}'::jsonb,"
        " contract_override_note boolean not null default false,"
        " source_id bigint references public.rule_sources(id) on delete set null,"
        " source_page int,"
        " evidence_text text,"
        " created_at timestamptz not null default now()"
        ");"
    )
    lines.append("")
    lines.append("create index if not exists payer_rules_code_idx on public.payer_rules(code);")
    lines.append("create index if not exists payer_rules_payer_type_idx on public.payer_rules(payer_name, rule_type);")
    lines.append("")
    lines.append(
        "insert into public.rule_sources (source_slug, title, payer_name, source_file, effective_date) values "
        f"('{source_slug}', '{esc(source_title)}', '{esc(payer_name)}', '{esc(pdf_name)}', '2026-01-01') "
        "on conflict (source_slug) do update set "
        "title = excluded.title, payer_name = excluded.payer_name, source_file = excluded.source_file, effective_date = excluded.effective_date;"
    )
    lines.append("")

    values = []
    for code in sorted(entries):
        e = entries[code]
        unique_policies: list[str] = []
        seen = set()
        for p in e.policy_lines:
            key = p.lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique_policies.append(p)
        if not unique_policies and e.descriptor:
            unique_policies = [f"{code} {e.descriptor}"]

        for pol in unique_policies[:8]:
            related = sorted(set(c for c in CODE_RE.findall(pol) if c != code))
            processed = re.search(r"(?:processed as|payable as)\s+(D\d{4})", pol, flags=re.IGNORECASE)
            transforms_to = processed.group(1) if processed else None
            cond = {
                "section": e.section,
                "source_slug": source_slug,
                "contract_terms_precedence": True,
                "parser": "delta_heuristic_v1",
            }
            rule_type = classify_rule(pol)
            values.append(
                "("
                f"'{esc(payer_name)}', "
                "'model_policy', "
                f"'{rule_type}', "
                f"'{code}', "
                f"{'null' if transforms_to is None else f"'{transforms_to}'"}, "
                f"{'null' if not related else '\'{' + ','.join(related) + '}\'::text[]'}, "
                f"'{esc(pol[:2400])}', "
                f"'{esc(json.dumps(cond))}'::jsonb, "
                "true, "
                "(select id from public.rule_sources where source_slug = 'delta_dentist_handbook_2026'), "
                f"{'null' if e.page is None else e.page}, "
                f"'{esc(pol[:2400])}'"
                ")"
            )

    if values:
        lines.append(
            "insert into public.payer_rules ("
            "payer_name, payer_plan_scope, rule_type, code, transforms_to_code, related_codes, rule_text, conditions, "
            "contract_override_note, source_id, source_page, evidence_text"
            ") values"
        )
        lines.append(",\n".join(values))
        lines.append(";")

    lines.append("")
    lines.append("-- Agent-specific scoped views")
    lines.append(
        "create or replace view public.v_rules_for_coding_agent as "
        "select * from public.payer_rules "
        "where rule_type in ('processed_as', 'coverage_rule', 'frequency_limit', 'telehealth_policy');"
    )
    lines.append(
        "create or replace view public.v_rules_for_preauth_agent as "
        "select * from public.payer_rules "
        "where rule_type in ('prior_auth', 'documentation_required');"
    )
    lines.append(
        "create or replace view public.v_rules_for_scrubber_agent as "
        "select * from public.payer_rules "
        "where rule_type in ('billing_exclusion', 'deny', 'bundling_rule', 'not_billable_to_patient');"
    )
    lines.append(
        "create or replace view public.v_rules_for_estimation_agent as "
        "select * from public.payer_rules "
        "where rule_type in ('not_billable_to_patient', 'alternative_benefit', 'processed_as');"
    )
    lines.append(
        "create or replace view public.v_rules_for_appeals_agent as "
        "select * from public.payer_rules;"
    )
    lines.append("")
    lines.append("grant select on public.payer_rules to service_role, authenticated, anon;")
    lines.append("grant select on public.v_rules_for_coding_agent to service_role, authenticated, anon;")
    lines.append("grant select on public.v_rules_for_preauth_agent to service_role, authenticated, anon;")
    lines.append("grant select on public.v_rules_for_scrubber_agent to service_role, authenticated, anon;")
    lines.append("grant select on public.v_rules_for_estimation_agent to service_role, authenticated, anon;")
    lines.append("grant select on public.v_rules_for_appeals_agent to service_role, authenticated, anon;")

    return "\n".join(lines) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pdf_path = Path(r"c:\Users\ZT\Downloads\Telegram Desktop\Delta Dental Dentist Handbook 2026.pdf")
    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing source PDF: {pdf_path}")

    entries = parse_pdf(pdf_path)
    out = root / "supabase" / "migrations" / "009_delta_dentist_handbook_rules.sql"
    out.write_text(build_sql(entries, pdf_path.name), encoding="utf-8")
    print(f"Wrote {out} with {len(entries)} CDT codes parsed.")


if __name__ == "__main__":
    main()
