import type { Metadata } from "next";
import { DM_Sans, Source_Code_Pro } from "next/font/google";
import { DashboardChrome } from "@/components/DashboardChrome";
import "./globals.css";

const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500"],
  variable: "--font-dm-sans",
});

const sourceCodePro = Source_Code_Pro({
  subsets: ["latin"],
  weight: ["500"],
  variable: "--font-source-code-pro",
});

export const metadata: Metadata = {
  title: "Vanguard MD | Revenue Cycle Platform",
  description:
    "AI-powered revenue cycle management: eligibility, coding, prior authorization, claims, and denials in one platform.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${dmSans.variable} ${sourceCodePro.variable}`}>
        <div className="min-h-screen">
          <DashboardChrome>{children}</DashboardChrome>
        </div>
      </body>
    </html>
  );
}
