import type { Metadata } from "next";
import "./globals.css";
import DeploymentRefresh from "./_components/DeploymentRefresh";
import PreviewBanner from "./_components/PreviewBanner";

export const metadata: Metadata = {
  title: "Aurum Signal Room",
  description: "XAUUSD Forward-only shadow intelligence dashboard",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body><DeploymentRefresh /><PreviewBanner />{children}</body></html>;
}
