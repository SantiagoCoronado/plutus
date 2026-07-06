import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // backend runs on host port 8800 (see Makefile `make api` / docker-compose)
      '/api': 'http://localhost:8800',
      '/health': 'http://localhost:8800',
      // live-quote websocket (ws:true upgrades the proxied connection)
      '/ws': { target: 'http://localhost:8800', ws: true },
    },
  },
})
