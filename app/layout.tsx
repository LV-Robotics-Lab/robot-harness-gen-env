import type { Metadata } from "next";
import { headers } from "next/headers";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("host") || "localhost:3000";
  const protocol =
    requestHeaders.get("x-forwarded-proto") ||
    (host.startsWith("localhost") ? "http" : "https");
  const metadataBase = new URL(`${protocol}://${host}`);
  const socialImage = new URL("/og.png", metadataBase).toString();

  return {
    metadataBase,
    title: "PEARL Self-Improving Agents",
    description:
      "An evidence portal for SceneAgent, Text2Env, anchored generation, asset import, Open X Sim, and the embodied harness system.",
    openGraph: {
      title: "PEARL Self-Improving Agents",
      description:
        "Five bounded reports, one auditable chain from scene intent to promotion decision.",
      type: "website",
      images: [
        {
          url: socialImage,
          width: 1536,
          height: 1024,
          alt: "PEARL Self-Improving Agents evidence portal",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "PEARL Self-Improving Agents",
      description:
        "Scene composition, anchored generation, autonomous asset import, cross-simulator reuse, and embodied harness evidence.",
      images: [socialImage],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
