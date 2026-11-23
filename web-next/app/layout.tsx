import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const sans = Geist({ subsets: ["latin"], variable: "--font-geist" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: "Calibrain Harness",
  description: "Calibrain Harness: forward-test calibrated classification models (Kairos, Jev) on crypto perpetuals with Bybit Demo Trading or Testnet, and measure whether the model has an edge.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#0b0c0e" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
