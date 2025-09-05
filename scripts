import { Telegraf } from 'telegraf';

const BOT_TOKEN = process.env.BOT_TOKEN!;
const VERCEL_URL = process.env.VERCEL_URL!;
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET!;

const bot = new Telegraf(BOT_TOKEN);

async function setWebhook() {
  try {
    const webhookUrl = `https://${VERCEL_URL}/api/bot`;
    await bot.telegram.setWebhook(webhookUrl, {
      secret_token: WEBHOOK_SECRET
    });
    console.log('Webhook set successfully:', webhookUrl);
  } catch (error) {
    console.error('Error setting webhook:', error);
    process.exit(1);
  }
}

setWebhook();
