import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Binance API for BTC (Polymarket uses Binance BTCUSDT)
const BINANCE_API = 'https://api.binance.com/api/v3';

// Chainlink Data Streams Feed IDs
// From https://data.chain.link/streams
const FEED_IDS = {
  btc: '0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8', // BTC/USD
  eth: '0x000359843a543ee2fe414dc14c7e7920ef10f4372990b79d6361cdc0dd1ba782', // ETH/USD
  sol: '0x0003b778d3f6b2ac4991302b89cb313f99a42467d6c9c5f96f57c29c0d2bc24f', // SOL/USD
  xrp: '0x0003c16c6aed42294f5cb4741f6e59ba2d728f0eae2eb9e6d3f555808c59fc45', // XRP/USD
};

interface ChainlinkResponse {
  data: {
    liveStreamReports: {
      nodes: Array<{
        validFromTimestamp: string;
        price: string;
        bid: string;
        ask: string;
      }>;
    };
  };
}

async function getBinancePrice(symbol: string): Promise<number | null> {
  try {
    const response = await fetch(`${BINANCE_API}/ticker/price?symbol=${symbol}`, {
      next: { revalidate: 0 }, // No cache
    });

    if (!response.ok) {
      console.error(`Binance API error for ${symbol}:`, response.status);
      return null;
    }

    const data = await response.json();
    return parseFloat(data.price);
  } catch (error) {
    console.error(`Error fetching Binance price for ${symbol}:`, error);
    return null;
  }
}

async function getChainlinkPrice(feedId: string): Promise<number | null> {
  try {
    const query = 'LIVE_STREAM_REPORTS_QUERY';
    const variables = JSON.stringify({ feedId });
    const url = `https://data.chain.link/api/query-timescale?query=${query}&variables=${encodeURIComponent(variables)}`;

    const response = await fetch(url, {
      next: { revalidate: 0 }, // No cache
    });

    if (!response.ok) {
      console.error(`Chainlink API error for ${feedId}:`, response.status);
      return null;
    }

    const data: ChainlinkResponse = await response.json();
    const latestReport = data.data.liveStreamReports.nodes[0];

    if (!latestReport) {
      return null;
    }

    // Chainlink price has 18 decimals, convert to USD
    const price = parseFloat(latestReport.price) / 1e18;
    return price;
  } catch (error) {
    console.error(`Error fetching Chainlink price for ${feedId}:`, error);
    return null;
  }
}

export async function GET() {
  try {
    // Fetch all prices in parallel
    const [
      btcBinance,
      btcChainlink,
      ethBinance,
      ethChainlink,
      solBinance,
      solChainlink,
      xrpBinance,
      xrpChainlink,
    ] = await Promise.all([
      getBinancePrice('BTCUSDT'),
      getChainlinkPrice(FEED_IDS.btc),
      getBinancePrice('ETHUSDT'),
      getChainlinkPrice(FEED_IDS.eth),
      getBinancePrice('SOLUSDT'),
      getChainlinkPrice(FEED_IDS.sol),
      getBinancePrice('XRPUSDT'),
      getChainlinkPrice(FEED_IDS.xrp),
    ]);

    const prices = {
      btc: {
        binance: btcBinance,
        chainlink: btcChainlink,
        spread: btcBinance && btcChainlink ? btcChainlink - btcBinance : null,
        spreadPercent: btcBinance && btcChainlink ? ((btcChainlink - btcBinance) / btcBinance) * 100 : null,
      },
      eth: {
        binance: ethBinance,
        chainlink: ethChainlink,
        spread: ethBinance && ethChainlink ? ethChainlink - ethBinance : null,
        spreadPercent: ethBinance && ethChainlink ? ((ethChainlink - ethBinance) / ethBinance) * 100 : null,
      },
      sol: {
        binance: solBinance,
        chainlink: solChainlink,
        spread: solBinance && solChainlink ? solChainlink - solBinance : null,
        spreadPercent: solBinance && solChainlink ? ((solChainlink - solBinance) / solBinance) * 100 : null,
      },
      xrp: {
        binance: xrpBinance,
        chainlink: xrpChainlink,
        spread: xrpBinance && xrpChainlink ? xrpChainlink - xrpBinance : null,
        spreadPercent: xrpBinance && xrpChainlink ? ((xrpChainlink - xrpBinance) / xrpBinance) * 100 : null,
      },
    };

    return NextResponse.json({
      prices,
      timestamp: Date.now(),
      source: 'binance + chainlink',
    });
  } catch (error) {
    console.error('Failed to fetch prices:', error);
    return NextResponse.json(
      { error: 'Failed to load prices' },
      { status: 500 }
    );
  }
}
