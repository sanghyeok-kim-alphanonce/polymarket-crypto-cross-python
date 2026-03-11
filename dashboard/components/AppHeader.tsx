'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  BarChart3,
  BookOpen,
  Link2,
  Wallet,
  LineChart,
  FlaskConical,
  Crosshair,
  DollarSign,
} from 'lucide-react';

const navItems = [
  { href: '/', label: 'Dashboard', icon: BarChart3 },
  { href: '/analysis', label: 'Analysis', icon: LineChart },
  { href: '/crossing-analysis', label: 'Crossing', icon: Crosshair },
  { href: '/orderbooks', label: 'Orderbooks', icon: BookOpen },
  { href: '/chainlink-prices', label: 'Chainlink', icon: Link2 },
  { href: '/paper-trading', label: 'Paper Trading', icon: Wallet },
  { href: '/real-trading', label: 'Real Trading', icon: DollarSign },
  { href: '/backtest', label: 'Backtest', icon: FlaskConical },
];

export function AppHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="max-w-7xl mx-auto px-4">
        <div className="flex h-14 items-center justify-between">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2 group">
            <span className="font-semibold text-lg tracking-tight group-hover:text-purple-400 transition-colors">
              Polymarket
            </span>
          </Link>

          {/* Navigation */}
          <nav className="flex items-center gap-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href;

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all',
                    isActive
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-secondary'
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span className="hidden md:inline">{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
      </div>
    </header>
  );
}
