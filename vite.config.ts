import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import macros from 'unplugin-parcel-macros';

// https://vite.dev/config/
export default defineConfig({
    plugins: [
        macros.vite(), // Must be first!
        react()
    ],

    base: '/vibetrader/',
    //base: '/',

    server: {
        host: '0.0.0.0',
        proxy: {
            '/api': {
                target: 'http://127.0.0.1:8000',
                changeOrigin: true,
                secure: false,
            }
            ,
            '/binance-us': {
                target: 'https://api.binance.us',
                changeOrigin: true,
                secure: false,
                rewrite: (path) => path.replace(/^\/binance-us/, ''),
            },
            '/binance': {
                target: 'https://api.binance.com',
                changeOrigin: true,
                secure: false,
                rewrite: (path) => path.replace(/^\/binance/, ''),
            }
        }
    },

    build: {
        target: ['es2022'],
        // Lightning CSS produces a much smaller CSS bundle than the default minifier.
        cssMinify: 'lightningcss',
        sourcemap: true,
        minify: true, // TODO, transpile 'minified' indicator code correctly.
        rollupOptions: {
            output: {
                // Bundle all S2 and style-macro generated CSS into a single bundle instead of code splitting.
                // Because atomic CSS has so much overlap between components, loading all CSS up front results in
                // smaller bundles instead of producing duplication between pages.
                manualChunks(id) {
                    if (/macro-(.*)\.css$/.test(id) || /@react-spectrum\/s2\/.*\.css$/.test(id)) {
                        return 's2-styles';
                    }
                }
            }
        }
    },
})
