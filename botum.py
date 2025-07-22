# Gerekli kütüphaneleri içe aktarıyoruz.
# Bu kütüphaneyi yüklemek için: pip install "python-telegram-bot[persistence]" pytz
import logging
import uuid
import os
import pytz
from datetime import time as dt_time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    PicklePersistence,
)

# Botunuzun token'ını ortam değişkeninden alın veya buraya yapıştırın.
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')

# Türkiye saat dilimini tanımlıyoruz.
TURKISH_TIMEZONE = pytz.timezone("Europe/Istanbul")

# Hata ayıklama için loglamayı etkinleştiriyoruz.
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Sohbet adımları için sabitler tanımlıyoruz.
GET_CONTENT, GET_BUTTONS, GET_RECURRING_CHANNEL, GET_RECURRING_DAYS, GET_RECURRING_TIME, CONFIRM_RECURRING_SCHEDULE = range(6)


# --- YARDIMCI FONKSİYONLAR ---

def parse_turkish_days(day_string: str) -> tuple[int, ...] | None:
    """Türkçe gün isimlerini (Pazartesi, Salı vb.) sayılara (0, 1 vb.) çevirir."""
    day_map = {
        "pazartesi": 0, "pzt": 0, "monday": 0,
        "salı": 1, "sal": 1, "tuesday": 1,
        "çarşamba": 2, "çar": 2, "wednesday": 2,
        "perşembe": 3, "per": 3, "thursday": 3,
        "cuma": 4, "cum": 4, "friday": 4,
        "cumartesi": 5, "cmt": 5, "saturday": 5,
        "pazar": 6, "paz": 6, "sunday": 6,
    }
    days = day_string.lower().split(',')
    day_numbers = []
    for day in days:
        day = day.strip()
        if day in day_map:
            day_numbers.append(day_map[day])
        else:
            return None
    return tuple(sorted(list(set(day_numbers))))


# --- GÖNDERİ GÖNDERME FONKSİYONU ---

