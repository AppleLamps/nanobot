#!/usr/bin/env node
/**
 * nanobot WhatsApp Bridge
 * 
 * This bridge connects WhatsApp Web to nanobot's Python backend
 * via WebSocket. It handles authentication, message forwarding,
 * and reconnection logic.
 * 
 * Usage:
 *   npm run build && npm start
 *   
 * Or with custom settings:
 *   BRIDGE_PORT=3001 NANOBOT_PROFILE=jason npm start
 *   BRIDGE_PORT=3001 NANOBOT_DATA_DIR=~/.nanobot_jason npm start
 *   BRIDGE_PORT=3001 AUTH_DIR=~/.nanobot/whatsapp-auth npm start
 */

// Polyfill crypto for Baileys in ESM
import { webcrypto } from 'crypto';
if (!globalThis.crypto) {
  (globalThis as any).crypto = webcrypto;
}

import { BridgeServer } from './server.js';
import { homedir } from 'os';
import { join } from 'path';

const PORT = parseInt(process.env.BRIDGE_PORT || '3001', 10);

function resolveDataDir(): string {
  const override = (process.env.NANOBOT_DATA_DIR || '').trim();
  if (override) return override;

  const profile = (process.env.NANOBOT_PROFILE || '').trim();
  if (profile && !['default', 'main'].includes(profile.toLowerCase())) {
    const safe = profile.replace(/[^a-zA-Z0-9_-]/g, '_');
    if (safe) return join(homedir(), `.nanobot_${safe}`);
  }

  return join(homedir(), '.nanobot');
}

const AUTH_DIR = process.env.AUTH_DIR || join(resolveDataDir(), 'whatsapp-auth');

console.log('🐈 nanobot WhatsApp Bridge');
console.log('========================\n');

const server = new BridgeServer(PORT, AUTH_DIR);

// Handle graceful shutdown
process.on('SIGINT', async () => {
  console.log('\n\nShutting down...');
  await server.stop();
  process.exit(0);
});

process.on('SIGTERM', async () => {
  await server.stop();
  process.exit(0);
});

// Start the server
server.start().catch((error) => {
  console.error('Failed to start bridge:', error);
  process.exit(1);
});
