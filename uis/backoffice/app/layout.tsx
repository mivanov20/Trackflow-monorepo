import type { Metadata } from "next";
import "../styles.css";

export const metadata: Metadata = {
  title: "Operations desk | TrackFlow",
  description: "TrackFlow internal operations workspace demo with M2 analytics.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}