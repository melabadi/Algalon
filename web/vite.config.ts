import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { mockApiPlugin } from './mock/server';

export default defineConfig(({ command, mode }) => {
  const environment = loadEnv(mode, process.cwd(), 'VITE_');
  const useMockApi = command === 'serve' && mode === 'mock';
  return {
    plugins: [react(), ...(useMockApi ? [mockApiPlugin()] : [])],
    server: {
      proxy: useMockApi ? undefined : {
        '/api': environment.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
      }
    }
  };
});