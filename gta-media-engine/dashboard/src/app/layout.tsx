import type { Metadata } from "next";
import "./globals.css";
import Shell from "@/components/Shell";
import { ToastProvider } from "@/components/ui";

export const metadata: Metadata = {
  title: "VI Media Engine",
  description: "Control room for the autonomous GTA VI short-form media operation",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ToastProvider>
          <Shell>{children}</Shell>
        </ToastProvider>
      </body>
    </html>
  );
}
