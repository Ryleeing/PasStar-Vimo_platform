import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The mock layer imports ../fixtures/*.json so the frontend and the backend fake server share one source of truth.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, fs: { allow: ['..'] } },
});