async def send_scheduled_content(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Zamanı geldiğinde hafızadaki parçalardan gönderiyi inşa edip yollar."""
    job = context.job
    post_data = job.data

    if not post_data:
        logger.warning(f"İş {job.name} için gönderi verisi bulunamadı.")
        return

    channel_id = post_data.get('channel_id')
    text = post_data.get('text')
    photo_file_id = post_data.get('photo_file_id')
    
    entities = [MessageEntity.de_json(e, context.bot) for e in post_data.get('entities', [])]
    reply_markup = InlineKeyboardMarkup.de_json(post_data.get('reply_markup'), context.bot) if post_data.get('reply_markup') else None

    try:
        if photo_file_id:
            await context.bot.send_photo(
                chat_id=channel_id,
                photo=photo_file_id,
                caption=text,
                caption_entities=entities,
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=channel_id,
                text=text,
                entities=entities,
                reply_markup=reply_markup
            )
        logger.info(f"Gönderi {channel_id} kanalına başarıyla gönderildi.")
    except Exception as e:
        logger.error(f"{channel_id} kanalına gönderim başarısız: {e}")


# --- TEKRARLANAN GÖNDERİ AKIŞI ---

async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Tekrarlanan gönderi zamanlama akışını başlatır."""
    context.user_data.clear()
    await update.message.reply_html(
        "<b>Harika! Tekrarlanacak bir gönderi ayarlayalım.</b>\n\n"
        "Lütfen gönderinin içeriğini (metin, premium emoji, fotoğraf vb.) şimdi gönderin."
    )
    return GET_CONTENT

async def get_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gönderinin içeriğini (metin, format, fotoğraf) yakalar ve kaydeder."""
    message = update.message
    post_data = {}

    if message.photo:
        post_data['photo_file_id'] = message.photo[-1].file_id
        post_data['text'] = message.caption
        post_data['entities'] = [e.to_dict() for e in message.caption_entities]
    elif message.text:
        post_data['text'] = message.text
        post_data['entities'] = [e.to_dict() for e in message.entities]
    else:
        await message.reply_text("Lütfen metin veya fotoğraf içeren bir gönderi gönderin.")
        return GET_CONTENT

    context.user_data['post_data'] = post_data
    
    await message.reply_html(
        "✅ İçerik kaydedildi.\n\n"
        "Buton eklemek isterseniz şimdi gönderin (Format: <code>Buton Metni - https://link.com</code>).\n\n"
        "Buton eklemek istemiyorsanız /skip yazın."
    )
    return GET_BUTTONS

async def get_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Butonları alır ve hedef kanalı sorar."""
    buttons_text = update.message.text
    buttons = []
    try:
        for line in buttons_text.split('\n'):
            row_buttons = []
            parts = line.split(',')
            for part in parts:
                if ' - ' in part:
                    text, url = part.split(' - ', 1)
                    row_buttons.append(InlineKeyboardButton(text.strip(), url=url.strip()))
            if row_buttons:
                buttons.append(row_buttons)
        
        if buttons:
            reply_markup = InlineKeyboardMarkup(buttons)
            context.user_data['post_data']['reply_markup'] = reply_markup.to_dict()
            await update.message.reply_text("✅ Butonlar ayarlandı.")
        else:
            await update.message.reply_text("Geçerli buton bulunamadı.")

    except Exception as e:
        await update.message.reply_text(f"Buton formatı hatalı, lütfen tekrar deneyin. Hata: {e}")
        return GET_BUTTONS

    await update.message.reply_html("Şimdi bu gönderinin yayınlanacağı <b>kanalın ID'sini</b> veya <b>@kullaniciadini</b> girin:")
    return GET_RECURRING_CHANNEL

async def skip_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Buton ekleme adımını atlar."""
    context.user_data['post_data']['reply_markup'] = None
    await update.message.reply_html(
        "Buton eklenmedi.\n\n"
        "Şimdi bu gönderinin yayınlanacağı <b>kanalın ID'sini</b> veya <b>@kullaniciadini</b> girin:"
    )
    return GET_RECURRING_CHANNEL

async def get_recurring_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Hedef kanalı alır ve günleri sorar."""
    context.user_data['post_data']['channel_id'] = update.message.text
    await update.message.reply_html(
        "✅ Kanal ayarlandı.\n\n"
        "Bu gönderi haftanın hangi günleri yayınlansın?\n"
        "<i>(Örnek: Pazartesi, Çarşamba, Cuma)</i>"
    )
    return GET_RECURRING_DAYS

async def get_recurring_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Tekrarlanacak günleri alır ve saati sorar."""
    days = parse_turkish_days(update.message.text)
    if days is None:
        await update.message.reply_text("Geçersiz gün ismi. Lütfen tekrar deneyin (Örn: Salı, Perşembe).")
        return GET_RECURRING_DAYS
    context.user_data['days'] = days
    await update.message.reply_html("✅ Günler ayarlandı.\n\nPeki saat kaçta yayınlansın?\n<i>(Format: SS:DD, Örn: 09:30)</i>")
    return GET_RECURRING_TIME

async def get_recurring_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Tekrarlanacak saati alır ve onay ister."""
    try:
        schedule_time = dt_time.fromisoformat(update.message.text)
        context.user_data['time'] = schedule_time
    except ValueError:
        await update.message.reply_text("Zaman formatı yanlış. Lütfen `SS:DD` formatında girin.")
        return GET_RECURRING_TIME

    ud = context.user_data
    post_data = ud['post_data']
    
    await update.message.reply_text("--- GÖNDERİ ÖNİZLEMESİ ---")
    await send_scheduled_content(ContextTypes.DEFAULT_TYPE(application=context.application, chat_id=update.effective_chat.id, job=type('Job', (object,), {'data': {'channel_id': update.effective_chat.id, **post_data}})))

    day_names = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    selected_days = ", ".join([day_names[i] for i in ud['days']])
    
    confirmation_text = (
        f"Yukarıdaki gönderi, <b>{post_data['channel_id']}</b> kanalına her <b>{selected_days}</b> günü saat "
        f"<b>{ud['time'].strftime('%H:%M')}</b>'da (Türkiye saati ile) paylaşılmak üzere ayarlanacak.\n\nOnaylıyor musunuz?"
    )
    confirm_buttons = [[InlineKeyboardButton("✅ Onayla ve Zamanla", callback_data="confirm_recurring")], [InlineKeyboardButton("❌ İptal Et", callback_data="cancel_recurring")]]
    await update.message.reply_html(confirmation_text, reply_markup=InlineKeyboardMarkup(confirm_buttons))
    
    return CONFIRM_RECURRING_SCHEDULE

async def schedule_recurring_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Onay sonrası tekrarlanan görevi oluşturur."""
    query = update.callback_query
    await query.answer()

    if query.data == 'cancel_recurring':
        await query.edit_message_text("İşlem iptal edildi.")
        context.user_data.clear()
        return ConversationHandler.END

    ud = context.user_data
    job_name = f"recurring_{update.effective_chat.id}_{uuid.uuid4()}"
    
    context.job_queue.run_daily(
        send_scheduled_content,
        time=ud['time'],
        days=ud['days'],
        tzinfo=TURKISH_TIMEZONE,
        chat_id=update.effective_chat.id,
        name=job_name,
        data=ud['post_data']
    )

    await query.edit_message_text("✅ Harika! Gönderiniz başarıyla zamanlandı.")
    context.user_data.clear()
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botu başlatan ve ana komutları gösteren fonksiyon."""
    await update.message.reply_html(
        "<b>👋 Merhaba! Kanal Yönetim Botuna Hoş Geldiniz!</b>\n\n"
        "Tekrarlanan gönderiler (fotoğraf, emoji, buton destekli) zamanlamak için /schedule komutunu kullanın.\n\n"
        "İşlemi istediğiniz zaman iptal etmek için /cancel komutunu kullanabilirsiniz."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Herhangi bir sohbeti iptal eder."""
    context.user_data.clear()
    await update.message.reply_text("İşlem iptal edildi.")
    return ConversationHandler.END

def main() -> None:
    """Botu başlatır ve çalıştırır."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN':
        logger.error("TELEGRAM_BOT_TOKEN bulunamadı! Lütfen kod içinde veya ortam değişkeni olarak ayarlayın.")
        return
        
    persistence = PicklePersistence(filepath="channel_helper_bot_data")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()

    schedule_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("schedule", schedule_command)],
        states={
            GET_CONTENT: [MessageHandler(filters.TEXT | filters.PHOTO, get_content)],
            GET_BUTTONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_buttons), CommandHandler("skip", skip_buttons)],
            GET_RECURRING_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_recurring_channel)],
            GET_RECURRING_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_recurring_days)],
            GET_RECURRING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_recurring_time)],
            CONFIRM_RECURRING_SCHEDULE: [CallbackQueryHandler(schedule_recurring_post, pattern="^confirm_recurring$")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern="^cancel_recurring$")
        ],
        persistent=True,
        name="schedule_post_conversation"
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(schedule_conv_handler)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
