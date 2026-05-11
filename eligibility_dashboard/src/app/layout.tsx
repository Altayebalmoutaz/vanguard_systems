import type { Metadata } from "next";
import { DM_Sans, Source_Code_Pro } from "next/font/google";
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
  title: "VanguardDental | Eligibility Agent",
  description: "Real-time eligibility verification dashboard for dental practices.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${dmSans.variable} ${sourceCodePro.variable}`}>{children}</body>
    </html>
  );
}
