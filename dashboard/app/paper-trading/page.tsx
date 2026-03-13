'use client';

import { useState } from 'react';
import HftTab from './components/HftTab';
import CrossingV2Tab from './components/CrossingV2Tab';
import CrossingLimitTab from './components/CrossingLimitTab';

type TabType = 'hft' | 'crossing_v2' | 'crossing_limit_5m' | 'crossing_limit_15m';

export default function PaperTradingPage() {
  const [activeTab, setActiveTab] = useState<TabType>('crossing_limit_15m');

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
        <div className="flex items-center gap-2 mb-6 border-b border-border pb-4 flex-wrap">
          <button
            onClick={() => setActiveTab('crossing_limit_15m')}
            className={`px-6 py-2.5 rounded-lg text-sm font-bold transition-colors ${
              activeTab === 'crossing_limit_15m'
                ? 'bg-cyan-600 text-white'
                : 'bg-secondary text-muted-foreground hover:bg-secondary/80'
            }`}
          >
            Cross Limit 15M
            <span className="ml-1 text-[10px] opacity-75">(max 10)</span>
          </button>
          <button
            onClick={() => setActiveTab('crossing_limit_5m')}
            className={`px-6 py-2.5 rounded-lg text-sm font-bold transition-colors ${
              activeTab === 'crossing_limit_5m'
                ? 'bg-purple-600 text-white'
                : 'bg-secondary text-muted-foreground hover:bg-secondary/80'
            }`}
          >
            Cross Limit 5M
            <span className="ml-1 text-[10px] opacity-75">(max 10)</span>
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
        </div>

        {/* Tab Content */}
        {activeTab === 'crossing_limit_15m' && (
          <CrossingLimitTab
            strategyName="paper_cross_limit_15m"
            timeframe="15m"
            candleMinutes={15}
            maxCount={10}
          />
        )}
        {activeTab === 'crossing_limit_5m' && (
          <CrossingLimitTab
            strategyName="paper_cross_limit_5m"
            timeframe="5m"
            candleMinutes={5}
            maxCount={10}
          />
        )}
        {activeTab === 'crossing_v2' && <CrossingV2Tab />}
        {activeTab === 'hft' && <HftTab />}
      </div>
    </div>
  );
}
