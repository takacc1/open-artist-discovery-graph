import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

const title = "Open Artist Discovery — 好きの交差点から次の一組へ";
const description = "複数の好きなアーティストを重ね、理由と信頼度を確かめながら次の音楽を探す、透明なアーティスト推薦サービス。";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const base = new URL(`${protocol}://${host}`);
  const socialImage = new URL("/og.png", base).toString();

  return {
    metadataBase: base,
    title,
    description,
    openGraph: { title, description, type: "website", images: [{ url: socialImage, width: 1200, height: 630 }] },
    twitter: { card: "summary_large_image", title, description, images: [socialImage] },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
