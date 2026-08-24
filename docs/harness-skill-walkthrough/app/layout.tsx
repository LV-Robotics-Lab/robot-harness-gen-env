import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Robot Harness · 三个 Skill Walkthrough",
  description: "从一句输入到可发布环境：Text2Env compile、replay、validate 的互动式项目组导览。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
