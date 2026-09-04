/**
 * JAX Health Check HTTP Server
 * 
 * Provides a lightweight HTTP endpoint for monitoring.
 * Hermes/OpenClaw both expose health endpoints for self-hosted monitoring.
 */

import http from 'http';
import { getHealthStatus } from './memory.mjs';

const HEALTH_PORT = parseInt(process.env.HEALTH_PORT || '9090');

export function startHealthServer() {
  const server = http.createServer((req, res) => {
    if (req.url === '/health' || req.url === '/') {
      const health = getHealthStatus();
      res.writeHead(health.status === 'healthy' ? 200 : 503, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(health, null, 2));
    } else if (req.url === '/ping') {
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('pong');
    } else {
      res.writeHead(404);
      res.end('Not Found');
    }
  });

  server.listen(HEALTH_PORT, '0.0.0.0', () => {
    console.log(`🏥 Health check server listening on http://0.0.0.0:${HEALTH_PORT}/health`);
  });

  server.on('error', (err) => {
    console.warn(`⚠️ Health server failed to start on port ${HEALTH_PORT}: ${err.message}`);
  });

  return server;
}
