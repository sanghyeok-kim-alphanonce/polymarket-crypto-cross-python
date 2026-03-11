'use client';

import { useEffect, useState } from 'react';
import { getCurrentTimeAllZones } from '@/lib/utils/time';

export function TimezoneDisplay({ compact = false }: { compact?: boolean }) {
  const [times, setTimes] = useState<ReturnType<typeof getCurrentTimeAllZones> | null>(null);

  useEffect(() => {
    setTimes(getCurrentTimeAllZones());
    const interval = setInterval(() => {
      setTimes(getCurrentTimeAllZones());
    }, 1000);

    return () => clearInterval(interval);
  }, []);

  if (compact) {
    return (
      <div className="flex items-center gap-4 text-sm">
        <span><span className="text-gray-500">ET</span> <span className="font-mono text-blue-400">{times?.et ?? '--'}</span></span>
        <span><span className="text-gray-500">KST</span> <span className="font-mono text-green-400">{times?.kst ?? '--'}</span></span>
        <span><span className="text-gray-500">UTC</span> <span className="font-mono text-purple-400">{times?.utc ?? '--'}</span></span>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 p-6 rounded-lg border border-gray-800">
      <h2 className="text-xl font-semibold mb-4">Current Time</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-gray-800 p-4 rounded-lg">
          <div className="text-gray-400 text-sm mb-1">Eastern Time (ET)</div>
          <div className="text-2xl font-mono font-bold text-blue-400">{times?.et ?? '--'}</div>
        </div>

        <div className="bg-gray-800 p-4 rounded-lg">
          <div className="text-gray-400 text-sm mb-1">Korea Standard Time (KST)</div>
          <div className="text-2xl font-mono font-bold text-green-400">{times?.kst ?? '--'}</div>
        </div>

        <div className="bg-gray-800 p-4 rounded-lg">
          <div className="text-gray-400 text-sm mb-1">Coordinated Universal Time (UTC)</div>
          <div className="text-2xl font-mono font-bold text-purple-400">{times?.utc ?? '--'}</div>
        </div>
      </div>
    </div>
  );
}
