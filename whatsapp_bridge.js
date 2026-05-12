/**
 * E.D.I.T.H. WhatsApp Bridge
 * Connects WhatsApp Web to the EDITH Flask backend.
 * Usage: node whatsapp_bridge.js
 * Then scan the QR code with your WhatsApp mobile app.
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

// ── Config ────────────────────────────────────────────────────────────────
const EDITH_URL = process.env.EDITH_URL || 'http://localhost:7420';
const AUTHORIZED_NUMBER = process.env.EDITH_AUTHORIZED_NUMBER || ''; // Optional: set your number e.g. '919876543210@c.us'
const BOT_PREFIX = process.env.EDITH_BOT_PREFIX || '';              // Optional: require prefix like '!'

// ── WhatsApp Client ───────────────────────────────────────────────────────
const client = new Client({
    authStrategy: new LocalAuth({ clientId: 'edith-bot' }),
    puppeteer: {
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

// ── QR Code ───────────────────────────────────────────────────────────────
client.on('qr', (qr) => {
    axios.post(`${EDITH_URL}/api/whatsapp/status`, { status: 'qr' }).catch(() => {});
    console.log('\n╔══════════════════════════════════════════════╗');
    console.log('║   E.D.I.T.H. WhatsApp — Scan QR to Connect  ║');
    console.log('╚══════════════════════════════════════════════╝\n');
    qrcode.generate(qr, { small: true });
    console.log('\nOpen WhatsApp → Linked Devices → Link a Device → Scan the QR above\n');
});

// ── Ready ─────────────────────────────────────────────────────────────────
client.on('ready', async () => {
    console.log('✅ E.D.I.T.H. WhatsApp Bridge ONLINE');
    console.log(`🤖 Connected as: ${client.info.wid.user}`);
    console.log(`📡 EDITH backend: ${EDITH_URL}`);
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

    // Notify EDITH is online
    try {
        await axios.post(`${EDITH_URL}/api/whatsapp/status`, { status: 'online' });
    } catch (e) { /* ignore */ }
});

// ── Auth Failure ──────────────────────────────────────────────────────────
client.on('auth_failure', msg => {
    console.error('❌ Authentication failed:', msg);
    console.log('Delete the .wwebjs_auth folder and restart to re-scan QR.');
    axios.post(`${EDITH_URL}/api/whatsapp/status`, { status: 'auth_failure', error: String(msg || '') }).catch(() => {});
});

client.on('disconnected', (reason) => {
    console.log('⚠️  WhatsApp disconnected:', reason);
    axios.post(`${EDITH_URL}/api/whatsapp/status`, { status: 'disconnected', error: String(reason || '') }).catch(() => {});
    console.log('Reconnecting in 5 seconds...');
    setTimeout(() => client.initialize(), 5000);
});

// ── Message Handler ───────────────────────────────────────────────────────
client.on('loading_screen', (percent) => {
    if (percent === 100 || percent % 25 === 0) {
        axios.post(`${EDITH_URL}/api/whatsapp/status`, { status: `loading ${percent}%` }).catch(() => {});
    }
});

client.on('message', async (msg) => {
    // Skip group messages (only respond to DMs)
    if (msg.from && msg.from.endsWith('@g.us')) return;

    // Skip status updates
    if (msg.from === 'status@broadcast') return;

    // Authorization check
    if (AUTHORIZED_NUMBER && msg.from !== AUTHORIZED_NUMBER) {
        console.log(`⛔ Blocked message from unauthorized: ${msg.from}`);
        return;
    }

    // Prefix check
    if (BOT_PREFIX && !msg.body.startsWith(BOT_PREFIX)) return;

    const text = msg.body.replace(BOT_PREFIX, '').trim();
    if (!text) return;

    console.log(`📩 WhatsApp message from ${msg.from}: "${text}"`);

    // Show typing indicator
    const chat = await msg.getChat();
    await chat.sendStateTyping();

    try {
        let reply = '';

        // Check if it's a system command first
        const systemCommands = [
            'open', 'close', 'run ', 'screenshot', 'volume', 'shutdown',
            'restart', 'translate', 'weather', 'ip address', 'flip a coin',
            'roll a dice', 'battery', 'time', 'date', 'location'
        ];

        const isSystemCmd = systemCommands.some(cmd => text.toLowerCase().includes(cmd));

        if (isSystemCmd) {
            // Route to Jarvis system commands
            const res = await axios.post(`${EDITH_URL}/api/jarvis_cmd`, {
                command: text
            }, { timeout: 30000 });

            const data = res.data;
            if (data.response) {
                reply = data.response;
            } else if (data.unhandled) {
                // Fall through to AI
                reply = await askAI(text, msg.from);
            } else if (data.error) {
                reply = `⚠️ Error: ${data.error}`;
            }
        } else {
            // Route to AI (Ollama)
            reply = await askAI(text, msg.from);
        }

        if (!reply) reply = "I didn't understand that. Try again.";

        // Send reply
        await msg.reply(`🤖 ${reply}`);
        console.log(`📤 Replied: "${reply.substring(0, 60)}..."`);

    } catch (err) {
        console.error('Error processing message:', err.message);
        await msg.reply('⚠️ E.D.I.T.H. backend is offline. Please restart edith.py');
    }
});

// ── AI Query Helper ───────────────────────────────────────────────────────
async function askAI(question, sender) {
    try {
        const res = await axios.post(`${EDITH_URL}/api/whatsapp`, {
            message: question,
            sender: sender
        }, { timeout: 60000 });

        return res.data.reply || 'No response from AI.';
    } catch (err) {
        console.error('AI Error:', err.message);
        return 'AI backend is unreachable. Make sure edith.py is running.';
    }
}

// ── Start ─────────────────────────────────────────────────────────────────
console.log('🚀 Starting E.D.I.T.H. WhatsApp Bridge...');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
client.initialize();
