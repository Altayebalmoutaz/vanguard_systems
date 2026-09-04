export const PRACTICE_COOKIE = "smilesuites_practice_id";

export function practiceLabel(practiceId: string): string {
  switch (practiceId) {
    case "vgd_mock_brooklyn":
      return "Brooklyn (mock)";
    case "partner_clinic":
      return "Partner Clinic";
    default: {
      const human = practiceId.replace(/[_-]+/g, " ").trim();
      return human.replace(/\b\w/g, (char) => char.toUpperCase()) || practiceId;
    }
  }
}

export function resolveActivePracticeId(options: {
  cookiePracticeId?: string | null;
  allowedPracticeIds?: readonly string[];
  fallbackPracticeId?: string | null;
}): string {
  const cookie = (options.cookiePracticeId ?? "").trim();
  const fallback = (options.fallbackPracticeId ?? "").trim();
  const allowed = (options.allowedPracticeIds ?? []).map((id) => id.trim()).filter(Boolean);

  if (cookie && (allowed.length === 0 || allowed.includes(cookie))) {
    return cookie;
  }
  if (allowed.length === 1) {
    return allowed[0];
  }
  if (fallback && (allowed.length === 0 || allowed.includes(fallback))) {
    return fallback;
  }
  return allowed[0] ?? fallback;
}
