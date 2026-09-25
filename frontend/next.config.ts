import type { NextConfig } from 'next';

const config: NextConfig = {
  devIndicators: false,
  distDir: process.env.KLACK_E2E === '1' ? '.next-e2e' : '.next',
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.API_ORIGIN || 'http://127.0.0.1:8000'}/api/v1/:path*`,
      },
    ];
  },
};
export default config;
