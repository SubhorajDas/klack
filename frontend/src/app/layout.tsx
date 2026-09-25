import type { Metadata } from 'next';
import { App } from '@/components/app';
import './globals.css';
export const metadata: Metadata = {
  title: 'Klack — Your team, in sync',
  description: 'A calmer place for your team to connect and collaborate.',
  robots: { index: false, follow: false },
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <App />
        {children}
      </body>
    </html>
  );
}
