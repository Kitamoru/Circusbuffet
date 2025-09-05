import { VercelRequest, VercelResponse } from '@vercel/node';
import { Telegraf, Markup, session, Scenes, Context } from 'telegraf';
import { createClient } from '@supabase/supabase-js';
import { Update } from 'telegraf/typings/core/types/typegram';

const BOT_TOKEN = process.env.BOT_TOKEN!;
const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_ANON_KEY!;
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET!;

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

// Интерфейсы для типизации
interface Product {
  id: number;
  name: string;
  category: string;
  price: number;
  is_available: boolean;
}

// Базовый интерфейс для сессии
interface SessionData {
  orderId?: number;
}

// Интерфейс для сессии сцены
interface MySceneSession extends Scenes.SceneSessionData, SessionData {}

// Интерфейс для контекста бота
interface MyContext extends Context {
  session: SessionData;
  scene: Scenes.SceneContextScene<MyContext, MySceneSession>;
  match?: RegExpExecArray | null;
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

// Обработчики сцен
customerScene.enter(async (ctx: MyContext) => {
  await showMainMenu(ctx, 'Добро пожаловать в Popcorn Shop!');
});

customerScene.action(/add_(\d+)/, async (ctx: MyContext) => {
  try {
    if (!ctx.match || !ctx.from) {
      await ctx.answerCbQuery('Ошибка');
      return;
    }
    
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
    const { data: order, error: orderError } = await supabase
      .from('orders')
      .select('*')
      .eq('customer_id', ctx.from.id)
      .eq('status', 'cart')
      .single();

    let orderId = order?.id;
    
    if (!orderId) {
      const { data: newOrder, error: createError } = await supabase
        .from('orders')
        .insert({ customer_id: ctx.from.id })
        .select()
        .single();
      
      if (createError) {
        console.error('Error creating order:', createError);
        await ctx.answerCbQuery('Ошибка при создании заказа');
        return;
      }
      orderId = newOrder.id;
      ctx.session.orderId = orderId;
    }

    // Add item to order
    const { error: upsertError } = await supabase
      .from('order_items')
      .upsert({
        order_id: orderId,
        product_id: productId,
        quantity: 1,
        price_at_time: product.price
      }, { 
        onConflict: 'order_id,product_id',
        ignoreDuplicates: false
      });

    if (upsertError) {
      console.error('Error adding item:', upsertError);
      await ctx.answerCbQuery('Ошибка при добавлении товара');
      return;
    }

    await ctx.answerCbQuery(`${product.name} добавлен в корзину!`);
  } catch (error) {
    console.error('Error in add action:', error);
    await ctx.answerCbQuery('Произошла ошибка');
  }
});

// Seller scene handlers
sellerScene.action(/take_(\d+)/, async (ctx: MyContext) => {
  try {
    if (!ctx.match) return;
    
    const orderId = parseInt(ctx.match[1]);
    
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

// Обработчики основных команд
customerScene.action('show_menu', async (ctx: MyContext) => {
  try {
    const products = await getProducts();
    if (!products) {
      await ctx.answerCbQuery('Ошибка загрузки меню');
      return;
    }

    const buttons = products.map(product => [
      Markup.button.callback(
        `${product.name} - ${product.price} руб.`,
        `add_${product.id}`
      )
    ]);

    await ctx.editMessageText(
      'Выберите товар:',
      Markup.inlineKeyboard([...buttons, [Markup.button.callback('↩️ Назад', 'back_to_main')]])
    );
  } catch (error) {
    console.error('Error showing menu:', error);
    await ctx.answerCbQuery('Ошибка при загрузке меню');
  }
});

customerScene.action('show_cart', async (ctx: MyContext) => {
  await ctx.answerCbQuery('Функция корзины в разработке');
});

customerScene.action('show_orders', async (ctx: MyContext) => {
  await ctx.answerCbQuery('Функция заказов в разработке');
});

customerScene.action('back_to_main', async (ctx: MyContext) => {
  await ctx.scene.enter('customer');
});

const stage = new Scenes.Stage<MyContext>([customerScene, sellerScene]);
const bot = new Telegraf<MyContext>(BOT_TOKEN);

// Инициализация сессии
bot.use(session({ 
  defaultSession: () => ({}) 
}));
bot.use(stage.middleware());

bot.start(async (ctx: MyContext) => {
  if (!ctx.from) return;

  try {
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
      await ctx.scene.enter('seller');
    } else {
      await ctx.scene.enter('customer');
    }
  } catch (error) {
    console.error('Error in start command:', error);
    await ctx.reply('Произошла ошибка при запуске бота');
  }
});

async function showMainMenu(ctx: MyContext, message: string) {
  try {
    let cartCount = 0;
    
    if (ctx.from) {
      const { count, error } = await supabase
        .from('order_items')
        .select('*', { count: 'exact', head: true })
        .eq('order.customer_id', ctx.from.id)
        .eq('order.status', 'cart');

      if (!error) {
        cartCount = count || 0;
      }
    }

    await ctx.reply(
      message,
      Markup.inlineKeyboard([
        [Markup.button.callback('🍿 Заказать', 'show_menu')],
        [Markup.button.callback(`🛒 Корзина (${cartCount})`, 'show_cart')],
        [Markup.button.callback('📋 Мои заказы', 'show_orders')]
      ])
    );
  } catch (error) {
    console.error('Error showing main menu:', error);
    await ctx.reply('Произошла ошибка при загрузке меню');
  }
}

// Обработка ошибок
bot.catch((err: any, ctx: MyContext) => {
  console.error(`Error for ${ctx.updateType}:`, err);
});

export default async function handler(req: VercelRequest, res: VercelResponse) {
  try {
    if (req.method === 'POST') {
      // Проверка секретного токена
      if (req.headers['x-telegram-bot-api-secret-token'] !== WEBHOOK_SECRET) {
        console.log('Unauthorized webhook attempt');
        return res.status(401).send('Unauthorized');
      }

      console.log('Webhook received:', req.body);
      
      // Обработка обновления
      await bot.handleUpdate(req.body as Update, res);
    } else {
      // Для GET запросов просто возвращаем OK
      res.status(200).json({ status: 'OK', message: 'Bot is running' });
    }
  } catch (error) {
    console.error('Error handling request:', error);
    res.status(500).send('Error');
  }
}
