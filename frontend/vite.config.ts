import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    host: true,          // 0.0.0.0 — LAN 접근 허용
    strictPort: true,    // 포트 충돌 시 조용히 다른 포트 사용 금지
    proxy: {
      // /api/* 를 백엔드로 중계 — LAN에서 포트 8000 직접 노출 불필요
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        timeout: 300000,   // 긴 API 응답(스캔 등) 타임아웃 방지
        proxyTimeout: 300000,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,    // 프로덕션 빌드 — 소스맵 비공개
    rollupOptions: {
      output: {
        // 청크 분리로 초기 로드 최적화
        manualChunks: {
          vendor: ['react', 'react-dom'],
          charts: ['recharts'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
})
