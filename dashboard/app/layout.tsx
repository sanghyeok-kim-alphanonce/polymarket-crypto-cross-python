import type { Metadata } from 'next';
import './globals.css';
import { AppHeader } from '@/components/AppHeader';

export const metadata: Metadata = {
  title: 'Polymarket Dashboard',
  description: 'Polymarket Crypto Trading Dashboard',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-background antialiased">
        <AppHeader />
        <main>{children}</main>
      </body>
    </html>
  );
}
