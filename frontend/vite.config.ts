import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import preprocess from 'svelte-preprocess'

export default defineConfig({
  plugins: [svelte({ preprocess: preprocess() })],
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api':      { target: 'http://localhost:8000', changeOrigin: true, rewrite: p => p.replace(/^\/api/, '') },
      '/ws':       { target: 'ws://localhost:8000',   ws: true },
      '/system':     'http://localhost:8000',
      '/settings':   'http://localhost:8000',
      '/voice':      'http://localhost:8000',
      '/rig':        'http://localhost:8000',
      '/kinematics': 'http://localhost:8000',
      '/robot':      'http://localhost:8000',
      '/teleop':     'http://localhost:8000',
      '/camera':     'http://localhost:8000',
      '/vision':     'http://localhost:8000',
      '/recordings': 'http://localhost:8000',
      '/commands':   'http://localhost:8000',
      '/arcade':     'http://localhost:8000',
      '/pick':       'http://localhost:8000',
      '/directives': 'http://localhost:8000',
      '/poses':      'http://localhost:8000',
      '/sounds':     'http://localhost:8000',
      '/rules':      'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
