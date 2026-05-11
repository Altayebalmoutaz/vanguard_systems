export type PayerEntry = {
  slug: string;
  displayName: string;
  shortName: string;
  brandColor: string;
  aliases: string[];
};

export const PAYER_CATALOG: PayerEntry[] = [
  { slug: "delta-dental", displayName: "Delta Dental", shortName: "Delta Dental", brandColor: "#005DAA", aliases: ["delta"] },
  { slug: "metlife", displayName: "MetLife", shortName: "MetLife", brandColor: "#0090DA", aliases: ["metlife"] },
  { slug: "cigna", displayName: "Cigna", shortName: "Cigna", brandColor: "#00A88E", aliases: ["cigna"] },
  { slug: "aetna", displayName: "Aetna", shortName: "Aetna", brandColor: "#7D3F98", aliases: ["aetna"] },
  { slug: "guardian", displayName: "Guardian", shortName: "Guardian", brandColor: "#002B5C", aliases: ["guardian"] },
  { slug: "united-healthcare", displayName: "UnitedHealthcare", shortName: "UnitedHealthcare", brandColor: "#002677", aliases: ["unitedhealth", "uhc", "united"] },
  { slug: "humana", displayName: "Humana", shortName: "Humana", brandColor: "#7AB800", aliases: ["humana"] },
  { slug: "anthem-bcbs", displayName: "Anthem BCBS", shortName: "Anthem", brandColor: "#003594", aliases: ["anthem", "bcbs", "bluecross", "blueshield"] },
  { slug: "principal", displayName: "Principal", shortName: "Principal", brandColor: "#0073CF", aliases: ["principal"] },
  { slug: "ameritas", displayName: "Ameritas", shortName: "Ameritas", brandColor: "#0033A0", aliases: ["ameritas"] },
  { slug: "sun-life", displayName: "Sun Life", shortName: "Sun Life", brandColor: "#FFB81C", aliases: ["sunlife", "assurant"] },
  { slug: "lincoln-financial", displayName: "Lincoln Financial", shortName: "Lincoln", brandColor: "#1B365D", aliases: ["lincoln"] },
  { slug: "mutual-of-omaha", displayName: "Mutual of Omaha", shortName: "Mutual of Omaha", brandColor: "#003A70", aliases: ["mutualofomaha", "mutual"] },
  { slug: "renaissance-dental", displayName: "Renaissance Dental", shortName: "Renaissance", brandColor: "#702F8A", aliases: ["renaissance"] },
  { slug: "beam-dental", displayName: "Beam Dental", shortName: "Beam", brandColor: "#00B5A1", aliases: ["beam"] },
  { slug: "careington", displayName: "Careington", shortName: "Careington", brandColor: "#E31837", aliases: ["careington"] },
  { slug: "dentaquest", displayName: "DentaQuest", shortName: "DentaQuest", brandColor: "#8DC63F", aliases: ["dentaquest"] },
  { slug: "envolve-dental", displayName: "Envolve Dental", shortName: "Envolve", brandColor: "#0085CA", aliases: ["envolve"] },
  { slug: "liberty-dental", displayName: "Liberty Dental", shortName: "Liberty", brandColor: "#C8102E", aliases: ["liberty"] },
  { slug: "solstice-benefits", displayName: "Solstice Benefits", shortName: "Solstice", brandColor: "#F47B20", aliases: ["solstice"] },
  { slug: "spirit-dental", displayName: "Spirit Dental", shortName: "Spirit", brandColor: "#005EB8", aliases: ["spirit"] },
];

const FALLBACK: PayerEntry = {
  slug: "unknown",
  displayName: "Unknown Payer",
  shortName: "Unknown",
  brandColor: "#64748B",
  aliases: [],
};

export function findPayer(label: string | null | undefined): PayerEntry {
  if (!label) return FALLBACK;
  const key = label.toLowerCase().replace(/[^a-z]/g, "");
  for (const payer of PAYER_CATALOG) {
    const slugKey = payer.slug.replace(/-/g, "");
    if (key.includes(slugKey)) return payer;
    for (const alias of payer.aliases) {
      if (key.includes(alias)) return payer;
    }
  }
  return { ...FALLBACK, displayName: label, shortName: label };
}

export function payerInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
  const word = parts[0] ?? "?";
  return word.slice(0, 2).toUpperCase();
}
