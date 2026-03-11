'use client';

import { useState } from 'react';
import HftTab from './components/HftTab';
import CrossingV2Tab from './components/CrossingV2Tab';

type TabType = 'hft' | 'crossing_v2';

export default function PaperTradingPage() {
  const [activeTab, setActiveTab] = useState<TabType>('hft');

  return (
    <div className="min-h-screen p-4">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Paper Trading</h1>
          <div className="text-sm text-muted-foreground">
            Strategy Dashboard
          </div>
        </div>

        {/* Tab Selector */}
        <div className="flex items-center gap-2 mb-6 border-b border-border pb-4">
          <button
            onClick={() => setActiveTab('hft')}
            className={`px-6 py-2.5 rounded-lg text-sm font-bold transition-colors ${
              activeTab === 'hft'
                ? 'bg-blue-600 text-white'
                : 'bg-secondary text-muted-foreground hover:bg-secondary/80'
            }`}
          >
            HFT Strategy
          </button>
          <button
            onClick={() => setActiveTab('crossing_v2')}
            className={`px-6 py-2.5 rounded-lg text-sm font-bold transition-colors ${
              activeTab === 'crossing_v2'
                ? 'bg-orange-600 text-white'
                : 'bg-secondary text-muted-foreground hover:bg-secondary/80'
            }`}
          >
            Crossing V2
            <span className="ml-1 text-[10px] opacity-75">(unlimited)</span>
          </button>
        </div>

        {/* Tab Content */}
        {activeTab === 'hft' && <HftTab />}
        {activeTab === 'crossing_v2' && <CrossingV2Tab />}
      </div>
    </div>
  );
}
