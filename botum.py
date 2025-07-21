# Gerekli kütüphaneleri içe aktarıyoruz.
# Bu kütüphaneyi yüklemek için: pip install "python-telegram-bot[persistence]" pytz
import logging
import uuid
import os
import pytz
from datetime import datetime, time as dt_time
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
# Render'da çalıştırırken, token'ı 'TELEGRAM_BOT_TOKEN' adlı bir ortam değişkeni olarak ayarlayın.
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
GET_FORWARDED_POST, GET_RECURRING_CHANNEL, GET_RECURRING_DAYS, GET_RECURRING_TIME, CONFIRM_RECURRING_SCHEDULE = range(5)


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
            return None # Geçersiz gün ismi
    return tuple(sorted(list(set(day_numbers))))


# --- GÖNDERİ GÖNDERME FONKSİYONU ---

async def send_scheduled_content(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Zamanı geldiğinde kaydedilmiş gönderiyi (fotoğraf veya metin) kanala yollar."""
    job = context.job
    post_data = job.data

    if not post_data:
        logger.warning(f"İş {job.name} için gönderi verisi bulunamadı.")
        return

    channel_id = post_data.get('channel_id')
    text = post_data.get('text')
    photo_file_id = post_data.get('photo_file_id')

    # Kaydedilmiş sözlüklerden MessageEntity nesnelerini güvenli bir şekilde yeniden oluşturur.
    entities = []
    if post_data.get('entities'):
        for entity_dict in post_data['entities']:
            clean_dict = entity_dict.copy()
            clean_dict.pop('user', None)
            entities.append(MessageEntity(**clean_dict))

    # Kaydedilmiş sözlüklerden InlineKeyboardButton nesnelerini yeniden oluşturur.
    buttons_data = post_data.get('buttons', [])
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(b['text'], url=b['url']) for b in row] for row in buttons_data]) if buttons_data else None

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
        "Lütfen zamanlamak istediğiniz gönderiyi (fotoğraf, metin, buton ve premium emoji içerebilir) bana <b>iletin</b>."
    )
    return GET_FORWARDED_POST

async def get_forwarded_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """İletilen gönderiyi yakalar, kaydeder ve hedef kanalı sorar."""
    message = update.message
    post_data = {}

    def entities_to_dict(entities):
        if not entities:
            return []
        return [e.to_dict() for e in entities]

    if message.photo:
        post_data['photo_file_id'] = message.photo[-1].file_id
        post_data['text'] = message.caption
        post_data['entities'] = entities_to_dict(message.caption_entities)
    elif message.text:
        post_data['text'] = message.text
        post_data['entities'] = entities_to_dict(message.entities)
    else:
        await message.reply_text("Lütfen metin veya fotoğraf içeren bir gönderi iletin.")
        return GET_FORWARDED_POST

    if message.reply_markup:
        post_data['buttons'] = [[{'text': b.text, 'url': b.url} for b in row] for row in message.reply_markup.inline_keyboard]

    context.user_data['post_data'] = post_data
    
    await message.reply_html(
        "✅ Gönderi kaydedildi.\n\n"
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
    await update.message.reply_html(
        "✅ Günler ayarlandı.\n\n"
        "Peki saat kaçta yayınlansın?\n"
        "<i>(Format: SS:DD, Örn: 09:30)</i>"
    )
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
    photo_file_id = post_data.get('photo_file_id')
    text = post_data.get('text')

    entities = []
    if post_data.get('entities'):
        for entity_dict in post_data['entities']:
            clean_dict = entity_dict.copy()
            clean_dict.pop('user', None)
            entities.append(MessageEntity(**clean_dict))
            
    buttons_data = post_data.get('buttons', [])
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(b['text'], url=b['url']) for b in row] for row in buttons_data]) if buttons_data else None

    await update.message.reply_text("--- GÖNDERİ ÖNİZLEMESİ ---")
    if photo_file_id:
        await update.message.reply_photo(photo=photo_file_id, caption=text, caption_entities=entities, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text=text, entities=entities, reply_markup=reply_markup)

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
    
    # --- GÜNCELLENMİŞ BLOK ---
    # Zamanlayıcıyı Türkiye saat dilimine göre ayarlıyoruz.
    context.job_queue.run_daily(
        send_scheduled_content,
        time=ud['time'],
        days=ud['days'],
        tzinfo=TURKISH_TIMEZONE,  # Saat dilimini burada belirtiyoruz
        chat_id=update.effective_chat.id,
        name=job_name,
        data=ud['post_data']
    )
    # --- GÜNCELLENMİŞ BLOK SONU ---

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
            GET_FORWARDED_POST: [MessageHandler(filters.ALL & ~filters.COMMAND, get_forwarded_post)],
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
