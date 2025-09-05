import { Telegraf, Markup, session, Scenes, Context } from 'telegraf';
import { createClient } from '@supabase/supabase-js';
import { Update } from 'telegraf/typings/core/types/typegram';

const BOT_TOKEN = process.env.BOT_TOKEN!;
const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_ANON_KEY!;
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET!;

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

// Интерфейсы для типизации
interface SessionData {
  orderId?: number;
  state?: string;
}

interface Product {
  id: number;
  name: string;
  category: string;
  price: number;
  is_available: boolean;
}

// Расширяем контекст Telegraf
interface MyContext extends Context {
  session: SessionData;
  scene: Scenes.SceneContextScene<MyContext>;
}

// Product cache
let productCache: { data: Product[] | null; timestamp: number } = { 
  data: null, 
  timestamp: 0 
};
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes

const getProducts = async (): Promise<Product[] | null> => {
  const now = Date.now();
  if (productCache.data && now - productCache.timestamp < CACHE_DURATION) {
    return productCache.data;
  }

  const { data, error } = await supabase
    .from('products')
    .select('*')
    .eq('is_available', true);

  if (error) {
    console.error('Error fetching products:', error);
    return null;
  }

  productCache = { data, timestamp: now };
  return data;
};

// Scenes
const customerScene = new Scenes.BaseScene<MyContext>('customer');
const sellerScene = new Scenes.BaseScene<MyContext>('seller');

customerScene.enter(async (ctx: MyContext) => {
  await showMainMenu(ctx, 'Добро пожаловать в Popcorn Shop!');
});

customerScene.action(/add_(\d+)/, async (ctx: MyContext) => {
  if (!ctx.match || !ctx.from) return;
  
  const productId = parseInt(ctx.match[1]);
  const products = await getProducts();
  
  if (!products) {
    await ctx.answerCbQuery('Ошибка загрузки товаров');
    return;
  }
  
  const product = products.find(p => p.id === productId);
  
  if (!product) {
    await ctx.answerCbQuery('Товар не найден');
    return;
  }

  // Find or create cart
  const { data: order } = await supabase
    .from('orders')
    .select('*')
    .eq('customer_id', ctx.from.id)
    .eq('status', 'cart')
    .single();

  let orderId = order?.id;
  if (!orderId) {
    const { data: newOrder, error } = await supabase
      .from('orders')
      .insert({ customer_id: ctx.from.id })
      .select()
      .single();
    
    if (error) {
      console.error('Error creating order:', error);
      await ctx.answerCbQuery('Ошибка при создании заказа');
      return;
    }
    orderId = newOrder.id;
  }

  // Add item to order
  const { error } = await supabase
    .from('order_items')
    .upsert({
      order_id: orderId,
      product_id: productId,
      quantity: 1,
      price_at_time: product.price
    }, { onConflict: 'order_id,product_id' });

  if (error) {
    console.error('Error adding item:', error);
    await ctx.answerCbQuery('Ошибка при добавлении товара');
    return;
  }

  await ctx.answerCbQuery(`${product.name} добавлен в корзину!`);
});

// Seller scene handlers
sellerScene.action(/take_(\d+)/, async (ctx: MyContext) => {
  if (!ctx.match) return;
  
  const orderId = parseInt(ctx.match[1]);
  
  try {
    const { data: order, error } = await supabase
      .from('orders')
      .select('*')
      .eq('id', orderId)
      .eq('status', 'pending')
      .single();

    if (error || !order) {
      await ctx.answerCbQuery('Заказ уже взят в работу или не существует');
      return;
    }

    const { error: updateError } = await supabase
      .from('orders')
      .update({ status: 'preparing' })
      .eq('id', orderId);

    if (updateError) throw updateError;

    await ctx.editMessageText(
      `Заказ #${orderId} взят в работу!`,
      Markup.inlineKeyboard([
        [Markup.button.callback('✅ Готово', `ready_${orderId}`)],
        [Markup.button.callback('📦 Выдан', `complete_${orderId}`)]
      ])
    );
  } catch (error) {
    console.error('Error taking order:', error);
    await ctx.answerCbQuery('Ошибка при обработке заказа');
  }
});

const stage = new Scenes.Stage<MyContext>([customerScene, sellerScene]);

const bot = new Telegraf<MyContext>(BOT_TOKEN);

// Расширяем сессию
bot.use(session());
bot.use(stage.middleware());

bot.start(async (ctx: MyContext) => {
  if (!ctx.from) return;

  // Register user
  const { error } = await supabase
    .from('profiles')
    .upsert({
      user_id: ctx.from.id,
      username: ctx.from.username,
      full_name: `${ctx.from.first_name} ${ctx.from.last_name || ''}`.trim()
    });

  if (error) console.error('Error registering user:', error);

  // Check if seller
  const { data: profile } = await supabase
    .from('profiles')
    .select('role')
    .eq('user_id', ctx.from.id)
    .single();

  if (profile?.role?.startsWith('seller_')) {
    ctx.scene.enter('seller');
  } else {
    ctx.scene.enter('customer');
  }
});

async function showMainMenu(ctx: MyContext, message: string) {
  const { count } = await supabase
    .from('order_items')
    .select('*', { count: 'exact' })
    .eq('order_id', ctx.session.orderId || 0);

  const cartCount = count || 0;

  await ctx.reply(
    message,
    Markup.inlineKeyboard([
      [Markup.button.callback('🍿 Заказать', 'show_menu')],
      [Markup.button.callback(`🛒 Корзина (${cartCount})`, 'show_cart')],
      [Markup.button.callback('📋 Мои заказы', 'show_orders')]
    ])
  );
}

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method === 'POST') {
    if (req.headers['x-telegram-bot-api-secret-token'] !== WEBHOOK_SECRET) {
      return res.status(401).send('Unauthorized');
    }

    try {
      await bot.handleUpdate(req.body as Update, res);
    } catch (error) {
      console.error('Error handling update:', error);
      res.status(500).send('Error');
    }
  } else {
    res.status(200).send('Bot is running');
  }
}
