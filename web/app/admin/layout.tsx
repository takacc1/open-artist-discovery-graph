import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "アンケート管理 — Open Artist Discovery",
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
