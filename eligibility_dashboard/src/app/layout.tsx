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
  title: "ezFi | Eligibility Agent",
  description:
    "AI-powered dental eligibility verification: queue, multi-modal checks, OpenDental sync, and exception review.",
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
