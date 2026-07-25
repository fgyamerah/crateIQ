import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
    // CRATEIQ_API_PROXY_TARGET (shell env or .env file) lets local service
    // helpers point the dev proxy at a non-default backend port, e.g.
    // http://127.0.0.1:8020. Defaults preserve the original behavior.
    const env = loadEnv(mode, '.', 'CRATEIQ_');
    const apiProxyTarget = env.CRATEIQ_API_PROXY_TARGET || 'http://localhost:8000';
    return {
        plugins: [react()],
        server: {
            port: 5173,
            proxy: {
                // All /api/* requests are forwarded to the FastAPI backend.
                '/api': {
                    target: apiProxyTarget,
                    changeOrigin: true,
                },
            },
        },
    };
});
