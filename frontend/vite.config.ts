import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// 포트는 환경변수로 덮어쓴다. 병렬 worktree 에서 창마다 다른 포트를 써야
// 하기 때문이다 (창 A 3000/8000, 창 B 3001/8001 ...).
// strictPort 라 포트가 겹치면 조용히 옮겨가지 않고 즉시 실패한다 — 두 창이
// 같은 백엔드를 보고 있는 상태를 눈치 못 채는 것보다 낫다.
const FE_PORT = Number(process.env.PFP_FE_PORT ?? 3000)
const BE_PORT = Number(process.env.PFP_BE_PORT ?? 8000)

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: FE_PORT,
    host: '0.0.0.0',     // LAN 접근 허용
    strictPort: true,
    // HMR WebSocket: 브라우저가 페이지를 로드한 IP:port를 그대로 사용하도록 설정
    // LAN에서 접속할 때 'localhost'로 HMR이 연결을 시도해 빈 화면이 뜨는 버그 방지
    hmr: {
      clientPort: FE_PORT,  // 브라우저의 WS 연결 포트 = 페이지 서빙 포트와 동일
    },
    proxy: {
      // /api/* 를 백엔드로 중계 — LAN에서 포트 8000 직접 노출 불필요
      '/api': {
        target: `http://127.0.0.1:${BE_PORT}`,
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
          firebase: ['firebase/app', 'firebase/auth'],
        },
      },
    },
  },
})
