from quart import Quart, request, Response
from telegram import Update
from api.bot import application, WEBHOOK_SECRET

app = Quart(__name__)

@app.route('/', methods=['POST'])
async def webhook():
    if WEBHOOK_SECRET:
        token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
        if token != WEBHOOK_SECRET:
            return Response('Forbidden', status=403)

    try:
        data = await request.get_json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        print(f"Error processing update: {e}")
        return Response('Error', status=500)

    return Response('OK', status=200)

# Vercel требует, чтобы мы экспортировали Quart приложение под именем "app"
if __name__ == '__main__':
    app.run()
