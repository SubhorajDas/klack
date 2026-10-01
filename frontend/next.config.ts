import type { NextConfig } from 'next';

const config: NextConfig = {
  devIndicators: false,
  output: 'standalone',
  experimental: { proxyClientMaxBodySize: '101mb' },
  distDir: process.env.KLACK_E2E === '1' ? '.next-e2e' : '.next',
  async rewrites() {
    return [
      {
        source: '/health/:path*',
        destination: `${process.env.API_ORIGIN || 'http://127.0.0.1:8000'}/health/:path*`,
      },
      {
        source: '/api/v1/:path*',
        destination: `${process.env.API_ORIGIN || 'http://127.0.0.1:8000'}/api/v1/:path*`,
      },
    ];
  },
};
export default config;
