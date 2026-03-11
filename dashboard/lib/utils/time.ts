/**
 * 시간 변환 유틸리티
 */

export type Timezone = 'UTC' | 'ET' | 'KST';

/**
 * 타임존별 포맷팅
 */
export function formatTimeByZone(date: Date, timezone: Timezone): string {
  const options: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: timezone === 'ET',
  };

  const tzMap = {
    UTC: 'UTC',
    ET: 'America/New_York',
    KST: 'Asia/Seoul',
  };

  return new Intl.DateTimeFormat('en-US', {
    ...options,
    timeZone: tzMap[timezone],
  }).format(date);
}

/**
 * 현재 시간을 모든 타임존으로
 */
export function getCurrentTimeAllZones() {
  const now = new Date();
  return {
    utc: formatTimeByZone(now, 'UTC'),
    et: formatTimeByZone(now, 'ET'),
    kst: formatTimeByZone(now, 'KST'),
  };
}
